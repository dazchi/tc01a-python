"""High-level TC01A camera API (libusb backend)."""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import usb1

from . import protocol as P
from .frame import HEIGHT, WIDTH, ThermalFrame
from .measure import MeasureArea, MeasureMixin, MeasurePoint, Measurements  # noqa: F401
from .uvc import UVCStream, describe_device

log = logging.getLogger(__name__)

FLUKE_VID = 0x0F7E
# res/xml/device_filter.xml of Fluke iSee 3.29 (vendor-id 3966 = 0x0F7E)
FLUKE_PIDS = (0x00BC, 0x00E0, 0x00F0, 0x00B1)

HEARTBEAT_PERIOD_S = 20.0   # IRCMD.startHeartbeatTimer: schedule(task, 20000, 20000)


class TC01AError(RuntimeError):
    pass


@dataclass
class DeviceEntry:
    vid: int
    pid: int
    bus: int
    address: int
    product: str = ""
    serial: str = ""


def list_devices(vid: Optional[int] = None, pids=None) -> List[DeviceEntry]:
    out = []
    with usb1.USBContext() as ctx:
        for dev in ctx.getDeviceList(skip_on_error=True):
            v, p = dev.getVendorID(), dev.getProductID()
            if (vid is None and v == FLUKE_VID and p in FLUKE_PIDS) or \
               (vid is not None and v == vid and (pids is None or p in pids)):
                entry = DeviceEntry(v, p, dev.getBusNumber(), dev.getDeviceAddress())
                try:
                    entry.product = dev.getProduct() or ""
                    entry.serial = dev.getSerialNumber() or ""
                except Exception:   # no driver access yet (e.g. Windows without WinUSB)
                    pass
                out.append(entry)
    return out


