from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from app import usb_camera


class UsbCameraTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysfs = self.root / "video4linux"
        self.sysfs.mkdir()
        for context in (
            patch.object(usb_camera.sys, "platform", "linux"),
            patch.object(usb_camera, "VIDEO_SYSFS_ROOT", self.sysfs),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.frame = np.zeros((16, 16, 3), dtype=np.uint8)

    def device(self, index, bus):
        node = self.sysfs / f"video{index}"
        node.mkdir()
        device = self.root / "devices" / f"device{index}"
        device.mkdir(parents=True)
        subsystem = self.root / "bus" / bus
        subsystem.mkdir(parents=True, exist_ok=True)
        (device / "subsystem").symlink_to(subsystem, target_is_directory=True)
        (node / "device").symlink_to(device, target_is_directory=True)

    def capture(self, frame=None):
        cap = Mock()
        cap.isOpened.return_value = True
        cap.read.return_value = (True, self.frame if frame is None else frame)
        return cap

    def test_auto_uses_usb_even_when_internal_camera_has_index_zero(self):
        self.device(0, "pci")
        self.device(32, "usb")
        cap = self.capture()
        with patch.object(usb_camera.cv2, "VideoCapture", return_value=cap) as capture:
            opened, frame, index = usb_camera.open_webcam("auto")
        capture.assert_called_once_with(32)
        self.assertIs(opened, cap)
        self.assertIs(frame, self.frame)
        self.assertEqual(index, 32)
        cap.release.assert_not_called()

    def test_no_usb_does_not_open_internal_camera(self):
        self.device(0, "pci")
        with patch.object(usb_camera.cv2, "VideoCapture") as capture:
            with self.assertRaisesRegex(RuntimeError, "USB"):
                usb_camera.open_webcam("auto")
        capture.assert_not_called()

    def test_auto_skips_unreadable_usb_metadata_nodes(self):
        self.device(32, "usb")
        self.device(33, "usb")
        bad, good = self.capture(), self.capture()
        bad.read.return_value = (False, None)
        with patch.object(usb_camera.cv2, "VideoCapture", side_effect=[bad, good]):
            cap, _, index = usb_camera.open_webcam("auto")
        self.assertIs(cap, good)
        self.assertEqual(index, 33)
        bad.release.assert_called_once()

    def test_explicit_index_is_respected_and_non_usb_is_rejected(self):
        self.device(0, "pci")
        self.device(32, "usb")
        with patch.object(usb_camera.cv2, "VideoCapture", return_value=self.capture()) as capture:
            self.assertEqual(usb_camera.open_webcam(32)[2], 32)
            with self.assertRaisesRegex(RuntimeError, "USB"):
                usb_camera.open_webcam(0)
        capture.assert_called_once_with(32)

    def test_empty_frames_and_opencv_errors_release_cameras(self):
        self.device(32, "usb")
        for result in ((False, None), (True, None), (True, np.empty((0, 0, 3), dtype=np.uint8))):
            with self.subTest(result=result):
                cap = self.capture()
                cap.read.return_value = result
                with patch.object(usb_camera.cv2, "VideoCapture", return_value=cap):
                    with self.assertRaisesRegex(RuntimeError, "USB"):
                        usb_camera.open_webcam("auto")
                cap.release.assert_called_once()
        cap = self.capture()
        cap.read.side_effect = cv2.error("disconnected")
        with patch.object(usb_camera.cv2, "VideoCapture", return_value=cap):
            with self.assertRaisesRegex(RuntimeError, "USB"):
                usb_camera.open_webcam("auto")
        cap.release.assert_called_once()

    def test_device_numbers_are_sorted_numerically(self):
        for index in (10, 2, 8):
            self.device(index, "usb")
        self.assertEqual(usb_camera.usb_camera_indices(), [2, 8, 10])

    def test_windows_keeps_explicit_opencv_camera_number(self):
        with patch.object(usb_camera.sys, "platform", "win32"), \
             patch.object(usb_camera.cv2, "VideoCapture", return_value=self.capture()) as capture:
            self.assertEqual(usb_camera.open_webcam(1)[2], 1)
        capture.assert_called_once_with(1)
