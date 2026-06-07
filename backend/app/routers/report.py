"""리포트 관련 API 라우터."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import DetectionLog, DrivingSession, User
from app.schemas import (
    DrowsyEventItem,
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

    hour_counts: dict[int, int] = {}
    for log in logs:
        if log.drowsy_level > 0:
            hour = log.timestamp.hour
            hour_counts[hour] = hour_counts.get(hour, 0) + 1

    chart_data = [{"hour": h, "count": c} for h, c in sorted(hour_counts.items())]

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
