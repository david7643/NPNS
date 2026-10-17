"""DrowsyGuard 데이터베이스 설정."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import inspect

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
        await conn.run_sync(migrate_record_metadata)


def migrate_record_metadata(connection):
    """기존 SQLite 기록을 보존하며 위치 동기화·거리 컬럼을 추가한다."""
    additions = {
        "users": {"auto_sms_enabled": "BOOLEAN NOT NULL DEFAULT 0"},
        "driving_sessions": {"distance_meters": "FLOAT NOT NULL DEFAULT 0"},
        "detection_logs": {
            "event_id": "VARCHAR(36)", "app_received_at": "DATETIME",
            "location_measured_at": "DATETIME", "location_accuracy_meters": "FLOAT",
            "location_status": "VARCHAR(32)",
            "episode_id": "VARCHAR(36)",
        },
    }
    inspector = inspect(connection)
    for table, columns in additions.items():
        if not inspector.has_table(table):
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, declaration in columns.items():
            if name not in existing:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    if inspector.has_table("detection_logs"):
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_detection_logs_event_id ON detection_logs(event_id)"
        )
