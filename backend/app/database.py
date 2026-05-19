"""DrowsyGuard 데이터베이스 설정."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.DATABASE_URL, echo=(settings.APP_ENV == "development"))
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """모든 모델의 베이스 클래스."""
    pass


async def get_db():
    """FastAPI 의존성: DB 세션 제공."""
    async with async_session() as session:
        yield session


async def init_db():
    """애플리케이션 시작 시 테이블 생성."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
