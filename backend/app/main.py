"""DrowsyGuard 백엔드 서버 메인 엔트리포인트."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.detection_service import load_models
from app.routers import auth, detection, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 DB 테이블 생성 및 AI 모델 로드."""
    await init_db()
    load_models()
    yield


app = FastAPI(
    title="DrowsyGuard API",
    description="졸음운전 방지 시스템 백엔드",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(detection.router)


@app.get("/health")
async def health_check():
    """서버 상태 확인용 헬스체크 엔드포인트."""
    return {"status": "ok"}