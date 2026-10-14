"""촬영 세션을 분리하여 LSTM 후보를 학습하고 기존 모델과 비교한다."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile

import joblib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_training.dataset import ensure_disjoint, fingerprint, fit_scaler, make_sequences, read_sessions
from backend.app.ear_sequence import SAMPLE_RATE_HZ, WINDOW_SECONDS, WINDOW_SIZE

ROOT = Path(__file__).resolve().parents[1]


def metrics(labels, scores, threshold):
    predicted = scores >= threshold
    positive = labels == 1
    tp = int(np.sum(positive & predicted))
    fp = int(np.sum(~positive & predicted))
    tn = int(np.sum(~positive & ~predicted))
    fn = int(np.sum(positive & ~predicted))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "threshold": float(threshold), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "accuracy": (tp + tn) / len(labels), "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
        "samples": len(labels),
    }


def choose_threshold(labels, scores, minimum_recall=0.90):
    # 임계값은 검증 데이터만으로 선택한다. 테스트 정답은 사용하지 않는다.
    candidates = [metrics(labels, scores, float(value)) for value in np.linspace(0.01, 0.99, 99)]
    acceptable = [m for m in candidates if m["recall"] >= minimum_recall]
    if not acceptable:
        return max(candidates, key=lambda m: (m["recall"], m["f1"]))
    return min(acceptable, key=lambda m: (m["false_positive_rate"], -m["recall"]))


def transform(scaler, sequences):
    return scaler.transform(sequences.reshape(-1, 1)).reshape(sequences.shape).astype(np.float32)


def predict(model, sequences):
    return np.concatenate([
        np.asarray(model(sequences[start:start + 256], training=False)).reshape(-1)
        for start in range(0, len(sequences), 256)
    ])


def build_model():
    from tensorflow import keras

    # 적은 세션으로 학습하므로 기존 다층 LSTM보다 작은 모델로 과적합을 줄인다.
    model = keras.Sequential([
        keras.layers.Input(shape=(WINDOW_SIZE, 1)),
        keras.layers.LSTM(16),
        keras.layers.Dense(8, activation="relu"),
        keras.layers.Dropout(0.15),
        keras.layers.Dense(1, activation="sigmoid"),
    ])
    model.compile(optimizer=keras.optimizers.Adam(learning_rate=0.001),
                  loss="binary_crossentropy", metrics=["accuracy"])
    return model


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", nargs="+", type=Path, required=True)
    parser.add_argument("--validation", nargs="+", type=Path, required=True)
    parser.add_argument("--test", nargs="+", type=Path, required=True)
    parser.add_argument("--normalization", choices=("raw", "personal_baseline"), default="personal_baseline")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    if args.epochs < 1:
        parser.error("epochs는 1 이상이어야 합니다.")
    # 같은 파일을 경로 표기만 달리하여 양쪽에 넣는 누수도 차단한다.
    path_sets = [{p.resolve() for p in paths} for paths in (args.train, args.validation, args.test)]
    if any(path_sets[i] & path_sets[j] for i in range(3) for j in range(i + 1, 3)):
        parser.error("학습·검증·테스트 파일이 겹칩니다.")
    groups = [read_sessions(paths, args.normalization)
              for paths in (args.train, args.validation, args.test)]
    ensure_disjoint(*groups)
    scaler = fit_scaler(groups[0], args.normalization)
    datasets = [make_sequences(group, args.normalization) for group in groups]
    for name, (_, _, labels, _) in zip(("train", "validation", "test"), datasets):
        if set(labels.tolist()) != {0, 1}:
            raise ValueError(f"{name}: 정상과 눈 감김 두 라벨이 모두 필요합니다.")
        print(f"{name}: {len(labels)} sequences, normal={np.sum(labels == 0)}, closed={np.sum(labels == 1)}", flush=True)
    from tensorflow import keras
    keras.utils.set_random_seed(args.seed)
    model = build_model()
    output_dir = ROOT / "backend" / "ai_models" / "candidates" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir.mkdir(parents=True, exist_ok=False)
    print(f"Candidate output: {output_dir}", flush=True)
    features = [transform(scaler, dataset[1]) for dataset in datasets]
    labels = [dataset[2] for dataset in datasets]
    train_count = len(labels[0])
    class_weight = {value: train_count / (2 * int(np.sum(labels[0] == value))) for value in (0, 1)}
    history = model.fit(
        features[0], labels[0], validation_data=(features[1], labels[1]),
        epochs=args.epochs, batch_size=64, class_weight=class_weight, verbose=2,
        callbacks=[keras.callbacks.EarlyStopping(monitor="val_loss", patience=6,
                                                 min_delta=0.001, restore_best_weights=True)],
    )
    validation_scores = predict(model, features[1])
    selection = choose_threshold(labels[1], validation_scores)
    test_scores = predict(model, features[2])
    candidate_test = metrics(labels[2], test_scores, selection["threshold"])
    print("Saving candidate model...", flush=True)
    # Keras가 만드는 임시 HDF5 파일도 쓰기 허용된 후보 폴더 안에 둔다.
    previous_temp_dir = tempfile.tempdir
    with tempfile.TemporaryDirectory(dir=output_dir) as save_temp_dir:
        try:
            tempfile.tempdir = save_temp_dir
            model.save(output_dir / "best_drowsy_model.keras")
        finally:
            tempfile.tempdir = previous_temp_dir
    print("Candidate saved; evaluating existing model...", flush=True)
    joblib.dump(scaler, output_dir / "ear_scaler.pkl")
    existing_model = keras.models.load_model(ROOT / "backend" / "ai_models" / "best_drowsy_model.keras", compile=False)
    existing_scaler = joblib.load(ROOT / "backend" / "ai_models" / "ear_scaler.pkl")
    existing_test_scores = predict(existing_model, transform(existing_scaler, datasets[2][0]))
    existing_test = metrics(labels[2], existing_test_scores, 0.5)
    metadata = {
        "normalization": args.normalization,
        "decision_threshold": selection["threshold"],
        "sample_rate_hz": SAMPLE_RATE_HZ, "window_size": WINDOW_SIZE, "window_seconds": WINDOW_SECONDS,
        "seed": args.seed, "epochs_run": len(history.history["loss"]),
        "split_strategy": "explicit disjoint recording sessions",
        "sources": {name: [{"path": str(path.resolve()), "sha256": fingerprint(path)} for path in paths]
                    for name, paths in zip(("train", "validation", "test"), (args.train, args.validation, args.test))},
    }
    report = {
        "validation_threshold_selection": selection,
        "test_existing_model": existing_test,
        "test_candidate_model": candidate_test,
        "candidate_better_on_this_test": candidate_test["f1"] > existing_test["f1"]
            and candidate_test["false_positive_rate"] < existing_test["false_positive_rate"]
            and candidate_test["recall"] >= 0.85,
        "limitations": [
            "Labels come from user key presses; not clinically verified drowsiness.",
            "Single user and few recording sessions; not evidence of generalization to other users.",
            "Test recording was previously inspected for decision-rule debugging.",
            "Frames overlap and are correlated; sample counts are not independent trials.",
            "Raw model metrics; live warning latency and event performance are not measured here.",
        ],
        "history": {key: [float(v) for v in values] for key, values in history.history.items()},
    }
    for name, payload in (("metadata.json", metadata), ("evaluation.json", report)):
        with (output_dir / name).open("w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
    with (output_dir / "test_predictions.csv").open("w", newline="", encoding="utf-8") as output:
        import csv
        writer = csv.writer(output)
        writer.writerow(["session", "label", "existing_score", "candidate_score"])
        writer.writerows(zip(datasets[2][3], labels[2], existing_test_scores, test_scores))
    print(json.dumps({key: value for key, value in report.items() if key != "history"}, ensure_ascii=False, indent=2))
    print(f"Saved candidate and comparison: {output_dir}")


if __name__ == "__main__":
    main()
