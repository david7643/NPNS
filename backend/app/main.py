"""DrowsyGuard 백엔드 서버 메인 엔트리포인트."""

from contextlib import asynccontextmanager
import asyncio
import os
from pathlib import Path
import sys

# Linux에서 MediaPipe의 CPU 프로파일러 타이머가 프로세스를 종료하는 문제를 우회한다.
# TensorFlow/MediaPipe가 로드되기 전에 설정해야 한다 (mediapipe#6375).
if sys.platform.startswith("linux"):
    os.environ.setdefault("CPUPROFILE_FREQUENCY", "0")

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
from app.routers import auth, contacts, detection, devices, report, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """DB·모델·기록 저장 작업·아두이노의 시작과 종료를 관리한다."""
    await init_db()
    from app.record_journal import get_journal, run_writer

    get_journal()
    writer_stop = asyncio.Event()
    writer = asyncio.create_task(run_writer(writer_stop))
    try:
        load_models()
        start_arduino()
        yield
    finally:
        try:
            await asyncio.to_thread(stop_detection)
        finally:
            try:
                await asyncio.to_thread(get_arduino().close)
            finally:
                writer_stop.set()
                await writer


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
app.include_router(devices.router)


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
        **get_arduino().status(),
        "detection_active": bool(active and active.is_running),
        "active_session_id": active.session_id if active and active.is_running else None,
        "record_storage_error": active.latest_result.get("storage_error") if active and active.latest_result else None,
    }


if __name__ == "__main__":
    import uvicorn
    print("Starting DrowsyGuard Backend Server directly from main.py...")
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=8000,
        reload=True, app_dir=str(BACKEND_DIR),
    )
