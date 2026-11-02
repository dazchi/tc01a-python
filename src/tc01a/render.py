"""Palette rendering (numpy only; OpenCV optional for drawing)."""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from .frame import HEIGHT, WIDTH, ThermalFrame


def _lut(stops) -> np.ndarray:
    """256x3 uint8 RGB LUT from [(pos 0..1, (r,g,b)), ...]."""
    pos = np.array([s[0] for s in stops])
    cols = np.array([s[1] for s in stops], dtype=np.float64)
    x = np.linspace(0, 1, 256)
    return np.stack([np.interp(x, pos, cols[:, c]) for c in range(3)], axis=1).astype(np.uint8)


PALETTES = {
    "ironbow": _lut([(0, (0, 0, 0)), (0.15, (32, 0, 140)), (0.35, (150, 0, 150)),
                     (0.55, (230, 70, 30)), (0.75, (250, 160, 0)), (0.9, (255, 230, 60)),
                     (1, (255, 255, 255))]),
    "rainbow": _lut([(0, (0, 0, 130)), (0.2, (0, 60, 255)), (0.4, (0, 220, 220)),
                     (0.6, (60, 230, 30)), (0.8, (255, 200, 0)), (1, (230, 0, 0))]),
    "whitehot": _lut([(0, (0, 0, 0)), (1, (255, 255, 255))]),
    "blackhot": _lut([(0, (255, 255, 255)), (1, (0, 0, 0))]),
    "redhot": _lut([(0, (0, 0, 0)), (0.6, (180, 180, 180)), (0.8, (230, 120, 60)), (1, (255, 0, 0))]),
}


ROTATIONS = (0, 90, 180, 270)   # clockwise degrees


def _check_rotation(deg: int) -> int:
    deg %= 360
    if deg not in ROTATIONS:
        raise ValueError("rotation must be a multiple of 90 degrees")
    return deg


def rotate_image(img: np.ndarray, deg: int) -> np.ndarray:
    """Rotate an HxW(xC) image clockwise by ``deg``."""
    return np.ascontiguousarray(np.rot90(img, k=-(_check_rotation(deg) // 90)))


def display_size(deg: int, width: int = WIDTH, height: int = HEIGHT) -> Tuple[int, int]:
    """(width, height) of the frame after rotation."""
    return (height, width) if _check_rotation(deg) in (90, 270) else (width, height)


def sensor_to_display(x: int, y: int, deg: int,
                      width: int = WIDTH, height: int = HEIGHT) -> Tuple[int, int]:
    deg = _check_rotation(deg)
    if deg == 90:
        return height - 1 - y, x
    if deg == 180:
        return width - 1 - x, height - 1 - y
    if deg == 270:
        return y, width - 1 - x
    return x, y


def display_to_sensor(dx: int, dy: int, deg: int,
                      width: int = WIDTH, height: int = HEIGHT) -> Tuple[int, int]:
    deg = _check_rotation(deg)
    if deg == 90:
        return dy, height - 1 - dx
    if deg == 180:
        return width - 1 - dx, height - 1 - dy
    if deg == 270:
        return width - 1 - dy, dx
    return dx, dy


def colorize(frame: ThermalFrame, palette: str = "ironbow",
             span: Optional[Tuple[float, float]] = None, use_temperature: bool = True) -> np.ndarray:
    """Return an RGB uint8 image (192x256x3).

    ``use_temperature`` maps the calibrated temperature plane linearly between
    ``span`` (default: frame min/max); otherwise the camera's own AGC image is used.
    """
    lut = PALETTES[palette]
    if use_temperature:
        t = frame.celsius
        lo, hi = span if span else (float(t.min()), float(t.max()))
        idx = np.clip((t - lo) / max(hi - lo, 1e-3) * 255, 0, 255).astype(np.uint8)
    else:
        idx = frame.image
    return lut[idx]
