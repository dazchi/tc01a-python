import struct

import numpy as np
import pytest

from tc01a.frame import FRAME_BYTES, HALF_BYTES, ThermalFrame, celsius_to_raw
from tc01a.uvc import FrameAssembler, build_probe, parse_probe, parse_vs_descriptors


def make_frame(raw_value=None, celsius=None):
    img = bytes(range(256)) * (HALF_BYTES // 256)
    if celsius is not None:
        raw_value = celsius_to_raw(celsius)
    raw = np.full((192, 256), raw_value, dtype="<u2")
    return img + raw.tobytes()


def test_temperature_formula():
    # IRUVC.java: ((lo + hi*256) / 64.0f) - 273.15
    raw = 0x4A2B
    f = ThermalFrame(make_frame(raw))
    assert f.point(0, 0) == pytest.approx(raw / 64.0 - 273.15, abs=1e-3)
    assert f.raw.shape == (192, 256)
    assert f.image.shape == (192, 256)
    assert f.image[0, 0] == 0 and f.image[0, 1] == 2   # Y = even bytes of YUYV


def test_measurements_and_area():
    temp = np.full((192, 256), 20.0)
    temp[50, 100] = 80.0
    temp[150, 10] = -5.0
    f = ThermalFrame.synthetic(temp)
    assert f.point(100, 50) == pytest.approx(80.0, abs=0.02)
    st = f.stats()
    assert st.max_pos == (100, 50) and st.min_pos == (10, 150)
    a = f.area(90, 40, 110, 60)
    assert a.max == pytest.approx(80.0, abs=0.02) and a.min == pytest.approx(20.0, abs=0.02)
    assert f.point_avg(100, 50, 1) == pytest.approx((80 + 8 * 20) / 9, abs=0.02)


def test_assembler_eof_and_fid():
    got = []
    data = make_frame(celsius=25)
    asm = FrameAssembler(FRAME_BYTES, got.append)
    chunk = 3060
    fid = 0
    for _ in range(2):
        for i in range(0, len(data), chunk):
            last = i + chunk >= len(data)
            asm.feed(bytes([12, 0x80 | fid | (0x02 if last else 0)]) + bytes(10) + data[i:i + chunk])
        fid ^= 1
    assert len(got) == 2 and got[0] == data
    # truncated frame (FID toggles early) is dropped, not emitted
    asm.feed(bytes([2, fid]) + data[:1000])
    asm.feed(bytes([2, fid ^ 1]) + data[:1000])
    assert asm.frames_dropped == 1 and len(got) == 2


def test_assembler_error_bit_drops_frame():
    got = []
    data = make_frame(celsius=25)
    asm = FrameAssembler(FRAME_BYTES, got.append)
    asm.feed(bytes([2, 0x40]) + data[:1000])
    asm.feed(bytes([2, 0x02]) + data[1000:])
    assert got == [] and asm.frames_dropped == 1


def test_vs_descriptor_parsing():
    fmt = bytes([27, 0x24, 0x04, 1, 1]) + b"YUY2" + bytes([0, 0, 0x10, 0, 0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71]) \
        + bytes([16, 1, 0, 0, 0, 0])
    frm = bytearray([30, 0x24, 0x05, 1, 0])
    frm += struct.pack("<HHIII", 256, 384, 0, 0, FRAME_BYTES) + struct.pack("<I", 400000) + bytes([1])
    frm += struct.pack("<I", 400000)
    formats = parse_vs_descriptors([fmt, bytes(frm)])
    assert formats[0].fourcc == "YUY2" and formats[0].bits_per_pixel == 16
    fr = formats[0].frames[0]
    assert (fr.width, fr.height, fr.intervals, fr.max_frame_size) == (256, 384, [400000], FRAME_BYTES)


def test_probe_roundtrip():
    p = build_probe(1, 2, 400000, 26)
    struct.pack_into("<II", p, 18, FRAME_BYTES, 3072)
    d = parse_probe(bytes(p))
    assert d["format_index"] == 1 and d["frame_index"] == 2 and d["max_payload_transfer_size"] == 3072


def test_negotiate_probe_commit():
    from tc01a.uvc import FormatDesc, FrameDesc, StreamingInterface, UVCStream

    class H:
        def __init__(self):
            self.log = []

        def controlWrite(self, rt, req, val, idx, data, timeout=0):
            self.log.append(("W", rt, req, val, idx, bytes(data)))

        def controlRead(self, rt, req, val, idx, n, timeout=0):
            self.log.append(("R", rt, req, val, idx, n))
            b = bytearray(self.log[0][5])
            struct.pack_into("<II", b, 18, FRAME_BYTES, 3072)
            return bytes(b)

    fmt = FormatDesc(1, 0x04, b"YUY2" + bytes(12), 16, [FrameDesc(1, 256, 384, [400000], FRAME_BYTES)])
    h = H()
    s = UVCStream(None, h, StreamingInterface(1, [fmt], []), 0x0100, 256, 384, lambda f: None)
    neg = s.negotiate()
    assert h.log[0][:5] == ("W", 0x21, 0x01, 0x0100, 1) and len(h.log[0][5]) == 26
    assert h.log[1][:5] == ("R", 0xA1, 0x81, 0x0100, 1)
    assert h.log[2][:5] == ("W", 0x21, 0x01, 0x0200, 1)
    assert neg["max_payload_transfer_size"] == 3072 and neg["width"] == 256
