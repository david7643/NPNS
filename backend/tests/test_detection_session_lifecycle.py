import asyncio
from contextlib import asynccontextmanager
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import detection_service as detection
from app.arduino_bridge import ArduinoBridge
from app.routers import detection as router
from app.schemas import DetectionStartRequest


class DetectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def blocked_camera(self, after_release=None):
        entered, release = threading.Event(), threading.Event()
        engine = detection.DrowsinessDecisionEngine()
        engine.left_baseline = engine.right_baseline = 0.3
        engine.current_level = 3
        engine.event_registered = True
        engine.update(0.15, 0.15, None, 100.0)
        processor = SimpleNamespace(engine=engine)
        bridge = ArduinoBridge(None)
        session = detection.DetectionSession(1, 7, asyncio.get_running_loop())
        frames = []

        class Camera:
            reads = 0

            def __enter__(self):
                self.processor = processor
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                self.reads += 1
                if self.reads == 2:
                    entered.set()
                    if not release.wait(10):
                        raise RuntimeError("test camera timed out")
                    if after_release is not None:
                        after_release(engine)
                elif self.reads > 2:
                    raise RuntimeError("test camera finished")
                return None, {
                    "drowsy_level": 3, "alarm_acknowledged": engine.alarm_acknowledged,
                    "alarm_ack_required": not engine.alarm_acknowledged,
                    "alert_event": False, "ear_value": 0.15, "pred_score": 0.9,
                }

        original = session._put_result

        def record(result):
            frames.append(result)
            original(result)

        with patch.object(detection, "CameraDetector", Camera), \
             patch.object(detection, "get_arduino", return_value=bridge), \
             patch.object(detection, "_active_session", session), \
             patch.object(session, "_put_result", side_effect=record):
            session.start()
            worker = session._thread
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                yield session, bridge, release, worker, frames
            finally:
                release.set()
                await asyncio.to_thread(worker.join, 2)
                session.stop()

    async def acknowledge(self):
        return await router.acknowledge_alarm(
            DetectionStartRequest(session_id=1), SimpleNamespace(id=7),
        )

    async def test_stop_discards_late_frames_and_keeps_thread_until_exit(self):
        async with self.blocked_camera() as (session, bridge, release, worker, frames):
            self.assertTrue(bridge.desired_alarm)
            with patch.object(detection, "DETECTION_STOP_JOIN_SECONDS", 0.01, create=True):
                await asyncio.to_thread(detection.stop_detection)
            self.assertFalse(bridge.desired_alarm)
            self.assertIs(session._thread, worker)
            self.assertTrue(worker.is_alive())
            with self.assertRaises(RuntimeError):
                detection.start_detection(2, 7, asyncio.get_running_loop())
            release.set()
            await asyncio.to_thread(worker.join, 2)
            self.assertFalse(bridge.desired_alarm)
            self.assertEqual(len(frames), 1)

    async def test_ack_mutes_immediately_and_late_frame_stays_muted(self):
        async with self.blocked_camera() as (_, bridge, release, worker, frames):
            response = await self.acknowledge()
            self.assertEqual(response["status"], "accepted")
            self.assertFalse(bridge.desired_alarm)
            release.set()
            await asyncio.to_thread(worker.join, 2)
            self.assertFalse(bridge.desired_alarm)
            self.assertTrue(frames[1]["alarm_acknowledged"])
            self.assertFalse(frames[1]["alarm_ack_required"])

    async def test_new_emergency_after_ack_requires_a_new_ack(self):
        def emergency(engine):
            for index in range(1, 32):
                engine.update(0.15, 0.15, None, 100.0 + index / 10)

        async with self.blocked_camera(emergency) as (_, bridge, release, worker, frames):
            await self.acknowledge()
            self.assertFalse(bridge.desired_alarm)
            release.set()
            await asyncio.to_thread(worker.join, 2)
            self.assertTrue(bridge.desired_alarm)
            self.assertFalse(frames[1]["alarm_acknowledged"])
            self.assertTrue(frames[1]["alarm_ack_required"])
