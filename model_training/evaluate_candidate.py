"""기존 모델과 후보를 같은 기록에서 재추론하고 같은 개선 판정 규칙으로 비교한다."""

import argparse
import csv
import json
from pathlib import Path
import sys

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from app.detection_service import DETECTION_CODE_VERSION, DrowsinessDecisionEngine
from app.ear_sequence import EarSequenceBuffer, normalize_eye_ear
from model_training.compare_decisions import evaluate
from model_training.train_model import predict, transform


def replay(rows, model_dir, *, engine_options=None):
    from tensorflow import keras

    metadata_path = model_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {
        "normalization": "raw", "decision_threshold": 0.5,
    }
    buffer = EarSequenceBuffer()
    sequences, indices = [], []
    for index, row in enumerate(rows):
        try:
            if row.get("face_detected") != "True" or row.get("calibrated") != "True":
                raise ValueError("Unavailable")
            left, right = float(row["left_ear"]), float(row["right_ear"])
            ear = float(row["ear_value"])
            if metadata["normalization"] == "personal_baseline":
                ear = normalize_eye_ear(left, right, float(row["left_ear_baseline"]), float(row["right_ear_baseline"]))
            if not np.isfinite(ear) or ear <= 0:
                raise ValueError("Invalid EAR")
            buffer.append(float(row["elapsed_seconds"]), ear)
            sequence = buffer.sequence()
            if sequence is not None:
                indices.append(index)
                sequences.append(sequence)
        except (ValueError, KeyError):
            buffer.clear()
    model = keras.models.load_model(model_dir / "best_drowsy_model.keras", compile=False)
    scaler = joblib.load(model_dir / "ear_scaler.pkl")
    scores = {}
    if sequences:
        scores = dict(zip(indices, predict(model, transform(scaler, np.asarray(sequences, dtype=np.float32)))))
    engine = DrowsinessDecisionEngine(metadata["decision_threshold"], **(engine_options or {}))
    levels = []
    for index, row in enumerate(rows):
        if row.get("calibrated") != "True":
            levels.append(0)
            continue
        if row.get("face_detected") != "True" or not row.get("left_ear") or not row.get("right_ear"):
            engine.interrupt_evidence()
            if float(row.get("no_face_duration_ms") or 0) >= 1000:
                engine.reset_transient()
            levels.append(0)
            continue
        engine.left_baseline = float(row["left_ear_baseline"])
        engine.right_baseline = float(row["right_ear_baseline"])
        result = engine.update(float(row["left_ear"]), float(row["right_ear"]),
                               float(scores[index]) if index in scores else None,
                               float(row["elapsed_seconds"]))
        levels.append(result["level"])
    return evaluate(rows, levels)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--recording", type=Path, required=True)
    args = parser.parse_args()
    with args.recording.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    report = {
        "recording": str(args.recording),
        "existing_model_with_updated_rules": replay(rows, ROOT / "backend" / "ai_models"),
        "candidate_model_with_updated_rules": replay(rows, args.candidate),
        "scope": "Recorded landmarks and calibration; fresh model inference; not a live camera test",
    }
    # 다른 기록·코드 버전의 기존 비교 결과를 덮어쓰지 않는다.
    output_path = args.candidate / f"pipeline_evaluation_{DETECTION_CODE_VERSION}_{args.recording.stem}.json"
    with output_path.open("w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
