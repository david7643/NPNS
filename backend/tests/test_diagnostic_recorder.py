import io
import unittest
from unittest.mock import patch

from test_client import DiagnosticRecorder


class DiagnosticRecorderTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        open_patch = patch("test_client.Path.open", return_value=self.output)
        open_patch.start()
        self.addCleanup(open_patch.stop)
        self.clock = patch("test_client.time.monotonic", return_value=0.0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.recorder = DiagnosticRecorder(1, "tests")
        self.addCleanup(self.recorder.close)

    def record(self, at, level=0, score=0.0, state=None):
        self.now.return_value = at
        self.recorder.record({
            "face_detected": True,
            "calibrated": True,
            "prediction_ready": True,
            "pred_score": score,
            "drowsy_level": level,
            "state": state or ("DROWSY" if level else "AWAKE"),
        })

    def label(self, at, label):
        self.now.return_value = at
        self.recorder.set_ground_truth(label)

    def test_raw_model_and_final_decision_are_evaluated_separately(self):
        self.label(0, "awake")
        self.record(1, score=0.9, state="DROWSY_PENDING")
        self.assertEqual(self.recorder.confusion["fp"], 1)
        self.assertEqual(self.recorder.final_confusion["tn"], 1)
        self.assertEqual(self.recorder.false_alarm_events, 0)

    def test_recovery_is_not_a_new_alarm_but_is_in_frame_metrics(self):
        self.label(0, "drowsy")
        self.record(1, level=1)
        self.label(2, "awake")
        self.record(3, level=1)
        self.record(4)
        self.assertEqual(self.recorder.false_alarm_events, 0)
        self.assertEqual(self.recorder.final_confusion["fp"], 1)
        self.assertEqual(self.recorder.recovery_delays, [2])
        self.record(5, level=1)
        self.assertEqual(self.recorder.false_alarm_events, 1)

    def test_completed_missed_trial_and_incomplete_trial_are_distinct(self):
        self.label(0, "drowsy")
        self.record(1)
        self.label(2, "awake")
        self.assertTrue(self.recorder.episodes[0]["completed"])
        self.assertFalse(self.recorder.episodes[0]["detected"])
        self.label(3, "drowsy")
        self.assertFalse(self.recorder.episodes[1]["completed"])

    def test_preexisting_alarm_is_excluded_from_detection_latency(self):
        self.label(0, "awake")
        self.record(1, level=1)
        self.label(2, "drowsy")
        self.record(3, level=1)
        self.assertTrue(self.recorder.episodes[0]["preexisting_alarm"])
        self.assertEqual(self.recorder.alert_latencies, [])

    def test_unavailable_frame_does_not_clear_alarm_or_count_as_awake(self):
        self.label(0, "drowsy")
        self.record(1, level=1)
        self.label(2, "awake")
        self.record(3, state="NO_FACE")
        self.record(4, level=1)
        self.assertEqual(self.recorder.final_unavailable_frames, 1)
        self.assertEqual(self.recorder.false_alarm_events, 0)
        self.assertEqual(self.recorder.recovery_delays, [])

    def test_model_metrics_use_the_loaded_candidate_threshold(self):
        self.label(0, "drowsy")
        self.recorder.record({
            "face_detected": True, "calibrated": True, "prediction_ready": True,
            "pred_score": 0.03, "model_threshold": 0.02,
            "drowsy_level": 0, "state": "DROWSY_PENDING",
        })
        self.assertEqual(self.recorder.confusion["tp"], 1)
        self.assertEqual(self.recorder.final_confusion["fn"], 1)


if __name__ == "__main__":
    unittest.main()
