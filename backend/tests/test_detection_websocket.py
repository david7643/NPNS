import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from starlette.websockets import WebSocket
from fastapi import HTTPException

from app.routers import detection


class DetectionWebsocketTests(unittest.IsolatedAsyncioTestCase):
    def socket(self):
        send = AsyncMock()
        socket = WebSocket(
            {"type": "websocket", "headers": [(b"authorization", b"Bearer test")]},
            receive=AsyncMock(return_value={"type": "websocket.connect"}), send=send,
        )
        return socket, send

    def authentication(self):
        context = AsyncMock()
        context.__aenter__.return_value = object()
        return (
            patch.object(detection, "async_session", return_value=context),
            patch.object(detection, "resolve_current_user", new=AsyncMock(return_value=SimpleNamespace(id=7))),
        )

    async def test_app_receives_json_alert_fields_and_normal_close(self):
        payload = {
            "state": "DROWSY", "drowsy_level": 1,
            "alert_active": True, "alert_event": True,
            "observation_valid": True, "calibrated": True,
            "pred_score": 0.8, "ear_value": 0.15,
        }
        queue = asyncio.Queue()
        queue.put_nowait(payload)

        class Session:
            user_id = 7
            session_id = 1
            latest_result = payload
            accesses = 0

            @property
            def is_running(self):
                self.accesses += 1
                return self.accesses == 1

        session = Session()
        session.queue = queue
        socket, send = self.socket()
        db_patch, auth_patch = self.authentication()
        with db_patch, auth_patch, patch.object(detection, "get_active_session", return_value=session):
            await detection.detection_ws(socket, 1)
        messages = [call.args[0] for call in send.call_args_list]
        self.assertEqual(messages[0]["type"], "websocket.accept")
        self.assertEqual(json.loads(messages[1]["text"]), payload)
        self.assertEqual(messages[-1]["code"], 1000)

    async def test_camera_start_failure_is_delivered_to_app(self):
        session = SimpleNamespace(
            user_id=7, session_id=1, is_running=False,
            latest_result={"state": "ERROR", "error": "카메라를 열 수 없습니다"},
        )
        socket, send = self.socket()
        db_patch, auth_patch = self.authentication()
        with db_patch, auth_patch, patch.object(detection, "get_active_session", return_value=session):
            await detection.detection_ws(socket, 1)
        messages = [call.args[0] for call in send.call_args_list]
        self.assertEqual(json.loads(messages[1]["text"])["state"], "ERROR")
        self.assertEqual(messages[-1]["code"], 1011)

    async def test_other_users_cannot_receive_session_results(self):
        socket, send = self.socket()
        db_patch, auth_patch = self.authentication()
        with db_patch, auth_patch, patch.object(
            detection, "get_active_session", return_value=SimpleNamespace(user_id=8, session_id=1),
        ):
            await detection.detection_ws(socket, 1)
        self.assertEqual(send.await_count, 2)
        self.assertEqual(send.call_args_list[0].args[0]["type"], "websocket.accept")
        self.assertEqual(send.call_args.args[0]["code"], 4004)

    async def test_invalid_token_gets_an_accepted_close_frame(self):
        socket, send = self.socket()
        db_patch, _ = self.authentication()
        with db_patch, patch.object(detection, "resolve_current_user", new=AsyncMock(side_effect=HTTPException(401))):
            await detection.detection_ws(socket, 1)
        messages = [c.args[0] for c in send.call_args_list]
        self.assertEqual([m['type'] for m in messages], ['websocket.accept', 'websocket.close'])
        self.assertEqual(messages[-1]['code'], 4001)

    async def test_revoked_token_closes_an_idle_connection(self):
        socket, send = self.socket()
        db_patch, _ = self.authentication()
        active = SimpleNamespace(user_id=7, session_id=1, is_running=True,
                                 queue=asyncio.Queue(), latest_result=None)
        auth = AsyncMock(side_effect=[SimpleNamespace(id=7), HTTPException(401)])
        with db_patch, patch.object(detection, "resolve_current_user", new=auth), \
             patch.object(detection, "get_active_session", return_value=active), \
             patch.object(detection, "WS_AUTH_RECHECK_SECONDS", .01):
            await asyncio.wait_for(detection.detection_ws(socket, 1), timeout=1)
        self.assertEqual(send.call_args.args[0]['code'], 4001)
        self.assertFalse(any(c.args[0]['type'] == 'websocket.send' for c in send.call_args_list))


if __name__ == "__main__":
    unittest.main()
