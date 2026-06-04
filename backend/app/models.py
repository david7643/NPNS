"""DrowsyGuard 데이터베이스 모델 정의."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
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

    sessions: Mapped[list["DrivingSession"]] = relationship(back_populates="user")


class DrivingSession(Base):
    """운전 세션 테이블."""

    __tablename__ = "driving_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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

    session: Mapped["DrivingSession"] = relationship(back_populates="logs")
