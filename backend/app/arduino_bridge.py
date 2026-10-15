"""아두이노 경보기(USB 시리얼) 연결.

서버 → 아두이노: "A1\n"(경보 켜기) / "A0\n"(경보 끄기)를 1초마다 보낸다.
아두이노는 3초 동안 명령이 없으면 스스로 경보를 끈다.
아두이노 → 서버: "CO2:850\n" 형식으로 이산화탄소 농도를 보낸다.
창문(서보) 개폐는 아두이노가 CO2 값으로 직접 판단한다.
연결이 끊기면 백그라운드에서 5초마다 다시 연결한다.
"""

import logging
import threading
import time

from app.config import get_settings

logger = logging.getLogger(__name__)

RESEND_SECONDS = 1.0
RECONNECT_SECONDS = 5.0
WRITE_TIMEOUT_SECONDS = 0.5   # 아두이노가 멈춰도 감지 루프가 기다리지 않게
CO2_STALE_SECONDS = 10.0      # 이 시간 동안 새 값이 없으면 co2_ppm을 None으로


def alarm_required(result: dict) -> bool:
    """2·3단계이고 사용자가 아직 경보를 중지하지 않았으면 경보가 필요하다."""
    return result.get("drowsy_level") in (2, 3) and not result.get("alarm_acknowledged", False)


def _open_serial(port: str, baud: int):
    import serial  # pyserial
    connection = serial.Serial(port, baud, timeout=1, write_timeout=WRITE_TIMEOUT_SECONDS)
    time.sleep(2)  # 포트를 열면 아두이노가 재부팅된다
    return connection


class ArduinoBridge:
    def __init__(self, port: str | None, baud: int = 9600, serial_factory=None):
        self._port = port
        self._baud = baud
        self._serial_factory = serial_factory or _open_serial
        self._serial = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._running = False
        self._last_alarm: bool | None = None
        self._last_sent_at = 0.0
        self._co2_value: int | None = None
        self._co2_at = 0.0

    @property
    def connected(self) -> bool:
        return self._serial is not None

    @property
    def co2_ppm(self) -> int | None:
        return self.co2_ppm_at(time.monotonic())

    def co2_ppm_at(self, now: float) -> int | None:
        if self._co2_value is None or now - self._co2_at > CO2_STALE_SECONDS:
            return None
        return self._co2_value

    def open(self):
        """첫 연결을 시도하고, 수신·재연결을 맡는 백그라운드 스레드를 시작한다."""
        if not self._port or self._running:
            return
        self._running = True
        self._connect()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def update(self, result: dict, now: float | None = None):
        """감지 결과마다 호출. 상태가 바뀌었거나 1초가 지났으면 명령을 보낸다."""
        alarm = alarm_required(result)
        now = time.monotonic() if now is None else now
        if alarm != self._last_alarm or now - self._last_sent_at >= RESEND_SECONDS:
            self._send(alarm, now)

    def alarm_off(self):
        self._send(False, time.monotonic())

    def close(self):
        self.alarm_off()
        self._running = False
        self._drop_connection()

    def _connect(self) -> bool:
        try:
            connection = self._serial_factory(self._port, self._baud)
        except Exception as error:
            logger.warning("아두이노 연결 실패: port=%s (%s). %d초 후 재시도", self._port, error, RECONNECT_SECONDS)
            return False
        with self._lock:
            self._serial = connection
        self._last_alarm = None  # 재부팅된 아두이노에 현재 상태를 바로 다시 보낸다
        logger.info("아두이노 연결됨: port=%s", self._port)
        return True

    def _drop_connection(self):
        with self._lock:
            connection, self._serial = self._serial, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.exception("아두이노 포트 닫기 실패")

    def _send(self, alarm: bool, now: float):
        with self._lock:
            connection = self._serial
            if connection is None:
                return
            try:
                connection.write(b"A1\n" if alarm else b"A0\n")
            except Exception as error:
                logger.warning("아두이노 전송 실패 (%s). 연결을 끊고 재연결합니다", error)
                connection = None
        if connection is None:
            self._drop_connection()
            return
        self._last_alarm = alarm
        self._last_sent_at = now

    def handle_line(self, line: str, now: float | None = None):
        line = line.strip()
        if line.startswith("CO2:"):
            try:
                value = int(line[4:])
            except ValueError:
                return
            self._co2_value = value
            self._co2_at = time.monotonic() if now is None else now

    def _worker_loop(self):
        while self._running:
            connection = self._serial
            if connection is None:
                time.sleep(RECONNECT_SECONDS)
                if self._running:
                    self._connect()
                continue
            try:
                raw = connection.readline()
            except Exception as error:
                logger.warning("아두이노 수신 실패 (%s). 재연결합니다", error)
                self._drop_connection()
                continue
            if raw:
                self.handle_line(raw.decode("utf-8", errors="ignore"))


arduino = ArduinoBridge(None)


def start_arduino():
    global arduino
    settings = get_settings()
    arduino = ArduinoBridge(settings.ARDUINO_PORT, settings.ARDUINO_BAUD)
    arduino.open()


def get_arduino() -> ArduinoBridge:
    return arduino
