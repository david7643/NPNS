"""졸음 감지 로그 관련 API 라우터."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import DetectionLog, DrivingSession, User
from app.schemas import DetectionLogCreate, DetectionLogResponse

router = APIRouter(prefix="/detection", tags=["detection"])


@router.post("/log", response_model=DetectionLogResponse, status_code=status.HTTP_201_CREATED, summary="졸음 감지 데이터 저장")
async def create_detection_log(
    body: DetectionLogCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    LSTM 모델에서 넘어온 EAR, pred_score, drowsy_level, 위치(위도/경도)를 원본 그대로 DB에 저장합니다.

    drowsy_level 기준:
    - 0: 정상
    - 1: 1단계 (pred_score > 0.5, 10프레임 연속)
    - 2: 2단계 (pred_score > 0.7, 15프레임 연속)
    - 3: 3단계 (pred_score > 0.85, 20프레임 연속)
    """
    result = await db.execute(select(DrivingSession).where(DrivingSession.id == body.session_id))
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=400, detail="존재하지 않는 세션입니다.")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="본인의 세션에만 데이터를 저장할 수 있습니다.")
    if session.ended_at is not None:
        raise HTTPException(status_code=400, detail="이미 종료된 세션에는 데이터를 저장할 수 없습니다.")

    log = DetectionLog(
        user_id=current_user.id,
        session_id=body.session_id,
        ear_value=body.ear_value,
        pred_score=body.pred_score,
        drowsy_level=body.drowsy_level,
        latitude=body.latitude,
        longitude=body.longitude,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log
