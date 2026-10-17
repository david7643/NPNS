"""카메라·졸음 감지의 실행 여부와 독립적인 장치 상태 조회."""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.arduino_bridge import get_arduino
from app.dependencies import get_current_user


class DeviceTelemetry(BaseModel):
    arduino_enabled: bool
    arduino_connected: bool
    arduino_responding: bool
    arduino_alarm_on: bool | None
    co2_ppm: int | None
    # 센서 자체의 시계가 아닌, 서버가 시리얼 값을 수신한 뒤 경과한 시간.
    co2_age_seconds: float | None


router = APIRouter(prefix="/devices", tags=["devices"], dependencies=[Depends(get_current_user)])


@router.get("/telemetry", response_model=DeviceTelemetry)
async def telemetry(response: Response):
    """시리얼 워커의 최신 상태만 읽는다. 카메라나 장치를 작동시키지 않는다."""
    response.headers["Cache-Control"] = "no-store"
    return get_arduino().status()
