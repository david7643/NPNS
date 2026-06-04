"""실시간 졸음 감지 API."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.detection_service import get_active_session, start_detection, stop_detection
from app.models import DrivingSession, User
from app.schemas import DetectionStartRequest, DetectionStartResponse, DetectionStatusResponse

router = APIRouter(prefix="/detection", tags=["detection"])


@router.post("/start", response_model=DetectionStartResponse)
async def start(
    body: DetectionStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """카메라를 열고 졸음 감지를 시작."""
    result = await db.execute(
        select(DrivingSession).where(
            DrivingSession.id == body.session_id,
            DrivingSession.user_id == current_user.id,
            DrivingSession.ended_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="유효한 진행 중 세션을 찾을 수 없습니다",
        )

    loop = asyncio.get_running_loop()
    try:
        start_detection(body.session_id, loop)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    return DetectionStartResponse(
        session_id=body.session_id,
        status="running",
        message="졸음 감지가 시작되었습니다",
    )


@router.post("/stop")
async def stop(
    body: DetectionStartRequest,
    current_user: User = Depends(get_current_user),
):
    """졸음 감지를 종료하고 카메라를 해제."""
    active = get_active_session()
    if active is None or active.session_id != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 세션의 감지가 실행 중이 아닙니다",
        )
    stop_detection()
    return {"status": "stopped", "message": "졸음 감지가 종료되었습니다"}


@router.websocket("/ws/{session_id}")
async def detection_ws(websocket: WebSocket, session_id: int):
    """실시간 감지 결과를 WebSocket으로 스트리밍."""
    await websocket.accept()

    active = get_active_session()
    if active is None or active.session_id != session_id:
        await websocket.close(
            code=4004, reason="해당 세션의 감지가 실행 중이 아닙니다"
        )
        return

    try:
        while active.is_running:
            try:
                result = await asyncio.wait_for(active.queue.get(), timeout=5.0)
                await websocket.send_json(result)
            except asyncio.TimeoutError:
                await websocket.send_json({"ping": True})
    except WebSocketDisconnect:
        pass


@router.get("/status/{session_id}", response_model=DetectionStatusResponse)
async def detection_status(
    session_id: int,
    current_user: User = Depends(get_current_user),
):
    """현재 감지 상태 조회."""
    active = get_active_session()
    is_running = active is not None and active.session_id == session_id and active.is_running
    return DetectionStatusResponse(session_id=session_id, is_running=is_running)
