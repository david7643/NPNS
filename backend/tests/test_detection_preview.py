import asyncio
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from app import detection_preview as preview
from app import detection_service as detection
from app.arduino_bridge import ArduinoBridge


class PreviewTests(unittest.TestCase):
    def test_rendering_keeps_inference_image_unchanged_and_handles_unavailable_measurements(self):
        frame = np.arange(64 * 48 * 3, dtype=np.uint8).reshape(48, 64, 3)
        original = frame.copy()
        result = preview.render_preview(frame, dict(state="NO_FACE", drowsy_level=3),
                                        [[(0.1, 0.1), (0.2, 0.2), (0.1, 0.3)]], session_id=5)
        np.testing.assert_array_equal(frame, original)
        self.assertEqual(result.shape, (720, 1280, 3))
        self.assertEqual(result.dtype, np.uint8)

    def test_disabled_preview_does_not_start_gui_or_copy_frames(self):
        frame = Mock()
        viewer = preview.DetectionPreview(enabled=False)
        with patch.object(preview.subprocess, "Popen") as process:
            viewer.show(frame, {})
            viewer.close()
        process.assert_not_called()
        frame.copy.assert_not_called()

    def test_gui_launch_failure_is_reported_without_propagating_to_detection(self):
        viewer = preview.DetectionPreview()
        with patch.dict(os.environ, {"DISPLAY": ":0"}), \
             patch.object(preview.subprocess, "Popen", side_effect=OSError("GUI unavailable")), \
             self.assertLogs(preview.logger, level="WARNING"):
            viewer.show(np.zeros((16, 16, 3), dtype=np.uint8), {})
            viewer._thread.join(timeout=2)
            viewer.close()
        self.assertFalse(viewer._thread.is_alive())

    def test_closed_window_is_not_reopened_until_a_new_session(self):
        viewer = preview.DetectionPreview()
        process = Mock()
        process.poll.return_value = 0
        process.returncode = 0
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        with patch.dict(os.environ, {"DISPLAY": ":0"}), \
             patch.object(preview.subprocess, "Popen", return_value=process) as launch:
            viewer.show(frame, {})
            viewer._thread.join(timeout=2)
            for _ in range(5):
                viewer.show(frame, {})
            viewer.close()
        launch.assert_called_once()
        self.assertTrue(viewer._closed)
        self.assertFalse(viewer._thread.is_alive())

    def test_slow_display_does_not_block_new_detection_frames(self):
        entered, release = threading.Event(), threading.Event()
        process = Mock()
        process.poll.return_value = None
        process.stdin.write.side_effect = lambda data: (entered.set(), release.wait(5))
        viewer = preview.DetectionPreview()
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        with patch.dict(os.environ, {"DISPLAY": ":0"}), \
             patch.object(preview.subprocess, "Popen", return_value=process), \
             patch.object(preview, "render_preview", return_value=frame):
            viewer.show(frame, {})
            try:
                self.assertTrue(entered.wait(2))
                completed = threading.Event()
                def produce():
                    for _ in range(100):
                        viewer.show(frame, {})
                    completed.set()
                worker = threading.Thread(target=produce)
                worker.start()
                self.assertTrue(completed.wait(2), "display pipe blocked detection frames")
                worker.join(timeout=2)
            finally:
                release.set()
                viewer.close()


class PreviewIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_window_failure_does_not_interrupt_session_results_or_camera_cleanup(self):
        session = detection.DetectionSession(1, 7, asyncio.get_running_loop())
        session._running = True
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        closed = Mock()
        class Camera:
            processor = SimpleNamespace(engine=SimpleNamespace(alarm_generation=0, alarm_acknowledged=False))
            count = 0
            def __enter__(self):
                return self
            def __exit__(self, *args):
                closed()
            def read(self):
                self.count += 1
                if self.count == 3:
                    session._running = False
                return frame, dict(drowsy_level=0, alert_event=False, observation_valid=False)
        results = []
        with patch.object(detection, "CameraDetector", Camera), \
             patch.object(detection, "get_arduino", return_value=ArduinoBridge(None)), \
             patch.object(session, "_put_result", side_effect=results.append), \
             patch.object(session, "_write_record"), patch.object(session, "_flush_journal_records"), \
             patch.dict(os.environ, {"DISPLAY": ":0"}), \
             patch.object(preview.subprocess, "Popen", side_effect=OSError("GUI unavailable")), \
             self.assertLogs(preview.logger, level="WARNING"):
            await asyncio.to_thread(session._detection_loop)
        self.assertEqual(len(results), 2)
        self.assertTrue(all("error" not in result for result in results))
        closed.assert_called_once()
        self.assertFalse(session.is_running)