class TC01A(MeasureMixin):
    """Fluke iSee TC01A thermal camera.

    >>> with TC01A() as cam:
    ...     cam.add_point(128, 96, "center")
    ...     frame = cam.read()
    ...     print(cam.measure(frame).points)
    """

    def __init__(self, vid: int = FLUKE_VID, pid: Optional[int] = None,
                 heartbeat: bool = True, queue_size: int = 2):
        self.vid = vid
        self.pid = pid
        self.heartbeat = heartbeat
        self._ctx: Optional[usb1.USBContext] = None
        self._handle = None
        self._vc_if = 0
        self._vs = None
        self._bcd = 0x0100
        self._stream: Optional[UVCStream] = None
        self._frames: "queue.Queue[ThermalFrame]" = queue.Queue(maxsize=queue_size)
        self._cmd_lock = threading.Lock()
        self._hb_stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        self._claimed: List[int] = []
        self._init_measure()
        self.frame_count = 0

    # ------------------------------------------------------------------ open
    def open(self) -> "TC01A":
        self._ctx = usb1.USBContext()
        self._ctx.open()
        dev = None
        for d in self._ctx.getDeviceList(skip_on_error=True):
            if d.getVendorID() != self.vid:
                continue
            if (self.pid is None and d.getProductID() in FLUKE_PIDS) or d.getProductID() == self.pid:
                dev = d
                break
        if dev is None:
            self._ctx.close()
            raise TC01AError(f"no TC01A found (VID {self.vid:04x}, PIDs "
                             f"{', '.join(f'{p:04x}' for p in ((self.pid,) if self.pid else FLUKE_PIDS))})")
        self.pid = dev.getProductID()
        self._vc_if, self._bcd, self._vs = describe_device(dev)
        try:
            self._handle = dev.open()
        except usb1.USBError as exc:
            self._ctx.close()
            raise TC01AError(
                f"cannot open device ({exc}). Windows: install the WinUSB driver with Zadig; "
                "Linux: install the udev rule (see README); macOS: run with sudo") from exc
        try:
            self._handle.setAutoDetachKernelDriver(True)   # Linux: unbind uvcvideo
        except usb1.USBError:
            pass                                           # not supported on Windows/macOS
        for num in (self._vc_if, self._vs.number):
            self._handle.claimInterface(num)
            self._claimed.append(num)
        log.info("opened %04x:%04x VC=%d VS=%d bcdUVC=%04x", self.vid, self.pid,
                 self._vc_if, self._vs.number, self._bcd)
        if self.heartbeat:
            try:
                self.send(P.handshake())
            except TC01AError as exc:
                log.warning("handshake failed: %s", exc)
            self._hb_stop.clear()
            self._hb_thread = threading.Thread(target=self._heartbeat_loop, name="tc01a-hb", daemon=True)
            self._hb_thread.start()
        return self

    def close(self) -> None:
        self.stop()
        self._hb_stop.set()
        if self._hb_thread:
            self._hb_thread.join(timeout=1)
            self._hb_thread = None
        if self._handle is not None:
            for num in self._claimed:
                try:
                    self._handle.releaseInterface(num)
                except usb1.USBError:
                    pass
            self._claimed = []
            self._handle.close()
            self._handle = None
        if self._ctx is not None:
            self._ctx.close()
            self._ctx = None

    def __enter__(self):
        if self._handle is None:
            self.open()
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def _heartbeat_loop(self):
        while not self._hb_stop.wait(HEARTBEAT_PERIOD_S):
            try:
                self.send(P.handshake())
            except Exception as exc:
                log.warning("heartbeat failed: %s", exc)

    # -------------------------------------------------------------- commands
    def _ctrl_write(self, reg: int, data: bytes):
        self._handle.controlWrite(P.REQ_TYPE_WRITE, P.REQ_WRITE, P.W_VALUE, reg, data, timeout=1000)

    def _ctrl_read(self, reg: int, n: int) -> bytes:
        return bytes(self._handle.controlRead(P.REQ_TYPE_READ, P.REQ_READ, P.W_VALUE, reg, n, timeout=1000))

    def _wait_done(self, tries: int = 1000):
        for _ in range(tries):
            st = self._ctrl_read(P.REG_STATUS, 1)[0]
            res = P.status_is_done(st)
            if res is True:
                return
            if res is False:
                raise TC01AError(f"command failed, status=0x{st:02x}")
        raise TC01AError("command timed out")

    def send(self, cmd: P.Command) -> bytes:
        """Execute one IRCMD transaction and return the response bytes."""
        if self._handle is None:
            raise TC01AError("device not open")
        with self._cmd_lock:
            try:
                if cmd.is_long:
                    self._ctrl_write(P.REG_CMD_LONG, cmd.header)
                    self._ctrl_write(P.REG_PARAM, cmd.params)
                else:
                    self._ctrl_write(P.REG_CMD, cmd.header)
                self._wait_done()
                if cmd.read_len:
                    return self._ctrl_read(cmd.read_reg, cmd.read_len)
                return b""
            except usb1.USBError as exc:
                raise TC01AError(f"USB error: {exc}") from exc

    def device_info(self, kind: P.DeviceInfoType) -> bytes:
        return self.send(P.get_device_info(kind))

    def info(self) -> Dict[str, str]:
        out = {"vid": f"{self.vid:04x}", "pid": f"{self.pid:04x}"}
        for kind in (P.DeviceInfoType.CHIP_ID, P.DeviceInfoType.FW_COMPILE_DATE,
                     P.DeviceInfoType.PROJECT_INFO, P.DeviceInfoType.FW_BUILD_VERSION_INFO,
                     P.DeviceInfoType.PN, P.DeviceInfoType.SN):
            try:
                b = self.device_info(kind)
            except TC01AError as exc:
                out[kind.name.lower()] = f"<error {exc}>"
                continue
            if kind == P.DeviceInfoType.PROJECT_INFO and len(b) >= 4:
                # IRUVC.updatePropertiesOfIR formats it as "%1d.%02d.%02d.%02d" of b[1],b[0],b[2],b[3]
                out["project_info"] = f"{b[1]}.{b[0]:02d}.{b[2]:02d}.{b[3]:02d}"
            elif kind == P.DeviceInfoType.CHIP_ID:
                out["chip_id"] = b.hex()
            else:
                txt = b.split(b"\x00", 1)[0]
                out[kind.name.lower()] = txt.decode("ascii", "replace") if txt.isascii() else b.hex()
        return out

    def shutter(self) -> None:
        """Trigger the shutter / flat-field (NUC) correction."""
        self.send(P.shutter_update(P.ShutterUpdate.B))

    def set_auto_shutter(self, enable: bool) -> None:
        self.send(P.auto_shutter(enable))

    def get_gain(self) -> P.Gain:
        return P.Gain(P.be16(self.send(P.get_tpd_param(P.TPDParam.GAIN_SEL))))

    def set_gain(self, gain: P.Gain) -> None:
        """HIGH = normal range, LOW = high-temperature range."""
        self.send(P.set_tpd_param(P.TPDParam.GAIN_SEL, int(gain)))

    def get_tpd(self, param: P.TPDParam) -> int:
        return P.be16(self.send(P.get_tpd_param(param)))

    def set_tpd(self, param: P.TPDParam, value: int) -> None:
        self.send(P.set_tpd_param(param, value))

    def set_emissivity(self, e: float) -> None:
        """Firmware emissivity (1/128 steps). Applied by the camera to its
        temperature output."""
        v = int(round(e * 128))
        if not 1 <= v <= 128:
            raise ValueError("emissivity must be in (0, 1]")
        self.set_tpd(P.TPDParam.EMS, v)

    def get_emissivity(self) -> float:
        return self.get_tpd(P.TPDParam.EMS) / 128.0

    def sensor_temperature_raw(self) -> int:
        """Current focal-plane 'vtemp' (raw units, as the app logs it)."""
        return P.be16(self.send(P.get_vtemp()))

    def device_point_temp_raw(self, x: int, y: int) -> int:
        """Ask the firmware for one pixel's temperature (raw, 1/16 Kelvin).
        Per-frame values from :meth:`read` are usually more convenient."""
        return P.be16(self.send(P.get_point_temp(x, y)))

    def device_point_temp(self, x: int, y: int) -> float:
        """Firmware point temperature in deg C (matches the frame value)."""
        return self.device_point_temp_raw(x, y) / 16.0 - 273.15

    # ------------------------------------------------------------- streaming
    def start(self) -> None:
        if self._stream is not None:
            return
        if self._handle is None:
            self.open()
        self._stream = UVCStream(self._ctx, self._handle, self._vs, self._bcd,
                                 WIDTH, HEIGHT * 2, self._on_frame)
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream = None

    def _on_frame(self, data: bytes) -> None:
        frame = ThermalFrame(data, time.time())
        self.frame_count += 1
        self.last_frame = frame
        try:
            self._frames.put_nowait(frame)
        except queue.Full:
            try:
                self._frames.get_nowait()       # drop oldest, keep latency low
            except queue.Empty:
                pass
            self._frames.put_nowait(frame)

    def read(self, timeout: float = 3.0) -> ThermalFrame:
        """Block until the next frame arrives."""
        if self._stream is None:
            self.start()
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            raise TC01AError(f"no frame within {timeout}s "
                             f"(ok={self._stream.assembler.frames_ok}, "
                             f"dropped={self._stream.assembler.frames_dropped})") from None

    def frames(self):
        while True:
            yield self.read()

    @property
    def stream_stats(self) -> Tuple[int, int]:
        if self._stream is None:
            return (0, 0)
        return self._stream.assembler.frames_ok, self._stream.assembler.frames_dropped
