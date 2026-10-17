"""DrowsyGuard Pydantic 스키마 정의."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, computed_field, field_validator, model_validator


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
    device_id: str = Field(..., min_length=3, max_length=200)


class ContactUpdate(BaseModel):
    """비상 연락처 수정."""

    emergency_contact: str


# ── Session 관련 스키마 ──


class _SessionTimeResponse(BaseModel):
    """SQLite의 UTC 시각을 명시하고 같은 서버 시계로 경과 시간을 계산한다."""

    started_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("started_at", "ended_at", check_fields=False)
    @classmethod
    def normalize_utc(cls, value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @computed_field
    @property
    def elapsed_seconds(self) -> int:
        end = getattr(self, "ended_at", None) or datetime.now(timezone.utc)
        return max(0, int((end - self.started_at).total_seconds()))


class SessionStartResponse(_SessionTimeResponse):
    """세션 시작 응답."""

    session_id: int


class SessionEndResponse(_SessionTimeResponse):
    """세션 종료 응답."""

    session_id: int
    ended_at: datetime


class SessionResponse(_SessionTimeResponse):
    """세션 상세 응답."""

    id: int
    user_id: int
    ended_at: datetime | None
    distance_meters: float = 0.0


class SessionEndRequest(BaseModel):
    """세션 종료 요청."""

    session_id: int


class SessionDistanceUpdate(BaseModel):
    distance_meters: float = Field(ge=0, le=100_000_000, allow_inf_nan=False)


class EventLocationUpdate(BaseModel):
    session_id: int = Field(gt=0)
    received_at: AwareDatetime
    latitude: float | None = Field(None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(None, ge=-180, le=180, allow_inf_nan=False)
    measured_at: AwareDatetime | None = None
    accuracy_meters: float | None = Field(None, ge=0, allow_inf_nan=False)
    location_status: Literal["available", "permission_denied", "services_disabled", "stale", "inaccurate", "no_fix"]

    @model_validator(mode="after")
    def validate_location(self):
        if self.location_status == "available":
            if any(value is None for value in (self.latitude, self.longitude, self.measured_at, self.accuracy_meters)):
                raise ValueError("위치에는 좌표·측정 시각·정확도가 필요합니다")
            if self.accuracy_meters > 100 or not 0 <= (self.received_at - self.measured_at).total_seconds() <= 30:
                raise ValueError("수신 당시 유효한 위치만 연결할 수 있습니다")
        elif self.latitude is not None or self.longitude is not None:
            raise ValueError("유효하지 않은 위치 상태에 좌표를 넣을 수 없습니다")
        return self


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
    """감지 시작/종료 요청."""

    session_id: int


class AlarmAcknowledgeRequest(DetectionStartRequest):
    risk_id: str | None = Field(None, max_length=36)


class SmsSettingsUpdate(BaseModel):
    enabled: bool


class DetectionStartResponse(BaseModel):
    """감지 시작 응답."""

    session_id: int
    status: str
    message: str
    calibration_required_seconds: float | None = None
    detection_code_version: str | None = None
    model_release_id: str | None = None


class DetectionStatusResponse(BaseModel):
    """감지 상태 조회 응답."""

    session_id: int
    is_running: bool


# ── Report 관련 스키마 ──


class ChartDataItem(BaseModel):
    """시간대별 차트 데이터 항목."""

    hour: int
    count: int
    level1: int = 0
    level2: int = 0
    level3: int = 0
    max_level: int = 0


class DrowsyEventItem(BaseModel):
    """졸음 감지 이벤트 항목 (시간 + 위치 + 단계)."""

    timestamp: datetime
    drowsy_level: int
    latitude: float | None
    longitude: float | None
    event_id: str | None = None
    app_received_at: datetime | None = None
    location_measured_at: datetime | None = None
    location_accuracy_meters: float | None = None
    location_status: str | None = None


class ObservationMetrics(BaseModel):
    independent_event_count: int | None = None
    valid_observation_seconds: float = 0.0
    unavailable_observation_seconds: float = 0.0
    risk_observation_seconds: float = 0.0
    observed_seconds: float = 0.0
    observation_coverage: float | None = None
    independent_events_per_hour: float | None = None
    assessment_status: str = "no_valid_observation"
    episode_peak_level: int = 0


class ReportSummaryResponse(ObservationMetrics):
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
    distance_meters: float = 0.0


class ReportHistoryItem(ObservationMetrics):
    """히스토리 목록의 세션 항목."""

    session_id: int
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: int | None
    safety_score: int
    grade: str
    total_drowsy_count: int
    distance_meters: float = 0.0


class ReportHistoryResponse(BaseModel):
    """전체 세션 히스토리 응답."""

    total_sessions: int
    sessions: list[ReportHistoryItem]


class ReportByDateResponse(ObservationMetrics):
    """특정 날짜 리포트 응답 (해당 날짜의 모든 세션 합산)."""

    date: str
    session_count: int
    safety_score: int
    grade: str
    total_drowsy_count: int
    level1_count: int
    level2_count: int
    level3_count: int
    most_dangerous_time: str | None
    chart_data: list[ChartDataItem]
    events: list[DrowsyEventItem]


class ReportDetailResponse(ObservationMetrics):
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
    distance_meters: float = 0.0


# ── Contact 관련 스키마 ──


class ContactCreate(BaseModel):
    """긴급 연락처 생성 요청."""

    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=8, max_length=30)
    message: str = Field(..., min_length=1, max_length=500)
    model_config = {"str_strip_whitespace": True}


class SavedContactUpdate(BaseModel):
    """긴급 연락처 수정 요청."""

    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=8, max_length=30)
    message: str = Field(..., min_length=1, max_length=500)
    model_config = {"str_strip_whitespace": True}


class ContactResponse(BaseModel):
    """긴급 연락처 응답."""

    id: int
    name: str
    phone: str
    message: str

    model_config = {"from_attributes": True}
