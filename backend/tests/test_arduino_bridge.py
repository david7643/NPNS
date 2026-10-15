import unittest

from app.arduino_bridge import ArduinoBridge, alarm_required


class FakeSerial:
    def __init__(self, *args):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def readline(self):
        return b""

    def close(self):
        pass


class ArduinoBridgeTests(unittest.TestCase):
    def make_bridge(self):
        fake = FakeSerial()
        bridge = ArduinoBridge("FAKE", serial_factory=lambda *a: fake)
        bridge.open()
        return bridge, fake

    def test_alarm_only_for_unacknowledged_level_two_and_three(self):
        self.assertFalse(alarm_required({"drowsy_level": 0}))
        self.assertFalse(alarm_required({"drowsy_level": 1}))
        self.assertTrue(alarm_required({"drowsy_level": 2}))
        self.assertTrue(alarm_required({"drowsy_level": 3, "alarm_acknowledged": False}))
        self.assertFalse(alarm_required({"drowsy_level": 3, "alarm_acknowledged": True}))

    def test_sends_on_change_and_resends_every_second(self):
        bridge, fake = self.make_bridge()
        bridge.update({"drowsy_level": 0}, now=10.0)
        bridge.update({"drowsy_level": 0}, now=10.3)   # 변화 없음, 1초 안 됨
        bridge.update({"drowsy_level": 2}, now=10.4)   # 변화 → 즉시
        bridge.update({"drowsy_level": 2}, now=11.5)   # 1초 지남 → 재전송
        self.assertEqual(fake.written, [b"A0\n", b"A1\n", b"A1\n"])

    def test_close_turns_alarm_off(self):
        bridge, fake = self.make_bridge()
        bridge.update({"drowsy_level": 3}, now=1.0)
        bridge.close()
        self.assertEqual(fake.written[-1], b"A0\n")
        self.assertFalse(bridge.connected)

    def test_parses_co2_lines_and_ignores_others(self):
        bridge = ArduinoBridge(None)
        bridge.handle_line("CO2:1234\r\n")
        bridge.handle_line("MH-Z19E 예열 중")
        bridge.handle_line("CO2:abc")
        self.assertEqual(bridge.co2_ppm, 1234)

    def test_without_port_does_nothing(self):
        bridge = ArduinoBridge(None)
        bridge.open()
        bridge.update({"drowsy_level": 3})
        self.assertFalse(bridge.connected)
