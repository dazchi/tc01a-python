"""Synthetic frame source for trying the viewer without hardware."""
from __future__ import annotations

import time

import numpy as np

from .frame import HEIGHT, WIDTH, ThermalFrame
from .measure import MeasureMixin


class SyntheticSource(MeasureMixin):
    def __init__(self, fps: float = 25.0):
        self.fps = fps
        self._init_measure()
        self._t0 = time.time()
        yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
        self._xx, self._yy = xx, yy

    def read(self, timeout: float = 1.0) -> ThermalFrame:
        time.sleep(1.0 / self.fps)
        t = time.time() - self._t0
        cx, cy = 128 + 70 * np.cos(t), 96 + 50 * np.sin(t)
        temp = 22 + 0.02 * self._xx + 45 * np.exp(-((self._xx - cx) ** 2 + (self._yy - cy) ** 2) / 300.0)
        temp -= 8 * np.exp(-((self._xx - 40) ** 2 + (self._yy - 150) ** 2) / 500.0)
        temp += np.random.normal(0, 0.08, temp.shape)
        self.last_frame = ThermalFrame.synthetic(temp, time.time())
        return self.last_frame

    def close(self):
        pass
