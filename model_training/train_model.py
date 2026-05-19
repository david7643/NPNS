"""
졸음운전 방지 - LSTM 모델 학습 스크립트
수집된 EAR 데이터를 시계열 윈도우로 변환하고 LSTM 모델을 학습합니다.

사용법:
    python train_model.py
"""

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
import matplotlib.pyplot as plt
import joblib
import os

# ────────────────────────────────────────
# 설정
# ────────────────────────────────────────
WINDOW_SIZE = 30       # 30프레임 = 약 1초 (30fps 기준)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "ear_data.csv")
MODEL_FILE = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "best_drowsy_model.keras")
SCALER_FILE = os.path.join(SCRIPT_DIR, "..", "backend", "ai_models", "ear_scaler.pkl")


def load_and_inspect_data():
    """데이터 로드 및 기본 통계 확인"""
    if not os.path.exists(DATA_FILE):
        print(f"❌ '{DATA_FILE}' 파일이 없습니다!")
        print("먼저 'python collect_data.py'를 실행하세요.")
        exit()

    df = pd.read_csv(DATA_FILE, header=None, names=["ear", "label"])

    print("=" * 50)
    print("📊 데이터 통계")
    print("=" * 50)
    print(f"전체 데이터: {len(df)}개")
    print(f"  정상(0): {(df.label == 0).sum()}개")
    print(f"  졸음(1): {(df.label == 1).sum()}개")
    print(f"\nEAR 통계:")
    print(f"  정상 - 평균: {df[df.label==0]['ear'].mean():.4f}, "
          f"표준편차: {df[df.label==0]['ear'].std():.4f}")
    print(f"  졸음 - 평균: {df[df.label==1]['ear'].mean():.4f}, "
          f"표준편차: {df[df.label==1]['ear'].std():.4f}")
    print()

    return df


def create_sequences(data, labels, window_size):
    """
    시계열 윈도우 생성

    연속된 window_size개의 EAR 값을 하나의 시퀀스로 묶고,
    마지막 프레임의 라벨을 해당 시퀀스의 라벨로 사용

    Parameters:
        data: 스케일링된 EAR 값 배열
        labels: 라벨 배열 (0 또는 1)
        window_size: 윈도우 크기
    Returns:
        X: (num_samples, window_size, 1) 형태의 입력 데이터
        y: (num_samples,) 형태의 라벨
    """
    X, y = [], []
    for i in range(len(data) - window_size):
        X.append(data[i:i + window_size])
        y.append(labels[i + window_size])
    return np.array(X), np.array(y)


def build_model(window_size):
    """LSTM 모델 구축"""
    model = Sequential([
        # 첫 번째 LSTM 레이어 - 시계열 패턴의 고수준 특징 추출
        LSTM(64, return_sequences=True, input_shape=(window_size, 1)),
        BatchNormalization(),
        Dropout(0.3),

        # 두 번째 LSTM 레이어 - 추출된 특징을 압축
        LSTM(32, return_sequences=False),
        BatchNormalization(),
        Dropout(0.3),

        # 분류 레이어
        Dense(16, activation='relu'),
        Dropout(0.2),
        Dense(1, activation='sigmoid')  # 이진 분류
    ])

    model.compile(
        optimizer='adam',
        loss='binary_crossentropy',
        metrics=['accuracy']
    )

    return model


def plot_training_history(history):
    """학습 히스토리 시각화"""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Loss
    axes[0].plot(history.history['loss'], label='Train Loss', linewidth=2)
    axes[0].plot(history.history['val_loss'], label='Val Loss', linewidth=2)
    axes[0].set_title('Model Loss', fontsize=14)
    axes[0].set_xlabel('Epoch')
    axes[0].set_ylabel('Loss')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # Accuracy
    axes[1].plot(history.history['accuracy'], label='Train Accuracy', linewidth=2)
    axes[1].plot(history.history['val_accuracy'], label='Val Accuracy', linewidth=2)
    axes[1].set_title('Model Accuracy', fontsize=14)
    axes[1].set_xlabel('Epoch')
    axes[1].set_ylabel('Accuracy')
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("training_result.png", dpi=150)
    plt.show()
    print("📈 학습 결과 그래프가 'training_result.png'에 저장되었습니다.")


def main():
    # 1. 데이터 로드
    df = load_and_inspect_data()

    # 2. 스케일링
    scaler = StandardScaler()
    ear_scaled = scaler.fit_transform(df[["ear"]].values)

    # 스케일러 저장 (실시간 추론에서 동일한 스케일링 적용 필요)
    joblib.dump(scaler, SCALER_FILE)
    print(f"✅ 스케일러 저장: {SCALER_FILE}")

    # 3. 시계열 윈도우 생성
    X, y = create_sequences(ear_scaled, df["label"].values, WINDOW_SIZE)
    X = X.reshape(-1, WINDOW_SIZE, 1)
    print(f"시계열 데이터: X={X.shape}, y={y.shape}")
    print(f"  정상 시퀀스: {(y == 0).sum()}, 졸음 시퀀스: {(y == 1).sum()}")

    # 4. 학습/검증 분할
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"학습: {len(X_train)}개, 검증: {len(X_val)}개\n")

    # 5. 모델 구축 및 학습
    model = build_model(WINDOW_SIZE)
    model.summary()

    callbacks = [
        EarlyStopping(
            monitor='val_loss',
            patience=10,
            restore_best_weights=True,
            verbose=1
        ),
        ModelCheckpoint(
            MODEL_FILE,
            monitor='val_accuracy',
            save_best_only=True,
            verbose=1
        )
    ]

    print("\n🚀 학습 시작...")
    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=100,
        batch_size=32,
        callbacks=callbacks,
        verbose=1
    )

    # 6. 평가
    print("\n" + "=" * 50)
    print("📊 모델 평가 결과")
    print("=" * 50)

    y_pred = (model.predict(X_val) > 0.5).astype(int).flatten()
    print(classification_report(y_val, y_pred, target_names=["정상", "졸음"]))

    cm = confusion_matrix(y_val, y_pred)
    print(f"혼동 행렬:\n{cm}")

    best_val_acc = max(history.history['val_accuracy'])
    print(f"\n🏆 최고 검증 정확도: {best_val_acc:.4f}")
    print(f"✅ 모델 저장 완료: {MODEL_FILE}")

    # 7. 시각화
    plot_training_history(history)

    print("\n다음 단계: python realtime_detect.py")


if __name__ == "__main__":
    main()
