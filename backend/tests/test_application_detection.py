import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from app import detection_service as detection
from app import usb_camera


class ApplicationProcessorTests(unittest.TestCase):
    def setUp(self):
        self.model = Mock(return_value=np.array([[0.9]], dtype=np.float32))
        self.scaler = Mock()
        self.scaler.transform.side_effect = lambda value: value
        self.processor = detection.DrowsinessProcessor(
            self.model, self.scaler,
            {"normalization": "personal_baseline", "decision_threshold": 0.02},
        )
        for index in range(41):
            self.processor.process(0.30, 0.28, index / 10)

    def test_shared_processor_normalizes_inputs_and_rejects_open_eye_alarm(self):
        result = self.processor.process(0.30, 0.28, 4.1)
        self.assertTrue(result["prediction_ready"])
        self.assertFalse(result["alert_active"])
        self.assertEqual(result["state"], "AWAKE")
        inputs = self.model.call_args.args[0]
        np.testing.assert_allclose(inputs, 1.0)
        self.assertEqual(inputs.shape, (1, 30, 1))
        json.dumps(result)

    def test_alert_event_only_occurs_on_level_rise(self):
        results = [self.processor.process(0.15, 0.14, 4.2 + index / 10) for index in range(16)]
        alerts = [result for result in results if result["alert_event"]]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["drowsy_level"], 1)
        self.assertTrue(results[-1]["alert_active"])
        self.assertFalse(results[-1]["alert_event"])

    def test_tracking_interruption_is_not_a_duplicate_alert(self):
        for index in range(16):
            self.processor.process(0.15, 0.14, 4.2 + index / 10)
        missing = self.processor.process(None, None, 5.8, face_detected=False)
        restored = self.processor.process(0.15, 0.14, 5.9)
        self.assertEqual(missing["state"], "NO_FACE")
        self.assertFalse(missing["observation_valid"])
        self.assertFalse(restored["alert_event"])

    def test_app_payload_recovers_after_normal_blinks_without_new_alerts(self):
        for index in range(16):
            self.processor.process(0.15, 0.14, 4.2 + index / 10)
        results = []
        # 10초 동안 2초마다 짧게 깜빡이며, 모델 점수는 계속 높게 유지한다.
        for index in range(101):
            blinking = index % 20 in (10, 11)
            left, right = (0.15, 0.14) if blinking else (0.30, 0.28)
            results.append(self.processor.process(left, right, 5.8 + index / 10))
        self.assertFalse(any(result["alert_event"] for result in results))
        self.assertEqual(results[-1]["drowsy_level"], 0)
        self.assertEqual(results[-1]["state"], "AWAKE")
        self.assertFalse(results[-1]["alert_active"])
        json.dumps(results[-1])

    def test_reset_recalibrates_but_long_missing_face_preserves_risk(self):
        for index in range(16):
            self.processor.process(0.15, 0.14, 4.2 + index / 10)
        self.processor.process(None, None, 5.8, face_detected=False)
        missing = self.processor.process(None, None, 6.9, face_detected=False)
        self.assertEqual(self.processor.engine.current_level, 1)
        self.assertEqual(missing["drowsy_level"], 1)
        self.assertFalse(missing["alert_event"])
        self.processor.reset()
        result = self.processor.process(0.30, 0.28, 7.0)
        self.assertEqual(result["state"], "CALIBRATING")
        self.assertFalse(result["calibrated"])
        self.assertFalse(result["alert_active"])


class CameraLifecycleTests(unittest.TestCase):
    def setUp(self):
        context = patch.object(usb_camera, "usb_camera_indices", return_value=[0])
        context.start()
        self.addCleanup(context.stop)

    def test_health_probe_uses_the_configured_camera(self):
        cap = Mock()
        cap.isOpened.return_value = True
        cap.read.return_value = (True, np.zeros((16, 16, 3), dtype=np.uint8))
        with patch.object(detection, "get_active_session", return_value=None), \
             patch.object(usb_camera, "usb_camera_indices", return_value=[2]), \
             patch.object(detection, "get_settings", return_value=SimpleNamespace(DROWSINESS_CAMERA_INDEX=2)), \
             patch.object(detection.cv2, "VideoCapture", return_value=cap) as capture:
            self.assertTrue(detection.camera_available())
        capture.assert_called_once_with(2)
        cap.release.assert_called_once()

    def test_open_camera_without_a_frame_is_not_ready(self):
        for frame in (None, np.empty((0, 0, 3), dtype=np.uint8)):
            with self.subTest(frame=frame):
                cap = Mock()
                cap.isOpened.return_value = True
                cap.read.return_value = (True, frame)
                with patch.object(detection, "get_active_session", return_value=None), \
                     patch.object(detection.cv2, "VideoCapture", return_value=cap):
                    self.assertFalse(detection.camera_available())
                cap.release.assert_called_once()

    def test_failed_frame_read_is_not_ready(self):
        cap = Mock()
        cap.isOpened.return_value = True
        cap.read.return_value = (False, None)
        with patch.object(detection, "get_active_session", return_value=None), \
             patch.object(detection.cv2, "VideoCapture", return_value=cap):
            self.assertFalse(detection.camera_available())
        cap.release.assert_called_once()

    def test_camera_failure_closes_landmarker_and_capture(self):
        cap = Mock()
        cap.isOpened.return_value = False
        landmarker = Mock()
        with patch.object(detection, "models_ready", return_value=True), \
             patch.object(detection.python, "BaseOptions"), \
             patch.object(detection.vision.FaceLandmarker, "create_from_options", return_value=landmarker), \
             patch.object(detection.cv2, "VideoCapture", return_value=cap):
            with self.assertRaisesRegex(RuntimeError, "카메라"):
                with detection.CameraDetector(0):
                    pass
        cap.release.assert_called_once()
        landmarker.close.assert_called_once()

    def test_read_error_releases_resources_and_video_timestamps_increase(self):
        cap = Mock()
        cap.isOpened.return_value = True
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        cap.read.side_effect = [(True, frame), (True, frame), (False, None)]
        landmarker = Mock()
        landmarker.detect_for_video.return_value = SimpleNamespace(face_landmarks=[])
        with patch.object(detection, "models_ready", return_value=True), \
             patch.object(detection.python, "BaseOptions"), \
             patch.object(detection.vision.FaceLandmarker, "create_from_options", return_value=landmarker), \
             patch.object(detection.cv2, "VideoCapture", return_value=cap), \
             patch.object(detection.time, "monotonic", return_value=10.0):
            with self.assertRaisesRegex(RuntimeError, "프레임"):
                with detection.CameraDetector(0) as camera:
                    camera.read()
                    camera.read()
                    camera.read()
        timestamps = [call.args[1] for call in landmarker.detect_for_video.call_args_list]
        self.assertEqual(timestamps, [0, 1])
        cap.release.assert_called_once()
        landmarker.close.assert_called_once()


class ReleasedModelTests(unittest.TestCase):
    def test_default_release_contains_matching_model_bundle(self):
        folder = Path(detection.resolve_model_directory())
        self.assertEqual(folder.name, "personal-ear-v1")
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["normalization"], "personal_baseline")
        import hashlib
        for name, expected in metadata["asset_sha256"].items():
            self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), expected)

    def test_relative_override_is_independent_of_working_directory(self):
        path = detection.resolve_model_directory("ai_models")
        self.assertEqual(path, str(Path(detection.AI_MODELS_DIR).resolve()))


if __name__ == "__main__":
    unittest.main()
