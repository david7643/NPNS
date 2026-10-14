import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.detection_service import DetectionSession, DrowsinessDecisionEngine
from app.routers import detection
from app.schemas import DetectionStartRequest


class AlarmAcknowledgementApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_request_is_queued_without_stopping_camera(self):
        active = SimpleNamespace(session_id=1, user_id=7, is_running=True,
                                 latest_result={"drowsy_level": 3}, acknowledge_alarm=Mock())
        with patch.object(detection, "get_active_session", return_value=active):
            result = await detection.acknowledge_alarm(DetectionStartRequest(session_id=1), SimpleNamespace(id=7))
        self.assertEqual(result["status"], "accepted")
        active.acknowledge_alarm.assert_called_once()

    async def test_wrong_owner_and_inactive_and_low_level_are_rejected(self):
        for user_id, running, level, expected in ((8, True, 3, 403), (7, False, 3, 404), (7, True, 1, 409)):
            active = SimpleNamespace(session_id=1, user_id=user_id, is_running=running,
                                     latest_result={"drowsy_level": level}, acknowledge_alarm=Mock())
            with patch.object(detection, "get_active_session", return_value=active):
                with self.assertRaises(HTTPException) as error:
                    await detection.acknowledge_alarm(DetectionStartRequest(session_id=1), SimpleNamespace(id=7))
            self.assertEqual(error.exception.status_code, expected)
            active.acknowledge_alarm.assert_not_called()

    async def test_request_is_applied_on_detection_thread_not_api_thread(self):
        session = DetectionSession(1, 7, asyncio.get_running_loop())
        engine = DrowsinessDecisionEngine()
        engine.current_level = 3
        session.acknowledge_alarm()
        self.assertFalse(engine.alarm_acknowledged)
        session._apply_alarm_ack_requests(SimpleNamespace(engine=engine))
        self.assertTrue(engine.alarm_acknowledged)


if __name__ == "__main__":
    unittest.main()
