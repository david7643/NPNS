from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import schemas
from app.database import Base
from app.models import DrivingSession, User
from app.routers import sessions
from app.schemas import SessionEndRequest, SessionResponse, SessionStartResponse


class SessionTimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite://")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.owner = SimpleNamespace(id=1)
        async with self.factory() as db:
            db.add(User(id=1, username="timer", name="timer", hashed_password="fixture"))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_new_session_response_identifies_utc_and_server_elapsed_time(self):
        async with self.factory() as db:
            response = await sessions.start_session(self.owner, db)
            payload = response.model_dump(mode="json")
            self.assertTrue(payload["started_at"].endswith("Z"))
            self.assertGreaterEqual(payload["elapsed_seconds"], 0)
            self.assertLessEqual(payload["elapsed_seconds"], 2)

    async def test_recovery_uses_server_elapsed_even_when_phone_clock_is_28_seconds_behind(self):
        now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
        async with self.factory() as db:
            db.add(DrivingSession(user_id=1, started_at=(now - timedelta(seconds=10)).replace(tzinfo=None)))
            await db.commit()
            current = await sessions.current_session(self.owner, db)
            with patch.object(schemas, "datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                payload = SessionResponse.model_validate(current).model_dump(mode="json")
            self.assertEqual(payload["elapsed_seconds"], 10)
            self.assertTrue(payload["started_at"].endswith("Z"))
            # The previous client-side subtraction returns -18 for this valid session.
            phone_now = now - timedelta(seconds=28)
            self.assertEqual((phone_now - datetime.fromisoformat(payload["started_at"])).total_seconds(), -18)
            stored = await db.get(DrivingSession, current.id)
            self.assertIsNone(stored.started_at.tzinfo)

    async def test_end_retries_and_history_keep_utc_and_freeze_completed_duration(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        async with self.factory() as db:
            session = DrivingSession(user_id=1, started_at=(now - timedelta(seconds=65)).replace(tzinfo=None))
            db.add(session)
            await db.commit()
            with patch("app.detection_service.get_active_session", return_value=None), \
                 patch.object(sessions, "datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                first = await sessions.end_session(SessionEndRequest(session_id=session.id), self.owner, db)
                retry = await sessions.end_session(SessionEndRequest(session_id=session.id), self.owner, db)
            self.assertEqual(first.model_dump(mode="json"), retry.model_dump(mode="json"))
            self.assertEqual(first.model_dump(mode="json")["elapsed_seconds"], 65)
            history = await sessions.session_history(self.owner, db)
            payload = SessionResponse.model_validate(history[0]).model_dump(mode="json")
            self.assertEqual(payload["elapsed_seconds"], 65)
            self.assertTrue(payload["started_at"].endswith("Z"))
            self.assertTrue(payload["ended_at"].endswith("Z"))


class SessionTimestampTests(unittest.TestCase):
    def test_explicit_offsets_preserve_the_instant_and_open_sessions_keep_null_end(self):
        response = SessionResponse(id=1, user_id=1, started_at="2026-10-17T19:00:00+09:00", ended_at=None)
        payload = response.model_dump(mode="json")
        self.assertEqual(payload["started_at"], "2026-10-17T10:00:00Z")
        self.assertIsNone(payload["ended_at"])

    def test_clock_adjustment_cannot_produce_negative_server_elapsed(self):
        now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
        with patch.object(schemas, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            response = SessionStartResponse(session_id=1, started_at=now + timedelta(seconds=28))
            self.assertEqual(response.model_dump(mode="json")["elapsed_seconds"], 0)
