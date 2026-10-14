import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from model_training.dataset import Session, ensure_disjoint, fit_scaler, make_sequences, read_sessions
from model_training.train_model import choose_threshold


class TrainingDatasetTests(unittest.TestCase):
    def session(self, name, ear):
        times = np.arange(0, 2.01, 0.1)
        values = np.full(len(times), ear)
        return Session(name, times, values, values.copy(), np.zeros(len(times), dtype=int))

    def test_scaler_is_fit_only_on_training_frames(self):
        train = self.session("train", 0.3)
        validation = self.session("validation", 0.1)
        scaler = fit_scaler([train], "raw")
        self.assertAlmostEqual(float(scaler.mean_[0]), 0.3)
        self.assertLess(float(scaler.transform(validation.raw_ear[:1].reshape(-1, 1))[0, 0]), 0)

    def test_duplicate_recording_rejected(self):
        session = self.session("same", 0.3)
        with self.assertRaises(ValueError):
            ensure_disjoint([session], [session], [])

    def test_unknown_or_invalid_row_breaks_sequence(self):
        session = self.session("a", 0.3)
        session.labels[10] = -1
        session.timestamps[10] = np.nan
        with self.assertRaises(ValueError):
            make_sequences([session], "raw")

    def test_personal_input_and_raw_baseline_share_same_windows(self):
        session = self.session("a", 0.3)
        session.personal_ear[:] = 1.0
        raw, personal, labels, names = make_sequences([session], "personal_baseline")
        np.testing.assert_allclose(raw, 0.3)
        np.testing.assert_allclose(personal, 1.0)
        self.assertEqual(raw.shape, personal.shape)
        self.assertEqual(len(labels), len(names))

    def test_model_scores_are_not_used_as_training_labels(self):
        contents = (
            "elapsed_seconds,ear_value,ground_truth,face_detected,calibrated,left_ear,right_ear,left_ear_baseline,right_ear_baseline,pred_score\n"
            "0,0.3,awake,True,True,0.3,0.3,0.3,0.3,1.0\n"
            "0.1,0.1,drowsy,True,True,0.1,0.1,0.3,0.3,0.0\n"
        )
        with patch("pathlib.Path.open", return_value=io.StringIO(contents)):
            sessions = read_sessions(["recording.csv"])
        self.assertEqual(sessions[0].labels.tolist(), [0, 1])

    def test_threshold_selection_meets_validation_recall_target(self):
        labels = np.array([0, 0, 0, 1, 1])
        scores = np.array([0.1, 0.2, 0.6, 0.7, 0.9])
        selected = choose_threshold(labels, scores)
        self.assertEqual(selected["recall"], 1.0)
        self.assertEqual(selected["fp"], 0)


if __name__ == "__main__":
    unittest.main()
