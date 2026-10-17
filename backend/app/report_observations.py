"""Additional report metrics; legacy stage logs are never guessed into episodes."""
from datetime import datetime
from sqlalchemy import select
from app.models import DrowsinessEpisode, ObservationSegment


def summarize_observations(episodes, segments, start=None, end=None):
    valid = unavailable = risk = 0.0
    for row in segments:
        left = max(row.started_at, start) if start else row.started_at
        right = min(row.ended_at, end) if end else row.ended_at
        seconds = max(0.0, (right - left).total_seconds())
        if row.valid:
            valid += seconds
            if row.level > 0:
                risk += seconds
        else:
            unavailable += seconds
    selected = [row for row in episodes if (start is None or row.started_at >= start)
                and (end is None or row.started_at < end)]
    measured = valid + unavailable
    return dict(independent_event_count=len(selected) if measured > 0 or selected else None,
        valid_observation_seconds=round(valid, 3), unavailable_observation_seconds=round(unavailable, 3),
        risk_observation_seconds=round(risk, 3), observed_seconds=round(measured, 3),
        observation_coverage=valid / measured if measured else None,
        independent_events_per_hour=round(len(selected) * 3600 / valid, 3) if valid else None,
        assessment_status="no_valid_observation" if valid == 0 else ("partial" if unavailable > 0 else "observed"),
        episode_peak_level=max((row.peak_level for row in selected), default=0))


async def metrics_for(db, session_ids, start=None, end=None):
    episodes = (await db.execute(select(DrowsinessEpisode).where(
        DrowsinessEpisode.session_id.in_(session_ids)))).scalars().all()
    segments = (await db.execute(select(ObservationSegment).where(
        ObservationSegment.session_id.in_(session_ids)))).scalars().all()
    return summarize_observations(episodes, segments, start, end)
