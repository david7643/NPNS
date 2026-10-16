import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4, UUID
import unittest

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base, migrate_record_metadata
from app.models import DetectionLog, DrivingSession, User
from app.schemas import EventLocationUpdate, SessionDistanceUpdate
from app.routers.detection import update_event_location
from app.routers.sessions import update_distance
from app.detection_service import DetectionSession


class MigrationTests(unittest.TestCase):
    def test_old_rows_survive_repeated_additive_migration(self):
        engine = create_engine("sqlite://")
        with engine.begin() as db:
            db.exec_driver_sql("CREATE TABLE driving_sessions (id INTEGER PRIMARY KEY)")
            db.exec_driver_sql("CREATE TABLE detection_logs (id INTEGER PRIMARY KEY, latitude FLOAT)")
            db.exec_driver_sql("INSERT INTO detection_logs VALUES (7,37.5)")
            migrate_record_metadata(db)
            migrate_record_metadata(db)
            row = db.exec_driver_sql("SELECT id,latitude,event_id,location_status FROM detection_logs").one()
            self.assertEqual(tuple(row), (7,37.5,None,None))
        engine.dispose()

    def test_event_identity_survives_frame_loss_and_changes_only_on_new_log(self):
        session = DetectionSession(1,7,None)
        first = session.identify_result(dict(alert_event=True,observation_valid=True,drowsy_level=1))
        again = session.identify_result(dict(alert_event=False,observation_valid=True,drowsy_level=1))
        missing = session.identify_result(dict(alert_event=False,observation_valid=False,drowsy_level=1))
        self.assertEqual(first['event_id'], again['event_id'])
        self.assertEqual(first['detected_at'], missing['detected_at'])
        normal = session.identify_result(dict(alert_event=False,observation_valid=True,drowsy_level=0))
        self.assertIsNone(normal['event_id'])
        next_event = session.identify_result(dict(alert_event=True,observation_valid=True,drowsy_level=1))
        self.assertNotEqual(first['event_id'], next_event['event_id'])


class RecordSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite://")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine,expire_on_commit=False)
        self.event_id = uuid4()
        self.received = datetime.now(timezone.utc)
        async with self.sessions() as db:
            db.add(User(id=7,username="sync-test",name="test",hashed_password="unused"))
            db.add(DrivingSession(id=1,user_id=7,ended_at=self.received))
            db.add(DetectionLog(user_id=7,session_id=1,event_id=str(self.event_id),ear_value=.15,pred_score=.3,drowsy_level=1))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def body(self, **changes):
        values = dict(session_id=1,received_at=self.received,latitude=37.5,longitude=127.1,
                      measured_at=self.received-timedelta(seconds=2),accuracy_meters=10,location_status="available")
        return EventLocationUpdate(**(values | changes))

    async def test_delayed_upload_and_retries_preserve_first_receipt(self):
        async with self.sessions() as db:
            await update_event_location(self.event_id,self.body(),SimpleNamespace(id=7),db)
            await update_event_location(self.event_id,self.body(latitude=38),SimpleNamespace(id=7),db)
            log = (await db.execute(select(DetectionLog))).scalar_one()
            self.assertEqual(log.latitude,37.5)
            self.assertEqual(log.location_status,"available")
            self.assertEqual(log.app_received_at,self.received.replace(tzinfo=None))

    async def test_other_account_and_wrong_session_cannot_attach_location(self):
        for user,body in [(8,self.body()),(7,self.body(session_id=2))]:
            async with self.sessions() as db:
                with self.assertRaises(HTTPException) as caught:
                    await update_event_location(self.event_id,body,SimpleNamespace(id=user),db)
                self.assertEqual(caught.exception.status_code,404)

    async def test_no_location_keeps_event_and_failure_reason(self):
        async with self.sessions() as db:
            await update_event_location(self.event_id,self.body(latitude=None,longitude=None,
                measured_at=None,accuracy_meters=None,location_status="permission_denied"),SimpleNamespace(id=7),db)
            log = (await db.execute(select(DetectionLog))).scalar_one()
            self.assertIsNone(log.latitude)
            self.assertEqual(log.location_status,"permission_denied")

    async def test_distance_is_monotonic_and_owned_even_after_session_end(self):
        async with self.sessions() as db:
            for value in (1200,800,1200):
                await update_distance(1,SessionDistanceUpdate(distance_meters=value),SimpleNamespace(id=7),db)
            session = (await db.execute(select(DrivingSession))).scalar_one()
            self.assertEqual(session.distance_meters,1200)
            with self.assertRaises(HTTPException):
                await update_distance(1,SessionDistanceUpdate(distance_meters=5000),SimpleNamespace(id=8),db)

    async def test_invalid_location_cannot_be_presented_as_available(self):
        for change in [dict(longitude=None),dict(latitude=float('nan')),dict(accuracy_meters=101),
                       dict(measured_at=self.received-timedelta(seconds=31)),dict(location_status="no_fix")]:
            with self.subTest(change=change),self.assertRaises(ValidationError):
                self.body(**change)
