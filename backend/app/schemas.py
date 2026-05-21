"""DrowsyGuard Pydantic 스키마 정의."""

from datetime import datetime
from pydantic import BaseModel


# ── Auth 관련 스키마 ──


class UserCreate(BaseModel):
    """회원가입 요청."""

    username: str
    name: str
    password: str
    emergency_contact: str | None = None


class UserResponse(BaseModel):
    """사용자 정보 응답."""

    id: int
    username: str
    name: str
    emergency_contact: str | None

    model_config = {"from_attributes": True}


class Token(BaseModel):
    """JWT 토큰 응답."""

    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    """로그인 요청."""

    username: str
    password: str


class ContactUpdate(BaseModel):
    """비상 연락처 수정."""

    emergency_contact: str

# ── Session 관련 스키마 ──

class SessionResponse(BaseModel):
    """운전 세션 응답."""

    id: int
    user_id: int
    started_at: datetime
    ended_at: datetime | None

    model_config = {"from_attributes": True}


class SessionEndRequest(BaseModel):
    """운전 세션 종료 요청."""

    session_id: int