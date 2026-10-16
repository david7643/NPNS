"""아두이노 경보기(USB 시리얼) 연결.

통신 규칙 (arduino/README.md와 동일)
- 서버 → 아두이노: "A1\\n"(경보 켜기) / "A0\\n"(경보 끄기). 바뀌면 즉시, 아니면 1초마다 반복.
- 아두이노 → 서버: "ACK:A1"/"ACK:A0"(명령 처리), "ERR:CMD"(거부),
  "BOOT"(재부팅), "CO2:850"(측정값), "CO2:NA"(측정값 없음).

스레드 구조
- 감지 스레드는 update()/clear_alarm()으로 원하는 경보 상태만 바꾼다. 입출력을 하지 않는다.
- 연결·송신·수신·재연결은 전용 워커 스레드 하나가 모두 맡는다.

경보 정책
- 원하는 경보 상태는 새 감지 결과로만 바뀐다. 감지가 멈추면 마지막 상태를 유지한다.
  2·3단계 경보는 앱의 중지 확인(alarm_acknowledged)이나 감지 종료(clear_alarm)로만 꺼진다.
"""

import logging
import threading
import time

from app.config import get_settings

logger = logging.getLogger(__name__)

RESEND_SECONDS = 1.0
RECONNECT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 0.1    # 워커가 송신을 놓치지 않도록 짧게
WRITE_TIMEOUT_SECONDS = 0.5   # 워커 스레드에서만 기다린다
ACK_FRESH_SECONDS = 3.0       # 이 안에 ACK가 왔으면 장치가 응답 중
CO2_STALE_SECONDS = 10.0
CO2_MIN_VALID = 300           # 펌웨어와 같은 유효 범위
CO2_MAX_VALID = 10000
BOOT_WAIT_SECONDS = 2.0       # 포트를 열면 아두이노가 재부팅된다
CLOSE_JOIN_SECONDS = BOOT_WAIT_SECONDS + 1.0


def alarm_required(result: dict) -> bool:
    """2·3단계이고 사용자가 아직 경보를 중지하지 않았으면 경보가 필요하다."""
    return result.get("drowsy_level") in (2, 3) and not result.get("alarm_acknowledged", False)


def _open_serial(port: str, baud: int):
    import serial  # pyserial
    connection = serial.Serial(
        port, baud, timeout=READ_TIMEOUT_SECONDS, write_timeout=WRITE_TIMEOUT_SECONDS,
    )
    time.sleep(BOOT_WAIT_SECONDS)
    return connection


