"""리포트 관련 API 라우터."""

from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import DetectionLog, DrivingSession, User
from app.schemas import (
    DrowsyEventItem,
    ReportByDateResponse,
    ReportDetailResponse,
    ReportHistoryItem,
    ReportHistoryResponse,
    ReportSummaryResponse,
)

router = APIRouter(prefix="/report", tags=["report"])

DEDUCTIONS = {1: 2, 2: 5, 3: 15}


def calc_safety_score(logs: list[DetectionLog]) -> int:
    score = 100 - sum(DEDUCTIONS.get(log.drowsy_level, 0) for log in logs if log.drowsy_level > 0)
    return max(0, score)


def get_grade(score: int) -> str:
    if score >= 80:
        return "safe"
    elif score >= 50:
        return "caution"
    else:
        return "danger"


def build_report_data(session: DrivingSession, logs: list[DetectionLog]) -> dict:
    """세션 + 로그로 리포트 데이터 계산."""
    score = calc_safety_score(logs)
    grade = get_grade(score)

    level_counts = {1: 0, 2: 0, 3: 0}
    for log in logs:
        if log.drowsy_level in level_counts:
            level_counts[log.drowsy_level] += 1

    duration_minutes = None
    if session.ended_at and session.started_at:
        delta = session.ended_at - session.started_at
        duration_minutes = int(delta.total_seconds() / 60)

    # 시간대별 단계별 집계
    hour_level_counts: dict[int, dict[int, int]] = {}
    hour_max_level: dict[int, int] = {}
    for log in logs:
        if log.drowsy_level > 0:
            hour = log.timestamp.hour
            if hour not in hour_level_counts:
                hour_level_counts[hour] = {1: 0, 2: 0, 3: 0}
            hour_level_counts[hour][log.drowsy_level] += 1
            # 해당 시간대 최고 단계 갱신
            hour_max_level[hour] = max(hour_max_level.get(hour, 0), log.drowsy_level)

    chart_data = [
        {
            "hour": h,
            "count": sum(counts.values()),
            "level1": counts[1],
            "level2": counts[2],
            "level3": counts[3],
            "max_level": hour_max_level.get(h, 0),
        }
        for h, counts in sorted(hour_level_counts.items())
    ]

    most_dangerous_time = None
    if hour_level_counts:
        peak_hour = max(hour_level_counts, key=lambda h: sum(hour_level_counts[h].values()))
        most_dangerous_time = f"{peak_hour:02d}:00~{(peak_hour + 1) % 24:02d}:00"

    events = [
        DrowsyEventItem(
            timestamp=log.timestamp,
            drowsy_level=log.drowsy_level,
            latitude=log.latitude,
            longitude=log.longitude,
        )
        for log in logs
        if log.drowsy_level > 0
    ]

    return {
        "session_id": session.id,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "duration_minutes": duration_minutes,
        "safety_score": score,
        "grade": grade,
        "total_drowsy_count": sum(level_counts.values()),
        "level1_count": level_counts[1],
        "level2_count": level_counts[2],
        "level3_count": level_counts[3],
        "most_dangerous_time": most_dangerous_time,
        "chart_data": chart_data,
        "events": events,
    }


@router.get("/summary", response_model=ReportSummaryResponse, summary="최근 세션 리포트 요약")
async def get_report_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DrivingSession)
        .where(DrivingSession.user_id == current_user.id, DrivingSession.ended_at.is_not(None))
        .order_by(DrivingSession.ended_at.desc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="종료된 세션이 없습니다.")

    logs_result = await db.execute(select(DetectionLog).where(DetectionLog.session_id == session.id))
    logs = logs_result.scalars().all()

    return ReportSummaryResponse(**build_report_data(session, logs))


