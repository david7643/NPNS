"""Persists episode boundaries and observed intervals without changing the v7 model/policy."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.ear_sequence import MAX_OBSERVATION_GAP_SECONDS


class ObservationRecorder:
    def __init__(self, session_id, user_id, account_key, write):
        self.common = dict(session_id=session_id, user_id=user_id, account_key=account_key)
        self.write = write
        self.previous = None
        self.segment = None
        self.episode = None
        self.last_flush = 0.0

    def emit(self, item):
        self.write({**self.common, **item})

    def accept(self, result, now, monotonic):
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
        if self.previous:
            previous_time, previous_mono, previous_valid, previous_level = self.previous
            elapsed = monotonic - previous_mono
            if elapsed > 0:
                # Monotonic elapsed prevents wall-clock adjustments from inventing observation time.
                end = previous_time + timedelta(seconds=elapsed)
                valid = bool(previous_valid and result.get("observation_valid") and elapsed <= MAX_OBSERVATION_GAP_SECONDS)
                level = previous_level if valid else 0
                if not self.segment or (self.segment["valid"], self.segment["level"]) != (valid, level):
                    if self.segment:
                        self.emit(self.segment)
                    self.segment = dict(kind="observation", id=str(uuid4()), started_at=previous_time.isoformat(),
                                        ended_at=end.isoformat(), valid=valid, level=level)
                else:
                    self.segment["ended_at"] = end.isoformat()
                now = end
        self.previous = (now, monotonic, bool(result.get("observation_valid")), result["drowsy_level"])
        if result.get("new_drowsiness_event"):
            self.close_episode(now)
            self.episode = dict(kind="episode", id=result["episode_id"], started_at=now.isoformat(),
                                ended_at=None, peak_level=result["drowsy_level"])
            self.emit(self.episode)
        if self.episode:
            if result["drowsy_level"] > self.episode["peak_level"]:
                self.episode["peak_level"] = result["drowsy_level"]
                self.emit(self.episode)
            if result.get("observation_valid") and not result.get("episode_active", True):
                self.close_episode(now)
        if monotonic - self.last_flush >= 5:
            if self.segment:
                self.emit(self.segment)
            self.last_flush = monotonic

    def close_episode(self, now):
        if self.episode:
            self.episode["ended_at"] = now.isoformat()
            self.emit(self.episode)
            self.episode = None

    def finish(self):
        if self.segment:
            self.emit(self.segment)
        if self.previous:
            self.close_episode(self.previous[0])
