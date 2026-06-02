"""
이미지 자동 분석 및 라벨링 스크립트
운전 중 사진에서 MediaPipe로 EAR, MAR, Head Tilt을 추출하고 자동 라벨링합니다.

사용법:
    python analyze_images.py
    - image_dataset/train/ 과 image_dataset/test/ 에 사진을 넣은 후 실행
    - 결과는 image_dataset/labeled/ 하위에 저장됩니다
"""

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np
import csv
import os
import shutil
import sys

# Windows 환경에서 한글 터미널 및 이모지 출력 시 UnicodeEncodeError(cp949) 방지
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


# ────────────────────────────────────────
# 설정
# ────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "face_landmarker.task")
DATASET_DIR = os.path.join(SCRIPT_DIR, "image_dataset")

# 자동 라벨링 기준값
EAR_THRESHOLD = 0.2      # 이 값 미만이면 졸음 (눈 감음)
MAR_THRESHOLD = 0.6      # 이 값 초과이면 졸음 (하품)
TILT_THRESHOLD = 20.0    # 이 값 초과이면 졸음 (고개 떨굼)

# MediaPipe 눈 랜드마크 인덱스 (기존 collect_data.py와 동일)
LEFT_EYE = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]

# 입 랜드마크 인덱스
UPPER_LIP = 13
LOWER_LIP = 14
LEFT_MOUTH = 78
RIGHT_MOUTH = 308

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


def calc_ear(landmarks, eye_indices, w, h):
    """EAR(Eye Aspect Ratio) 계산 — collect_data.py와 동일한 로직"""
    pts = np.array([(landmarks[i].x * w, landmarks[i].y * h) for i in eye_indices])
    vertical_1 = np.linalg.norm(pts[1] - pts[5])
    vertical_2 = np.linalg.norm(pts[2] - pts[4])
    horizontal = np.linalg.norm(pts[0] - pts[3])
    if horizontal == 0:
        return 0.0
    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def calc_mar(landmarks, w, h):
    """MAR(Mouth Aspect Ratio) 계산 — 입 벌림 정도"""
    upper = np.array([landmarks[UPPER_LIP].x * w, landmarks[UPPER_LIP].y * h])
    lower = np.array([landmarks[LOWER_LIP].x * w, landmarks[LOWER_LIP].y * h])
    left = np.array([landmarks[LEFT_MOUTH].x * w, landmarks[LEFT_MOUTH].y * h])
    right = np.array([landmarks[RIGHT_MOUTH].x * w, landmarks[RIGHT_MOUTH].y * h])

    vertical = np.linalg.norm(lower - upper)
    horizontal = np.linalg.norm(right - left)

    if horizontal == 0:
        return 0.0
    return vertical / horizontal


def calc_head_tilt(landmarks, w, h):
    """
    머리 앞쪽 기울기 추정 (도 단위, 근사값)

    이마(10)→코(1)→턱(152) 간 비율 변화로 고개 숙임을 감지합니다.
    정면일 때 코→턱 비율 ~0.37, 고개가 숙여지면 이 비율이 감소합니다.
    정지 사진에서의 추정이므로 대략적인 값입니다.
    """
    forehead = np.array([landmarks[10].x * w, landmarks[10].y * h])
    nose_tip = np.array([landmarks[1].x * w, landmarks[1].y * h])
    chin = np.array([landmarks[152].x * w, landmarks[152].y * h])

    face_height = np.linalg.norm(chin - forehead)
    nose_to_chin = np.linalg.norm(chin - nose_tip)

    if face_height == 0:
        return 0.0

    ratio = nose_to_chin / face_height
    normal_ratio = 0.37
    tilt = max(0.0, (normal_ratio - ratio) / normal_ratio * 45)
    return tilt


def auto_label(ear, mar, head_tilt):
    """규칙 기반 자동 라벨링 — 하나라도 해당하면 졸음"""
    if ear < EAR_THRESHOLD:
        return "drowsy"
    if mar > MAR_THRESHOLD:
        return "drowsy"
    if head_tilt > TILT_THRESHOLD:
        return "drowsy"
    return "awake"


