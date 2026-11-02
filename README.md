# tc01a — Python driver for the Fluke iSee TC01A thermal camera

Cross-platform (Windows / Linux / macOS) Python library for the Fluke iSee
**TC01A** USB‑C thermal camera. It was reverse engineered from the official
Android app (Fluke iSee 3.29). The camera is a rebadged Infisense 256×192
module that speaks standard **UVC** for video plus Infisense vendor control
transfers.

It talks to the camera through **libusb** only, using the
[`libusb1`](https://pypi.org/project/libusb1/) bindings, which bundle libusb on Windows and macOS. There is no
vendor SDK and no native code to compile.

* Live 256×192 thermal image and a per‑pixel temperature map (°C)
* Measure points (single pixel or averaged) and measure areas (min/max/avg + positions)
* Shutter / NUC, gain range (high / low temperature), emissivity, device info
* Interactive OpenCV viewer: click to add points, drag to add areas
* Both isochronous and bulk UVC streaming are handled; the endpoint type is picked from the descriptors

> **Status:** verified on a real TC01A (`0F7E:00BC`, firmware `P2_Fluke_V1.02`)
> on Windows 11 with WinUSB. Streaming runs at ~24 fps (isochronous, alt 7),
> commands work, and frame temperatures match the camera's own point
> measurement to 0.01 °C. Also confirmed working on macOS with libusb from
> Homebrew. Linux shares the same code path but hasn't been run on hardware yet.

## Install

```bash
pip install -e ".[viewer]"        # from this folder; viewer extra pulls opencv-python
```

## USB driver setup (one time)

The library uses the generic libusb driver stack instead of the OS webcam driver.

| OS | What to do |
|----|------------|
| **Windows** | Run [Zadig](https://zadig.akeo.ie). Choose *Options → List All Devices* and **untick** *Ignore Hubs or Composite Parents*. Select the composite device with USB ID `0F7E xxxx` (the entry *without* "Interface" in its name), choose **WinUSB**, then *Replace Driver*. To undo it, open Device Manager, uninstall the device and tick *delete driver*. |
| **Linux** | Copy `udev/99-tc01a.rules` to `/etc/udev/rules.d/`, then run `sudo udevadm control --reload && sudo udevadm trigger`. The library detaches the `uvcvideo` kernel driver itself while it runs. |
| **macOS** | `brew install libusb`. If opening fails with an access error, run with `sudo`. |

If you can't replace the driver, `tc01a view --opencv <index>` reads video
through the OS webcam driver (Linux V4L2, Windows DirectShow). It is video only:
no shutter, gain or emissivity commands and no heartbeat.

## Command line

```bash
tc01a list                       # find cameras
tc01a info                       # firmware / project info, gain, emissivity
tc01a view --point 128,96        # live viewer with a centre measure point
tc01a snapshot --point 50,60 -o shot   # shot.png + shot.npy (raw 1/64 K)
tc01a demo                       # viewer on synthetic data, no camera needed
```

Viewer controls: **left‑click** adds a point, **drag** adds an area,
**right‑click** removes the nearest one. Keys: `p` palette, `a` AGC image vs
temperature mapping, `r` / `R` rotate 90° clockwise / counter‑clockwise,
`s` shutter/NUC, `g` toggle gain range, `c` clear, `f` save, `q` quit.
The view starts rotated 270° clockwise; pick another start angle with
`tc01a view --rotate 0` (0/90/180/270). Rotation only
affects the display: point coordinates and saved `.npy` data stay in sensor
orientation (x 0–255, y 0–191).

## Python API

```python
from tc01a import TC01A

with TC01A() as cam:                      # opens, handshakes, starts streaming
    cam.add_point(128, 96, "center")
    cam.add_point(40, 150, "pipe", radius=1)   # 3x3 average
    cam.add_area(150, 20, 230, 80, "motor")
    for _ in range(100):
        frame = cam.read()                # ThermalFrame
        m = cam.measure(frame)
        print(m.points["center"], m.areas["motor"].max)

    frame.celsius        # (192, 256) float32 °C
    frame.raw            # (192, 256) uint16, 1/64 Kelvin
    frame.image          # (192, 256) uint8 camera AGC image
    frame.stats()        # min/max/mean + positions

    cam.shutter()                         # flat-field / NUC
    cam.set_emissivity(0.95)
    cam.info()
```

Coordinates are sensor pixels: x 0–255, y 0–191.

## How it works (short)

* **Video**: UVC, YUYV 256×384. The top half is the ISP image, the bottom half
  is 16‑bit little‑endian temperatures, `°C = raw / 64 − 273.15`
  (`IRUVC.java`, image‑processing runnable).
* **Commands**: `bRequest 0x45, wValue 0x0078, wIndex = register` (0x1D00
  short command, 0x9D00 + 0x1D08 long command). Then poll status register
  0x0200 with `bRequest 0x44` and read results from 0x1D08 or 0x1D10. The
  Android SDK uses interface recipient (`0x41/0xC1`), but WinUSB overwrites the
  low byte of `wIndex` for those requests (0x1D08 becomes 0x1D00). The library
  therefore uses device recipient (`0x40/0xC0`), which the firmware accepts
  identically on every OS.
* **Heartbeat**: the SDK sends handshake `05 53 00…` after init and every 20 s.
  This library does the same unless you pass `heartbeat=False`.

See [FINDINGS.md](FINDINGS.md) for the full write‑up with addresses.

## Known gaps

* Hardware‑tested on Windows and macOS; Linux not yet.
* The tested TC01A is PID `0x00BC`. The library also accepts the other Fluke
  PIDs the app lists (`0x00E0, 0x00F0, 0x00B1`); use `--pid` to force one.
* On Windows the `--opencv` fallback through the stock webcam driver didn't
  deliver frames from this camera. Use WinUSB.
* In **low‑gain** (high temperature) mode the app applies an extra software
  correction from per‑device calibration data. This is not reproduced, so
  low‑gain readings may be off. High‑gain (default) readings use the camera's
  own values, as the app does.
* The library never writes the SDK "private key" area, never writes flash
  and never runs calibration commands.

## License

MIT, see [LICENSE](LICENSE). Not affiliated with or endorsed by Fluke or Infisense.
