"""Cross-platform driver for the Fluke iSee TC01A USB thermal camera."""
from .camera import FLUKE_PIDS, FLUKE_VID, TC01A, TC01AError, list_devices
from .frame import HEIGHT, WIDTH, ThermalFrame, raw_to_celsius
from .protocol import DeviceInfoType, Gain, ImageParam, TPDParam
from .render import PALETTES, colorize

__all__ = [
    "TC01A", "TC01AError", "list_devices", "FLUKE_VID", "FLUKE_PIDS",
    "ThermalFrame", "raw_to_celsius", "WIDTH", "HEIGHT",
    "DeviceInfoType", "Gain", "ImageParam", "TPDParam",
    "PALETTES", "colorize",
]
__version__ = "0.1.0"
