"""고정된 모델 점수로 경고 진입 조건의 오경보·지연 절충을 비교한다."""

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.detection_service import LegacyDrowsinessDecisionEngine as DrowsinessDecisionEngine
from model_training.compare_decisions import evaluate, recorded_model_threshold, replay_recorded_scores


def compare(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    threshold = recorded_model_threshold(rows)
    profiles = {"v3": {
        "model_level_seconds": (0.5, 1.0, 1.5),
        "eye_level_seconds": (1.0, 2.0, 3.0),
        # 이전 코드의 무기한 경계 구간 유지 동작을 짧은 기록에서 재현한다.
        "closed_gap_tolerance": 1e9,
    }}
    for hold in (0.75, 1.0, 1.25, 1.5, 2.0):
        profiles[f"hold_{hold}"] = {
            "model_level_seconds": (hold, hold + 0.5, hold + 1.0),
            "eye_level_seconds": (max(1.0, hold), max(2.0, hold + 0.5), max(3.0, hold + 1.0)),
            "closed_gap_tolerance": 0.15,
        }
    results = {}
    for name, config in profiles.items():
        engine = DrowsinessDecisionEngine(threshold, **config)
        results[name] = evaluate(rows, replay_recorded_scores(rows, engine))
    return {
        "recording": path.name,
        "model_threshold": threshold,
        "scope": "Recorded model scores; development comparison, not independent validation",
        "profiles": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.files:
        print(json.dumps(compare(path), ensure_ascii=False, indent=2))
