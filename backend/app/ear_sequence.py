"""학습과 추론에서 동일한 시간 범위의 EAR 입력을 만든다."""

from collections import deque

import numpy as np

WINDOW_SIZE = 30
SAMPLE_RATE_HZ = 30.0
WINDOW_SECONDS = (WINDOW_SIZE - 1) / SAMPLE_RATE_HZ
MAX_OBSERVATION_GAP_SECONDS = 0.5


def normalize_eye_ear(left, right, left_baseline, right_baseline):
    values = (left, right, left_baseline, right_baseline)
    if not all(np.isfinite(value) and 0 < value < 1 for value in values):
        raise ValueError("Invalid eye measurements or calibration")
    return float((left / left_baseline + right / right_baseline) / 2.0)


class EarSequenceBuffer:
    def __init__(self):
        self.samples = deque()

    def clear(self):
        self.samples.clear()

    def append(self, timestamp, ear):
        if not np.isfinite(timestamp) or not np.isfinite(ear):
            self.clear()
            return
        if self.samples:
            gap = timestamp - self.samples[-1][0]
            if gap <= 0 or gap > MAX_OBSERVATION_GAP_SECONDS:
                self.clear()
        self.samples.append((float(timestamp), float(ear)))
        # 보간을 위해 윈도우 시작 직전의 관측 하나를 남긴다.
        cutoff = timestamp - WINDOW_SECONDS
        while len(self.samples) > 2 and self.samples[1][0] <= cutoff:
            self.samples.popleft()

    def sequence(self):
        if len(self.samples) < 2:
            return None
        end = self.samples[-1][0]
        start = end - WINDOW_SECONDS
        if self.samples[0][0] > start + 1e-9:
            return None
        timestamps, ears = zip(*self.samples)
        grid = np.linspace(start, end, WINDOW_SIZE)
        return np.interp(grid, timestamps, ears).reshape(WINDOW_SIZE, 1)

    @property
    def progress(self):
        if not self.samples:
            return 0
        span = self.samples[-1][0] - self.samples[0][0]
        return min(WINDOW_SIZE, 1 + int(span * SAMPLE_RATE_HZ + 1e-9))
