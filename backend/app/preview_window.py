"""OpenCV GUI entrypoint; receives JPEG frames over a local pipe, never a camera."""

import os
from pathlib import Path
from queue import Empty, Full, Queue
import struct
import sys
import threading

import cv2
import numpy as np

WINDOW_NAME = "DrowsyGuard - Live detection demo"


def main():
    # Some OpenCV wheels point Qt at a font directory they do not ship.
    if sys.platform.startswith("linux") and not Path(os.environ.get("QT_QPA_FONTDIR", "")).is_dir():
        for directory in ("/usr/share/fonts/noto", "/usr/share/fonts/TTF", "/usr/share/fonts/truetype/dejavu"):
            if Path(directory).is_dir():
                os.environ["QT_QPA_FONTDIR"] = directory
                break
    frames = Queue(maxsize=1)
    ended = threading.Event()

    def receive():
        try:
            while not ended.is_set():
                header = sys.stdin.buffer.read(4)
                if len(header) != 4:
                    break
                size = struct.unpack("!I", header)[0]
                if not 0 < size <= 10_000_000:
                    break
                packet = sys.stdin.buffer.read(size)
                if len(packet) != size:
                    break
                frame = cv2.imdecode(np.frombuffer(packet, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                try:
                    frames.put_nowait(frame)
                except Full:
                    try:
                        frames.get_nowait()
                    except Empty:
                        pass
                    frames.put_nowait(frame)
        finally:
            ended.set()

    threading.Thread(target=receive, name="preview-input", daemon=True).start()
    try:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_NAME, 1280, 720)
        displayed = False
        while not ended.is_set():
            try:
                frame = frames.get(timeout=0.02)
                cv2.imshow(WINDOW_NAME, frame)
                displayed = True
            except Empty:
                pass
            if cv2.waitKey(10) & 0xFF in (27, ord("q"), ord("Q")):
                break
            if displayed and cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        ended.set()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
