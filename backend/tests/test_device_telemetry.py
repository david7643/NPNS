import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from fastapi import FastAPI

from app.arduino_bridge import ArduinoBridge
from app.database import get_db
from app.dependencies import get_current_user
from app.routers import devices


class DeviceTelemetryTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 100.0
        self.bridge = ArduinoBridge("FAKE", clock=lambda: self.now)
        # Querying telemetry must not read/write/open a physical serial port.
        self.bridge._serial = object()
        self.app = FastAPI()
        self.app.include_router(devices.router)
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)

    async def request(self):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method="GET",
                     scheme="http", path="/devices/telemetry", raw_path=b"/devices/telemetry",
                     query_string=b"", root_path="", headers=[], server=("test", 80), client=("test", 1))
        with patch.object(devices, "get_arduino", return_value=self.bridge):
            await self.app(scope, receive, send)
        start = next(item for item in sent if item["type"] == "http.response.start")
        body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
        return start["status"], dict(start["headers"]), json.loads(body)

    async def test_sensor_is_available_without_a_camera_or_detection_session(self):
        self.bridge.handle_line("CO2:850", now=98.5)
        status, headers, body = await self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertEqual(body["co2_ppm"], 850)
        self.assertEqual(body["co2_age_seconds"], 1.5)
        self.assertTrue(body["arduino_connected"])
        self.assertFalse(body["arduino_responding"])  # CO2 does not require an alarm ACK.

    async def test_stale_na_reboot_and_disconnect_clear_the_measurement(self):
        self.bridge.handle_line("CO2:2000", now=100.0)
        self.now = 110.1
        self.assertIsNone((await self.request())[2]["co2_ppm"])
        self.assertIsNone((await self.request())[2]["co2_age_seconds"])
        for line in ("CO2:NA", "BOOT"):
            self.bridge.handle_line("CO2:850", now=self.now)
            self.bridge.handle_line(line, now=self.now)
            self.assertIsNone((await self.request())[2]["co2_ppm"])
        self.bridge.handle_line("CO2:850", now=self.now)
        self.bridge._detach_connection()
        body = (await self.request())[2]
        self.assertFalse(body["arduino_connected"])
        self.assertIsNone(body["co2_ppm"])

    async def test_query_requires_login(self):
        self.app.dependency_overrides.pop(get_current_user)

        async def unused_db():
            yield None  # Missing credentials must be rejected before any DB query.

        self.app.dependency_overrides[get_db] = unused_db
        self.assertEqual((await self.request())[0], 401)
