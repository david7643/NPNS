"""Local demonstration display fed by the existing detector, without opening a camera."""

from functools import lru_cache
import logging
import os
from pathlib import Path
from queue import Empty, Full, Queue
import struct
import subprocess
import sys
import threading

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)
EYES = ((362, 385, 387, 263, 373, 380), (33, 160, 158, 133, 153, 144))
COLORS = ((67, 198, 145), (246, 202, 78), (255, 155, 70), (245, 91, 107))
LEVELS = ("정상", "주의", "경고", "위험")
STATES = {
    "CALIBRATING": "눈을 뜨고 정면을 봐주세요",
    "NO_FACE": "얼굴을 찾는 중",
    "INVALID_EAR": "눈 관측을 확인해주세요",
    "WARMING_UP": "모델 입력 준비 중",
    "DROWSY_PENDING": "눈 감김 지속시간 확인 중",
    "AWAKE": "정상 관측 중", "DROWSY": "졸음 위험 관측 중",
}


@lru_cache(maxsize=8)
def _font(size):
    for path in (
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/malgun.ttf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def render_preview(frame, data, eyes=(), session_id=0):
    """Render a mirrored copy; never modify the inference frame or its measurements."""
    level = max(0, min(3, int(data.get("drowsy_level", 0))))
    color = COLORS[level]
    video = cv2.flip(frame, 1)
    height, width = video.shape[:2]
    for eye in eyes:
        points = np.array([((1 - x) * (width - 1), y * (height - 1)) for x, y in eye], dtype=np.int32)
        cv2.polylines(video, [points], True, color[::-1], 2, cv2.LINE_AA)
    ratio = min(860 / width, 570 / height)
    video = cv2.resize(video, (max(1, round(width * ratio)), max(1, round(height * ratio))))
    canvas = Image.new("RGB", (1280, 720), (15, 20, 30))
    canvas.paste(Image.fromarray(cv2.cvtColor(video, cv2.COLOR_BGR2RGB)),
                 (20 + (860 - video.shape[1]) // 2, 100 + (570 - video.shape[0]) // 2))
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), "DrowsyGuard  |  실시간 졸음 감지 시연", font=_font(30), fill="white")
    draw.text((26, 65), "얼굴·눈 위치 → EAR → LSTM + 눈 감김 지속시간 → 위험 단계", font=_font(17), fill=(155, 170, 190))
    draw.rounded_rectangle((900, 100, 1258, 175), radius=12, fill=color)
    draw.text((918, 116), f"{level}단계  ·  {LEVELS[level]}", font=_font(30), fill=(15, 20, 30))

    def number(key, digits=3):
        value = data.get(key)
        return "—" if value is None else f"{value:.{digits}f}"

    ack = "중지 확인 대기" if data.get("alarm_ack_required") else (
        "사용자 중지 확인됨" if data.get("alarm_acknowledged") else "경보 없음")
    rows = (
        ("관측 상태", STATES.get(data.get("state"), data.get("state", "준비 중"))),
        ("개인별 눈 기준 보정", "완료" if data.get("calibrated") else f"{data.get('calibration_progress', 0) * 100:.0f}% · 눈을 뜨고 정면 보기"),
        ("좌·우 EAR", f"L {number('left_ear')}  /  R {number('right_ear')}"),
        ("LSTM 점수 / 진입 기준", f"{number('pred_score')}  /  {number('model_threshold')}" if data.get("prediction_ready") else "입력 준비 중"),
        ("눈 감김 / 사건 진입 시간", f"{number('eye_closed_seconds', 2)}초  /  {number('eye_entry_seconds', 2)}초"),
        ("최근 30초 사건 / 정상 회복", f"{data.get('recent_event_count', 0)}회  /  {number('recovery_seconds', 1)}초"),
        ("경보 중지 확인", ack),
        ("세션 / 카메라 / 처리 속도", f"#{session_id} / USB {data.get('camera_index', '—')} / {number('processing_fps', 1)} FPS"),
    )
    for row, (label, value) in enumerate(rows):
        y = 193 + row * 59
        draw.text((910, y), label, font=_font(15), fill=(155, 170, 190))
        draw.text((910, y + 22), value, font=_font(20), fill="white")
    draw.text((24, 683), "Q / ESC / 창 닫기: 미리보기만 닫기    |    감지·경보 제어: 앱에서 조작", font=_font(18), fill=(185, 198, 215))
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


class DetectionPreview:
    """One latest-frame queue and an isolated GUI process keep display I/O off detection."""

    def __init__(self, enabled=True, session_id=0):
        self.enabled = enabled
        self.session_id = session_id
        self._frames = Queue(maxsize=1)
        self._stop = threading.Event()
        self._closed = False
        self._thread = None
        self._process = None

    def show(self, frame, data, landmarks=None):
        if not self.enabled or self._closed or frame is None:
            return
        eyes = [[(landmarks[i].x, landmarks[i].y) for i in indices] for indices in EYES] if landmarks else []
        item = (frame.copy(), dict(data), eyes)
        try:
            self._frames.put_nowait(item)
        except Full:
            try:
                self._frames.get_nowait()
            except Empty:
                pass
            self._frames.put_nowait(item)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="detection-preview", daemon=True)
            self._thread.start()

    def _run(self):
        process = None
        try:
            if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                raise RuntimeError("데스크톱 그래픽 세션이 없습니다. DROWSINESS_PREVIEW_ENABLED=false로 비활성화할 수 있습니다")
            env = os.environ.copy()
            if sys.platform.startswith("linux") and env.get("DISPLAY"):
                env.setdefault("QT_QPA_PLATFORM", "xcb")
            process = subprocess.Popen(
                [sys.executable, "-u", "-m", "app.preview_window"], stdin=subprocess.PIPE,
                cwd=Path(__file__).resolve().parents[1], env=env,
            )
            self._process = process
            while not self._stop.is_set():
                if process.poll() is not None:
                    if process.returncode:
                        logger.warning("시연 창 종료: exit=%s. 졸음 감지는 계속됩니다", process.returncode)
                    break
                try:
                    frame, data, eyes = self._frames.get(timeout=0.1)
                except Empty:
                    continue
                display = render_preview(frame, data, eyes, self.session_id)
                ok, encoded = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok and not self._stop.is_set():
                    packet = encoded.tobytes()
                    process.stdin.write(struct.pack("!I", len(packet)) + packet)
                    process.stdin.flush()
        except Exception:
            logger.warning("시연 창을 표시하지 못했습니다. 졸음 감지는 계속됩니다", exc_info=True)
        finally:
            self._closed = True
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if process.stdin:
                    try:
                        process.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass

    def close(self):
        self._closed = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
