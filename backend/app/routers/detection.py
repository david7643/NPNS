"""실시간 졸음 감지 API."""

import asyncio
from uuid import UUID
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.auth import storage_account_key
from app.dependencies import get_current_user, resolve_current_user
from app.detection_service import get_active_session, models_ready, model_runtime_info, start_detection, stop_detection
from app.models import DetectionLog, DrivingSession, User
from app.schemas import AlarmAcknowledgeRequest, DetectionStartRequest, DetectionStartResponse, DetectionStatusResponse, EventLocationUpdate

router = APIRouter(prefix="/detection", tags=["detection"])
WS_AUTH_RECHECK_SECONDS = 1.0


@router.get("/events")
async def recover_events(
    after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Committed row IDs are the cursor; UUIDs identify records, not their order."""
    logs = (await db.execute(select(DetectionLog).where(
        DetectionLog.user_id == current_user.id, DetectionLog.id > after,
        DetectionLog.drowsy_level > 0,
    ).order_by(DetectionLog.id).limit(limit + 1))).scalars().all()
    rows = logs[:limit]
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value else None
    return {"next_cursor": rows[-1].id if rows else after, "has_more": len(logs) > limit,
        "events": [dict(cursor=log.id, event_id=log.event_id or f"legacy:{log.id}", session_id=log.session_id,
            detected_at=utc(log.timestamp), episode_id=log.episode_id, stage=log.drowsy_level,
            ear=log.ear_value, prediction_score=log.pred_score,
            latitude=log.latitude, longitude=log.longitude, app_received_at=utc(log.app_received_at),
            location_measured_at=utc(log.location_measured_at), accuracy_meters=log.location_accuracy_meters,
            location_status=log.location_status or "not_recorded") for log in rows]}


@router.put("/events/{event_id}/location")
async def update_event_location(
    event_id: UUID, body: EventLocationUpdate,
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """서버 사건에 최초 앱 수신 당시 위치를 연결한다. 재전송은 같은 결과를 반환한다."""
    result = await db.execute(select(DetectionLog).where(
        DetectionLog.event_id == str(event_id), DetectionLog.user_id == current_user.id,
        DetectionLog.session_id == body.session_id,
    ))
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=404, detail="연결할 감지 기록을 찾을 수 없습니다")
    if log.app_received_at is None:
        await db.execute(update(DetectionLog).where(
            DetectionLog.id == log.id, DetectionLog.app_received_at.is_(None),
        ).values(
            app_received_at=body.received_at.astimezone(timezone.utc).replace(tzinfo=None),
            latitude=body.latitude, longitude=body.longitude,
            location_measured_at=body.measured_at.astimezone(timezone.utc).replace(tzinfo=None) if body.measured_at else None,
            location_accuracy_meters=body.accuracy_meters, location_status=body.location_status,
        ))
        await db.commit()
    return {"event_id": str(event_id), "status": "saved"}


@router.post("/start", response_model=DetectionStartResponse)
async def start(
    body: DetectionStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start drowsiness detection using the server camera."""
    if not models_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Drowsiness-detection models are not ready. Check TensorFlow and ai_models.",
        )

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
        start_detection(body.session_id, current_user.id, loop, storage_account_key(current_user))
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    return DetectionStartResponse(
        session_id=body.session_id,
        status="running",
        message="감지를 시작합니다. 눈을 뜨고 정면을 보며 개인 보정이 끝날 때까지 기다려주세요.",
        calibration_required_seconds=model_runtime_info()["calibration_required_seconds"],
        detection_code_version=model_runtime_info()["detection_code_version"],
        model_release_id=model_runtime_info()["model_release_id"],
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
    if active.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="다른 사용자의 감지 세션은 종료할 수 없습니다",
        )
    stop_detection()
    return {"status": "stopped", "message": "졸음 감지가 종료되었습니다"}


