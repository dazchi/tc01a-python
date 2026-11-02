"""Minimal user-space USB Video Class (UVC) streaming on top of libusb.

The Android app streams the TC01A with a bundled libuvc (``libUSBUVCCamera.so``:
``UVCPreview::setPreviewSize`` -> ``uvc_get_stream_ctrl_format_size_fps`` with
the YUYV format, 256x384). We do the same in Python so a single code path
works on Windows (WinUSB), Linux (usbfs) and macOS without the OS camera stack:

* parse the VideoStreaming descriptors to find the 256x384 uncompressed format
* run the PROBE / COMMIT negotiation
* stream via isochronous *or* bulk endpoint (whatever the device exposes)
* reassemble frames from UVC payload headers (FID / EOF bits)
"""
from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

# UVC constants
CC_VIDEO = 0x0E
SC_VIDEOCONTROL = 0x01
SC_VIDEOSTREAMING = 0x02
CS_INTERFACE = 0x24
VC_HEADER = 0x01
VS_FORMAT_UNCOMPRESSED = 0x04
VS_FRAME_UNCOMPRESSED = 0x05
VS_FORMAT_MJPEG = 0x06
VS_FRAME_MJPEG = 0x07

SET_CUR = 0x01
GET_CUR = 0x81
VS_PROBE_CONTROL = 0x01
VS_COMMIT_CONTROL = 0x02

HDR_FID = 0x01
HDR_EOF = 0x02
HDR_ERR = 0x40

ENDPOINT_IN = 0x80
TRANSFER_TYPE_ISO = 1
TRANSFER_TYPE_BULK = 2


@dataclass
class FrameDesc:
    index: int
    width: int
    height: int
    intervals: List[int]           # 100 ns units
    max_frame_size: int


@dataclass
class FormatDesc:
    index: int
    subtype: int
    guid: bytes
    bits_per_pixel: int
    frames: List[FrameDesc] = field(default_factory=list)

    @property
    def fourcc(self) -> str:
        return self.guid[:4].decode("ascii", "replace")


@dataclass
class StreamEndpoint:
    alt: int
    address: int
    transfer_type: int
    max_packet: int                # bytes per (micro)frame incl. high-bandwidth mult


@dataclass
class StreamingInterface:
    number: int
    formats: List[FormatDesc]
    endpoints: List[StreamEndpoint]


def parse_vs_descriptors(extra: List[bytes]) -> List[FormatDesc]:
    """Parse class-specific VideoStreaming descriptors (from alt-setting 0 extra)."""
    formats: List[FormatDesc] = []
    for d in extra:
        d = bytes(d)
        if len(d) < 3 or d[1] != CS_INTERFACE:
            continue
        sub = d[2]
        if sub == VS_FORMAT_UNCOMPRESSED and len(d) >= 27:
            formats.append(FormatDesc(index=d[3], subtype=sub, guid=d[5:21], bits_per_pixel=d[21]))
        elif sub == VS_FORMAT_MJPEG and len(d) >= 11:
            formats.append(FormatDesc(index=d[3], subtype=sub, guid=b"MJPG" + bytes(12), bits_per_pixel=0))
        elif sub in (VS_FRAME_UNCOMPRESSED, VS_FRAME_MJPEG) and len(d) >= 26 and formats:
            idx = d[3]
            width, height = struct.unpack_from("<HH", d, 5)
            max_buf = struct.unpack_from("<I", d, 17)[0]
            n_int = d[25]
            if n_int == 0:          # continuous: min, max, step
                intervals = list(struct.unpack_from("<III", d, 26)) if len(d) >= 38 else []
            else:
                intervals = [struct.unpack_from("<I", d, 26 + 4 * i)[0]
                             for i in range(n_int) if 30 + 4 * i <= len(d)]
            formats[-1].frames.append(FrameDesc(idx, width, height, intervals, max_buf))
    return formats


def _iso_packet_bytes(w_max_packet_size: int) -> int:
    base = w_max_packet_size & 0x7FF
    mult = ((w_max_packet_size >> 11) & 0x3) + 1
    return base * mult


