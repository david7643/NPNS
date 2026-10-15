"""아두이노 경보기(USB 시리얼) 연결.

서버 → 아두이노: "A1\n"(경보 켜기) / "A0\n"(경보 끄기)를 1초마다 보낸다.
아두이노는 3초 동안 명령이 없으면 스스로 경보를 끈다.
아두이노 → 서버: "CO2:850\n" 형식으로 이산화탄소 농도를 보낸다.
창문(서보) 개폐는 아두이노가 CO2 값으로 직접 판단한다.
"""

import logging
import threading
import time

from app.config import get_settings

logger = logging.getLogger(__name__)

RESEND_SECONDS = 1.0


def alarm_required(result: dict) -> bool:
    """2·3단계이고 사용자가 아직 경보를 중지하지 않았으면 경보가 필요하다."""
    return result.get("drowsy_level") in (2, 3) and not result.get("alarm_acknowledged", False)


class ArduinoBridge:
    def __init__(self, port: str | None, baud: int = 9600, serial_factory=None):
        self._port = port
        self._baud = baud
        self._serial_factory = serial_factory
        self._serial = None
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._last_alarm: bool | None = None
        self._last_sent_at = 0.0
        self.co2_ppm: int | None = None

    @property
    def connected(self) -> bool:
        return self._serial is not None

    def open(self):
        if not self._port:
            return
        try:
            if self._serial_factory is None:
                import serial  # pyserial
                self._serial = serial.Serial(self._port, self._baud, timeout=1)
                time.sleep(2)  # 포트를 열면 아두이노가 재부팅된다
            else:
                self._serial = self._serial_factory(self._port, self._baud)
        except Exception:
            logger.exception("아두이노 연결 실패: port=%s (경보기 없이 계속 실행)", self._port)
            self._serial = None
            return
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        logger.info("아두이노 연결됨: port=%s", self._port)

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
        with self._lock:
            serial_port, self._serial = self._serial, None
        if serial_port is not None:
            try:
                serial_port.close()
            except Exception:
                logger.exception("아두이노 포트 닫기 실패")

    def _send(self, alarm: bool, now: float):
        with self._lock:
            if self._serial is None:
                return
            try:
                self._serial.write(b"A1\n" if alarm else b"A0\n")
            except Exception:
                logger.exception("아두이노 전송 실패. 경보기 연결을 끊습니다")
                self._serial = None
                return
        self._last_alarm = alarm
        self._last_sent_at = now

    def handle_line(self, line: str):
        line = line.strip()
        if line.startswith("CO2:"):
            try:
                self.co2_ppm = int(line[4:])
            except ValueError:
                pass

    def _read_loop(self):
        while True:
            serial_port = self._serial
            if serial_port is None:
                return
            try:
                raw = serial_port.readline()
            except Exception:
                logger.exception("아두이노 수신 실패")
                return
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
