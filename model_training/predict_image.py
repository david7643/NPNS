"""
이미지 기반 졸음 감지 - 추론 스크립트
학습된 CNN 모델로 새 사진의 졸음 여부를 판별합니다.

사용법:
    python predict_image.py --image 사진.jpg
    python predict_image.py --folder 폴더경로
"""

import cv2
import numpy as np
from tensorflow.keras.models import load_model
import argparse
import os
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
MODEL_FILE = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "best_image_drowsy_model.keras")
IMG_SIZE = (224, 224)
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


def predict_single(model, image_path):
    """
    단일 이미지 추론

    Returns:
        (label, confidence, prob, img) 또는 None
    """
    # Windows에서 한글 경로 및 파일명이 포함된 이미지를 cv2.imread로 읽지 못하는 문제 해결
    try:
        img_array = np.fromfile(image_path, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    except Exception:
        img = None

    if img is None:
        print(f"  ⚠️ '{image_path}' 읽기 실패")
        return None

    resized = cv2.resize(img, IMG_SIZE)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    normalized = rgb.astype("float32") / 255.0
    batch = np.expand_dims(normalized, axis=0)

    prob = model.predict(batch, verbose=0)[0][0]
    label = "DROWSY" if prob > 0.5 else "AWAKE"
    confidence = prob if prob > 0.5 else 1.0 - prob

    return label, confidence, prob, img


def main():
    parser = argparse.ArgumentParser(description="이미지 기반 졸음 판별")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image", help="판별할 이미지 파일 경로")
    group.add_argument("--folder", help="판별할 이미지 폴더 경로")
    args = parser.parse_args()

    # 모델 파일 확인
    if not os.path.exists(MODEL_FILE):
        print(f"❌ 모델 파일 '{MODEL_FILE}'이 없습니다!")
        print("먼저 'python train_image_model.py'를 실행하세요.")
        return

    print("🔄 모델 로딩 중...")
    model = load_model(MODEL_FILE)
    print("✅ 모델 로드 완료\n")

    if args.image:
        # 단일 이미지 추론
        if not os.path.exists(args.image):
            print(f"❌ '{args.image}' 파일이 없습니다.")
            return

        result = predict_single(model, args.image)
        if result is None:
            return
        label, confidence, prob, img = result

        print(f"📷 {os.path.basename(args.image)}")
        print(f"  판정: {label} (졸음 확률: {prob:.4f}, 신뢰도: {confidence:.1%})")

        # 결과를 이미지에 표시
        color = (0, 0, 255) if label == "DROWSY" else (0, 255, 0)
        cv2.putText(img, f"{label} ({confidence:.1%})", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.imshow("Prediction", img)
        print("\n아무 키나 누르면 종료합니다.")
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    elif args.folder:
        # 폴더 내 이미지 일괄 추론
        if not os.path.isdir(args.folder):
            print(f"❌ '{args.folder}'는 유효한 폴더가 아닙니다.")
            return

        image_files = sorted([
            f for f in os.listdir(args.folder)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
        ])

        if not image_files:
            print(f"⚠️ '{args.folder}'에 이미지 파일이 없습니다.")
            return

        print(f"📁 {len(image_files)}장 분석 중...\n")
        awake_count = 0
        drowsy_count = 0

        for filename in image_files:
            filepath = os.path.join(args.folder, filename)
            result = predict_single(model, filepath)
            if result is None:
                continue
            label, confidence, prob, _ = result
            print(f"  {filename}: {label} (졸음 확률: {prob:.4f}, 신뢰도: {confidence:.1%})")

            if label == "AWAKE":
                awake_count += 1
            else:
                drowsy_count += 1

        total = awake_count + drowsy_count
        print(f"\n📊 결과 요약: 정상={awake_count}, 졸음={drowsy_count}, 전체={total}")


if __name__ == "__main__":
    main()
