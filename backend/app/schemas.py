"""DrowsyGuard Pydantic 스키마 정의."""

from datetime import datetime

from pydantic import BaseModel, Field


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


class SessionStartResponse(BaseModel):
    """세션 시작 응답."""

    session_id: int
    started_at: datetime

    model_config = {"from_attributes": True}


class SessionEndResponse(BaseModel):
    """세션 종료 응답."""

    session_id: int
    started_at: datetime
    ended_at: datetime

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    """세션 상세 응답."""

    id: int
    user_id: int
    started_at: datetime
    ended_at: datetime | None

    model_config = {"from_attributes": True}


class SessionEndRequest(BaseModel):
    """세션 종료 요청."""

    session_id: int


# ── Detection 관련 스키마 ──


class DetectionLogCreate(BaseModel):
    """졸음 감지 로그 저장 요청."""

    session_id: int
    ear_value: float = Field(..., ge=0.0, le=1.0, description="EAR 값 (0.0 ~ 1.0)")
    pred_score: float = Field(..., ge=0.0, le=1.0, description="모델 예측 확률 (0.0 ~ 1.0)")
    drowsy_level: int = Field(..., ge=0, le=3, description="졸음 단계 (0=정상, 1~3단계)")
    latitude: float | None = Field(None, description="위도")
    longitude: float | None = Field(None, description="경도")


class DetectionLogResponse(BaseModel):
    """졸음 감지 로그 저장 응답."""

    id: int
    session_id: int
    user_id: int
    timestamp: datetime
    ear_value: float
    pred_score: float
    drowsy_level: int
    latitude: float | None
    longitude: float | None

    model_config = {"from_attributes": True}


class DetectionStartRequest(BaseModel):
    """감지 시작/종료 요청 (팀원 추가)."""

    session_id: int


class DetectionStartResponse(BaseModel):
    """감지 시작 응답 (팀원 추가)."""

    session_id: int
    status: str
    message: str


class DetectionStatusResponse(BaseModel):
    """감지 상태 조회 응답 (팀원 추가)."""

    session_id: int
    is_running: bool


# ── Report 관련 스키마 ──


class ChartDataItem(BaseModel):
    """시간대별 차트 데이터 항목."""

    hour: int
    count: int


class DrowsyEventItem(BaseModel):
    """졸음 감지 이벤트 항목 (시간 + 위치 + 단계)."""

    timestamp: datetime
    drowsy_level: int
    latitude: float | None
    longitude: float | None


class ReportSummaryResponse(BaseModel):
    """리포트 요약 응답."""

    session_id: int
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: int | None
    safety_score: int
    grade: str
    total_drowsy_count: int
    level1_count: int
    level2_count: int
    level3_count: int
    most_dangerous_time: str | None
    chart_data: list[ChartDataItem]
    events: list[DrowsyEventItem]


class ReportHistoryItem(BaseModel):
    """히스토리 목록의 세션 항목."""

    session_id: int
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: int | None
    safety_score: int
    grade: str
    total_drowsy_count: int


class ReportHistoryResponse(BaseModel):
    """전체 세션 히스토리 응답."""

    total_sessions: int
    sessions: list[ReportHistoryItem]


class ReportDetailResponse(BaseModel):
    """세션 상세 리포트 응답."""

    session_id: int
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: int | None
    safety_score: int
    grade: str
    total_drowsy_count: int
    level1_count: int
    level2_count: int
    level3_count: int
    most_dangerous_time: str | None
    chart_data: list[ChartDataItem]
    events: list[DrowsyEventItem]