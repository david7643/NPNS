"""DrowsyGuard 백엔드 서버 메인 엔트리포인트."""

from contextlib import asynccontextmanager
from pathlib import Path
import sys

# IDE에서 파일을 직접 실행해도 backend 아래의 app 패키지를 찾는다.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI

from app.arduino_bridge import get_arduino, start_arduino
from app.database import init_db
from app.detection_service import (
    camera_available,
    get_active_session,
    load_models,
    models_ready,
    model_runtime_info,
    stop_detection,
)
from app.routers import auth, contacts, detection, report, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 DB 테이블 생성 및 AI 모델 로드."""
    await init_db()
    load_models()
    start_arduino()
    try:
        yield
    finally:
        stop_detection()
        get_arduino().close()


app = FastAPI(
    title="DrowsyGuard API",
    description="졸음운전 방지 시스템 백엔드",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(detection.router)
app.include_router(report.router)
app.include_router(contacts.router)


@app.get("/health")
async def health_check():
    """서버 상태 확인용 헬스체크 엔드포인트."""
    active = get_active_session()
    model_status = models_ready()
    camera_status = camera_available()
    return {
        **model_runtime_info(),
        "status": "ok" if model_status and camera_status else "not_ready",
        "models_ready": model_status,
        "camera_available": camera_status,
        "arduino_connected": get_arduino().connected,
        "co2_ppm": get_arduino().co2_ppm,
        "detection_active": bool(active and active.is_running),
        "active_session_id": active.session_id if active and active.is_running else None,
    }


if __name__ == "__main__":
    import uvicorn
    print("Starting DrowsyGuard Backend Server directly from main.py...")
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=8000,
        reload=True, app_dir=str(BACKEND_DIR),
    )
