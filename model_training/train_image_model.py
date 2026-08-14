"""
이미지 기반 졸음 감지 - CNN 모델 학습 스크립트
자동 라벨링된 이미지로 MobileNetV2 전이학습을 수행합니다.

사용법:
    python train_image_model.py
    - 먼저 analyze_images.py를 실행하여 라벨링을 완료하세요
"""

import numpy as np
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

from sklearn.metrics import classification_report, confusion_matrix
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Dense, Dropout, GlobalAveragePooling2D
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
import matplotlib.pyplot as plt
import os

# ────────────────────────────────────────
# 설정
# ────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(SCRIPT_DIR, "image_dataset")
TRAIN_DIR = os.path.join(DATASET_DIR, "labeled", "train")
TEST_DIR = os.path.join(DATASET_DIR, "labeled", "test")
MODEL_FILE = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "best_image_drowsy_model.keras")

IMG_SIZE = (224, 224)
BATCH_SIZE = 16


def plot_training_history(history):
    """학습 히스토리 시각화"""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    axes[0].plot(history.history["loss"], label="Train Loss", linewidth=2)
    axes[0].plot(history.history["val_loss"], label="Val Loss", linewidth=2)
    axes[0].set_title("Model Loss", fontsize=14)
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(history.history["accuracy"], label="Train Accuracy", linewidth=2)
    axes[1].plot(history.history["val_accuracy"], label="Val Accuracy", linewidth=2)
    axes[1].set_title("Model Accuracy", fontsize=14)
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("Accuracy")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    result_path = os.path.join(SCRIPT_DIR, "training_result_image.png")
    plt.savefig(result_path, dpi=150)
    plt.show()
    print(f"📈 학습 결과 그래프: '{result_path}'")


def main():
    # 데이터 존재 확인
    for d in [TRAIN_DIR, TEST_DIR]:
        if not os.path.exists(d):
            print(f"❌ '{d}' 폴더가 없습니다!")
            print("먼저 'python analyze_images.py'를 실행하세요.")
            return

    print("=" * 50)
    print("🖼️ 이미지 CNN 모델 학습")
    print("=" * 50)

    # 1. 데이터 로드
    print("\n[1/4] 데이터 로드...")

    # 학습 데이터: 증강 + validation 분할
    train_datagen = ImageDataGenerator(
        rescale=1.0 / 255,
        validation_split=0.2,
        horizontal_flip=True,
        brightness_range=[0.8, 1.2],
        rotation_range=10,
    )

    # 테스트 데이터: 증강 없이 스케일링만
    test_datagen = ImageDataGenerator(rescale=1.0 / 255)

    train_gen = train_datagen.flow_from_directory(
        TRAIN_DIR,
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="binary",
        subset="training",
        classes=["awake", "drowsy"],
    )

    val_gen = train_datagen.flow_from_directory(
        TRAIN_DIR,
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="binary",
        subset="validation",
        classes=["awake", "drowsy"],
    )

    test_gen = test_datagen.flow_from_directory(
        TEST_DIR,
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="binary",
        shuffle=False,
        classes=["awake", "drowsy"],
    )

    print(f"  학습: {train_gen.samples}장, 검증: {val_gen.samples}장, 테스트: {test_gen.samples}장")
    print(f"  클래스: {train_gen.class_indices}")

    if train_gen.samples == 0:
        print("❌ 학습 데이터가 없습니다! 이미지를 추가하세요.")
        return

    # 2. 모델 구축 또는 로드
    if os.path.exists(MODEL_FILE):
        print(f"\n[2/4] 기존 학습 모델 발견! '{MODEL_FILE}'을 로드하여 이어서 학습합니다...")
        from tensorflow.keras.models import load_model
        model = load_model(MODEL_FILE)
    else:
        print("\n[2/4] 새 모델 구축...")
        base_model = MobileNetV2(
            weights="imagenet", include_top=False, input_shape=(224, 224, 3)
        )
        base_model.trainable = False  # feature extractor만 사용

        model = Sequential([
            base_model,
            GlobalAveragePooling2D(),
            Dropout(0.3),
            Dense(64, activation="relu"),
            Dropout(0.2),
            Dense(1, activation="sigmoid"),  # 이진 분류: 0=정상, 1=졸음
        ])

        model.compile(
            optimizer="adam",
            loss="binary_crossentropy",
            metrics=["accuracy"],
        )
    model.summary()

    # 3. 학습
    print("\n[3/4] 학습 시작...")
    callbacks = [
        EarlyStopping(
            monitor="val_loss",
            patience=10,
            restore_best_weights=True,
            verbose=1,
        ),
        ModelCheckpoint(
            MODEL_FILE,
            monitor="val_accuracy",
            save_best_only=True,
            verbose=1,
        ),
    ]

    history = model.fit(
        train_gen,
        validation_data=val_gen,
        epochs=50,
        callbacks=callbacks,
        verbose=1,
    )

    # 4. 테스트 데이터 평가
    print("\n[4/4] 테스트 데이터 평가")
    print("=" * 50)
    print("📊 테스트 평가 결과")
    print("=" * 50)

    test_loss, test_acc = model.evaluate(test_gen, verbose=0)
    print(f"테스트 정확도: {test_acc:.4f}")
    print(f"테스트 손실: {test_loss:.4f}")

    y_pred = (model.predict(test_gen, verbose=0) > 0.5).astype(int).flatten()
    y_true = test_gen.classes
    print("\n" + classification_report(
        y_true, y_pred, target_names=["정상(awake)", "졸음(drowsy)"]
    ))

    cm = confusion_matrix(y_true, y_pred)
    print(f"혼동 행렬:\n{cm}")

    best_val_acc = max(history.history["val_accuracy"])
    print(f"\n🏆 최고 검증 정확도: {best_val_acc:.4f}")
    print(f"✅ 모델 저장 완료: {MODEL_FILE}")

    # 5. 시각화
    plot_training_history(history)

    print("\n다음 단계: python predict_image.py --image 사진.jpg")


if __name__ == "__main__":
    main()
