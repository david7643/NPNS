"""졸음 감지 서비스 — 카메라 제어 + 모델 추론 + 3단계 판정."""

import asyncio
import json
import hashlib
import logging
import os
import threading
import time
from queue import SimpleQueue
from collections import deque

import cv2
import mediapipe as mp_lib
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import joblib
from app.arduino_bridge import get_arduino
from app.config import get_settings

from app.ear_sequence import (
    EarSequenceBuffer, MAX_OBSERVATION_GAP_SECONDS, WINDOW_SIZE, WINDOW_SECONDS, normalize_eye_ear,
)

try:
    from tensorflow.keras.models import load_model
except ModuleNotFoundError:
    # The HTTP API can still run when the optional AI runtime is absent.
    load_model = None

# ── 전역: 서버 시작 시 1회 로드 ──
_lstm_model = None
_scaler = None
_face_model_buffer = None
_model_metadata = {"normalization": "raw", "decision_threshold": 0.5}
_loaded_model_dir = None
DETECTION_CODE_VERSION = "event-risk-v7"

# MediaPipe 눈 랜드마크 인덱스 (realtime_detect.py와 동일)
LEFT_EYE = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]

NO_FACE_RESET_SECONDS = 1.0  # 이 시간 이상 얼굴이 사라지면 과거 시계열 폐기
FPS_SAMPLE_SIZE = 60
CALIBRATION_SECONDS = 3.0
CALIBRATION_MIN_SAMPLES = 15
EYE_CLOSE_RATIO = 0.72
EYE_REOPEN_RATIO = 0.82
EYE_REOPEN_HOLD_SECONDS = 0.5
MODEL_EYE_SUPPORT_RATIO = 0.80
EYE_CLOSED_GAP_TOLERANCE_SECONDS = 0.15

# v3/v4 재현용 지속 시간. 현재 정책은 첫 값을 사건 진입에, 눈 감김 마지막 값을 긴급 판정에 사용한다.
MODEL_LEVEL_SECONDS = (1.25, 1.75, 2.25)
EYE_CLOSED_LEVEL_SECONDS = (1.25, 2.0, 3.0)
EVENT_WINDOW_SECONDS = 30.0
LEVEL_ONE_RECOVERY_SECONDS = 10.0
LEVEL_TWO_RECOVERY_SECONDS = 10.0
RECOVERY_BLINK_TOLERANCE_SECONDS = 0.5
CLOSURE_RATIO_WINDOW_SECONDS = 60.0

LEVEL_MESSAGES = {
    0: "정상",
    1: "졸음 1단계 — 주의",
    2: "졸음 2단계 — 경고",
    3: "졸음 3단계 — 위험",
}

logger = logging.getLogger(__name__)

AI_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ai_models")
DEFAULT_MODEL_DIR = os.path.join(AI_MODELS_DIR, "released", "personal-ear-v1")


def resolve_model_directory(configured_dir=None):
    if not configured_dir:
        return os.path.abspath(DEFAULT_MODEL_DIR)
    if not os.path.isabs(configured_dir):
        configured_dir = os.path.join(os.path.dirname(AI_MODELS_DIR), configured_dir)
    return os.path.abspath(configured_dir)


