import threading
import time
import unittest
from unittest.mock import patch

from app import arduino_bridge
from app.arduino_bridge import ArduinoBridge, alarm_required


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeSerial:
    """쓴 줄을 기록하고, 미리 넣어둔 줄을 readline()으로 돌려주는 가짜 포트."""

    def __init__(self, fail_write=False, write_delay=0.0):
        self.written = []
        self.incoming = []
        self.fail_write = fail_write
        self.write_delay = write_delay
        self.closed = False

    def write(self, data):
        if self.write_delay:
            time.sleep(self.write_delay)
        if self.fail_write:
            raise TimeoutError("write timeout")
        self.written.append(data)

    def readline(self):
        if self.incoming:
            return self.incoming.pop(0)
        time.sleep(0.005)
        return b""

    def close(self):
        self.closed = True


def make_bridge(*connections, clock=None):
    """service_once()를 직접 돌리는 결정적 브리지 (워커 스레드 없음)."""
    queue = list(connections)

    def factory(*args):
        if not queue:
            raise OSError("unplugged")
        return queue.pop(0)

    bridge = ArduinoBridge("FAKE", serial_factory=factory, clock=clock or FakeClock())
    bridge._running = True  # open() 없이 수명 주기 상태만 켠다
    return bridge


class AlarmPolicyTests(unittest.TestCase):
    def test_alarm_only_for_unacknowledged_level_two_and_three(self):
        self.assertFalse(alarm_required({"drowsy_level": 0}))
        self.assertFalse(alarm_required({"drowsy_level": 1}))
        self.assertTrue(alarm_required({"drowsy_level": 2}))
        self.assertTrue(alarm_required({"drowsy_level": 3, "alarm_acknowledged": False}))
        self.assertFalse(alarm_required({"drowsy_level": 3, "alarm_acknowledged": True}))


class SendingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.port = FakeSerial()
        self.bridge = make_bridge(self.port, clock=self.clock)
        self.bridge.service_once()  # 연결

    def test_sends_on_change_and_resends_every_second(self):
        self.bridge.service_once()                  # 첫 상태 A0
        self.clock.advance(0.3)
        self.bridge.service_once()                  # 변화 없음, 1초 안 됨
        self.bridge.update({"drowsy_level": 2})
        self.bridge.service_once()                  # 변화 → 즉시
        self.clock.advance(1.0)
        self.bridge.service_once()                  # 1초 → 재전송
        self.assertEqual(self.port.written, [b"A0\n", b"A1\n", b"A1\n"])

    def test_alarm_is_kept_and_resent_while_detection_is_stalled(self):
        self.bridge.update({"drowsy_level": 3})
        for _ in range(6):                          # 이후 감지 결과가 6초 동안 없음
            self.bridge.service_once()
            self.clock.advance(1.0)
        self.assertEqual(self.port.written, [b"A1\n"] * 6)

    def test_acknowledge_and_explicit_stop_turn_alarm_off(self):
        self.bridge.update({"drowsy_level": 3})
        self.bridge.service_once()
        self.bridge.update({"drowsy_level": 3, "alarm_acknowledged": True})
        self.bridge.service_once()
        self.bridge.update({"drowsy_level": 2})
        self.bridge.service_once()
        self.bridge.clear_alarm()
        self.bridge.service_once()
        self.assertEqual(self.port.written, [b"A1\n", b"A0\n", b"A1\n", b"A0\n"])

    def test_update_does_no_io_even_when_port_is_slow(self):
        self.port.write_delay = 0.5
        started = time.perf_counter()
        for level in (3, 0, 2, 0, 3):
            self.bridge.update({"drowsy_level": level})
        self.assertLess(time.perf_counter() - started, 0.05)
        self.assertEqual(self.port.written, [])


class DeviceResponseTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.port = FakeSerial()
        self.bridge = make_bridge(self.port, clock=self.clock)
        self.bridge.service_once()

    def test_open_but_silent_port_is_not_responding(self):
        self.bridge.service_once()
        self.assertTrue(self.bridge.connected)
        self.assertFalse(self.bridge.responding)
        self.assertIsNone(self.bridge.status()["arduino_alarm_on"])

    def test_ack_marks_device_responding_until_it_goes_quiet(self):
        self.bridge.update({"drowsy_level": 3})
        self.port.incoming.append(b"ACK:A1\r\n")
        self.bridge.service_once()
        status = self.bridge.status()
        self.assertTrue(status["arduino_responding"])
        self.assertTrue(status["arduino_alarm_on"])
        self.clock.advance(arduino_bridge.ACK_FRESH_SECONDS + 0.1)
        self.assertFalse(self.bridge.responding)

    def test_boot_line_forces_immediate_resend(self):
        self.bridge.update({"drowsy_level": 3})
        self.bridge.service_once()                  # A1
        self.port.incoming.append(b"BOOT\r\n")
        self.bridge.service_once()                  # 0초 경과지만 재부팅 → 다시 보냄
        self.bridge.service_once()
        self.assertEqual(self.port.written, [b"A1\n", b"A1\n"])

    def test_boot_requires_an_ack_from_the_new_boot(self):
        self.bridge.handle_line("ACK:A1", now=self.clock())
        self.assertTrue(self.bridge.responding)
        self.bridge.handle_line("BOOT", now=self.clock())
        self.assertFalse(self.bridge.responding)
        self.assertIsNone(self.bridge.status()["arduino_alarm_on"])
        self.bridge.handle_line("ACK:A0", now=self.clock())
        self.assertTrue(self.bridge.responding)
        self.assertFalse(self.bridge.status()["arduino_alarm_on"])


class ConnectionTests(unittest.TestCase):
    def test_write_failure_drops_and_reconnects_with_current_state(self):
        clock = FakeClock()
        stuck, fresh = FakeSerial(fail_write=True), FakeSerial()
        bridge = make_bridge(stuck, fresh, clock=clock)
        bridge.service_once()                       # stuck 연결
        bridge.update({"drowsy_level": 3})
        bridge.service_once()                       # 전송 실패 → 끊김
        self.assertFalse(bridge.connected)
        self.assertTrue(stuck.closed)
        bridge.service_once()                       # 아직 재연결 대기
        self.assertFalse(bridge.connected)
        clock.advance(arduino_bridge.RECONNECT_SECONDS)
        bridge.service_once()                       # fresh 연결
        bridge.service_once()                       # 현재 상태 즉시 전송
        self.assertEqual(fresh.written, [b"A1\n"])

    def test_missing_device_keeps_retrying_without_raising(self):
        clock = FakeClock()
        bridge = make_bridge(clock=clock)
        bridge.update({"drowsy_level": 3})
        bridge.service_once()
        clock.advance(arduino_bridge.RECONNECT_SECONDS)
        bridge.service_once()
        self.assertFalse(bridge.connected)

    def test_without_port_does_nothing(self):
        bridge = ArduinoBridge(None)
        bridge.open()
        bridge.update({"drowsy_level": 3})
        self.assertFalse(bridge.enabled)
        self.assertFalse(bridge.connected)
        bridge.close()


