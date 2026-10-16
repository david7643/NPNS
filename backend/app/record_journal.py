"""Separate durable journal: primary DB failures never turn into silent record loss.

One in-process consumer matches the existing single-camera/single-worker deployment.
The journal is deliberately separate from the primary DB. Disk failure is surfaced;
it cannot be made durable by retrying an in-memory queue.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import threading

from sqlalchemy import select

logger = logging.getLogger(__name__)


class RecordJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.last_error = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS pending (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, record_key TEXT UNIQUE NOT NULL,
                revision INTEGER NOT NULL, payload TEXT NOT NULL)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, payload):
        key = payload["kind"] + ":" + payload["id"]
        with self.lock, self.connect() as db:
            db.execute("""INSERT INTO pending(record_key,revision,payload) VALUES (?,1,?)
                ON CONFLICT(record_key) DO UPDATE SET revision=revision+1,payload=excluded.payload""",
                (key, json.dumps(payload, allow_nan=False)))

    def batch(self, limit=100):
        with self.lock, self.connect() as db:
            return [(key, rev, json.loads(payload)) for key, rev, payload in db.execute(
                "SELECT record_key,revision,payload FROM pending ORDER BY sequence LIMIT ?", (limit,))]

    def acknowledge(self, rows):
        with self.lock, self.connect() as db:
            db.executemany("DELETE FROM pending WHERE record_key=? AND revision=?",
                           [(key, rev) for key, rev, _ in rows])

    def count(self):
        with self.lock, self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    async def flush(self, factory):
        from app.auth import storage_account_key
        from app.models import User, DrivingSession, DetectionLog, DrowsinessEpisode, ObservationSegment
        rows = await asyncio.to_thread(self.batch)
        if not rows:
            return 0
        async with factory() as db:
            for _, _, item in rows:
                user = await db.get(User, item["user_id"])
                session = await db.get(DrivingSession, item["session_id"])
                # Discard tombstoned accounts/sessions; never resurrect data after withdrawal
                # or attach it to an account whose numeric IDs were reused.
                if (user is None or session is None or session.user_id != user.id or
                        storage_account_key(user) != item["account_key"]):
                    continue
                common = dict(user_id=user.id, session_id=session.id)
                if item["kind"] == "event":
                    existing = (await db.execute(select(DetectionLog.id).where(
                        DetectionLog.event_id == item["id"]))).scalar_one_or_none()
                    if existing is None:
                        db.add(DetectionLog(**common, event_id=item["id"], episode_id=item.get("episode_id"),
                            timestamp=datetime.fromisoformat(item["timestamp"]), ear_value=item["ear_value"],
                            pred_score=item["pred_score"], drowsy_level=item["level"]))
                elif item["kind"] == "episode":
                    record = await db.get(DrowsinessEpisode, item["id"])
                    values = dict(started_at=datetime.fromisoformat(item["started_at"]),
                        ended_at=datetime.fromisoformat(item["ended_at"]) if item.get("ended_at") else None,
                        peak_level=item["peak_level"])
                    if record is None:
                        db.add(DrowsinessEpisode(id=item["id"], **common, **values))
                    else:
                        record.peak_level = max(record.peak_level, values["peak_level"])
                        if values["ended_at"]:
                            record.ended_at = max(record.ended_at or values["ended_at"], values["ended_at"])
                elif item["kind"] == "observation":
                    record = await db.get(ObservationSegment, item["id"])
                    values = dict(started_at=datetime.fromisoformat(item["started_at"]),
                        ended_at=datetime.fromisoformat(item["ended_at"]), valid=item["valid"], level=item["level"])
                    if record is None:
                        db.add(ObservationSegment(id=item["id"], **common, **values))
                    else:
                        record.ended_at = max(record.ended_at, values["ended_at"])
            await db.commit()
        # A crash here only causes an idempotent replay. Revision guards concurrent updates.
        await asyncio.to_thread(self.acknowledge, rows)
        self.last_error = None
        return len(rows)


_journal = None


def get_journal():
    global _journal
    if _journal is None:
        from app.config import get_settings
        identity = hashlib.sha256(get_settings().DATABASE_URL.encode()).hexdigest()[:16]
        _journal = RecordJournal(Path(__file__).resolve().parents[1] / "data" / f"record-journal-{identity}.sqlite3")
    return _journal


async def run_writer(stop):
    from app.database import async_session
    journal = get_journal()
    while not stop.is_set():
        try:
            if await journal.flush(async_session):
                continue
        except Exception as error:
            journal.last_error = type(error).__name__
            logger.exception("기록 DB 저장 대기 — 디스크 일지에 보존됨")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass
    try:
        # Bounded shutdown; remaining durable rows are replayed at the next startup.
        await asyncio.wait_for(journal.flush(async_session), timeout=5)
    except Exception:
        logger.exception("종료 후 남은 기록은 다음 시작 시 재전송합니다")