def describe_device(device) -> tuple:
    """Return (vc_interface_number, bcdUVC, StreamingInterface) for a usb1 device."""
    vc_if = None
    bcd_uvc = 0x0100
    vs: Optional[StreamingInterface] = None
    config = next(iter(device.iterConfigurations()))
    for interface in config:
        for setting in interface:
            if setting.getClass() != CC_VIDEO:
                continue
            num = setting.getNumber()
            if setting.getSubClass() == SC_VIDEOCONTROL:
                vc_if = num
                for d in setting.getExtra():
                    d = bytes(d)
                    if len(d) >= 5 and d[1] == CS_INTERFACE and d[2] == VC_HEADER:
                        bcd_uvc = struct.unpack_from("<H", d, 3)[0]
            elif setting.getSubClass() == SC_VIDEOSTREAMING:
                if vs is None or vs.number != num:
                    if vs is not None:
                        continue    # only the first streaming interface
                    vs = StreamingInterface(num, [], [])
                if setting.getAlternateSetting() == 0:
                    extra = list(setting.getExtra())
                    for ep in setting:
                        extra += list(ep.getExtra())
                    vs.formats = parse_vs_descriptors(extra)
                for ep in setting:
                    addr = ep.getAddress()
                    ttype = ep.getAttributes() & 0x3
                    if addr & ENDPOINT_IN and ttype in (TRANSFER_TYPE_ISO, TRANSFER_TYPE_BULK):
                        vs.endpoints.append(StreamEndpoint(setting.getAlternateSetting(), addr, ttype,
                                                           _iso_packet_bytes(ep.getMaxPacketSize())))
    if vc_if is None or vs is None:
        raise RuntimeError("device does not look like a UVC camera")
    return vc_if, bcd_uvc, vs


def probe_length(bcd_uvc: int) -> int:
    if bcd_uvc >= 0x0150:
        return 48
    if bcd_uvc >= 0x0110:
        return 34
    return 26


def build_probe(fmt_index: int, frame_index: int, interval: int, length: int) -> bytearray:
    buf = bytearray(length)
    struct.pack_into("<HBBI", buf, 0, 0x0001, fmt_index, frame_index, interval)  # bmHint: keep dwFrameInterval
    return buf


def parse_probe(buf: bytes) -> dict:
    hint, fmt, frm, interval = struct.unpack_from("<HBBI", buf, 0)
    max_frame, max_payload = struct.unpack_from("<II", buf, 18)
    return dict(hint=hint, format_index=fmt, frame_index=frm, interval=interval,
                max_video_frame_size=max_frame, max_payload_transfer_size=max_payload)


class FrameAssembler:
    """Reassembles video frames from UVC payloads (one payload per iso packet
    or per bulk transfer). Emits only frames whose length == ``frame_size``."""

    def __init__(self, frame_size: int, on_frame: Callable[[bytes], None]):
        self.frame_size = frame_size
        self.on_frame = on_frame
        self._buf = bytearray()
        self._fid: Optional[int] = None
        self._error = False
        self.frames_ok = 0
        self.frames_dropped = 0

    def _finish(self):
        if self._buf:
            if not self._error and len(self._buf) == self.frame_size:
                self.frames_ok += 1
                self.on_frame(bytes(self._buf))
            else:
                self.frames_dropped += 1
        self._buf = bytearray()
        self._error = False

    def feed(self, payload) -> None:
        n = len(payload)
        if n < 2:
            return
        hlen = payload[0]
        if hlen < 2 or hlen > n:
            return                       # not a valid header: ignore packet
        info = payload[1]
        fid = info & HDR_FID
        if self._fid is not None and fid != self._fid and self._buf:
            self._finish()               # FID toggled without EOF
        self._fid = fid
        if info & HDR_ERR:
            self._error = True
        if n > hlen:
            self._buf += payload[hlen:]
            if len(self._buf) > self.frame_size:
                self._error = True
        if info & HDR_EOF:
            self._finish()
        elif len(self._buf) == self.frame_size and not self._error:
            # some firmware never sets EOF; a full frame is a frame
            self._finish()


