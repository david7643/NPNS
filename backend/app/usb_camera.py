"""Select directly connected USB webcams and verify real video frames."""

import logging
from pathlib import Path
import sys

import cv2

logger = logging.getLogger(__name__)
VIDEO_SYSFS_ROOT = Path("/sys/class/video4linux")


def usb_camera_indices() -> list[int]:
    indices = []
    for node in VIDEO_SYSFS_ROOT.glob("video*"):
        if not node.name[5:].isdigit():
            continue
        try:
            device = (node / "device").resolve()
            if not device.is_dir():
                continue
            if any((parent / "subsystem").resolve().name == "usb"
                   for parent in (device, *device.parents)):
                indices.append(int(node.name[5:]))
        except OSError:
            continue
    return sorted(indices)


def open_webcam(camera_index: int | str = "auto"):
    """Return (capture, first_frame, index); the caller owns the opened capture."""
    if sys.platform.startswith("linux"):
        available = usb_camera_indices()
        if camera_index == "auto":
            candidates = available
        else:
            if camera_index not in available:
                raise RuntimeError(
                    f"카메라 {camera_index}는 연결된 USB 웹캠이 아닙니다. "
                    "USB 웹캠을 연결하고 DROWSINESS_CAMERA_INDEX=auto로 설정해주세요."
                )
            candidates = [camera_index]
    else:
        # OpenCV camera numbers on Windows can be explicitly selected in .env.
        candidates = [0 if camera_index == "auto" else camera_index]
    if not candidates:
        raise RuntimeError("연결된 USB 웹캠을 찾을 수 없습니다. 서버 PC에 USB 웹캠을 연결해주세요.")

    for index in candidates:
        capture = None
        keep_capture = False
        try:
            capture = cv2.VideoCapture(index)
            if not capture.isOpened():
                continue
            success, frame = capture.read()
            if success and frame is not None and frame.size > 0:
                keep_capture = True
                logger.info("웹캠 프레임 수신 확인: camera=%s", index)
                return capture, frame, index
        except cv2.error:
            logger.warning("웹캠 입력 실패: camera=%s", index, exc_info=True)
        finally:
            if capture is not None and not keep_capture:
                capture.release()
    raise RuntimeError("USB 웹캠의 영상 프레임을 읽을 수 없습니다. 연결 상태와 다른 앱의 카메라 사용 여부를 확인해주세요.")
