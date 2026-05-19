"""
졸음운전 방지 - 실시간 탐지 스크립트
학습된 LSTM 모델을 사용하여 웹캠으로 실시간 졸음 감지 및 경보

사용법:
    python realtime_detect.py

키 조작:
    q: 종료
    r: 경보 리셋
"""

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np
from tensorflow.keras.models import load_model
from collections import deque
import joblib
import time
import os

# Windows 환경에서만 winsound 사용
try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# ────────────────────────────────────────
# 설정
# ────────────────────────────────────────
WINDOW_SIZE = 30        # 시계열 윈도우 크기 (학습 시와 동일해야 함)
THRESHOLD = 0.7         # 졸음 판정 확률 임계값
ALARM_FRAMES = 15       # 연속 N프레임 졸음이면 경보
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_FILE = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "best_drowsy_model.keras")
SCALER_FILE = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "ear_scaler.pkl")
FACE_MODEL_PATH = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "face_landmarker.task")

# MediaPipe 눈 랜드마크 인덱스
LEFT_EYE = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]


def calc_ear(landmarks, eye_indices, w, h):
    """EAR(Eye Aspect Ratio) 계산"""
    pts = np.array([(landmarks[i].x * w, landmarks[i].y * h) for i in eye_indices])
    vertical_1 = np.linalg.norm(pts[1] - pts[5])
    vertical_2 = np.linalg.norm(pts[2] - pts[4])
    horizontal = np.linalg.norm(pts[0] - pts[3])
    if horizontal == 0:
        return 0.0
    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def trigger_alarm():
    """경보 발생"""
    if HAS_WINSOUND:
        # 비동기로 경보음 재생 (1000Hz, 500ms)
        winsound.Beep(1000, 200)
    else:
        print("\a")  # 터미널 벨 소리


