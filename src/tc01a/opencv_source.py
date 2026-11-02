"""Video-only fallback through the operating system's UVC driver.

Use this when you cannot (or do not want to) replace the camera driver with
WinUSB / detach uvcvideo. The camera's vendor commands (shutter, gain,
emissivity, heartbeat) are NOT available this way, and raw YUYV access depends
on the OpenCV capture backend (works with V4L2 on Linux and usually with
DirectShow/MSMF on Windows; AVFoundation on macOS converts to RGB and is not
usable).
"""
from __future__ import annotations

import sys
import time

import numpy as np

from .frame import FRAME_BYTES, ThermalFrame
from .measure import MeasureMixin


class OpenCVSource(MeasureMixin):
    def __init__(self, index: int = 0, backend: int = None):
        import cv2
        if backend is None:
            backend = {"win32": cv2.CAP_DSHOW, "linux": cv2.CAP_V4L2}.get(sys.platform, cv2.CAP_ANY)
        self.cap = cv2.VideoCapture(index, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open video device {index}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 256)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 384)
        self.cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        self._init_measure()

    def read(self, timeout: float = 3.0) -> ThermalFrame:
        ok, data = self.cap.read()
        if not ok:
            raise RuntimeError("no frame from OpenCV capture")
        data = np.ascontiguousarray(data).reshape(-1)
        if data.size < FRAME_BYTES:
            raise RuntimeError(f"capture returned {data.size} bytes, expected raw YUYV {FRAME_BYTES}; "
                               "this OpenCV backend converts frames - use the libusb backend")
        self.last_frame = ThermalFrame(data[:FRAME_BYTES].tobytes(), time.time())
        return self.last_frame

    def close(self):
        self.cap.release()
