"""운전 세션 API."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import DrivingSession, User
from app.schemas import SessionEndRequest, SessionEndResponse, SessionResponse, SessionStartResponse

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/start", response_model=SessionStartResponse, status_code=status.HTTP_201_CREATED)
async def start_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """운전 세션 시작."""
    existing = await db.execute(
        select(DrivingSession).where(
            DrivingSession.user_id == current_user.id,
            DrivingSession.ended_at.is_(None),
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="이미 진행 중인 세션이 있습니다")

    session = DrivingSession(user_id=current_user.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionStartResponse(session_id=session.id, started_at=session.started_at)


@router.post("/end", response_model=SessionEndResponse)
async def end_session(
    body: SessionEndRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """운전 세션 종료."""
    result = await db.execute(
        select(DrivingSession).where(
            DrivingSession.id == body.session_id,
            DrivingSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="세션을 찾을 수 없습니다")
    if session.ended_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="이미 종료된 세션입니다")

    session.ended_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)
    return SessionEndResponse(session_id=session.id, started_at=session.started_at, ended_at=session.ended_at)


@router.get("/current", response_model=SessionResponse)
async def current_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현재 진행 중 세션 조회."""
    result = await db.execute(
        select(DrivingSession)
        .where(
            DrivingSession.user_id == current_user.id,
            DrivingSession.ended_at.is_(None),
        )
        .order_by(DrivingSession.started_at.desc())
    )
    session = result.scalars().first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="진행 중인 세션이 없습니다")
    return session


@router.get("/history", response_model=list[SessionResponse])
async def session_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """세션 기록 조회."""
    result = await db.execute(
        select(DrivingSession)
        .where(DrivingSession.user_id == current_user.id)
        .order_by(DrivingSession.started_at.desc())
    )
    return result.scalars().all()