class UVCStream:
    """Drives an isochronous or bulk UVC stream with libusb async transfers."""

    def __init__(self, context, handle, vs: StreamingInterface, bcd_uvc: int,
                 width: int, height: int, on_frame: Callable[[bytes], None],
                 num_transfers: int = 8, iso_packets: int = 32):
        self.context = context
        self.handle = handle
        self.vs = vs
        self.bcd_uvc = bcd_uvc
        self.width = width
        self.height = height
        self.num_transfers = num_transfers
        self.iso_packets = iso_packets
        self.frame_size = width * height * 2
        self.assembler = FrameAssembler(self.frame_size, on_frame)
        self._transfers = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.alt = 0
        self.negotiated: dict = {}

    # -- negotiation ------------------------------------------------------
    def _select_format(self):
        for fmt in self.vs.formats:
            if fmt.subtype != VS_FORMAT_UNCOMPRESSED:
                continue
            for fr in fmt.frames:
                if fr.width == self.width and fr.height == self.height:
                    return fmt, fr
        avail = [(f.fourcc, fr.width, fr.height) for f in self.vs.formats for fr in f.frames]
        raise RuntimeError(f"{self.width}x{self.height} uncompressed format not offered; device has {avail}")

    def negotiate(self) -> dict:
        fmt, fr = self._select_format()
        interval = fr.intervals[0] if fr.intervals else 400000
        n = probe_length(self.bcd_uvc)
        ctl = build_probe(fmt.index, fr.index, interval, n)
        h, idx = self.handle, self.vs.number
        try:
            h.controlWrite(0x21, SET_CUR, VS_PROBE_CONTROL << 8, idx, bytes(ctl), timeout=1000)
        except Exception:
            if n != 26:   # firmware may lie about bcdUVC: retry UVC 1.0 length
                n = 26
                ctl = build_probe(fmt.index, fr.index, interval, n)
                h.controlWrite(0x21, SET_CUR, VS_PROBE_CONTROL << 8, idx, bytes(ctl), timeout=1000)
            else:
                raise
        cur = h.controlRead(0xA1, GET_CUR, VS_PROBE_CONTROL << 8, idx, n, timeout=1000)
        h.controlWrite(0x21, SET_CUR, VS_COMMIT_CONTROL << 8, idx, bytes(cur), timeout=1000)
        self.negotiated = parse_probe(cur)
        self.negotiated.update(fourcc=fmt.fourcc, width=fr.width, height=fr.height)
        log.info("UVC committed: %s", self.negotiated)
        return self.negotiated

    # -- streaming ---------------------------------------------------------
    def start(self):
        neg = self.negotiate()
        payload = neg["max_payload_transfer_size"] or 0
        iso = [e for e in self.vs.endpoints if e.transfer_type == TRANSFER_TYPE_ISO and e.alt > 0]
        bulk = [e for e in self.vs.endpoints if e.transfer_type == TRANSFER_TYPE_BULK]
        self._running = True
        if iso:
            iso.sort(key=lambda e: e.max_packet)
            ep = next((e for e in iso if e.max_packet >= payload), iso[-1])
            self.alt = ep.alt
            self.handle.setInterfaceAltSetting(self.vs.number, ep.alt)
            log.info("isochronous stream: alt=%d ep=0x%02x packet=%d", ep.alt, ep.address, ep.max_packet)
            for _ in range(self.num_transfers):
                t = self.handle.getTransfer(iso_packets=self.iso_packets)
                t.setIsochronous(ep.address, ep.max_packet * self.iso_packets, callback=self._iso_cb)
                t.submit()
                self._transfers.append(t)
        elif bulk:
            ep = bulk[0]
            size = max(payload, 512)
            size = min(size, 512 * 1024)
            log.info("bulk stream: ep=0x%02x transfer=%d", ep.address, size)
            for _ in range(self.num_transfers):
                t = self.handle.getTransfer()
                t.setBulk(ep.address, size, callback=self._bulk_cb)
                t.submit()
                self._transfers.append(t)
        else:
            raise RuntimeError("no streaming endpoint found")
        self._thread = threading.Thread(target=self._event_loop, name="tc01a-usb", daemon=True)
        self._thread.start()

    def _event_loop(self):
        while self._running or any(t.isSubmitted() for t in self._transfers):
            try:
                self.context.handleEventsTimeout(0.1)
            except Exception as exc:  # pragma: no cover - device unplug etc.
                log.error("libusb event loop: %s", exc)
                break

    def _resubmit(self, t):
        if self._running:
            try:
                t.submit()
            except Exception as exc:  # pragma: no cover
                log.error("resubmit failed: %s", exc)

    def _iso_cb(self, t):
        if t.getStatus() == 0:  # TRANSFER_COMPLETED
            for status, buf in t.iterISO():
                if status == 0 and len(buf):
                    self.assembler.feed(buf)
        self._resubmit(t)

    def _bulk_cb(self, t):
        if t.getStatus() == 0:
            n = t.getActualLength()
            if n:
                self.assembler.feed(t.getBuffer()[:n])
        self._resubmit(t)

    def stop(self):
        self._running = False
        for t in self._transfers:
            try:
                if t.isSubmitted():
                    t.cancel()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        self._transfers = []
        if self.alt:
            try:
                self.handle.setInterfaceAltSetting(self.vs.number, 0)
            except Exception:
                pass
            self.alt = 0
