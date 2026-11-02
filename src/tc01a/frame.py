"""Thermal frame decoding and measurement.

Frame layout (``IRUVC.java``, ``imageOrTempDataLength = 98304``): the camera
delivers 256x384 YUYV. The top 256x192 half is the ISP image (Y = byte 0 of
each pixel pair), the bottom half is 256x192 little-endian uint16 temperature
in 1/64 Kelvin:

    celsius = (lo + hi * 256) / 64 - 273.15
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

WIDTH = 256
HEIGHT = 192
HALF_BYTES = WIDTH * HEIGHT * 2          # 98304
FRAME_BYTES = HALF_BYTES * 2             # 196608 (256 x 384 YUYV)
KELVIN_OFFSET = 273.15


def raw_to_celsius(raw: np.ndarray) -> np.ndarray:
    return raw.astype(np.float32) / 64.0 - KELVIN_OFFSET


def celsius_to_raw(c: float) -> int:
    return int(round((c + KELVIN_OFFSET) * 64.0))


@dataclass
class Stats:
    min: float
    max: float
    mean: float
    min_pos: Tuple[int, int]
    max_pos: Tuple[int, int]


class ThermalFrame:
    """One decoded frame. Coordinates are (x, y) in the 256x192 sensor grid."""

    def __init__(self, data: bytes, timestamp: float = 0.0):
        if len(data) < FRAME_BYTES:
            raise ValueError(f"frame too short: {len(data)} < {FRAME_BYTES}")
        buf = np.frombuffer(data, dtype=np.uint8, count=FRAME_BYTES)
        self.timestamp = timestamp
        self.yuyv = buf[:HALF_BYTES].reshape(HEIGHT, WIDTH, 2)
        self.raw = buf[HALF_BYTES:].view("<u2").reshape(HEIGHT, WIDTH)
        self._celsius: Optional[np.ndarray] = None

    @classmethod
    def synthetic(cls, celsius: np.ndarray, timestamp: float = 0.0) -> "ThermalFrame":
        """Build a frame from a 192x256 temperature array (tests / demos)."""
        celsius = np.asarray(celsius, dtype=np.float64)
        raw = np.clip(np.round((celsius + KELVIN_OFFSET) * 64), 0, 65535).astype("<u2")
        lo, hi = float(celsius.min()), float(celsius.max())
        gray = ((celsius - lo) / max(hi - lo, 1e-6) * 255).astype(np.uint8)
        yuyv = np.stack([gray, np.full_like(gray, 128)], axis=-1)
        return cls(yuyv.tobytes() + raw.tobytes(), timestamp)

    @property
    def image(self) -> np.ndarray:
        """8-bit grayscale ISP image (the camera's own AGC), 192x256."""
        return self.yuyv[:, :, 0]

    @property
    def celsius(self) -> np.ndarray:
        if self._celsius is None:
            self._celsius = raw_to_celsius(self.raw)
        return self._celsius

    def point(self, x: int, y: int) -> float:
        """Temperature (deg C) at sensor pixel (x, y)."""
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            raise ValueError(f"point ({x},{y}) outside {WIDTH}x{HEIGHT}")
        return float(self.celsius[y, x])

    def point_avg(self, x: int, y: int, radius: int = 1) -> float:
        """Mean over a (2r+1)^2 neighbourhood - less noisy than one pixel."""
        x0, x1 = max(0, x - radius), min(WIDTH, x + radius + 1)
        y0, y1 = max(0, y - radius), min(HEIGHT, y + radius + 1)
        return float(self.celsius[y0:y1, x0:x1].mean())

    def area(self, x0: int, y0: int, x1: int, y1: int) -> Stats:
        """Stats over the rectangle [x0,x1] x [y0,y1] (inclusive, any order)."""
        xa, xb = sorted((int(x0), int(x1)))
        ya, yb = sorted((int(y0), int(y1)))
        xa, ya = max(0, xa), max(0, ya)
        xb, yb = min(WIDTH - 1, xb), min(HEIGHT - 1, yb)
        roi = self.celsius[ya:yb + 1, xa:xb + 1]
        imin = np.unravel_index(int(np.argmin(roi)), roi.shape)
        imax = np.unravel_index(int(np.argmax(roi)), roi.shape)
        return Stats(float(roi.min()), float(roi.max()), float(roi.mean()),
                     (xa + int(imin[1]), ya + int(imin[0])),
                     (xa + int(imax[1]), ya + int(imax[0])))

    def stats(self) -> Stats:
        return self.area(0, 0, WIDTH - 1, HEIGHT - 1)

    def save_raw(self, path: str) -> None:
        """Save the temperature plane as .npy (raw uint16, 1/64 K)."""
        np.save(path, self.raw)