def load_models():
    """서버 시작 시 AI 모델을 메모리에 로드."""
    global _lstm_model, _scaler, _face_model_buffer, _model_metadata, _loaded_model_dir

    _lstm_model = _scaler = _face_model_buffer = None
    _loaded_model_dir = None
    _model_metadata = {"normalization": "raw", "decision_threshold": 0.5}
    selected_dir = resolve_model_directory(get_settings().DROWSINESS_MODEL_DIR)
    model_path = os.path.join(selected_dir, "best_drowsy_model.keras")
    scaler_path = os.path.join(selected_dir, "ear_scaler.pkl")
    face_model_path = os.path.join(AI_MODELS_DIR, "face_landmarker.task")

    missing_paths = [
        path for path in (model_path, scaler_path, face_model_path) if not os.path.isfile(path)
    ]
    if load_model is None:
        logger.warning("TensorFlow is not installed; drowsiness detection is unavailable.")
        return
    if missing_paths:
        logger.warning(
            "Drowsiness detection is unavailable; missing model files: %s",
            ", ".join(missing_paths),
        )
        return

    try:
        metadata_path = os.path.join(selected_dir, "metadata.json")
        if os.path.isfile(metadata_path):
            with open(metadata_path, encoding="utf-8") as source:
                metadata = json.load(source)
            if metadata.get("normalization") not in ("raw", "personal_baseline"):
                raise ValueError("Unsupported model normalization")
            if metadata.get("window_size") != WINDOW_SIZE or not np.isclose(
                metadata.get("window_seconds", -1), WINDOW_SECONDS
            ):
                raise ValueError("Model timing does not match runtime")
            if not 0 < float(metadata.get("decision_threshold", 0)) < 1:
                raise ValueError("Invalid model decision threshold")
            _model_metadata = metadata
            for name, asset_path in (("best_drowsy_model.keras", model_path), ("ear_scaler.pkl", scaler_path)):
                expected_hash = metadata.get("asset_sha256", {}).get(name)
                if expected_hash:
                    digest = hashlib.sha256()
                    with open(asset_path, "rb") as asset:
                        for chunk in iter(lambda: asset.read(65536), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != expected_hash:
                        raise ValueError(f"Release model bundle checksum mismatch: {name}")
        elif selected_dir != os.path.abspath(AI_MODELS_DIR):
            raise ValueError("Candidate model requires metadata.json")
        _lstm_model = load_model(model_path, compile=False)
        if _lstm_model.input_shape[1:] != (WINDOW_SIZE, 1):
            raise ValueError("Model input shape does not match runtime")
        _scaler = joblib.load(scaler_path)
        with open(face_model_path, "rb") as f:
            _face_model_buffer = f.read()
        _loaded_model_dir = selected_dir
        logger.info("Detection model loaded: %s (%s)", selected_dir, _model_metadata["normalization"])
    except Exception:
        _lstm_model = None
        _scaler = None
        _face_model_buffer = None
        logger.exception("Failed to load drowsiness-detection models.")


def model_runtime_info():
    return {
        "detection_code_version": DETECTION_CODE_VERSION,
        "model_directory": _loaded_model_dir,
        "model_release_id": _model_metadata.get("release_id", "custom" if _loaded_model_dir else None),
        "model_normalization": _model_metadata["normalization"],
        "model_threshold": float(_model_metadata["decision_threshold"]),
        "model_window_seconds": round(WINDOW_SECONDS, 3),
        "model_entry_seconds": MODEL_LEVEL_SECONDS[0],
        "eye_entry_seconds": EYE_CLOSED_LEVEL_SECONDS[0],
        "closed_gap_tolerance_seconds": EYE_CLOSED_GAP_TOLERANCE_SECONDS,
        "calibration_required_seconds": CALIBRATION_SECONDS,
        "calibration_required_samples": CALIBRATION_MIN_SAMPLES,
        "event_window_seconds": EVENT_WINDOW_SECONDS,
        "emergency_closed_seconds": EYE_CLOSED_LEVEL_SECONDS[2],
        "level_one_recovery_seconds": LEVEL_ONE_RECOVERY_SECONDS,
        "level_two_recovery_seconds": LEVEL_TWO_RECOVERY_SECONDS,
        "recovery_blink_tolerance_seconds": RECOVERY_BLINK_TOLERANCE_SECONDS,
        "alarm_stop_policy": "manual_app_button",
        "level_two_event_count": 2,
        "level_three_events_after_warning": 1,
        "high_level_recovery_requires_alarm_ack": True,
        "closure_ratio_window_seconds": CLOSURE_RATIO_WINDOW_SECONDS,
    }


def _calc_ear(landmarks, eye_indices, w, h):
    """EAR(Eye Aspect Ratio) 계산."""
    pts = np.array([(landmarks[i].x * w, landmarks[i].y * h) for i in eye_indices])
    v1 = np.linalg.norm(pts[1] - pts[5])
    v2 = np.linalg.norm(pts[2] - pts[4])
    horiz = np.linalg.norm(pts[0] - pts[3])
    if horiz == 0:
        return 0.0
    return float((v1 + v2) / (2.0 * horiz))


class LegacyDrowsinessDecisionEngine:
    """개인별 EAR 보정과 시간 기반 졸음 판정을 담당한다."""

    def __init__(
        self, model_threshold=0.5, *, model_level_seconds=None,
        eye_level_seconds=None, closed_gap_tolerance=None,
    ):
        self.model_threshold = float(model_threshold)
        self.model_level_seconds = tuple(
            MODEL_LEVEL_SECONDS if model_level_seconds is None else model_level_seconds
        )
        self.eye_level_seconds = tuple(
            EYE_CLOSED_LEVEL_SECONDS if eye_level_seconds is None else eye_level_seconds
        )
        self.closed_gap_tolerance = (
            EYE_CLOSED_GAP_TOLERANCE_SECONDS if closed_gap_tolerance is None else closed_gap_tolerance
        )
        for durations in (self.model_level_seconds, self.eye_level_seconds):
            if len(durations) != 3 or any(not np.isfinite(t) or t <= 0 for t in durations):
                raise ValueError("Three finite positive level durations are required")
            if sorted(durations) != list(durations):
                raise ValueError("Level durations must be nondecreasing")
        if not np.isfinite(self.closed_gap_tolerance) or self.closed_gap_tolerance < 0:
            raise ValueError("Closed-eye gap tolerance must be finite and nonnegative")
        self.model_thresholds = [
            self.model_threshold + (1 - self.model_threshold) * ratio
            for ratio in (0.0, 0.4, 0.7)
        ]
        self.calibration_started_at: float | None = None
        self.calibration_last_at: float | None = None
        self.left_samples: list[float] = []
        self.right_samples: list[float] = []
        self.left_baseline: float | None = None
        self.right_baseline: float | None = None
        self.closed_started_at: float | None = None
        self.closed_gap_started_at: float | None = None
        self.reopened_started_at: float | None = None
        self.model_started_at: list[float | None] = [None, None, None]
        self.current_level = 0

    @property
    def calibrated(self) -> bool:
        return self.left_baseline is not None and self.right_baseline is not None

    @property
    def calibration_progress(self) -> float:
        if self.calibration_started_at is None or self.calibration_last_at is None:
            return 0.0
        sample_progress = len(self.left_samples) / CALIBRATION_MIN_SAMPLES
        time_progress = (
            self.calibration_last_at - self.calibration_started_at
        ) / CALIBRATION_SECONDS
        return min(sample_progress, time_progress, 1.0)

    @property
    def close_thresholds(self) -> tuple[float | None, float | None]:
        if not self.calibrated:
            return None, None
        return self.left_baseline * EYE_CLOSE_RATIO, self.right_baseline * EYE_CLOSE_RATIO

    def add_calibration_sample(self, left_ear: float, right_ear: float, now: float) -> bool:
        if self.calibrated:
            return True
        if self.calibration_started_at is None:
            self.calibration_started_at = now
        self.calibration_last_at = now
        self.left_samples.append(left_ear)
        self.right_samples.append(right_ear)
        enough_time = now - self.calibration_started_at >= CALIBRATION_SECONDS
        enough_samples = len(self.left_samples) >= CALIBRATION_MIN_SAMPLES
        if enough_time and enough_samples:
            # 상위 분위수를 사용해 보정 중 자연스러운 깜빡임의 영향을 줄인다.
            self.left_baseline = float(np.percentile(self.left_samples, 70))
            self.right_baseline = float(np.percentile(self.right_samples, 70))
        return self.calibrated

    def reset_transient(self):
        """얼굴이 오래 사라졌을 때 진행 중인 판정만 폐기한다."""
        self.interrupt_evidence()
        self.current_level = 0

    def interrupt_evidence(self):
        """미검출 시간을 눈 감김이나 모델 지속 시간에 포함하지 않는다."""
        self.closed_started_at = None
        self.closed_gap_started_at = None
        self.reopened_started_at = None
        self.model_started_at = [None, None, None]

    def update(
        self,
        left_ear: float,
        right_ear: float,
        pred_score: float | None,
        now: float,
    ) -> dict:
        if not self.calibrated:
            return {
                "level": 0,
                "eyes_closed": False,
                "closed_seconds": 0.0,
                "model_durations": [0.0, 0.0, 0.0],
            }

        left_ratio = left_ear / self.left_baseline
        right_ratio = right_ear / self.right_baseline
        eyes_closed = bool(
            left_ratio <= EYE_CLOSE_RATIO and right_ratio <= EYE_CLOSE_RATIO
        )
        # 한쪽만 뜨거나 추적이 흔들리는 상태는 확실한 재개방으로 보지 않는다.
        eyes_reopened = (
            left_ratio >= EYE_REOPEN_RATIO and right_ratio >= EYE_REOPEN_RATIO
        )
        model_eye_support = bool(
            left_ratio <= MODEL_EYE_SUPPORT_RATIO
            and right_ratio <= MODEL_EYE_SUPPORT_RATIO
        )

        if eyes_closed:
            if (
                self.closed_gap_started_at is not None
                and now - self.closed_gap_started_at >= self.closed_gap_tolerance
            ):
                self.closed_started_at = None
            self.closed_gap_started_at = None
            if self.closed_started_at is None:
                self.closed_started_at = now
            self.reopened_started_at = None
        elif eyes_reopened:
            self.closed_started_at = None
            self.closed_gap_started_at = None
            if self.reopened_started_at is None:
                self.reopened_started_at = now
        else:
            # 경계 상태를 무기한 눈 감김으로 누적하지 않는다.
            if self.closed_started_at is not None:
                if self.closed_gap_started_at is None:
                    self.closed_gap_started_at = now
                if now - self.closed_gap_started_at >= self.closed_gap_tolerance:
                    self.closed_started_at = None
            self.reopened_started_at = None

        closed_seconds = (
            now - self.closed_started_at if self.closed_started_at is not None else 0.0
        )
        eye_level = int(sum(
            closed_seconds >= threshold for threshold in self.eye_level_seconds
        ))

        model_durations = []
        for index in range(3):
            if (
                model_eye_support
                and pred_score is not None
                and pred_score >= self.model_thresholds[index]
            ):
                if self.model_started_at[index] is None:
                    self.model_started_at[index] = now
            else:
                self.model_started_at[index] = None
            started_at = self.model_started_at[index]
            model_durations.append(now - started_at if started_at is not None else 0.0)

        model_level = 0
        for index, (duration, required) in enumerate(zip(model_durations, self.model_level_seconds)):
            if duration >= required:
                model_level = index + 1

        candidate_level = max(eye_level, model_level)
        if (
            self.reopened_started_at is not None
            and now - self.reopened_started_at >= EYE_REOPEN_HOLD_SECONDS
        ):
            # 이전 눈 감김이 LSTM 입력에 남아 있어도 재개방이 확인되면 해제한다.
            self.current_level = 0
            self.model_started_at = [None, None, None]
        elif candidate_level > 0:
            self.current_level = max(self.current_level, candidate_level)

        return {
            "level": self.current_level,
            "eyes_closed": eyes_closed,
            "closed_seconds": closed_seconds,
            "model_durations": model_durations,
            "model_eye_support": model_eye_support,
        }


class DrowsinessDecisionEngine(LegacyDrowsinessDecisionEngine):
    """v7: 짧은 깜빡임을 허용하며 사건·반복과 수동 중지 후 회복을 관리한다.

    경고음 중지 명령은 보내지 않는다. 앱의 중지 확인 이후 회복 시간을 센다.
    이전 v3/v4 재현은 LegacyDrowsinessDecisionEngine을 사용한다.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event_times = deque()
        self.event_registered = False
        self.recovery_started_at = None
        self.recovery_interruption_started_at = None
        self.previous_observation = None
        self.closure_intervals = deque()
        self.transition_reason = "normal"
        self.alarm_acknowledged = False
        self.alarm_acknowledged_at = None
        self.previous_emergency = False

    def acknowledge_alarm(self, now):
        """앱에서 경보를 중지한 사실을 알린다. 반복 요청은 타이머를 초기화하지 않는다."""
        if self.current_level in (2, 3) and not self.alarm_acknowledged:
            self.alarm_acknowledged = True
            self.alarm_acknowledged_at = now
            self.recovery_started_at = None

    def _invalidate_alarm_acknowledgement(self):
        self.alarm_acknowledged = False
        self.alarm_acknowledged_at = None

    def interrupt_evidence(self):
        super().interrupt_evidence()
        self.recovery_started_at = None
        self.recovery_interruption_started_at = None
        self.previous_observation = None
        # 미관측 구간에서는 같은 사건이 끝났다고 단정하지 않는다.

    def reset_transient(self):
        # 얼굴의 소실은 회복의 증거가 아니다. 이력과 위험 단계를 유지한다.
        self.interrupt_evidence()

    def update(self, left_ear, right_ear, pred_score, now):
        if not self.calibrated:
            return super().update(left_ear, right_ear, pred_score, now)
        left_ratio = left_ear / self.left_baseline
        right_ratio = right_ear / self.right_baseline
        closed = bool(left_ratio <= EYE_CLOSE_RATIO and right_ratio <= EYE_CLOSE_RATIO)
        reopened = bool(left_ratio >= EYE_REOPEN_RATIO and right_ratio >= EYE_REOPEN_RATIO)
        support = bool(left_ratio <= MODEL_EYE_SUPPORT_RATIO and right_ratio <= MODEL_EYE_SUPPORT_RATIO)

        # 긴 관측 간격도 눈 감김 시간·회복 시간에 포함하지 않는다.
        if self.previous_observation is not None:
            previous_at, previous_closed = self.previous_observation
            if now - previous_at > MAX_OBSERVATION_GAP_SECONDS:
                self.interrupt_evidence()
            elif now > previous_at:
                self.closure_intervals.append((previous_at, now, previous_closed))
        self.previous_observation = (now, closed)
        cutoff = now - CLOSURE_RATIO_WINDOW_SECONDS
        while self.closure_intervals and self.closure_intervals[0][1] <= cutoff:
            self.closure_intervals.popleft()
        observed = closed_time = 0.0
        for start, end, was_closed in self.closure_intervals:
            duration = end - max(start, cutoff)
            observed += duration
            if was_closed:
                closed_time += duration
        closure_ratio = closed_time / observed if observed else 0.0
        while self.event_times and now - self.event_times[0] > EVENT_WINDOW_SECONDS:
            self.event_times.popleft()

        if closed:
            if self.closed_gap_started_at is not None and now - self.closed_gap_started_at >= self.closed_gap_tolerance:
                self.closed_started_at = None
            self.closed_gap_started_at = None
            if self.closed_started_at is None:
                self.closed_started_at = now
        elif reopened:
            self.closed_started_at = None
            self.closed_gap_started_at = None
        elif self.closed_started_at is not None:
            if self.closed_gap_started_at is None:
                self.closed_gap_started_at = now
            if now - self.closed_gap_started_at >= self.closed_gap_tolerance:
                self.closed_started_at = None

        if reopened:
            # 재개방 프레임에서도 검사해 낮은 FPS에서 긴 눈 감김을 놓치지 않는다.
            if (
                self.recovery_interruption_started_at is not None
                and now - self.recovery_interruption_started_at >= RECOVERY_BLINK_TOLERANCE_SECONDS
            ):
                self.recovery_started_at = None
            self.recovery_interruption_started_at = None
            if self.reopened_started_at is None:
                self.reopened_started_at = now
            if self.recovery_started_at is None:
                self.recovery_started_at = now
            if now - self.reopened_started_at >= EYE_REOPEN_HOLD_SECONDS:
                self.event_registered = False
        else:
            self.reopened_started_at = None
            # 깜빡임의 닫힘/경계 프레임은 재개방될 때까지 잠정 보류한다.
            if self.recovery_interruption_started_at is None:
                self.recovery_interruption_started_at = now
            if now - self.recovery_interruption_started_at >= RECOVERY_BLINK_TOLERANCE_SECONDS:
                self.recovery_started_at = None

        if support and pred_score is not None and pred_score >= self.model_threshold:
            if self.model_started_at[0] is None:
                self.model_started_at[0] = now
        else:
            self.model_started_at[0] = None
        closed_seconds = now - self.closed_started_at if self.closed_started_at is not None else 0.0
        model_seconds = now - self.model_started_at[0] if self.model_started_at[0] is not None else 0.0
        qualifies = closed_seconds >= self.eye_level_seconds[0] or model_seconds >= self.model_level_seconds[0]
        new_event = bool(qualifies and not self.event_registered)
        if new_event:
            self._invalidate_alarm_acknowledgement()
            self.event_registered = True
            self.event_times.append(now)
            self.recovery_started_at = None
            if self.current_level == 2:
                self.current_level = 3
                self.transition_reason = "repeated_after_level_2"
            elif self.current_level < 2:
                self.current_level = 2 if len(self.event_times) >= 2 else 1
                self.transition_reason = "repeated_events" if self.current_level == 2 else "sustained_event"

        emergency = bool(closed and closed_seconds >= self.eye_level_seconds[2])
        if emergency:
            if not self.previous_emergency:
                self._invalidate_alarm_acknowledgement()
            self.current_level = 3
            self.transition_reason = "continuous_eye_closure"
        self.previous_emergency = emergency
        if self.current_level in (2, 3) and not self.alarm_acknowledged:
            self.recovery_started_at = None
        recovery_observed_until = (
            now if reopened else self.recovery_interruption_started_at
        )
        recovery_seconds = (
            recovery_observed_until - self.recovery_started_at
            if self.recovery_started_at is not None else 0.0
        )
        if reopened and self.current_level in (2, 3) and self.alarm_acknowledged and recovery_seconds >= LEVEL_TWO_RECOVERY_SECONDS:
            self.current_level = 0
            self.event_times.clear()
            self.event_registered = False
            self._invalidate_alarm_acknowledgement()
            self.interrupt_evidence()
            recovery_seconds = 0.0
            self.transition_reason = "alarm_acknowledged_and_recovered"
        elif reopened and self.current_level == 1 and recovery_seconds >= LEVEL_ONE_RECOVERY_SECONDS:
            self.current_level = 0
            self.transition_reason = "level_1_recovered"

        return {
            "level": self.current_level, "eyes_closed": closed,
            "closed_seconds": closed_seconds, "model_durations": [model_seconds, 0.0, 0.0],
            "model_eye_support": support, "new_drowsiness_event": new_event,
            "recent_event_count": len(self.event_times), "emergency_condition": emergency,
            "recovery_seconds": recovery_seconds,
            "recovery_pending": bool(reopened and self.current_level > 0 and (
                self.current_level == 1 or self.alarm_acknowledged
            )),
            "risk_latched": self.current_level == 3,
            "alarm_acknowledged": self.alarm_acknowledged,
            "alarm_ack_required": self.current_level in (2, 3) and not self.alarm_acknowledged,
            "transition_reason": self.transition_reason,
            "ear_closure_ratio": closure_ratio, "observed_window_seconds": observed,
        }


class DrowsinessProcessor:
    """앱 서버와 독립 실행 도구가 공유하는 EAR 입력·추론·판정 처리."""

    def __init__(self, model, scaler, metadata):
        self.model = model
        self.scaler = scaler
        self.metadata = dict(metadata)
        self.reset()

    def reset(self):
        self.buffer = EarSequenceBuffer()
        self.engine = DrowsinessDecisionEngine(self.metadata["decision_threshold"])
        self.no_face_since = None
        self.last_valid_at = None
        self.previous_level = 0

    def process(self, left, right, now, *, face_detected=True):
        valid = bool(face_detected and left is not None and right is not None and
                     all(np.isfinite(v) and 0 < v < 1 for v in (left, right)))
        score, inference_ms, level = 0.0, 0.0, self.engine.current_level
        ready = False
        decision = dict(eyes_closed=False, closed_seconds=0.0,
                        model_durations=[0.0, 0.0, 0.0], model_eye_support=False)
        state = "NO_FACE" if not face_detected else "INVALID_EAR"
        message = "얼굴을 찾지 못했습니다" if not face_detected else "눈 랜드마크 값이 유효하지 않습니다"
        if valid:
            self.no_face_since = None
            if self.last_valid_at is not None and now - self.last_valid_at > MAX_OBSERVATION_GAP_SECONDS:
                self.engine.interrupt_evidence()
            self.last_valid_at = now
            self.engine.add_calibration_sample(float(left), float(right), now)
            model_ear = (left + right) / 2.0
            if self.metadata["normalization"] == "personal_baseline":
                if self.engine.calibrated:
                    model_ear = normalize_eye_ear(left, right, self.engine.left_baseline, self.engine.right_baseline)
                    self.buffer.append(now, model_ear)
            else:
                self.buffer.append(now, model_ear)
            sequence = self.buffer.sequence()
            if sequence is not None:
                sequence = self.scaler.transform(sequence).reshape(1, WINDOW_SIZE, 1)
                inference_started = time.perf_counter()
                score = float(np.asarray(self.model(sequence, training=False))[0][0])
                inference_ms = (time.perf_counter() - inference_started) * 1000.0
                if not np.isfinite(score) or not 0 <= score <= 1:
                    raise RuntimeError("모델이 유효한 졸음 점수를 반환하지 않았습니다")
                ready = True
            if not self.engine.calibrated:
                state, message = "CALIBRATING", "개인별 눈 기준 보정 중 — 눈을 뜨고 정면을 보세요"
            else:
                decision = self.engine.update(left, right, score if ready else None, now)
                level = decision["level"]
                if level > 0:
                    state, message = "DROWSY", LEVEL_MESSAGES[level]
                    if decision.get("alarm_ack_required") and not decision["eyes_closed"]:
                        message = f"{LEVEL_MESSAGES[level]} — 앱에서 경보 중지 후 정상 회복 확인이 필요합니다"
                    elif decision.get("risk_latched") and not decision["eyes_closed"] and not decision.get("recovery_pending"):
                        message = "위험 발생 기록 유지 — 안전한 장소에서 휴식하세요"
                    elif decision.get("recovery_pending"):
                        message = f"{LEVEL_MESSAGES[level]} — 정상 회복 확인 중"
                elif decision["eyes_closed"] or (
                    decision["model_eye_support"] and ready and score >= self.engine.model_threshold
                ):
                    state, message = "DROWSY_PENDING", "눈 감김 또는 졸음 신호 지속 시간 확인 중"
                elif not ready:
                    state, message = "WARMING_UP", f"모델 입력 준비 중 ({self.buffer.progress}/{WINDOW_SIZE})"
                else:
                    state, message = "AWAKE", LEVEL_MESSAGES[0]
        else:
            self.engine.interrupt_evidence()
            self.buffer.clear()
            self.last_valid_at = None
            if not face_detected:
                if self.no_face_since is None:
                    self.no_face_since = now
                if now - self.no_face_since >= NO_FACE_RESET_SECONDS:
                    self.engine.reset_transient()
        observation_valid = valid and self.engine.calibrated
        alert_event = bool(observation_valid and level > self.previous_level)
        if observation_valid:
            self.previous_level = level
        left_threshold, right_threshold = self.engine.close_thresholds
        return {
            "pred_score": round(score, 4), "drowsy_level": level,
            "ear_value": round(float((left + right) / 2), 4) if valid else 0.0,
            "left_ear": round(float(left), 4) if valid else None,
            "right_ear": round(float(right), 4) if valid else None,
            "face_detected": bool(face_detected), "prediction_ready": ready,
            "state": state, "message": message,
            "observation_valid": bool(observation_valid),
            "alert_active": bool(observation_valid and level > 0), "alert_event": alert_event,
            "inference_ms": round(inference_ms, 2),
            "buffer_size": self.buffer.progress, "buffer_required": WINDOW_SIZE,
            "calibrated": self.engine.calibrated,
            "calibration_progress": round(self.engine.calibration_progress, 3),
            "left_ear_baseline": round(self.engine.left_baseline, 4) if self.engine.calibrated else None,
            "right_ear_baseline": round(self.engine.right_baseline, 4) if self.engine.calibrated else None,
            "left_close_threshold": round(left_threshold, 4) if left_threshold is not None else None,
            "right_close_threshold": round(right_threshold, 4) if right_threshold is not None else None,
            "eyes_closed": decision["eyes_closed"],
            "eye_closed_seconds": round(decision["closed_seconds"], 3),
            "model_eye_support": decision["model_eye_support"],
            **{key: decision.get(key, default) for key, default in {
                "new_drowsiness_event": False, "recent_event_count": len(self.engine.event_times),
                "emergency_condition": False, "recovery_seconds": 0.0,
                "recovery_pending": False, "risk_latched": self.engine.current_level == 3,
                "transition_reason": self.engine.transition_reason,
                "alarm_acknowledged": self.engine.alarm_acknowledged,
                "alarm_ack_required": self.engine.current_level in (2, 3) and not self.engine.alarm_acknowledged,
                "ear_closure_ratio": None, "observed_window_seconds": 0.0,
            }.items()},
            "level_durations_seconds": {
                f"level_{index + 1}": round(duration, 2)
                for index, duration in enumerate(decision["model_durations"])
            },
            "no_face_duration_ms": int((now - self.no_face_since) * 1000) if self.no_face_since is not None else 0,
        }


class CameraDetector:
    """서버 직결 웹캠을 공통 처리기에 연결하며 종료 시 자원을 해제한다."""

    def __init__(self, camera_index=None):
        self.camera_index = get_settings().DROWSINESS_CAMERA_INDEX if camera_index is None else camera_index
        self.cap = None
        self.landmarker = None
        self.latest_landmarks = None
        self.processor = None

    def __enter__(self):
        if not models_ready():
            raise RuntimeError("졸음 감지 모델이 준비되지 않았습니다. 모델 파일과 서버 로그를 확인하세요.")
        try:
            options = vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_buffer=_face_model_buffer),
                running_mode=vision.RunningMode.VIDEO, num_faces=1,
                min_face_detection_confidence=0.5, min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self.landmarker = vision.FaceLandmarker.create_from_options(options)
            self.cap = cv2.VideoCapture(self.camera_index)
            if not self.cap.isOpened():
                raise RuntimeError(f"카메라 {self.camera_index}를 열 수 없습니다")
            self.processor = DrowsinessProcessor(_lstm_model, _scaler, _model_metadata)
            self.started_at = time.monotonic()
            self.last_timestamp_ms = -1
            self.timestamps = deque(maxlen=FPS_SAMPLE_SIZE)
            self.total_frames = self.face_frames = 0
            return self
        except Exception:
            self.close()
            raise

    def read(self):
        ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError("카메라 프레임을 읽을 수 없습니다")
        now = time.monotonic()
        timestamp = max(self.last_timestamp_ms + 1, int((now - self.started_at) * 1000))
        self.last_timestamp_ms = timestamp
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self.landmarker.detect_for_video(
            mp_lib.Image(image_format=mp_lib.ImageFormat.SRGB, data=rgb), timestamp,
        )
        self.latest_landmarks = result.face_landmarks[0] if result.face_landmarks else None
        left = right = None
        if self.latest_landmarks:
            height, width = frame.shape[:2]
            left = _calc_ear(self.latest_landmarks, LEFT_EYE, width, height)
            right = _calc_ear(self.latest_landmarks, RIGHT_EYE, width, height)
        data = self.processor.process(left, right, now, face_detected=self.latest_landmarks is not None)
        self.total_frames += 1
        self.face_frames += int(data["face_detected"])
        finished = time.monotonic()
        self.timestamps.append(finished)
        span = self.timestamps[-1] - self.timestamps[0]
        fps = (len(self.timestamps) - 1) / span if span > 0 else 0.0
        data.update(model_runtime_info())
        data.update({
            "frame_index": self.total_frames, "processing_fps": round(fps, 2),
            "face_detection_rate": round(self.face_frames / self.total_frames, 4),
            "elapsed_ms": int((finished - self.started_at) * 1000),
            "level_counters": {
                name: round(duration * fps) for name, duration in data["level_durations_seconds"].items()
            },
        })
        return frame, data

    def reset(self):
        self.processor.reset()

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.landmarker is not None:
            self.landmarker.close()
            self.landmarker = None

    def __exit__(self, *args):
        self.close()


async def _save_log_to_db(
    user_id: int,
    session_id: int,
    ear_value: float,
    pred_score: float,
    drowsy_level: int,
    latitude: float | None = None,
    longitude: float | None = None,
):
    """감지 로그를 DB에 비동기로 저장."""
    from app.database import async_session
    from app.models import DetectionLog

    try:
        async with async_session() as db:
            log = DetectionLog(
                user_id=user_id,
                session_id=session_id,
                ear_value=ear_value,
                pred_score=pred_score,
                drowsy_level=drowsy_level,
                latitude=latitude,
                longitude=longitude,
            )
            db.add(log)
            await db.commit()
            logger.info("졸음 로그 저장 완료: session=%d, level=%d", session_id, drowsy_level)
    except Exception:
        logger.exception("졸음 로그 저장 실패: session=%d", session_id)


class DetectionSession:
    """하나의 감지 세션 — 카메라 루프를 스레드로 실행."""

    def __init__(self, session_id: int, user_id: int, loop: asyncio.AbstractEventLoop):
        self.session_id = session_id
        self._user_id = user_id
        self._loop = loop
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=30)
        self._running = False
        self._thread: threading.Thread | None = None
        self.latest_result: dict | None = None
        self._alarm_ack_requests = SimpleQueue()

    def acknowledge_alarm(self):
        """API 스레드는 요청만 넣고, 판정 상태 변경은 카메라 스레드에서 수행한다."""
        self._alarm_ack_requests.put(time.monotonic())

    def _apply_alarm_ack_requests(self, processor):
        while not self._alarm_ack_requests.empty():
            processor.engine.acknowledge_alarm(self._alarm_ack_requests.get_nowait())

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def user_id(self) -> int:
        return self._user_id

    @property
    def queue(self) -> asyncio.Queue:
        return self._queue

    def start(self):
        """백그라운드 스레드에서 감지 루프 시작."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """감지 루프 종료."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _put_result(self, result: dict):
        """스레드 → async 큐 브릿지."""
        self.latest_result = result

        def enqueue_latest():
            # 소비자가 느릴 때 오래된 결과를 버리고 최신 결과를 유지한다.
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self._queue.put_nowait(result)

        self._loop.call_soon_threadsafe(enqueue_latest)

    def _detection_loop(self):
        """공통 웹캠 처리기의 결과를 앱에 전송하고 경고 발생을 기록한다."""
        try:
            with CameraDetector() as detector:
                while self._running:
                    self._apply_alarm_ack_requests(detector.processor)
                    _, result = detector.read()
                    arduino = get_arduino()
                    arduino.update(result)
                    result["co2_ppm"] = arduino.co2_ppm
                    if result["alert_event"]:
                        asyncio.run_coroutine_threadsafe(
                            _save_log_to_db(
                                user_id=self._user_id, session_id=self.session_id,
                                ear_value=result["ear_value"], pred_score=result["pred_score"],
                                drowsy_level=result["drowsy_level"],
                            ),
                            self._loop,
                        )
                    self._put_result(result)
        except Exception as error:
            logger.exception("Detection session failed: session=%d", self.session_id)
            self._put_result({**model_runtime_info(), "state": "ERROR", "error": str(error)})
        finally:
            self._running = False
            get_arduino().alarm_off()


# ── 활성 세션 관리 (카메라 1대 → 세션 1개) ──
_active_session: DetectionSession | None = None


def start_detection(
    session_id: int, user_id: int, loop: asyncio.AbstractEventLoop
) -> DetectionSession:
    """감지 시작."""
    global _active_session
    if _active_session and _active_session.is_running:
        raise RuntimeError("이미 감지가 실행 중입니다")
    _active_session = DetectionSession(session_id, user_id, loop)
    _active_session.start()
    return _active_session


def stop_detection():
    """감지 종료."""
    global _active_session
    if _active_session:
        _active_session.stop()
        _active_session = None


def get_active_session() -> DetectionSession | None:
    """현재 활성 세션 반환."""
    return _active_session


def models_ready() -> bool:
    return _lstm_model is not None and _face_model_buffer is not None


def camera_available() -> bool:
    active = get_active_session()
    if active is not None and active.is_running:
        return True
    camera = cv2.VideoCapture(get_settings().DROWSINESS_CAMERA_INDEX)
    try:
        return camera.isOpened()
    finally:
        camera.release()
