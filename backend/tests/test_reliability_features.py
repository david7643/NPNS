import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.auth import storage_account_key
from app.database import Base
from app.models import User, DrivingSession, DetectionLog, DrowsinessEpisode, ObservationSegment
from app.record_journal import RecordJournal
from app.observation_recorder import ObservationRecorder
from app.report_observations import summarize_observations
from app.routers import contacts, detection, report
from app.schemas import SmsSettingsUpdate, ContactCreate, AlarmAcknowledgeRequest
from app.detection_service import DetectionSession, DrowsinessDecisionEngine


class ReliabilityApiTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.engine = create_async_engine("sqlite+aiosqlite://")
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.now = datetime(2026, 10, 15, 15)
        self.user = User(id=7, username="owner", name="owner", hashed_password="fixture")
        async with self.factory() as db:
            db.add_all([self.user, User(id=8, username="other", name="other", hashed_password="other")])
            db.add_all([DrivingSession(id=1, user_id=7, started_at=self.now, ended_at=self.now+timedelta(hours=1)),
                        DrivingSession(id=2, user_id=8, started_at=self.now)])
            await db.commit()
        self.journal_path = Path(self.temp.name) / "journal.sqlite3"
        self.journal = RecordJournal(self.journal_path)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    def event(self, **overrides):
        return dict(kind="event", id=str(uuid4()), user_id=7, session_id=1,
                    account_key=storage_account_key(self.user), timestamp=self.now.isoformat(),
                    ear_value=.15, pred_score=.7, level=3) | overrides

    async def test_journal_survives_primary_failure_and_restart_without_duplicate_rows(self):
        item = self.event()
        self.journal.put(item)
        def unavailable():
            raise RuntimeError("primary DB unavailable")
        with self.assertRaises(RuntimeError):
            await self.journal.flush(unavailable)
        self.assertEqual(self.journal.count(), 1)
        reopened = RecordJournal(self.journal_path)
        await reopened.flush(self.factory)
        # Simulate a crash after primary commit but before deleting the journal row.
        reopened.put(item)
        await reopened.flush(self.factory)
        self.assertEqual(reopened.count(), 0)
        async with self.factory() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(DetectionLog)), 1)

    async def test_journal_revision_cannot_drop_a_new_observation_update(self):
        item = self.event(kind="observation", started_at=self.now.isoformat(),
                          ended_at=(self.now+timedelta(seconds=1)).isoformat(), valid=True)
        self.journal.put(item)
        old_batch = self.journal.batch()
        self.journal.put(item | {"ended_at": (self.now+timedelta(seconds=5)).isoformat()})
        self.journal.acknowledge(old_batch)
        self.assertEqual(self.journal.count(), 1)
        await self.journal.flush(self.factory)
        self.journal.put(item)  # Older replay must not shorten already committed coverage.
        await self.journal.flush(self.factory)
        async with self.factory() as db:
            row = await db.get(ObservationSegment, item["id"])
            self.assertEqual((row.ended_at-row.started_at).total_seconds(), 5)

    async def test_journal_does_not_resurrect_withdrawn_or_reused_accounts(self):
        self.journal.put(self.event(account_key="old-account-identity"))
        self.journal.put(self.event(session_id=2))
        self.journal.put(self.event(session_id=999))
        await self.journal.flush(self.factory)
        self.assertEqual(self.journal.count(), 0)
        async with self.factory() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(DetectionLog)), 0)

    async def test_recovery_pagination_is_owner_scoped_and_includes_late_commits(self):
        async with self.factory() as db:
            for user, session, level in [(7,1,1),(8,2,3),(7,1,0),(7,1,2)]:
                db.add(DetectionLog(user_id=user,session_id=session,drowsy_level=level,ear_value=.2,pred_score=.5))
            await db.commit()
            first = await detection.recover_events(0,1,self.user,db)
            self.assertTrue(first["has_more"])
            self.assertEqual(first["events"][0]["stage"],1)
            self.assertTrue(first["events"][0]["event_id"].startswith("legacy:"))
            second = await detection.recover_events(first["next_cursor"],100,self.user,db)
            self.assertEqual([r["stage"] for r in second["events"]],[2])
            empty = await detection.recover_events(second["next_cursor"],100,self.user,db)
            self.assertEqual(empty["next_cursor"],second["next_cursor"])
        self.journal.put(self.event())
        await self.journal.flush(self.factory)
        async with self.factory() as db:
            late = await detection.recover_events(second["next_cursor"],100,self.user,db)
            self.assertEqual([r["stage"] for r in late["events"]],[3])
            self.assertIsNone(late["events"][0]["latitude"])

    async def test_sms_is_mandatory_and_last_contact_cannot_be_deleted(self):
        owner = SimpleNamespace(id=7)
        async with self.factory() as db:
            empty = await contacts.get_sms_settings(owner,db)
            self.assertTrue(empty["automatic_sms_required"])
            self.assertFalse(empty["enabled"])
            with self.assertRaises(HTTPException):
                await contacts.update_sms_settings(SmsSettingsUpdate(enabled=True),owner,db)
            await db.rollback()
            contact = await contacts.create_contact(ContactCreate(name="test",phone="01012345678",message="fixture"),owner,db)
            contact_id = contact.id
            self.assertTrue((await contacts.get_sms_settings(owner,db))["enabled"])
            with self.assertRaises(HTTPException) as error:
                await contacts.update_sms_settings(SmsSettingsUpdate(enabled=False),owner,db)
            self.assertEqual(error.exception.status_code,409)
            with self.assertRaises(HTTPException) as error:
                await contacts.delete_contact(contact_id,owner,db)
            self.assertEqual(error.exception.status_code,409)
        async with self.factory() as db:
            snapshot = await contacts.get_sms_settings(owner,db)
            self.assertEqual(snapshot["contact_count"],1)
            self.assertTrue(snapshot["enabled"])

    async def test_two_contacts_can_be_reduced_to_one_and_last_contact_can_be_edited(self):
        owner = SimpleNamespace(id=7)
        async with self.factory() as db:
            first = await contacts.create_contact(ContactCreate(name="first",phone="01011112222",message="fixture"),owner,db)
            second = await contacts.create_contact(ContactCreate(name="second",phone="01033334444",message="fixture"),owner,db)
            await contacts.delete_contact(first.id,owner,db)
            snapshot = await contacts.get_sms_settings(owner,db)
            self.assertEqual(snapshot["contact_count"],1)
            saved = await contacts.update_contact(second.id,
                contacts.SavedContactUpdate(name="replacement",phone="01055556666",message="updated"),owner,db)
            self.assertEqual(saved.phone,"01055556666")
            self.assertTrue((await contacts.get_sms_settings(owner,db))["enabled"])

    async def test_legacy_disabled_flag_does_not_disable_automatic_sms(self):
        owner = SimpleNamespace(id=7)
        async with self.factory() as db:
            await contacts.create_contact(ContactCreate(name="first",phone="01011112222",message="fixture"),owner,db)
            user = await db.get(User,7)
            user.auto_sms_enabled = False
            await db.commit()
            self.assertTrue((await contacts.get_sms_settings(owner,db))["enabled"])

    async def test_concurrent_deletes_cannot_remove_both_remaining_contacts(self):
        # Separate connections to a file DB exercise SQLite write serialization.
        engine = create_async_engine("sqlite+aiosqlite:///"+str(Path(self.temp.name)/"contacts.db"))
        factory = async_sessionmaker(engine,expire_on_commit=False)
        owner = SimpleNamespace(id=7)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:
                db.add(User(id=7,username="race",name="race",hashed_password="fixture"))
                await db.commit()
                one = await contacts.create_contact(ContactCreate(name="one",phone="01011112222",message="fixture"),owner,db)
                two = await contacts.create_contact(ContactCreate(name="two",phone="01033334444",message="fixture"),owner,db)
                ids = [one.id,two.id]
            async def remove(contact_id):
                async with factory() as db:
                    try:
                        await contacts.delete_contact(contact_id,owner,db)
                        return 204
                    except HTTPException as error:
                        await db.rollback()
                        return error.status_code
            outcomes = await asyncio.gather(*(remove(contact_id) for contact_id in ids))
            self.assertEqual(sorted(outcomes),[204,409])
            async with factory() as db:
                self.assertEqual((await contacts.get_sms_settings(owner,db))["contact_count"],1)
        finally:
            await engine.dispose()

    async def test_other_accounts_cannot_delete_or_enable_using_owners_contact(self):
        async with self.factory() as db:
            item = await contacts.create_contact(ContactCreate(name="test",phone="01012345678",message="fixture"),self.user,db)
            other = SimpleNamespace(id=8)
            with self.assertRaises(HTTPException) as error:
                await contacts.delete_contact(item.id,other,db)
            self.assertEqual(error.exception.status_code,404)
            with self.assertRaises(HTTPException):
                await contacts.update_sms_settings(SmsSettingsUpdate(enabled=True),other,db)

    async def test_reports_separate_episodes_from_stage_logs_and_mark_missing_coverage(self):
        async with self.factory() as db:
            legacy = await report.get_report_detail(1,540,self.user,db)
            self.assertEqual(legacy.assessment_status,"no_valid_observation")
            self.assertIsNone(legacy.independent_event_count)
            db.add(DrowsinessEpisode(id=str(uuid4()),user_id=7,session_id=1,started_at=self.now,peak_level=3))
            db.add(ObservationSegment(id=str(uuid4()),user_id=7,session_id=1,started_at=self.now,
                ended_at=self.now+timedelta(seconds=120),valid=True,level=3))
            db.add(ObservationSegment(id=str(uuid4()),user_id=7,session_id=1,started_at=self.now+timedelta(seconds=120),
                ended_at=self.now+timedelta(seconds=180),valid=False,level=0))
            for level in [1,2,3]:
                db.add(DetectionLog(user_id=7,session_id=1,timestamp=self.now,ear_value=.2,pred_score=.4,drowsy_level=level))
            await db.commit()
            result = await report.get_report_by_date("2026-10-16",540,self.user,db)
            self.assertEqual(result.total_drowsy_count,3)
            self.assertEqual(result.independent_event_count,1)
            self.assertEqual(result.independent_events_per_hour,30)
            self.assertAlmostEqual(result.observation_coverage,2/3)
            self.assertEqual(result.assessment_status,"partial")
            history = await report.get_report_history(self.user,db)
            self.assertEqual(history.sessions[0].risk_observation_seconds,120)

    async def test_scoped_ack_rejects_stale_risk_and_queues_current_risk(self):
        current = str(uuid4())
        active = SimpleNamespace(session_id=1,user_id=7,is_running=True,
                                 latest_result={"drowsy_level":3,"risk_id":current},acknowledge_alarm=Mock())
        with patch.object(detection,"get_active_session",return_value=active):
            with self.assertRaises(HTTPException) as error:
                await detection.acknowledge_alarm(AlarmAcknowledgeRequest(session_id=1,risk_id=str(uuid4())),self.user)
            self.assertEqual(error.exception.status_code,409)
            active.acknowledge_alarm.assert_not_called()
            await detection.acknowledge_alarm(AlarmAcknowledgeRequest(session_id=1,risk_id=current),self.user)
            active.acknowledge_alarm.assert_called_once_with(current)


