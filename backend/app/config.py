"""DrowsyGuard 백엔드 설정 모듈."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings
from pydantic import field_validator
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """환경변수 기반 앱 설정."""

    APP_ENV: str = "development"
    SECRET_KEY: str = "dev-secret-key-do-not-use-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24시간
    DATABASE_URL: str = "sqlite+aiosqlite:///./drowsyguard.db"
    # 상대 경로는 app/ 기준이 아닌 backend/ 기준으로 해석한다.
    DROWSINESS_MODEL_DIR: str | None = None
    # Linux에서는 프레임을 읽을 수 있는 USB 웹캠을 자동 선택한다.
    DROWSINESS_CAMERA_INDEX: int | Literal["auto"] = "auto"
    # 감지 시작 시 서버 노트북에 카메라 영상과 판정 지표를 표시한다.
    DROWSINESS_PREVIEW_ENABLED: bool = True
    # 아두이노 경보기 포트 (예: COM3, /dev/ttyACM0). 비워두면 사용하지 않는다.
    ARDUINO_PORT: str | None = None
    ARDUINO_BAUD: int = 9600

    @field_validator("DATABASE_URL")
    @classmethod
    def resolve_sqlite_database(cls, value):
        url = make_url(value)
        if url.drivername.startswith("sqlite") and url.database not in (None, "", ":memory:"):
            path = Path(url.database)
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[1] / path
            return url.set(database=path.resolve().as_posix()).render_as_string(hide_password=False)
        return value

    model_config = {"env_file": str(Path(__file__).resolve().parents[1] / ".env")}


@lru_cache
def get_settings() -> Settings:
    return Settings()
