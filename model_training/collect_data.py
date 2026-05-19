"""
졸음운전 방지 - EAR 데이터 수집 스크립트
웹캠으로 얼굴을 촬영하여 EAR(Eye Aspect Ratio) 값을 CSV로 저장합니다.

사용법:
    python collect_data.py
    - 먼저 정상 상태(눈 뜬 상태) 60초 수집
    - 이후 졸음 상태(눈 감는 상태) 60초 수집
"""

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np
import csv
import time
import os

# 모델 파일 경로
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "face_landmarker.task")

# MediaPipe 기준 눈 랜드마크 인덱스
LEFT_EYE = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]


def calc_ear(landmarks, eye_indices, w, h):
    """
    EAR(Eye Aspect Ratio) 계산

    EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)

    Parameters:
        landmarks: MediaPipe 얼굴 랜드마크 리스트
        eye_indices: 눈 랜드마크 인덱스 리스트 [p1, p2, p3, p4, p5, p6]
        w, h: 프레임 가로/세로 크기
    Returns:
        float: EAR 값 (보통 0.15~0.35 범위)
    """
    pts = np.array([(landmarks[i].x * w, landmarks[i].y * h) for i in eye_indices])
    # 세로 거리 2개
    vertical_1 = np.linalg.norm(pts[1] - pts[5])  # p2-p6
    vertical_2 = np.linalg.norm(pts[2] - pts[4])  # p3-p5
    # 가로 거리 1개
    horizontal = np.linalg.norm(pts[0] - pts[3])   # p1-p4

    if horizontal == 0:
        return 0.0
    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def collect_data(label, duration_sec=60, filename="ear_data.csv"):
    """
    EAR 데이터를 수집하여 CSV 파일에 저장

    Parameters:
        label: 0=정상, 1=졸음
        duration_sec: 수집 시간(초)
        filename: 저장할 CSV 파일명
    """
    # FaceLandmarker 옵션 설정 (VIDEO 모드)
    # 한글 경로 문제 우회: 파일을 바이트로 읽어서 전달
    with open(MODEL_PATH, "rb") as model_f:
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

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 웹캠을 열 수 없습니다!")
        landmarker.close()
        return

    start_time = time.time()
    frame_count = 0
    state_text = "NORMAL (눈 뜨세요)" if label == 0 else "DROWSY (천천히 눈 감으세요)"

    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)

        while time.time() - start_time < duration_sec:
            ret, frame = cap.read()
            if not ret:
                break

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # MediaPipe Image로 변환 후 detect
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = int((time.time() - start_time) * 1000)
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            elapsed = time.time() - start_time
            remaining = duration_sec - elapsed

            if result.face_landmarks:
                lm = result.face_landmarks[0]  # 첫 번째 얼굴의 랜드마크
                left_ear = calc_ear(lm, LEFT_EYE, w, h)
                right_ear = calc_ear(lm, RIGHT_EYE, w, h)
                avg_ear = (left_ear + right_ear) / 2.0

                # CSV에 저장
                writer.writerow([round(avg_ear, 4), label])
                frame_count += 1

                # 화면에 정보 표시
                color = (0, 255, 0) if label == 0 else (0, 0, 255)
                cv2.putText(frame, f"EAR: {avg_ear:.3f}", (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                cv2.putText(frame, f"State: {state_text}", (10, 60),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            else:
                cv2.putText(frame, "Face not detected!", (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            cv2.putText(frame, f"Remaining: {remaining:.1f}s | Frames: {frame_count}",
                       (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            cv2.imshow("EAR Data Collection", frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("사용자에 의해 중단됨")
                break

    cap.release()
    landmarker.close()
    cv2.destroyAllWindows()
    print(f"  → {frame_count}개 프레임 저장 완료")


if __name__ == "__main__":
    data_file = os.path.join(SCRIPT_DIR, "ear_data.csv")

    # 모델 파일 확인
    if not os.path.exists(MODEL_PATH):
        print(f"❌ 모델 파일 '{MODEL_PATH}'이 없습니다!")
        print("다음 명령어로 다운로드하세요:")
        print("  curl -o face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task")
        exit()

    # 기존 파일 존재 시 확인
    if os.path.exists(data_file):
        choice = input(f"'{data_file}'이 이미 존재합니다. 이어쓰기(a) / 덮어쓰기(w) / 취소(c): ")
        if choice == 'w':
            os.remove(data_file)
        elif choice == 'c':
            print("취소됨")
            exit()

    print("=" * 50)
    print("📷 EAR 데이터 수집기")
    print("=" * 50)

    # Step 1: 정상 상태 수집
    print("\n[1/2] 정상 상태 데이터 수집")
    print("  → 눈을 뜨고 정면을 응시하세요")
    print("  → 자연스럽게 깜빡여도 됩니다")
    input("  → 준비되면 Enter를 누르세요...")
    print("  3초 후 시작합니다...")
    time.sleep(3)
    collect_data(label=0, duration_sec=60, filename=data_file)

    # Step 2: 졸음 상태 수집
    print("\n[2/2] 졸음 상태 데이터 수집")
    print("  → 천천히 눈을 감았다 뜨기를 반복하세요")
    print("  → 눈을 반쯤 감은 상태도 포함하세요")
    print("  → 실제 졸린 것처럼 연기하세요")
    input("  → 준비되면 Enter를 누르세요...")
    print("  3초 후 시작합니다...")
    time.sleep(3)
    collect_data(label=1, duration_sec=60, filename=data_file)

    print("\n" + "=" * 50)
    print(f"✅ 데이터 수집 완료! '{data_file}' 파일을 확인하세요.")
    print("다음 단계: python train_model.py")
    print("=" * 50)
