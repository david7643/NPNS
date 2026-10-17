"""DrowsyGuard 데이터베이스 모델 정의."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    """사용자 테이블."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    emergency_contact: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Legacy compatibility column. SMS policy now depends on registered contacts, not this flag.
    auto_sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    sessions: Mapped[list["DrivingSession"]] = relationship(back_populates="user")
    contacts: Mapped[list["Contact"]] = relationship()


class LoginSession(Base):
    """사용자별 단일 활성 로그인 세션."""

    __tablename__ = "login_sessions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    token_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DrivingSession(Base):
    """운전 세션 테이블."""

    __tablename__ = "driving_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    distance_meters: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")

    user: Mapped["User"] = relationship(back_populates="sessions")
    logs: Mapped[list["DetectionLog"]] = relationship(back_populates="session")


class DetectionLog(Base):
    """졸음 감지 로그 테이블."""

    __tablename__ = "detection_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(ForeignKey("driving_sessions.id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ear_value: Mapped[float] = mapped_column(Float, nullable=False)
    pred_score: Mapped[float] = mapped_column(Float, nullable=False)
    drowsy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)   # 위도
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)  # 경도
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    app_received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    location_measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    location_accuracy_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    episode_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    session: Mapped["DrivingSession"] = relationship(back_populates="logs")


class DrowsinessEpisode(Base):
    """단계 상승과 별개로 재개방 이후 새로 인정된 눈 감김 사건."""
    __tablename__ = "drowsiness_episodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("driving_sessions.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    peak_level: Mapped[int] = mapped_column(Integer)


class ObservationSegment(Base):
    """프레임 사이 실제 관측 구간. 관측 공백을 정상 시간으로 간주하지 않는다."""
    __tablename__ = "observation_segments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("driving_sessions.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    valid: Mapped[bool] = mapped_column(Boolean)
    level: Mapped[int] = mapped_column(Integer)


class Contact(Base):
    """사용자별 긴급 연락처 테이블."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
