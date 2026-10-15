import time
import unittest

from app import arduino_bridge
from app.arduino_bridge import ArduinoBridge, alarm_required


class FakeSerial:
    def __init__(self, fail_write=False):
        self.written = []
        self.fail_write = fail_write
        self.closed = False

    def write(self, data):
        if self.fail_write:
            raise TimeoutError("write timeout")
        self.written.append(data)

    def readline(self):
        time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True


class ArduinoBridgeTests(unittest.TestCase):
    def make_bridge(self, *connections):
        queue = list(connections)

        def factory(*args):
            if not queue:
                raise OSError("unplugged")
            return queue.pop(0)

        bridge = ArduinoBridge("FAKE", serial_factory=factory)
        bridge.open()
        self.addCleanup(bridge.close)
        return bridge

    def test_alarm_only_for_unacknowledged_level_two_and_three(self):
        self.assertFalse(alarm_required({"drowsy_level": 0}))
        self.assertFalse(alarm_required({"drowsy_level": 1}))
        self.assertTrue(alarm_required({"drowsy_level": 2}))
        self.assertTrue(alarm_required({"drowsy_level": 3, "alarm_acknowledged": False}))
        self.assertFalse(alarm_required({"drowsy_level": 3, "alarm_acknowledged": True}))

    def test_sends_on_change_and_resends_every_second(self):
        fake = FakeSerial()
        bridge = self.make_bridge(fake)
        bridge.update({"drowsy_level": 0}, now=10.0)
        bridge.update({"drowsy_level": 0}, now=10.3)   # 변화 없음, 1초 안 됨
        bridge.update({"drowsy_level": 2}, now=10.4)   # 변화 → 즉시
        bridge.update({"drowsy_level": 2}, now=11.5)   # 1초 지남 → 재전송
        self.assertEqual(fake.written, [b"A0\n", b"A1\n", b"A1\n"])

    def test_close_turns_alarm_off(self):
        fake = FakeSerial()
        bridge = self.make_bridge(fake)
        bridge.update({"drowsy_level": 3}, now=1.0)
        bridge.close()
        self.assertEqual(fake.written[-1], b"A0\n")
        self.assertTrue(fake.closed)
        self.assertFalse(bridge.connected)

    def test_write_failure_drops_connection_without_raising(self):
        stuck = FakeSerial(fail_write=True)
        bridge = self.make_bridge(stuck)
        bridge.update({"drowsy_level": 3}, now=1.0)
        self.assertFalse(bridge.connected)
        self.assertTrue(stuck.closed)

    def test_reconnects_and_resends_current_state(self):
        first, second = FakeSerial(fail_write=True), FakeSerial()
        original = arduino_bridge.RECONNECT_SECONDS
        arduino_bridge.RECONNECT_SECONDS = 0.05
        self.addCleanup(setattr, arduino_bridge, "RECONNECT_SECONDS", original)

        bridge = self.make_bridge(first, second)
        bridge.update({"drowsy_level": 3}, now=1.0)    # 실패 → 연결 끊김
        deadline = time.monotonic() + 2
        while not bridge.connected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(bridge.connected)
        bridge.update({"drowsy_level": 3}, now=1.1)    # 1초 안 됐어도 재연결 후 바로 전송
        self.assertEqual(second.written, [b"A1\n"])

    def test_first_connection_failure_keeps_server_running(self):
        bridge = self.make_bridge()                     # 연결할 장치 없음
        bridge.update({"drowsy_level": 3})
        self.assertFalse(bridge.connected)

    def test_parses_co2_lines_and_ignores_others(self):
        bridge = ArduinoBridge(None)
        bridge.handle_line("CO2:1234\r\n", now=100.0)
        bridge.handle_line("MH-Z19E 예열 중", now=101.0)
        bridge.handle_line("CO2:abc", now=101.0)
        self.assertEqual(bridge.co2_ppm_at(101.0), 1234)

    def test_co2_becomes_none_when_stale(self):
        bridge = ArduinoBridge(None)
        self.assertIsNone(bridge.co2_ppm_at(0.0))
        bridge.handle_line("CO2:900", now=100.0)
        self.assertEqual(bridge.co2_ppm_at(109.0), 900)
        self.assertIsNone(bridge.co2_ppm_at(111.0))

    def test_without_port_does_nothing(self):
        bridge = ArduinoBridge(None)
        bridge.open()
        bridge.update({"drowsy_level": 3})
        self.assertFalse(bridge.connected)
