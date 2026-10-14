"""저장된 EAR와 기존 모델 점수로 판정 로직만 비교한다. 재추론 평가는 아니다."""

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.detection_service import DrowsinessDecisionEngine
from app.ear_sequence import MAX_OBSERVATION_GAP_SECONDS


def evaluate(rows, levels):
    confusion = dict(tp=0, fp=0, tn=0, fn=0)
    false_events = 0
    previous_alarm = False
    starts = []
    for index, (row, level) in enumerate(zip(rows, levels)):
        label = row["ground_truth"]
        alarm = level > 0
        ready = row.get("calibrated") == "True" and row["state"] in (
            "AWAKE", "DROWSY", "DROWSY_PENDING"
        )
        if ready and label in ("awake", "drowsy"):
            key = ("tp" if label == "drowsy" else "fp") if alarm else (
                "fn" if label == "drowsy" else "tn"
            )
            confusion[key] += 1
            if label == "awake" and alarm and not previous_alarm:
                false_events += 1
        if label == "drowsy" and (index == 0 or rows[index - 1]["ground_truth"] != label):
            starts.append((index, previous_alarm))
        if ready:
            previous_alarm = alarm

    latencies, recoveries = [], []
    missed, preexisting = 0, 0
    for start, already_alarm in starts:
        end = next((i for i in range(start + 1, len(rows))
                    if rows[i]["ground_truth"] != "drowsy"), len(rows))
        if already_alarm:
            preexisting += 1
        elif end < len(rows):
            hit = next((i for i in range(start, end) if levels[i] > 0), None)
            if hit is None:
                missed += 1
            else:
                latencies.append(float(rows[hit]["elapsed_seconds"]) - float(rows[start]["elapsed_seconds"]))
        if end < len(rows) and rows[end]["ground_truth"] == "awake" and levels[end - 1] > 0:
            stop = next((i for i in range(end, len(rows))
                         if rows[i]["ground_truth"] != "awake"), len(rows))
            cleared = next((i for i in range(end, stop) if levels[i] == 0
                            and rows[i]["state"] in ("AWAKE", "DROWSY", "DROWSY_PENDING")), None)
            if cleared is not None:
                recoveries.append(float(rows[cleared]["elapsed_seconds"]) - float(rows[end]["elapsed_seconds"]))
    total = sum(confusion.values())
    return {
        "confusion": confusion,
        "accuracy": round((confusion["tp"] + confusion["tn"]) / total, 4) if total else None,
        "new_false_alarms": false_events,
        "detected_completed_trials": len(latencies),
        "missed_completed_trials": missed,
        "preexisting_alarm_trials": preexisting,
        "mean_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "mean_recovery_seconds": round(sum(recoveries) / len(recoveries), 3) if recoveries else None,
    }


def replay_recorded_scores(rows, engine):
    levels = []
    last_observed_at = None
    for row in rows:
        if row.get("calibrated") != "True":
            levels.append(0)
            continue
        if (
            row.get("face_detected") != "True" or row["state"] in ("NO_FACE", "INVALID_EAR")
            or not row.get("left_ear") or not row.get("right_ear")
        ):
            engine.interrupt_evidence()
            if float(row.get("no_face_duration_ms") or 0) >= 1000:
                engine.reset_transient()
            levels.append(engine.current_level)
            continue
        now = float(row["elapsed_seconds"])
        if last_observed_at is not None and now - last_observed_at > MAX_OBSERVATION_GAP_SECONDS:
            engine.interrupt_evidence()
        last_observed_at = now
        engine.left_baseline = float(row["left_ear_baseline"])
        engine.right_baseline = float(row["right_ear_baseline"])
        decision = engine.update(
            float(row["left_ear"]), float(row["right_ear"]),
            float(row["pred_score"]) if row["prediction_ready"] == "True" else None,
            now,
        )
        levels.append(decision["level"])
    return levels


def recorded_model_threshold(rows):
    thresholds = {float(row["model_threshold"]) for row in rows if row.get("model_threshold")}
    if len(thresholds) > 1:
        raise ValueError("One recording must use a single model threshold")
    return next(iter(thresholds), 0.5)


def compare(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    engine = DrowsinessDecisionEngine(recorded_model_threshold(rows))
    levels = replay_recorded_scores(rows, engine)
    return {
        "file": path.name,
        "scope": "Decision rules only; recorded model scores and calibration unchanged",
        "previous": evaluate(rows, [int(row["drowsy_level"]) for row in rows]),
        "revised": evaluate(rows, levels),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.files:
        print(json.dumps(compare(path), ensure_ascii=False, indent=2))
