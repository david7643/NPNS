import json
import unittest

import numpy as np

from app.detection_service import (
    CALIBRATION_MIN_SAMPLES,
    LegacyDrowsinessDecisionEngine as DrowsinessDecisionEngine,
)


class DrowsinessDecisionEngineTests(unittest.TestCase):
    def calibrated_engine(self):
        engine = DrowsinessDecisionEngine()
        for index in range(CALIBRATION_MIN_SAMPLES):
            now = index * 3.1 / (CALIBRATION_MIN_SAMPLES - 1)
            engine.add_calibration_sample(0.30, 0.28, now)
        self.assertTrue(engine.calibrated)
        return engine

    def test_closed_eye_levels_use_elapsed_time(self):
        engine = self.calibrated_engine()

        pending = engine.update(0.15, 0.14, None, 10.0)
        level_one = engine.update(0.15, 0.14, None, 11.26)
        level_three = engine.update(0.15, 0.14, None, 13.01)

        self.assertEqual(pending["level"], 0)
        self.assertEqual(level_one["level"], 1)
        self.assertEqual(level_three["level"], 3)

    def test_alarm_clears_only_after_stable_reopening(self):
        engine = self.calibrated_engine()
        engine.update(0.15, 0.14, None, 10.0)
        self.assertEqual(engine.update(0.15, 0.14, None, 11.3)["level"], 1)

        self.assertEqual(engine.update(0.30, 0.28, None, 11.4)["level"], 1)
        self.assertEqual(engine.update(0.30, 0.28, None, 11.91)["level"], 0)

    def test_interrupted_observation_does_not_count_as_closed_time(self):
        engine = self.calibrated_engine()
        engine.update(0.15, 0.14, None, 10.0)
        engine.interrupt_evidence()

        result = engine.update(0.15, 0.14, None, 12.0)

        self.assertEqual(result["level"], 0)
        self.assertEqual(result["closed_seconds"], 0.0)

    def test_model_score_is_a_time_based_secondary_signal(self):
        engine = self.calibrated_engine()
        engine.update(0.23, 0.21, 0.9, 10.0)

        result = engine.update(0.23, 0.21, 0.9, 12.26)

        self.assertEqual(result["level"], 3)

    def test_high_model_score_with_open_eyes_does_not_trigger_alarm(self):
        engine = self.calibrated_engine()
        for now in (10.0, 11.0, 12.0, 13.0):
            result = engine.update(0.30, 0.28, 1.0, now)
            self.assertEqual(result["level"], 0)
            self.assertFalse(result["model_eye_support"])

    def test_open_eyes_clear_alarm_even_with_stale_high_score(self):
        engine = self.calibrated_engine()
        engine.update(0.15, 0.14, 1.0, 10.0)
        engine.update(0.15, 0.14, 1.0, 13.1)
        self.assertEqual(engine.update(0.30, 0.28, 1.0, 13.2)["level"], 3)
        self.assertEqual(engine.update(0.30, 0.28, 1.0, 13.71)["level"], 0)

    def test_single_eye_closure_does_not_support_model_alarm(self):
        engine = self.calibrated_engine()
        engine.update(0.15, 0.28, 1.0, 10.0)
        result = engine.update(0.15, 0.28, 1.0, 13.0)
        self.assertEqual(result["level"], 0)

    def test_candidate_threshold_is_used_instead_of_legacy_point_five(self):
        engine = DrowsinessDecisionEngine(model_threshold=0.02)
        engine.left_baseline, engine.right_baseline = 0.30, 0.28
        engine.update(0.23, 0.21, 0.03, 10.0)
        self.assertEqual(engine.update(0.23, 0.21, 0.03, 10.51)["level"], 0)
        self.assertEqual(engine.update(0.23, 0.21, 0.03, 11.26)["level"], 1)

    def test_short_closed_eyes_with_high_model_score_do_not_alarm(self):
        engine = self.calibrated_engine()
        for now in (10.0, 10.4, 10.8, 11.2):
            self.assertEqual(engine.update(0.15, 0.14, 1.0, now)["level"], 0)
        self.assertEqual(engine.update(0.30, 0.28, 1.0, 11.21)["level"], 0)

    def test_gray_band_does_not_extend_a_short_closure_into_an_alarm(self):
        engine = self.calibrated_engine()
        engine.update(0.15, 0.14, None, 10.0)
        engine.update(0.15, 0.14, None, 10.5)
        engine.update(0.23, 0.21, None, 10.6)
        result = engine.update(0.23, 0.21, None, 11.3)
        self.assertEqual(result["level"], 0)
        self.assertEqual(result["closed_seconds"], 0)

    def test_brief_landmark_jitter_keeps_a_long_closure_continuous(self):
        engine = self.calibrated_engine()
        engine.update(0.15, 0.14, None, 10.0)
        engine.update(0.23, 0.21, None, 10.7)
        engine.update(0.15, 0.14, None, 10.8)
        self.assertEqual(engine.update(0.15, 0.14, None, 11.26)["level"], 1)

    def test_closure_after_a_long_gray_gap_starts_new_evidence(self):
        engine = self.calibrated_engine()
        engine.update(0.15, 0.14, None, 10.0)
        engine.update(0.23, 0.21, None, 10.8)
        self.assertEqual(engine.update(0.15, 0.14, None, 11.1)["closed_seconds"], 0)
        self.assertEqual(engine.update(0.15, 0.14, None, 11.3)["level"], 0)

    def test_numpy_ear_results_can_be_sent_as_json_after_calibration(self):
        engine = self.calibrated_engine()
        for left, right in ((0.30, 0.28), (0.15, 0.14)):
            result = engine.update(np.float64(left), np.float64(right), 0.0, 10.0)
            payload = json.loads(json.dumps(result))
            self.assertIsInstance(payload["eyes_closed"], bool)


if __name__ == "__main__":
    unittest.main()
