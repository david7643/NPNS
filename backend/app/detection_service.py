"""졸음 감지 서비스 — 카메라 제어 + 모델 추론 + 3단계 판정."""

import asyncio
import os
import threading
import time
from collections import deque

import cv2
import mediapipe as mp_lib
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from tensorflow.keras.models import load_model
import joblib

# ── 전역: 서버 시작 시 1회 로드 ──
_lstm_model = None
_scaler = None
_face_model_buffer = None

# MediaPipe 눈 랜드마크 인덱스 (realtime_detect.py와 동일)
LEFT_EYE = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]

WINDOW_SIZE = 30  # LSTM 시계열 윈도우

# 3단계 판정 설정 (개발계획_요약.md 기준)
LEVEL_CONFIG = [
    {"threshold": 0.5, "frames": 10},   # 1단계
    {"threshold": 0.7, "frames": 15},   # 2단계
    {"threshold": 0.85, "frames": 20},  # 3단계
]

LEVEL_MESSAGES = {
    0: "정상",
    1: "졸음 1단계 — 주의",
    2: "졸음 2단계 — 경고",
    3: "졸음 3단계 — 위험",
}

AI_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ai_models")


def load_models():
    """서버 시작 시 AI 모델을 메모리에 로드."""
    global _lstm_model, _scaler, _face_model_buffer

    model_path = os.path.join(AI_MODELS_DIR, "best_drowsy_model.keras")
    scaler_path = os.path.join(AI_MODELS_DIR, "ear_scaler.pkl")
    face_model_path = os.path.join(AI_MODELS_DIR, "face_landmarker.task")

    _lstm_model = load_model(model_path)

    if os.path.exists(scaler_path):
        _scaler = joblib.load(scaler_path)

    with open(face_model_path, "rb") as f:
        _face_model_buffer = f.read()


def _calc_ear(landmarks, eye_indices, w, h):
    """EAR(Eye Aspect Ratio) 계산."""
    pts = np.array([(landmarks[i].x * w, landmarks[i].y * h) for i in eye_indices])
    v1 = np.linalg.norm(pts[1] - pts[5])
    v2 = np.linalg.norm(pts[2] - pts[4])
    horiz = np.linalg.norm(pts[0] - pts[3])
    if horiz == 0:
        return 0.0
    return (v1 + v2) / (2.0 * horiz)


class DetectionSession:
    """하나의 감지 세션 — 카메라 루프를 스레드로 실행."""

    def __init__(self, session_id: int, loop: asyncio.AbstractEventLoop):
        self.session_id = session_id
        self._loop = loop
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=30)
        self._running = False
        self._thread: threading.Thread | None = None
        self.latest_result: dict | None = None

    @property
    def is_running(self) -> bool:
        return self._running

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
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, result)
        except asyncio.QueueFull:
            pass

    def _detection_loop(self):
        """프레임 캡처 → EAR → LSTM → 3단계 판정."""
        base_options = python.BaseOptions(model_asset_buffer=_face_model_buffer)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        landmarker = vision.FaceLandmarker.create_from_options(options)

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self._put_result({"error": "카메라를 열 수 없습니다"})
            self._running = False
            return

        ear_buffer = deque(maxlen=WINDOW_SIZE)
        counters = [0, 0, 0]
        detect_start = time.time()

        try:
            while self._running:
                ret, frame = cap.read()
                if not ret:
                    continue

                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp_lib.Image(image_format=mp_lib.ImageFormat.SRGB, data=rgb)
                ts_ms = int((time.time() - detect_start) * 1000)
                result = landmarker.detect_for_video(mp_image, ts_ms)

                ear_value = 0.0
                pred_score = 0.0
                drowsy_level = 0
                face_detected = False

                if result.face_landmarks:
                    face_detected = True
                    lm = result.face_landmarks[0]
                    left = _calc_ear(lm, LEFT_EYE, w, h)
                    right = _calc_ear(lm, RIGHT_EYE, w, h)
                    ear_value = (left + right) / 2.0
                    ear_buffer.append(ear_value)

                    if len(ear_buffer) == WINDOW_SIZE:
                        seq = np.array(list(ear_buffer)).reshape(-1, 1)
                        if _scaler is not None:
                            seq = _scaler.transform(seq)
                        seq = seq.reshape(1, WINDOW_SIZE, 1)
                        pred_score = float(
                            _lstm_model.predict(seq, verbose=0)[0][0]
                        )

                        # 카운터 업데이트
                        for i, cfg in enumerate(LEVEL_CONFIG):
                            if pred_score > cfg["threshold"]:
                                counters[i] += 1
                            else:
                                counters[i] = 0

                        # 최고 달성 레벨
                        for i in range(2, -1, -1):
                            if counters[i] >= LEVEL_CONFIG[i]["frames"]:
                                drowsy_level = i + 1
                                break

                self._put_result({
                    "pred_score": round(pred_score, 4),
                    "drowsy_level": drowsy_level,
                    "ear_value": round(ear_value, 4),
                    "face_detected": face_detected,
                    "message": LEVEL_MESSAGES[drowsy_level],
                })
        finally:
            cap.release()
            landmarker.close()


# ── 활성 세션 관리 (카메라 1대 → 세션 1개) ──
_active_session: DetectionSession | None = None


def start_detection(
    session_id: int, loop: asyncio.AbstractEventLoop
) -> DetectionSession:
    """감지 시작."""
    global _active_session
    if _active_session and _active_session.is_running:
        raise RuntimeError("이미 감지가 실행 중입니다")
    _active_session = DetectionSession(session_id, loop)
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