class LifecycleTests(unittest.TestCase):
    def test_close_racing_with_connection_registration_closes_the_port(self):
        entered, release, close_started, close_done = (threading.Event() for _ in range(4))
        port = FakeSerial()

        class PausingBridge(ArduinoBridge):
            def __setattr__(self, name, value):
                if name == "_serial" and value is port and not entered.is_set():
                    entered.set()
                    release.wait(3)
                super().__setattr__(name, value)

        bridge = PausingBridge("FAKE", serial_factory=lambda *args: port)

        def close():
            close_started.set()
            bridge.close()
            close_done.set()

        with patch.object(arduino_bridge, "CLOSE_JOIN_SECONDS", 0.01):
            bridge.open()
            worker = bridge._worker
            closer = threading.Thread(target=close)
            try:
                self.assertTrue(entered.wait(2))
                closer.start()
                self.assertTrue(close_started.wait(2))
                close_done.wait(0.05)
            finally:
                release.set()
                if closer.ident is not None:
                    closer.join(2)
                worker.join(2)
                bridge.close()
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertTrue(port.closed)
        self.assertFalse(bridge.connected)

    def test_close_timeout_leaves_io_and_cleanup_to_the_worker(self):
        entered, release = threading.Event(), threading.Event()

        class SlowSerial(FakeSerial):
            def write(self, data):
                if not entered.is_set():
                    entered.set()
                    release.wait(3)
                super().write(data)

        port = SlowSerial()
        bridge = ArduinoBridge("FAKE", serial_factory=lambda *args: port)
        bridge.update({"drowsy_level": 3})
        bridge.open()
        worker = bridge._worker
        try:
            self.assertTrue(entered.wait(2))
            with patch.object(arduino_bridge, "CLOSE_JOIN_SECONDS", 0.01):
                bridge.close()
            self.assertIs(bridge._worker, worker)
            self.assertTrue(worker.is_alive())
            self.assertFalse(port.closed)
            bridge.open()
            self.assertIs(bridge._worker, worker)
        finally:
            release.set()
            worker.join(2)
            bridge.close()
        self.assertFalse(worker.is_alive())
        self.assertTrue(port.closed)
        self.assertFalse(bridge.connected)
        self.assertEqual(port.written[-1], b"A0\n")

    def test_open_does_not_block_on_slow_connect(self):
        release = threading.Event()

        def slow_factory(*args):
            release.wait(2)
            return FakeSerial()

        bridge = ArduinoBridge("FAKE", serial_factory=slow_factory)
        started = time.perf_counter()
        bridge.open()
        self.assertLess(time.perf_counter() - started, 0.1)
        release.set()
        bridge.close()

    def test_close_during_connect_leaves_no_open_port(self):
        entered, release = threading.Event(), threading.Event()
        port = FakeSerial()

        def blocking_factory(*args):
            entered.set()
            release.wait(5)
            return port

        bridge = ArduinoBridge("FAKE", serial_factory=blocking_factory)
        original = arduino_bridge.CLOSE_JOIN_SECONDS
        arduino_bridge.CLOSE_JOIN_SECONDS = 0.2
        self.addCleanup(setattr, arduino_bridge, "CLOSE_JOIN_SECONDS", original)

        bridge.open()
        self.assertTrue(entered.wait(2))
        bridge.close()                              # 워커가 연결 중일 때 종료
        release.set()                               # 그 뒤에 연결 완료
        deadline = time.monotonic() + 2
        while not port.closed and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(port.closed)
        self.assertFalse(bridge.connected)

    def test_close_sends_alarm_off_stops_worker_and_closes_port(self):
        port = FakeSerial()
        bridge = ArduinoBridge("FAKE", serial_factory=lambda *a: port)
        bridge.open()
        bridge.update({"drowsy_level": 3})
        deadline = time.monotonic() + 2
        while b"A1\n" not in port.written and time.monotonic() < deadline:
            time.sleep(0.01)
        worker = bridge._worker
        bridge.close()
        self.assertFalse(worker.is_alive())
        self.assertEqual(port.written[-1], b"A0\n")
        self.assertTrue(port.closed)
        self.assertFalse(bridge.connected)


