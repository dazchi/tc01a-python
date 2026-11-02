"""Measure points / areas shared by every frame source."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .frame import HEIGHT, WIDTH, Stats, ThermalFrame


@dataclass
class MeasurePoint:
    x: int
    y: int
    label: str = ""
    radius: int = 0          # 0 = single pixel, otherwise (2r+1)^2 box average


@dataclass
class MeasureArea:
    x0: int
    y0: int
    x1: int
    y1: int
    label: str = ""


@dataclass
class Measurements:
    points: Dict[str, float] = field(default_factory=dict)
    areas: Dict[str, Stats] = field(default_factory=dict)


class MeasureMixin:
    """Adds ``points`` / ``areas`` bookkeeping. Coordinates are sensor pixels
    (x 0..255, y 0..191)."""

    points: Dict[str, MeasurePoint]
    areas: Dict[str, MeasureArea]
    last_frame: Optional[ThermalFrame]

    def _init_measure(self):
        self.points = {}
        self.areas = {}
        self.last_frame = None

    def _next_label(self, prefix: str, existing) -> str:
        i = 1
        while f"{prefix}{i}" in existing:
            i += 1
        return f"{prefix}{i}"

    def add_point(self, x: int, y: int, label: Optional[str] = None, radius: int = 0) -> str:
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            raise ValueError(f"point must be within {WIDTH}x{HEIGHT}")
        label = label or self._next_label("P", self.points)
        self.points[label] = MeasurePoint(int(x), int(y), label, int(radius))
        return label

    def add_area(self, x0: int, y0: int, x1: int, y1: int, label: Optional[str] = None) -> str:
        label = label or self._next_label("A", self.areas)
        self.areas[label] = MeasureArea(int(x0), int(y0), int(x1), int(y1), label)
        return label

    def remove(self, label: str) -> None:
        self.points.pop(label, None)
        self.areas.pop(label, None)

    def clear_measurements(self) -> None:
        self.points.clear()
        self.areas.clear()

    def measure(self, frame: Optional[ThermalFrame] = None) -> Measurements:
        frame = frame or self.last_frame or self.read()
        m = Measurements()
        for lbl, p in self.points.items():
            m.points[lbl] = frame.point_avg(p.x, p.y, p.radius) if p.radius else frame.point(p.x, p.y)
        for lbl, a in self.areas.items():
            m.areas[lbl] = frame.area(a.x0, a.y0, a.x1, a.y1)
        return m
