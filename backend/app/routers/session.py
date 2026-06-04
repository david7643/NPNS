"""운전 세션 관련 API 라우터."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import DrivingSession, User
from app.schemas import SessionEndResponse, SessionResponse, SessionStartResponse

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/start", response_model=SessionStartResponse, status_code=status.HTTP_201_CREATED, summary="운전 세션 시작")
async def start_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DrivingSession).where(
            DrivingSession.user_id == current_user.id,
            DrivingSession.ended_at.is_(None),
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 진행 중인 세션이 있습니다.")

    new_session = DrivingSession(user_id=current_user.id)
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return SessionStartResponse(session_id=new_session.id, started_at=new_session.started_at)


@router.post("/end/{session_id}", response_model=SessionEndResponse, summary="운전 세션 종료")
async def end_session(
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
    if session.ended_at is not None:
        raise HTTPException(status_code=400, detail="이미 종료된 세션입니다.")

    session.ended_at = datetime.utcnow()
    await db.commit()
    await db.refresh(session)
    return SessionEndResponse(session_id=session.id, started_at=session.started_at, ended_at=session.ended_at)


@router.get("/{session_id}", response_model=SessionResponse, summary="세션 정보 조회")
async def get_session(
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
    return session