class ArduinoBridge:
    def __init__(self, port: str | None, baud: int = 9600, serial_factory=None, clock=time.monotonic):
        self._port = port
        self._baud = baud
        self._serial_factory = serial_factory or _open_serial
        self._clock = clock
        self._serial = None
        self._worker: threading.Thread | None = None
        self._running = False
        self._wake = threading.Event()
        self._lifecycle_lock = threading.Lock()

        # 감지 스레드가 쓰는 값 (단일 대입만 하므로 잠금 불필요)
        self._desired_alarm = False

        # 워커 스레드가 쓰는 값
        self._next_connect_at = 0.0
        self._sent_alarm: bool | None = None
        self._last_sent_at = 0.0
        self._last_ack_at: float | None = None
        self._device_alarm: bool | None = None
        self._co2: tuple[int, float] | None = None   # (ppm, 수신 시각)

    # ── 상태 조회 ──
    @property
    def enabled(self) -> bool:
        return bool(self._port)

    @property
    def connected(self) -> bool:
        return self._serial is not None

    @property
    def desired_alarm(self) -> bool:
        return self._desired_alarm

    def responding_at(self, now: float) -> bool:
        return self.connected and self._last_ack_at is not None and now - self._last_ack_at <= ACK_FRESH_SECONDS

    @property
    def responding(self) -> bool:
        return self.responding_at(self._clock())

    def co2_ppm_at(self, now: float) -> int | None:
        co2 = self._co2
        if co2 is None or now - co2[1] > CO2_STALE_SECONDS:
            return None
        return co2[0]

    @property
    def co2_ppm(self) -> int | None:
        return self.co2_ppm_at(self._clock())

    def status(self) -> dict:
        now = self._clock()
        return {
            "arduino_enabled": self.enabled,
            "arduino_connected": self.connected,
            "arduino_responding": self.responding_at(now),
            "arduino_alarm_on": self._device_alarm if self.responding_at(now) else None,
            "co2_ppm": self.co2_ppm_at(now),
        }

    # ── 감지 스레드용 (입출력 없음) ──
    def update(self, result: dict):
        """새 감지 결과로 원하는 경보 상태를 정한다. 즉시 반환한다."""
        self._set_desired(alarm_required(result))

    def clear_alarm(self):
        """감지 종료 등 명시적인 경보 해제."""
        self._set_desired(False)

    def _set_desired(self, alarm: bool):
        if alarm != self._desired_alarm:
            self._desired_alarm = alarm
            self._wake.set()

    # ── 수명 주기 ──
    def open(self):
        """워커 스레드를 시작한다. 연결은 워커가 하므로 서버 시작을 지연시키지 않는다."""
        with self._lifecycle_lock:
            if not self._port or self._running or (self._worker and self._worker.is_alive()):
                return
            self._running = True
            self._next_connect_at = 0.0
            self._worker = threading.Thread(target=self._worker_loop, name="arduino-bridge", daemon=True)
            self._worker.start()

    def close(self):
        """종료를 요청한다. 포트 입출력과 최종 정리는 기존 워커가 끝낸다."""
        with self._lifecycle_lock:
            self._desired_alarm = False
            self._running = False
            self._wake.set()
            worker = self._worker
            connection = self._detach_connection() if worker is None else None
        self._close_serial(connection, alarm_off=True)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=CLOSE_JOIN_SECONDS)
            if worker.is_alive():
                logger.warning("아두이노 워커 종료 대기 중입니다. 워커가 포트를 정리한 뒤 종료합니다")
                return
            with self._lifecycle_lock:
                if self._worker is worker:
                    self._worker = None

    # ── 워커 ──
    def _worker_loop(self):
        try:
            while self._running:
                self.service_once()
                if self._serial is None and self._running:
                    self._wake.wait(timeout=READ_TIMEOUT_SECONDS)
                    self._wake.clear()
        finally:
            with self._lifecycle_lock:
                self._running = False
                connection = self._detach_connection()
            self._close_serial(connection, alarm_off=True)

    def service_once(self):
        """워커의 한 주기: 연결 → 송신 → 수신. 테스트에서 직접 호출할 수 있다."""
        if self._serial is None:
            if self._clock() >= self._next_connect_at:
                self._connect()
            return
        self._send_if_due(self._clock())
        if self._serial is not None:
            self._read_one_line()

    def _connect(self):
        try:
            connection = self._serial_factory(self._port, self._baud)
        except Exception as error:
            logger.warning("아두이노 연결 실패: port=%s (%s). %d초 후 재시도", self._port, error, RECONNECT_SECONDS)
            self._next_connect_at = self._clock() + RECONNECT_SECONDS
            return
        with self._lifecycle_lock:
            accepted = self._running
            if accepted:
                self._serial = connection
                self._reset_link_state()
        if not accepted:
            # 연결하는 동안 close()가 호출됐다. 열린 포트를 남기지 않는다.
            try:
                connection.close()
            except Exception:
                logger.debug("종료 중 연결된 포트 닫기 실패", exc_info=True)
            return
        logger.info("아두이노 연결됨: port=%s", self._port)

    def _reset_link_state(self):
        """새 연결·재부팅·연결 끊김마다 이전 장치 상태와 CO2를 버린다."""
        self._sent_alarm = None
        self._last_ack_at = None
        self._device_alarm = None
        self._co2 = None

    def _detach_connection(self):
        """수명 주기 잠금을 잡은 호출자가 포트 소유권을 가져간다."""
        connection, self._serial = self._serial, None
        self._reset_link_state()
        return connection

    def _close_serial(self, connection, *, alarm_off=False):
        if connection is not None:
            try:
                if alarm_off:
                    connection.write(b"A0\n")
            except Exception:
                logger.warning("종료 중 아두이노 경보 해제 실패", exc_info=True)
            finally:
                try:
                    connection.close()
                except Exception:
                    logger.exception("아두이노 포트 닫기 실패")

    def _drop_connection(self):
        with self._lifecycle_lock:
            connection = self._detach_connection()
        self._close_serial(connection)

    def _send_if_due(self, now: float):
        alarm = self._desired_alarm
        if alarm == self._sent_alarm and now - self._last_sent_at < RESEND_SECONDS:
            return
        if self._write_line(b"A1\n" if alarm else b"A0\n"):
            self._sent_alarm = alarm
            self._last_sent_at = now

    def _write_line(self, data: bytes) -> bool:
        connection = self._serial
        if connection is None:
            return False
        try:
            connection.write(data)
            return True
        except Exception as error:
            logger.warning("아두이노 전송 실패 (%s). 연결을 끊고 재연결합니다", error)
            self._lose_connection()
            return False

    def _read_one_line(self):
        try:
            raw = self._serial.readline()
        except Exception as error:
            logger.warning("아두이노 수신 실패 (%s). 재연결합니다", error)
            self._lose_connection()
            return
        if raw:
            self.handle_line(raw.decode("utf-8", errors="ignore"), self._clock())

    def _lose_connection(self):
        self._drop_connection()
        self._next_connect_at = self._clock() + RECONNECT_SECONDS

    def handle_line(self, line: str, now: float):
        line = line.strip()
        if line in ("ACK:A0", "ACK:A1"):
            self._last_ack_at = now
            self._device_alarm = line == "ACK:A1"
        elif line == "BOOT":
            # 재부팅한 장치는 경보가 꺼져 있다. 현재 상태를 바로 다시 보낸다.
            self._sent_alarm = None
            self._last_ack_at = None
            self._device_alarm = False
            self._co2 = None
        elif line == "ERR:CMD":
            logger.warning("아두이노가 명령을 거부했습니다")
        elif line == "CO2:NA":
            self._co2 = None
        elif line.startswith("CO2:"):
            try:
                value = int(line[4:])
            except ValueError:
                return
            if CO2_MIN_VALID <= value <= CO2_MAX_VALID:
                self._co2 = (value, now)
            # 범위를 벗어난 값은 버리고 기존 값의 유효기간도 늘리지 않는다


arduino = ArduinoBridge(None)


def start_arduino():
    global arduino
    settings = get_settings()
    arduino = ArduinoBridge(settings.ARDUINO_PORT, settings.ARDUINO_BAUD)
    arduino.open()


def get_arduino() -> ArduinoBridge:
    return arduino