@router.post("/alarm/acknowledge", status_code=status.HTTP_202_ACCEPTED)
async def acknowledge_alarm(
    body: AlarmAcknowledgeRequest,
    current_user: User = Depends(get_current_user),
):
    """앱 중지 버튼의 경보 중지 확인을 카메라 스레드에 전달한다."""
    active = get_active_session()
    if active is None or active.session_id != body.session_id or not active.is_running:
        raise HTTPException(status_code=404, detail="해당 세션의 감지가 실행 중이 아닙니다")
    if active.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="다른 사용자의 경보는 제어할 수 없습니다")
    if not active.latest_result or active.latest_result.get("drowsy_level") not in (2, 3):
        raise HTTPException(status_code=409, detail="2·3단계 경보에서만 중지 확인이 필요합니다")
    risk_id = getattr(body, "risk_id", None)
    expected = active.latest_result.get("risk_id")
    if expected and risk_id != expected:
        raise HTTPException(status_code=409, detail="새 위험이 발생했습니다. 현재 경보에서 다시 중지해주세요")
    if expected:
        active.acknowledge_alarm(risk_id)
    else:
        active.acknowledge_alarm()
    return {"session_id": body.session_id, "status": "accepted",
            "message": "경보 중지 확인을 접수했습니다. 정상 상태 10초 확인 후 0단계로 회복합니다."}


@router.websocket("/ws/{session_id}")
async def detection_ws(websocket: WebSocket, session_id: int):
    """실시간 감지 결과를 WebSocket으로 스트리밍."""
    authorization = websocket.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    token = credentials.strip() if scheme.lower() == "bearer" else ""
    if not token:
        await websocket.accept()  # Deliver an actual close frame to OkHttp, not handshake HTTP 403.
        await websocket.close(code=4001, reason="로그인이 필요합니다")
        return

    try:
        async with async_session() as db:
            current_user = await resolve_current_user(token, db)
    except HTTPException:
        await websocket.accept()
        await websocket.close(code=4001, reason="로그인이 만료되었습니다")
        return

    active = get_active_session()
    if active is None or active.session_id != session_id or active.user_id != current_user.id:
        await websocket.accept()
        await websocket.close(
            code=4004, reason="해당 세션의 감지가 실행 중이 아닙니다"
        )
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    last_verified = loop.time()
    last_ping = loop.time()

    async def authenticated() -> bool:
        nonlocal last_verified
        if loop.time() - last_verified < WS_AUTH_RECHECK_SECONDS:
            return True
        try:
            async with async_session() as db:
                await resolve_current_user(token, db)
        except HTTPException:
            await websocket.close(code=4001, reason="로그인이 만료되었습니다. 다시 로그인해주세요")
            return False
        last_verified = loop.time()
        return True

    try:
        while active.is_running:
            try:
                result = await asyncio.wait_for(active.queue.get(), timeout=WS_AUTH_RECHECK_SECONDS)
                if not await authenticated():
                    return
                await websocket.send_json(result)
                if "error" in result:
                    await websocket.close(code=1011, reason="Detection stopped")
                    return
            except asyncio.TimeoutError:
                if not await authenticated():
                    return
                if loop.time() - last_ping >= 5.0:
                    await websocket.send_json({"ping": True})
                    last_ping = loop.time()
        if active.latest_result and "error" in active.latest_result:
            await websocket.send_json(active.latest_result)
            await websocket.close(code=1011, reason="Detection stopped")
        else:
            await websocket.close(code=1000, reason="Detection stopped")
    except WebSocketDisconnect:
        pass


@router.get("/status/{session_id}", response_model=DetectionStatusResponse)
async def detection_status(
    session_id: int,
    current_user: User = Depends(get_current_user),
):
    """현재 감지 상태 조회."""
    active = get_active_session()
    is_running = (
        active is not None
        and active.session_id == session_id
        and active.user_id == current_user.id
        and active.is_running
    )
    return DetectionStatusResponse(session_id=session_id, is_running=is_running)