class ObservationAndRiskTests(TestCase):
    def test_independent_same_level_events_get_new_episode_and_risk_but_not_new_stage_log(self):
        session = DetectionSession(1,7,None)
        first = session.identify_result(dict(drowsy_level=3,observation_valid=True,alert_event=True,new_drowsiness_event=True))
        second = session.identify_result(dict(drowsy_level=3,observation_valid=True,alert_event=False,new_drowsiness_event=True))
        repeated = session.identify_result(dict(drowsy_level=3,observation_valid=True,alert_event=False,new_drowsiness_event=False))
        self.assertNotEqual(first["episode_id"],second["episode_id"])
        self.assertNotEqual(first["risk_id"],second["risk_id"])
        self.assertEqual(first["event_id"],second["event_id"])
        self.assertEqual(second["episode_id"],repeated["episode_id"])
        session.acknowledge_alarm(first["risk_id"])
        engine = DrowsinessDecisionEngine()
        engine.current_level = 3
        session._apply_alarm_ack_requests(SimpleNamespace(engine=engine))
        self.assertFalse(engine.alarm_acknowledged)

    def test_failed_disk_writes_remain_pending_and_expose_error(self):
        session = DetectionSession(1,7,None)
        payload = dict(kind="event",id=str(uuid4()))
        with patch("app.record_journal.get_journal",side_effect=OSError("fixture disk failure")), self.assertLogs(level="ERROR"):
            session._write_record(payload)
        self.assertTrue(session._storage_error)
        self.assertEqual(len(session._undurable),1)
        session._journal_retry_at = 0
        with patch("app.record_journal.get_journal") as journal:
            journal.return_value.last_error = None
            session._flush_journal_records()
            journal.return_value.put.assert_called_once_with(payload)
        self.assertFalse(session._undurable)
        self.assertIsNone(session._storage_error)

    def test_observation_gaps_are_not_safe_time_and_same_level_episodes_count_twice(self):
        rows = {}
        recorder = ObservationRecorder(1,7,"account",lambda row: rows.update({row["id"]:dict(row)}))
        start = datetime(2026,10,16,tzinfo=timezone.utc)
        base = dict(drowsy_level=3,observation_valid=True,episode_active=True)
        first, second = str(uuid4()),str(uuid4())
        for offset, extra in [(0,dict(new_drowsiness_event=True,episode_id=first)),(.2,{}),
                              (.4,dict(observation_valid=False)),(2.4,{}),
                              (2.6,dict(new_drowsiness_event=True,episode_id=second)),(2.8,{})]:
            recorder.accept(base|extra,start+timedelta(seconds=offset),offset)
        recorder.finish()
        segments = [SimpleNamespace(**(r|dict(started_at=datetime.fromisoformat(r["started_at"]),
                    ended_at=datetime.fromisoformat(r["ended_at"])))) for r in rows.values() if r["kind"]=="observation"]
        episodes = [SimpleNamespace(**(r|dict(started_at=datetime.fromisoformat(r["started_at"]))))
                    for r in rows.values() if r["kind"]=="episode"]
        result = summarize_observations(episodes,segments)
        self.assertEqual(result["independent_event_count"],2)
        self.assertAlmostEqual(result["valid_observation_seconds"],.6)
        self.assertAlmostEqual(result["unavailable_observation_seconds"],2.2)

    def test_daily_clipping_and_no_observation_never_invent_event_counts(self):
        start = datetime(2026,10,16)
        segment = SimpleNamespace(started_at=start-timedelta(seconds=10),ended_at=start+timedelta(seconds=10),valid=True,level=2)
        result = summarize_observations([], [segment],start,start+timedelta(days=1))
        self.assertEqual(result["risk_observation_seconds"],10)
        empty = summarize_observations([],[])
        self.assertIsNone(empty["independent_event_count"])
        self.assertIsNone(empty["observation_coverage"])
        self.assertIsNone(empty["independent_events_per_hour"])
        self.assertEqual(empty["assessment_status"],"no_valid_observation")