def analyze_folder(folder_path, labeled_base, report_path, landmarker):
    """폴더 내 모든 이미지를 분석하고 라벨링 (이어쓰기 지원)"""
    if not os.path.exists(folder_path):
        print(f"  ⚠️ '{folder_path}' 폴더가 없습니다. 건너뜁니다.")
        return

    # labeled 하위 폴더 생성
    awake_dir = os.path.join(labeled_base, "awake")
    drowsy_dir = os.path.join(labeled_base, "drowsy")
    os.makedirs(awake_dir, exist_ok=True)
    os.makedirs(drowsy_dir, exist_ok=True)

    # 기존 리포트(CSV) 불러와 이미 처리된 파일 목록 수집
    processed_files = set()
    existing_results = []
    if os.path.exists(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)  # 헤더 건너뛰기
                if header:
                    for row in reader:
                        if row:
                            processed_files.add(row[0])
                            existing_results.append(row)
        except Exception:
            pass

    # 이미지 파일 목록
    all_image_files = sorted([
        f for f in os.listdir(folder_path)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
    ])

    if not all_image_files:
        print(f"  ⚠️ '{folder_path}'에 이미지 파일이 없습니다.")
        return

    # 이미 처리된 파일 제외
    image_files = [f for f in all_image_files if f not in processed_files]
    already_done = len(all_image_files) - len(image_files)

    # 기존 카운트 세팅
    awake_count = sum(1 for r in existing_results if r[4] == "awake")
    drowsy_count = sum(1 for r in existing_results if r[4] == "drowsy")
    no_face_count = sum(1 for r in existing_results if r[4] == "no_face")

    if already_done > 0:
        print(f"  ⏭️ 이미 처리된 파일 {already_done}장 건너뜀 (남은 파일: {len(image_files)}장 / 전체: {len(all_image_files)}장)")

    if not image_files:
        print(f"  ✅ 모든 파일이 이미 처리되었습니다.")
        return

    # CSV 파일 존재 여부 확인 및 실시간 쓰기
    file_exists = os.path.exists(report_path) and os.path.getsize(report_path) > 0

    with open(report_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["filename", "ear", "mar", "head_tilt", "label"])

        for i, filename in enumerate(image_files):
            filepath = os.path.join(folder_path, filename)
            # Windows에서 한글 경로 및 파일명이 포함된 이미지를 cv2.imread로 읽지 못하는 문제 해결
            try:
                img_array = np.fromfile(filepath, np.uint8)
                img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            except Exception:
                img = None

            if img is None:
                print(f"  ⚠️ '{filename}' 읽기 실패, 건너뜁니다.")
                continue

            h, w = img.shape[:2]
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect(mp_image)

            if not result.face_landmarks:
                writer.writerow([filename, "", "", "", "no_face"])
                f.flush()
                no_face_count += 1
                print(f"  [{already_done + i+1}/{len(all_image_files)}] {filename} → 얼굴 미감지")
                continue

            lm = result.face_landmarks[0]
            left_ear = calc_ear(lm, LEFT_EYE, w, h)
            right_ear = calc_ear(lm, RIGHT_EYE, w, h)
            ear = (left_ear + right_ear) / 2.0
            mar = calc_mar(lm, w, h)
            tilt = calc_head_tilt(lm, w, h)
            label = auto_label(ear, mar, tilt)

            writer.writerow([filename, f"{ear:.4f}", f"{mar:.4f}", f"{tilt:.1f}", label])
            f.flush()

            # 라벨에 따라 복사 (이미 복사되어 있는 경우 덮어씀)
            dest_dir = awake_dir if label == "awake" else drowsy_dir
            shutil.copy2(filepath, os.path.join(dest_dir, filename))

            if label == "awake":
                awake_count += 1
            else:
                drowsy_count += 1

            print(f"  [{already_done + i+1}/{len(all_image_files)}] {filename} → {label} "
                  f"(EAR={ear:.3f}, MAR={mar:.3f}, Tilt={tilt:.1f}°)")

    print(f"\n  📊 누적 결과: 정상={awake_count}, 졸음={drowsy_count}, 얼굴미감지={no_face_count}")
    print(f"  📄 리포트 업데이트 완료: {report_path}")


def main():
    # 모델 파일 확인
    if not os.path.exists(MODEL_PATH):
        print(f"❌ 모델 파일 '{MODEL_PATH}'이 없습니다!")
        print("다음 명령어로 다운로드하세요:")
        print("  curl -o face_landmarker.task https://storage.googleapis.com/mediapipe-models/"
              "face_landmarker/face_landmarker/float16/1/face_landmarker.task")
        exit()

    # FaceLandmarker 초기화 (IMAGE 모드 — 정지 사진용)
    # 한글 경로 문제 우회: 파일을 바이트로 읽어서 전달
    with open(MODEL_PATH, "rb") as f:
        model_data = f.read()
    base_options = python.BaseOptions(model_asset_buffer=model_data)
    options = vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
    )
    landmarker = vision.FaceLandmarker.create_from_options(options)

    print("=" * 50)
    print("📷 이미지 자동 분석 및 라벨링")
    print("=" * 50)
    print(f"  EAR 기준: < {EAR_THRESHOLD} → 졸음")
    print(f"  MAR 기준: > {MAR_THRESHOLD} → 졸음")
    print(f"  Tilt 기준: > {TILT_THRESHOLD}° → 졸음")
    print()

    # 이어쓰기(Resume) 모드 지원
    labeled_dir = os.path.join(DATASET_DIR, "labeled")
    os.makedirs(labeled_dir, exist_ok=True)
    print("  🔄 이어쓰기(Resume) 모드로 실행됩니다. (이미 처리된 사진은 건너뜁니다)\n")

    # Train 데이터 분석
    train_raw = os.path.join(DATASET_DIR, "train")
    train_labeled = os.path.join(DATASET_DIR, "labeled", "train")
    train_report = os.path.join(DATASET_DIR, "train_analysis_report.csv")

    print("[1/2] Train 데이터 분석")
    analyze_folder(train_raw, train_labeled, train_report, landmarker)

    # Test 데이터 분석
    test_raw = os.path.join(DATASET_DIR, "test")
    test_labeled = os.path.join(DATASET_DIR, "labeled", "test")
    test_report = os.path.join(DATASET_DIR, "test_analysis_report.csv")

    print("\n[2/2] Test 데이터 분석")
    analyze_folder(test_raw, test_labeled, test_report, landmarker)

    landmarker.close()

    print("\n" + "=" * 50)
    print("✅ 분석 완료!")
    print("다음 단계: python train_image_model.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