def draw_eye_landmarks(frame, landmarks, eye_indices, w, h, color=(0, 255, 0)):
    """눈 랜드마크를 화면에 그리기"""
    pts = [(int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in eye_indices]
    for pt in pts:
        cv2.circle(frame, pt, 2, color, -1)
    # 눈 윤곽선
    for i in range(len(pts)):
        cv2.line(frame, pts[i], pts[(i + 1) % len(pts)], color, 1)


def main():
    # ── 모델 및 스케일러 로드 ──
    if not os.path.exists(MODEL_FILE):
        print(f"❌ 모델 파일 '{MODEL_FILE}'이 없습니다!")
        print("먼저 'python train_model.py'를 실행하세요.")
        return

    # FaceLandmarker 모델 파일 확인
    if not os.path.exists(FACE_MODEL_PATH):
        print(f"❌ 얼굴 랜드마크 모델 파일 '{FACE_MODEL_PATH}'이 없습니다!")
        print("다음 명령어로 다운로드하세요:")
        print("  curl -o face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task")
        return

    print("🔄 모델 로딩 중...")
    model = load_model(MODEL_FILE)

    # 스케일러 로드 (있으면 사용, 없으면 스케일링 없이 진행)
    scaler = None
    if os.path.exists(SCALER_FILE):
        scaler = joblib.load(SCALER_FILE)
        print("✅ 스케일러 로드 완료")

    print("✅ 모델 로드 완료")

    # ── MediaPipe FaceLandmarker 초기화 (VIDEO 모드) ──
    # 한글 경로 문제 우회: 파일을 바이트로 읽어서 전달
    with open(FACE_MODEL_PATH, "rb") as model_f:
        model_data = model_f.read()
    base_options = python.BaseOptions(model_asset_buffer=model_data)
    options = vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = vision.FaceLandmarker.create_from_options(options)

    # ── 웹캠 초기화 ──
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 웹캠을 열 수 없습니다!")
        landmarker.close()
        return

    print("\n🚗 실시간 졸음 감지 시작!")
    print("   q: 종료 | r: 경보 리셋\n")

    # ── 상태 변수 ──
    ear_buffer = deque(maxlen=WINDOW_SIZE)
    drowsy_count = 0
    fps_start = time.time()
    fps_count = 0
    current_fps = 0
    detect_start_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_h, frame_w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # MediaPipe Image로 변환 후 detect
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int((time.time() - detect_start_time) * 1000)
        result = landmarker.detect_for_video(mp_image, timestamp_ms)

        # FPS 계산
        fps_count += 1
        if time.time() - fps_start >= 1.0:
            current_fps = fps_count
            fps_count = 0
            fps_start = time.time()

        status_text = "DETECTING..."
        status_color = (255, 255, 0)
        ear_value = 0.0
        pred_value = 0.0

        if result.face_landmarks:
            lm = result.face_landmarks[0]  # 첫 번째 얼굴의 랜드마크

            # EAR 계산
            left_ear = calc_ear(lm, LEFT_EYE, frame_w, frame_h)
            right_ear = calc_ear(lm, RIGHT_EYE, frame_w, frame_h)
            ear_value = (left_ear + right_ear) / 2.0

            # 버퍼에 추가
            ear_buffer.append(ear_value)

            # 기본 눈 색상 (초록)
            eye_color = (0, 255, 0)

            # 버퍼가 가득 차면 모델 추론
            if len(ear_buffer) == WINDOW_SIZE:
                seq = np.array(list(ear_buffer)).reshape(-1, 1)

                # 스케일러 적용
                if scaler is not None:
                    seq = scaler.transform(seq)

                seq = seq.reshape(1, WINDOW_SIZE, 1)
                pred_value = model.predict(seq, verbose=0)[0][0]

                if pred_value > THRESHOLD:
                    drowsy_count += 1
                    status_text = f"DROWSY ({pred_value:.2f})"
                    status_color = (0, 0, 255)
                    eye_color = (0, 0, 255)
                else:
                    drowsy_count = max(0, drowsy_count - 1)
                    status_text = f"AWAKE ({pred_value:.2f})"
                    status_color = (0, 255, 0)

                # 연속 졸음 감지 → 경보!
                if drowsy_count >= ALARM_FRAMES:
                    # 화면 중앙에 경고 표시
                    cv2.rectangle(frame, (0, frame_h // 3), (frame_w, 2 * frame_h // 3),
                                 (0, 0, 200), -1)
                    cv2.putText(frame, "!! WAKE UP !!",
                               (frame_w // 6, frame_h // 2 + 10),
                               cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
                    trigger_alarm()
                    drowsy_count = 0  # 경보 후 카운터 리셋

            # 눈 랜드마크 그리기 (추론 결과에 따라 색상 반영)
            draw_eye_landmarks(frame, lm, LEFT_EYE, frame_w, frame_h, eye_color)
            draw_eye_landmarks(frame, lm, RIGHT_EYE, frame_w, frame_h, eye_color)
        else:
            status_text = "NO FACE"
            status_color = (0, 0, 255)

        # ── UI 그리기 ──
        # 상단 바
        cv2.rectangle(frame, (0, 0), (frame_w, 80), (30, 30, 30), -1)
        cv2.putText(frame, status_text, (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
        cv2.putText(frame, f"EAR: {ear_value:.3f}", (10, 55),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.putText(frame, f"FPS: {current_fps}", (frame_w - 100, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        # 졸음 게이지 바
        gauge_w = 200
        gauge_h = 15
        gauge_x = frame_w - gauge_w - 10
        gauge_y = 50
        fill = min(drowsy_count / ALARM_FRAMES, 1.0)
        cv2.rectangle(frame, (gauge_x, gauge_y),
                     (gauge_x + gauge_w, gauge_y + gauge_h), (100, 100, 100), -1)
        fill_color = (0, int(255 * (1 - fill)), int(255 * fill))
        cv2.rectangle(frame, (gauge_x, gauge_y),
                     (gauge_x + int(gauge_w * fill), gauge_y + gauge_h), fill_color, -1)
        cv2.putText(frame, "Drowsy Gauge", (gauge_x, gauge_y - 5),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

        cv2.imshow("Drowsy Driver Detection", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('r'):
            drowsy_count = 0
            ear_buffer.clear()
            print("🔄 경보 리셋")

    cap.release()
    landmarker.close()
    cv2.destroyAllWindows()
    print("\n👋 프로그램 종료")


if __name__ == "__main__":
    main()