@router.get("/history", response_model=ReportHistoryResponse, summary="전체 세션 히스토리 목록")
async def get_report_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DrivingSession)
        .where(DrivingSession.user_id == current_user.id, DrivingSession.ended_at.is_not(None))
        .order_by(DrivingSession.ended_at.desc())
    )
    sessions = result.scalars().all()
    if not sessions:
        return ReportHistoryResponse(total_sessions=0, sessions=[])

    history = []
    for session in sessions:
        logs_result = await db.execute(select(DetectionLog).where(DetectionLog.session_id == session.id))
        logs = logs_result.scalars().all()
        data = build_report_data(session, logs)
        history.append(ReportHistoryItem(
            session_id=data["session_id"],
            started_at=data["started_at"],
            ended_at=data["ended_at"],
            duration_minutes=data["duration_minutes"],
            safety_score=data["safety_score"],
            grade=data["grade"],
            total_drowsy_count=data["total_drowsy_count"],
        ))

    return ReportHistoryResponse(total_sessions=len(history), sessions=history)


@router.get("/detail/{session_id}", response_model=ReportDetailResponse, summary="특정 세션 상세 리포트")
async def get_report_detail(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DrivingSession).where(
            DrivingSession.id == session_id,
            DrivingSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    logs_result = await db.execute(
        select(DetectionLog).where(DetectionLog.session_id == session_id).order_by(DetectionLog.timestamp)
    )
    logs = logs_result.scalars().all()

    return ReportDetailResponse(**build_report_data(session, logs))

@router.get("/by-date", response_model=ReportByDateResponse, summary="특정 날짜 리포트 (세션 합산)")
async def get_report_by_date(
    date: str = Query(..., description="조회할 날짜 (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")

    day_start = datetime.combine(target_date, time.min)
    day_end = datetime.combine(target_date, time.max)

    result = await db.execute(
        select(DrivingSession).where(
            DrivingSession.user_id == current_user.id,
            DrivingSession.started_at >= day_start,
            DrivingSession.started_at <= day_end,
        ).order_by(DrivingSession.started_at)
    )
    sessions = result.scalars().all()

    if not sessions:
        raise HTTPException(status_code=404, detail="해당 날짜의 운전 기록이 없습니다.")

    session_ids = [s.id for s in sessions]
    logs_result = await db.execute(
        select(DetectionLog).where(DetectionLog.session_id.in_(session_ids))
    )
    logs = logs_result.scalars().all()

    score = calc_safety_score(logs)
    grade = get_grade(score)

    level_counts = {1: 0, 2: 0, 3: 0}
    for log in logs:
        if log.drowsy_level in level_counts:
            level_counts[log.drowsy_level] += 1

    hour_counts: dict[int, int] = {}
    hour_level_counts: dict[int, dict[int, int]] = {}
    for log in logs:
        if log.drowsy_level > 0:
            hour = log.timestamp.hour
            hour_counts[hour] = hour_counts.get(hour, 0) + 1
            hour_level_counts.setdefault(hour, {1: 0, 2: 0, 3: 0})
            hour_level_counts[hour][log.drowsy_level] += 1

    chart_data = []
    for h in sorted(hour_counts.keys()):
        levels = hour_level_counts[h]
        max_level = max((lvl for lvl, cnt in levels.items() if cnt > 0), default=0)
        chart_data.append({
            "hour": h,
            "count": hour_counts[h],
            "level1": levels[1],
            "level2": levels[2],
            "level3": levels[3],
            "max_level": max_level,
        })

    most_dangerous_time = None
    if hour_counts:
        peak_hour = max(hour_counts, key=lambda h: hour_counts[h])
        most_dangerous_time = f"{peak_hour:02d}:00~{(peak_hour + 1) % 24:02d}:00"

    events = [
        DrowsyEventItem(
            timestamp=log.timestamp,
            drowsy_level=log.drowsy_level,
            latitude=log.latitude,
            longitude=log.longitude,
        )
        for log in logs
        if log.drowsy_level > 0
    ]

    return ReportByDateResponse(
        date=date,
        session_count=len(sessions),
        safety_score=score,
        grade=grade,
        total_drowsy_count=sum(level_counts.values()),
        level1_count=level_counts[1],
        level2_count=level_counts[2],
        level3_count=level_counts[3],
        most_dangerous_time=most_dangerous_time,
        chart_data=chart_data,
        events=events,
    )