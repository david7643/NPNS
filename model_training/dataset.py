"""CSV 기반 EAR 학습 데이터. pandas 없이 세션 분리와 시간 보간을 수행한다."""

import csv
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.ear_sequence import EarSequenceBuffer, normalize_eye_ear


@dataclass
class Session:
    name: str
    timestamps: np.ndarray
    raw_ear: np.ndarray
    personal_ear: np.ndarray
    labels: np.ndarray


def fingerprint(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_sessions(paths, normalization="personal_baseline"):
    """정답 불명·미검출·무효 관측에서 입력을 끊고, 모델 점수는 정답으로 사용하지 않는다."""
    sessions = []
    for path in map(Path, paths):
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            columns = set(reader.fieldnames or [])
            diagnostic = "ground_truth" in columns
            if not diagnostic and not {"session_id", "timestamp", "ear", "label"}.issubset(columns):
                raise ValueError(f"{path}: 타임스탬프와 라벨이 있는 CSV가 필요합니다.")
            if normalization == "personal_baseline" and not {
                "left_ear", "right_ear", "left_ear_baseline", "right_ear_baseline"
            }.issubset(columns):
                raise ValueError(f"{path}: 개인별 EAR 기준 열이 필요합니다. --normalization raw를 사용하거나 진단 데이터를 수집하세요.")
            groups = {}
            for row in reader:
                key = path.name if diagnostic else f"{path.name}:{row['session_id']}"
                values = groups.setdefault(key, [])
                label_text = row.get("ground_truth") if diagnostic else row.get("label")
                label = {"awake": 0, "drowsy": 1, "0": 0, "1": 1}.get(label_text)
                try:
                    time = float(row["elapsed_seconds"] if diagnostic else row["timestamp"])
                    ear = float(row["ear_value"] if diagnostic else row["ear"])
                    valid = label is not None and np.isfinite(time) and np.isfinite(ear) and 0 < ear < 1
                    if diagnostic:
                        valid = valid and row.get("face_detected") == "True" and row.get("calibrated") == "True"
                    personal = ear
                    if normalization == "personal_baseline":
                        left, right = float(row["left_ear"]), float(row["right_ear"])
                        left_base, right_base = float(row["left_ear_baseline"]), float(row["right_ear_baseline"])
                        valid = valid and all(np.isfinite(v) and 0 < v < 1 for v in (left, right, left_base, right_base))
                        personal = normalize_eye_ear(left, right, left_base, right_base)
                    if not valid:
                        raise ValueError("Unavailable observation")
                except (ValueError, KeyError, ZeroDivisionError):
                    # 남은 유효 프레임끼리 공백을 이어붙이지 않도록 구분자를 남긴다.
                    values.append((np.nan, np.nan, np.nan, -1))
                    continue
                values.append((time, ear, personal, label))
            for name, values in groups.items():
                if not values:
                    continue
                array = np.asarray(values)
                sessions.append(Session(name, array[:, 0], array[:, 1], array[:, 2], array[:, 3].astype(int)))
    return sessions


def make_sequences(sessions, normalization):
    raw_sequences, input_sequences, labels, session_ids = [], [], [], []
    for session in sessions:
        raw_buffer, input_buffer = EarSequenceBuffer(), EarSequenceBuffer()
        last_output_at = None
        for timestamp, raw, personal, label in zip(
            session.timestamps, session.raw_ear, session.personal_ear, session.labels
        ):
            if label not in (0, 1) or not np.isfinite(timestamp):
                raw_buffer.clear()
                input_buffer.clear()
                last_output_at = None
                continue
            raw_buffer.append(timestamp, raw)
            input_buffer.append(timestamp, personal if normalization == "personal_baseline" else raw)
            raw_seq, input_seq = raw_buffer.sequence(), input_buffer.sequence()
            if raw_seq is None or input_seq is None:
                continue
            # 과도하게 높은 FPS의 세션이 학습 표본 수를 지배하지 않게 한다.
            if last_output_at is not None and timestamp - last_output_at < 0.099:
                continue
            last_output_at = timestamp
            raw_sequences.append(raw_seq)
            input_sequences.append(input_seq)
            labels.append(label)
            session_ids.append(session.name)
    if not labels:
        raise ValueError("약 1초 이상의 연속된 유효 관측이 없습니다.")
    return (
        np.asarray(raw_sequences, dtype=np.float32),
        np.asarray(input_sequences, dtype=np.float32),
        np.asarray(labels, dtype=np.int32),
        np.asarray(session_ids),
    )


def ensure_disjoint(*groups):
    seen = set()
    for group in groups:
        names = {session.name for session in group}
        if seen & names:
            raise ValueError(f"학습·검증·테스트 촬영 세션이 겹칩니다: {sorted(seen & names)}")
        seen.update(names)


def fit_scaler(sessions, normalization):
    from sklearn.preprocessing import StandardScaler

    values = []
    for session in sessions:
        source = session.personal_ear if normalization == "personal_baseline" else session.raw_ear
        valid = (session.labels >= 0) & np.isfinite(source)
        values.extend(source[valid].tolist())
    if not values:
        raise ValueError("학습용 유효 프레임이 없습니다.")
    return StandardScaler().fit(np.asarray(values).reshape(-1, 1))