class Co2Tests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.bridge = ArduinoBridge(None, clock=self.clock)

    def test_parses_valid_values_and_ignores_other_lines(self):
        self.bridge.handle_line("CO2:1234\r\n", now=100.0)
        self.bridge.handle_line("MH-Z19E 예열 중", now=101.0)
        self.bridge.handle_line("CO2:abc", now=101.0)
        self.assertEqual(self.bridge.co2_ppm_at(101.0), 1234)

    def test_out_of_range_values_are_rejected_and_do_not_extend_validity(self):
        self.bridge.handle_line("CO2:900", now=100.0)
        for bad in ("CO2:-1", "CO2:0", "CO2:299", "CO2:10001", "CO2:999999"):
            self.bridge.handle_line(bad, now=108.0)
        self.assertEqual(self.bridge.co2_ppm_at(108.0), 900)
        self.assertIsNone(self.bridge.co2_ppm_at(110.5))

    def test_na_line_clears_value(self):
        self.bridge.handle_line("CO2:900", now=100.0)
        self.bridge.handle_line("CO2:NA", now=101.0)
        self.assertIsNone(self.bridge.co2_ppm_at(101.0))

    def test_value_becomes_none_when_stale(self):
        self.assertIsNone(self.bridge.co2_ppm_at(0.0))
        self.bridge.handle_line("CO2:900", now=100.0)
        self.assertEqual(self.bridge.co2_ppm_at(109.0), 900)
        self.assertIsNone(self.bridge.co2_ppm_at(111.0))

    def test_disconnect_and_reconnect_discard_previous_value(self):
        clock = FakeClock()
        first, second = FakeSerial(fail_write=True), FakeSerial()
        bridge = make_bridge(first, second, clock=clock)
        bridge.service_once()
        bridge.handle_line("CO2:2500", now=clock())
        self.assertEqual(bridge.co2_ppm, 2500)
        bridge.service_once()                       # 전송 실패 → 끊김
        self.assertIsNone(bridge.co2_ppm)
        clock.advance(arduino_bridge.RECONNECT_SECONDS)
        bridge.service_once()                       # 재연결, 아직 새 측정값 없음
        self.assertTrue(bridge.connected)
        self.assertIsNone(bridge.co2_ppm)


class DetectionSessionAlarmPolicyTests(unittest.IsolatedAsyncioTestCase):
    """감지 루프와 아두이노 경보 정책의 연결."""

    async def run_loop_with(self, results, error):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from app import detection_service

        bridge = ArduinoBridge(None)
        session = detection_service.DetectionSession(1, 7, asyncio.get_running_loop())
        frames = iter(results)

        class FakeDetector:
            processor = SimpleNamespace(engine=SimpleNamespace(
                acknowledge_alarm=lambda now: None, alarm_generation=0, alarm_acknowledged=False,
            ))

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                try:
                    return None, next(frames)
                except StopIteration:
                    raise error

        with patch.object(detection_service, "CameraDetector", FakeDetector), \
             patch.object(detection_service, "get_arduino", return_value=bridge):
            session._running = True
            await asyncio.to_thread(session._detection_loop)
            return bridge, session, detection_service

    def result(self, level):
        return {"drowsy_level": level, "alarm_acknowledged": False, "alert_event": False,
                "ear_value": 0.1, "pred_score": 0.9}

    async def test_alarm_stays_on_when_detection_crashes(self):
        bridge, session, _ = await self.run_loop_with([self.result(3)], RuntimeError("camera lost"))
        self.assertFalse(session.is_running)
        self.assertTrue(bridge.desired_alarm)

    async def test_explicit_stop_turns_alarm_off(self):
        from unittest.mock import patch
        bridge, session, detection_service = await self.run_loop_with([self.result(3)], RuntimeError("camera lost"))
        with patch.object(detection_service, "get_arduino", return_value=bridge):
            session.stop()
        self.assertFalse(bridge.desired_alarm)

    async def test_results_carry_device_status(self):
        from unittest.mock import patch
        from app import detection_service

        sent = []
        original = detection_service.DetectionSession._put_result

        def record(session, result):
            sent.append(result)
            original(session, result)

        with patch.object(detection_service.DetectionSession, "_put_result", record):
            await self.run_loop_with([self.result(2)], RuntimeError("end"))
        self.assertIn("co2_ppm", sent[0])
        self.assertIn("arduino_responding", sent[0])
