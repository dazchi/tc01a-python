"""Exercise TC01A.send() against a fake libusb handle (no hardware)."""
import pytest

from tc01a import protocol as P
from tc01a.camera import TC01A, TC01AError


class FakeHandle:
    def __init__(self, statuses=(0,), result=b"\x00\x01"):
        self.log = []
        self.statuses = list(statuses)
        self.result = result

    def controlWrite(self, rt, req, val, idx, data, timeout=0):
        self.log.append(("W", rt, req, val, idx, bytes(data)))
        return len(data)

    def controlRead(self, rt, req, val, idx, n, timeout=0):
        self.log.append(("R", rt, req, val, idx, n))
        if idx == P.REG_STATUS:
            return bytes([self.statuses.pop(0) if self.statuses else 0])
        return self.result[:n]


def cam_with(handle):
    c = TC01A(heartbeat=False)
    c._handle = handle
    return c


def test_long_command_sequence():
    h = FakeHandle(statuses=[1, 1, 0], result=b"\x00\x01")
    cam = cam_with(h)
    assert cam.get_gain() == P.Gain.HIGH
    writes = [e for e in h.log if e[0] == "W"]
    assert writes[0][1:5] == (0x40, 0x45, 0x78, 0x9D00)
    assert writes[1][1:5] == (0x40, 0x45, 0x78, 0x1D08)
    status_polls = [e for e in h.log if e[0] == "R" and e[4] == 0x200]
    assert len(status_polls) == 3 and status_polls[0][1:4] == (0xC0, 0x44, 0x78)
    assert h.log[-1][:5] == ("R", 0xC0, 0x44, 0x78, 0x1D10)


def test_short_command_and_error():
    h = FakeHandle(statuses=[0])
    cam = cam_with(h)
    cam.shutter()
    assert h.log[0] == ("W", 0x40, 0x45, 0x78, 0x1D00, bytes([0x0D, 0xC1, 0, 0, 0, 0, 0, 0]))
    h2 = FakeHandle(statuses=[0x06])
    with pytest.raises(TC01AError):
        cam_with(h2).shutter()


def test_emissivity_scaling():
    h = FakeHandle()
    cam_with(h).set_emissivity(0.95)
    assert h.log[0][5] == bytes([0x14, 0xC5, 0, 3, 0, 0, 0, 122])


def test_measure_points_on_camera():
    from tc01a.frame import ThermalFrame
    import numpy as np
    cam = TC01A(heartbeat=False)
    t = np.full((192, 256), 30.0)
    t[10, 20] = 99.0
    f = ThermalFrame.synthetic(t)
    lbl = cam.add_point(20, 10)
    cam.add_area(0, 0, 50, 50, "box")
    m = cam.measure(f)
    assert m.points[lbl] == pytest.approx(99.0, abs=0.02)
    assert m.areas["box"].max_pos == (20, 10)
    with pytest.raises(ValueError):
        cam.add_point(300, 10)
