"""DrowsyGuard 백엔드 설정 모듈."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """환경변수 기반 앱 설정."""

    APP_ENV: str = "development"
    SECRET_KEY: str = "dev-secret-key-do-not-use-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24시간
    DATABASE_URL: str = "sqlite+aiosqlite:///./drowsyguard.db"

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
