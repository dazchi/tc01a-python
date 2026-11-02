"""Infisense "IRCMD" vendor command encoding used by the TC01A.

Everything here was recovered from ``libircmd.so`` (Fluke iSee 3.29, arm64) and
``libUSBUVCCamera.so``'s ``UVCCamera::sendCommand`` which is a plain
``libusb_control_transfer`` wrapper. No I/O happens in this module so the
encoders can be unit-tested without hardware.

Transport (all requests use wValue = 0x0078):

* write : bmRequestType 0x41 (we send 0x40), bRequest 0x45, wIndex = register
* read  : bmRequestType 0xC1 (we send 0xC0), bRequest 0x44, wIndex = register

Registers (wIndex):

* 0x1D00  short command (8 bytes) - writing it executes the command
* 0x9D00  long command header (8 bytes); bit 15 = "parameters follow"
* 0x1D08  long command parameters (8 bytes) - writing it executes; also the
          read-back register for short commands
* 0x1D10  read-back register for long commands
* 0x0200  1-byte status: bit0 = busy, bit1 = pending; a value > 3 with bit0
          clear is an error (see ``check_access_done`` @ 0x11eb50)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

# --- USB transport constants -------------------------------------------------
# The Android SDK uses interface recipient (0x41 / 0xC1). WinUSB rewrites the
# low byte of wIndex to the interface number for interface-recipient requests,
# which turns register 0x1D08 into 0x1D00. The firmware ignores the recipient
# bits (verified on hardware), so we address the device instead: identical
# on the wire apart from bmRequestType, and portable to every libusb backend.
REQ_TYPE_WRITE = 0x40  # vendor | host->device | device
REQ_TYPE_READ = 0xC0   # vendor | device->host | device
REQ_TYPE_WRITE_ANDROID = 0x41
REQ_TYPE_READ_ANDROID = 0xC1
REQ_WRITE = 0x45
REQ_READ = 0x44
W_VALUE = 0x0078

REG_CMD = 0x1D00
REG_CMD_LONG = 0x9D00
REG_PARAM = 0x1D08
REG_RESULT_LONG = 0x1D10
REG_STATUS = 0x0200

STATUS_BUSY = 0x01
STATUS_PENDING = 0x02


class DeviceInfoType(IntEnum):
    CHIP_ID = 0
    FW_COMPILE_DATE = 1
    DEV_QUALIFICATION = 2
    IR_INFO = 3
    PROJECT_INFO = 4
    FW_BUILD_VERSION_INFO = 5
    PN = 6
    SN = 7
    SENSOR_ID = 8


# Response length per DeviceInfoType: table DAT_00111550 in libircmd.so
DEVICE_INFO_LENGTH = {0: 8, 1: 8, 2: 8, 3: 0x1A, 4: 4, 5: 0x32, 6: 0x30, 7: 0x10, 8: 0x10}


class TPDParam(IntEnum):
    """Temperature-processing ("TPD") properties, CommonParams.PropTPDParams."""
    DISTANCE = 0   # 0..25600, deprecated on newer firmware
    TU = 1         # reflected temperature, Kelvin (230..500 high gain / 230..900 low gain)
    TA = 2         # ambient temperature, Kelvin
    EMS = 3        # emissivity * 128, 1..128
    TAU = 4        # atmospheric transmittance * 128, 1..128
    GAIN_SEL = 5   # 0 = low gain (high temp range), 1 = high gain (normal range)


class ImageParam(IntEnum):
    """Image properties, CommonParams.PropImageParams."""
    TNR = 0
    SNR = 1
    DDE = 2
    BRIGHTNESS = 3
    CONTRAST = 4
    AGC_MODE = 5
    MAX_GAIN = 6
    BOS = 7
    AGC_ONOFF = 8
    MIRROR_FLIP = 9
    FLYER = 10
    ROI = 11
    DDE_STR = 12


class Gain(IntEnum):
    LOW = 0    # high temperature range
    HIGH = 1   # normal (low temperature) range


class ShutterUpdate(IntEnum):
    """CommonParams.UpdateOOCOrBType - argument of ooc_b_update (0x0d 0xc1)."""
    B = 0
    OOC = 1
    OOC_B = 2


@dataclass(frozen=True)
class Command:
    """One IRCMD transaction.

    ``header`` goes to REG_CMD (short) or REG_CMD_LONG (long); ``params`` (long
    commands only) goes to REG_PARAM. ``read_len`` bytes are then read from
    ``read_reg`` once the status register reports completion.
    """
    header: bytes
    params: Optional[bytes] = None
    read_len: int = 0
    read_reg: int = REG_PARAM

    @property
    def is_long(self) -> bool:
        return self.params is not None


def _short(b0: int, b1: int, b2: int = 0, tail: bytes = b"\x00" * 5) -> bytes:
    out = bytes([b0, b1, b2]) + tail
    assert len(out) == 8
    return out


# --- encoders (one per libircmd JNI function) --------------------------------

def handshake() -> Command:
    """hand_shake_preview @ 0x1294a4. The SDK sends this right after init and
    then every 20 s from a heartbeat timer (IRCMD.startHeartbeatTimer)."""
    return Command(_short(0x05, 0x53))


def get_device_info(info: DeviceInfoType) -> Command:
    """get_device_info @ 0x11ec10: [05 84 type 00 00 00 00 len], read from 0x1D08."""
    n = DEVICE_INFO_LENGTH[int(info)]
    return Command(bytes([0x05, 0x84, int(info), 0, 0, 0, 0, n]), read_len=n)


def get_tpd_param(param: TPDParam) -> Command:
    """getPropTPDParams @ 0x11fcf0. Result: 2 bytes big-endian from 0x1D10."""
    return Command(bytes([0x14, 0x85, 0, int(param), 0, 0, 0, 0]),
                   params=bytes(7) + b"\x02", read_len=2, read_reg=REG_RESULT_LONG)


def set_tpd_param(param: TPDParam, value: int) -> Command:
    """set_prop_tpd_params @ 0x11f968: value is big-endian in header bytes 6..7."""
    if not 0 <= value <= 0xFFFF:
        raise ValueError("value out of range")
    return Command(bytes([0x14, 0xC5, 0, int(param), 0, 0, value >> 8, value & 0xFF]),
                   params=bytes(8))


def get_image_param(param: ImageParam) -> Command:
    """get_prop_image_params @ 0x11f4b0."""
    return Command(bytes([0x14, 0x83, 0, int(param), 0, 0, 0, 0]),
                   params=bytes(7) + b"\x02", read_len=2, read_reg=REG_RESULT_LONG)


def set_image_param(param: ImageParam, value: int) -> Command:
    """set_prop_image_params @ 0x11f388."""
    if not 0 <= value <= 0xFFFF:
        raise ValueError("value out of range")
    return Command(bytes([0x14, 0xC3, 0, int(param), 0, 0, value >> 8, value & 0xFF]),
                   params=bytes(8))


def shutter_update(kind: ShutterUpdate = ShutterUpdate.B) -> Command:
    """ooc_b_update @ 0x12079c - closes the shutter and re-runs NUC (the app's
    "shutterUpdate" uses B_UPDATE)."""
    return Command(_short(0x0D, 0xC1, int(kind)))


def set_auto_shutter_param(param: int, value: int) -> Command:
    """set_prop_auto_shutter_params @ 0x11f09c: [14 c2 00 param 00 00 vH vL].
    param 0 = SHUTTER_PROP_SWITCH (0 off / 1 on), 1/2 = min/max interval, ..."""
    if not 0 <= value <= 0xFFFF:
        raise ValueError("value out of range")
    return Command(bytes([0x14, 0xC2, 0, param, 0, 0, value >> 8, value & 0xFF]),
                   params=bytes(8))


def auto_shutter(enable: bool) -> Command:
    return set_auto_shutter_param(0, 1 if enable else 0)


def get_vtemp() -> Command:
    """cur_vtemp_get @ 0x120c5c: [0d 8b 00 00 00 00 00 02], 2 bytes BE from 0x1D08.
    Sensor (FPA) temperature in raw units, used by the app's correction tables."""
    return Command(bytes([0x0D, 0x8B, 0, 0, 0, 0, 0x00, 0x02]), read_len=2)


def get_shutter_vtemp() -> Command:
    """shutter_vtemp_get @ 0x120ad4."""
    return Command(bytes([0x0C, 0x84, 0, 0, 0, 0, 0x00, 0x02]), read_len=2)


def get_point_temp(x: int, y: int) -> Command:
    """tpd_get_point_temp_info @ 0x121ccc: header [11 86 00 00 xH xL yH yL]
    (prefix from DAT_00111540), params [0]*7+[2], result 2 bytes BE from 0x1D10.
    Coordinates are in the 256x192 sensor frame."""
    if not (0 <= x <= 255 and 0 <= y <= 191):
        raise ValueError("point must be inside 256x192")
    return Command(bytes([0x11, 0x86, 0, 0]) + struct.pack(">HH", x, y),
                   params=bytes(7) + b"\x02", read_len=2, read_reg=REG_RESULT_LONG)


def status_is_done(status: int) -> Optional[bool]:
    """Mirror of the SDK polling loop. Returns True = done, False = error,
    None = keep polling."""
    if status & STATUS_BUSY:
        return None
    if not status & STATUS_PENDING:
        return True
    if status > 3:
        return False
    return None


def be16(data: bytes) -> int:
    return (data[0] << 8) | data[1]
