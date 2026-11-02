# TC01A reverse-engineering findings

Source: `Fluke-iSee_3.29_apkcube.apks` → `base.apk` (jadx 1.5.1) and
`split_config.arm64_v8a.apk` native libraries (Ghidra 11.4.2 headless).
Tags: **[D]** read in decompiler, **[J]** read in jadx Java, **[I]** inferred.

## Identity

| Item | Value | Source |
|---|---|---|
| USB VID | `0x0F7E` (3966) | `res/xml/device_filter.xml` [J] |
| USB PIDs accepted by app | `0x00BC`, `0x00E0`, `0x00F0`, `0x00B1` | same [J] |
| SDK | Infisense IRUVC (`com.infisense.iruvc`), `libircmd.so` "libcmd 1.0.0", `libirtemp 0.14.2 alpha` | strings [D] |
| Command type | `IRCMDType.USB_IR_256_384` | `IRUVC.initIRCMD` [J] |
| Resolution | 256×384 YUYV (image + temperature halves) | `MainActivity` L1178, `UVCCamera` default `FRAME_FORMAT_YUYV` [J] |

## Video stream

* Opened through bundled libuvc: `UVCPreview::setPreviewSize` @ `0x128428`
  calls `uvc_get_stream_ctrl_format_size_fps(…, fmt=3 (YUYV) or 9 (MJPEG), w, h, fps)` [D].
  Bandwidth factor 0.6 (`IRUVC.initUVCCamera`) [J].
* Frame split (`IRUVC$HandlerBlockTask$3.run`) [J]:
  * bytes `[0, 98304)` = ISP image, gray = even bytes (Y of YUYV)
  * bytes `[98304, 196608)` = temperature, `((b0 + b1*256)/64) - 273.15` °C
* The app's frame callback treats the trailing byte `== 1` as a "restart USB" flag.
  That byte is appended by the native layer, not sent over USB [J/I].

## Vendor command transport

`UVCCamera::sendCommand` @ `0x1257c0` = `libusb_control_transfer(h, rt, req, wValue, wIndex, data, len, timeout)` [D];
`UVCCamera::manualShut` @ `0x1257d8` shows the concrete call
`(0x41, 0x45, 0x78, 0x1d00, buf, 8, 1000)` [D].

| Register (wIndex) | Use |
|---|---|
| `0x1D00` | 8-byte short command (executes) |
| `0x9D00` | 8-byte long-command header |
| `0x1D08` | 8-byte long-command params (executes) / short-command result |
| `0x1D10` | long-command result |
| `0x0200` | status byte, polled up to 1000× (`check_access_done` @ `0x11eb50`) |

Status rule: bit0 set → busy; bit0 clear and bit1 clear → done; bit0 clear,
bit1 set, value > 3 → error; otherwise keep polling [D].

## Commands implemented

| Function (libircmd.so) | Address | Bytes |
|---|---|---|
| `hand_shake_preview` | `0x1294a4` | short `05 53 00 00 00 00 00 00` |
| `get_device_info` | `0x11ec10` | short `05 84 tt 00 00 00 00 nn`, n from table `@0x111550` = `08 08 08 1a 04 32 30 10 10` |
| `get_prop_tpd_params` | `0x11fcf0` | long `14 85 00 pp 00 00 00 00` / `00…02`, 2 B BE from 0x1D10 |
| `set_prop_tpd_params` | `0x11f968` | long `14 c5 00 pp 00 00 vH vL` / `00…00` |
| `get/set_prop_image_params` | `0x11f4b0` / `0x11f388` | `14 83 …` / `14 c3 …` |
| `set_prop_auto_shutter_params` | `0x11f09c` | `14 c2 00 pp 00 00 vH vL` |
| `ooc_b_update` (shutter/NUC) | `0x12079c` | short `0d c1 tt 00 00 00 00 00` |
| `cur_vtemp_get` | `0x120c5c` | short `0d 8b 00 00 00 00 00 02`, 2 B BE from 0x1D08 |
| `shutter_vtemp_get` | `0x120ad4` | short `0c 84 00 00 00 00 00 02` |
| `tpd_get_point_temp_info` | `0x121ccc` | long `11 86 00 00 xH xL yH yL` (prefix `@0x111540`), result 1/16 K |

TPD value ranges (`IRCMD.setPropTPDParams`) [J]: EMS and TAU 1..128 (×1/128),
TU and TA in Kelvin (230..500 in high gain, 230..900 in low gain), distance 0..25600
(deprecated on project ≥ 3.05).

## SDK init / "private key"

`IRCMD.onCreate` [J]: reads `DEV_INFO_CHIP_ID` twice, reads a "private key" blob
from the device and compares it in Java with `"custom" + "1111FLUKE"`, writing
it if absent. It then calls `hand_shake_preview` and starts a 20 s heartbeat
that repeats the handshake. The key comparison is client-side only. The
library sends only the handshake and heartbeat and **never** reads or writes
the key area, so it doesn't change device flash.

## Hardware verification (Windows 11, WinUSB, 2026-11-02) [R = observed at runtime]

* Device `0F7E:00BC` REV 3101. Firmware `P2_Fluke_V1.02`, compiled 02/06/23,
  project 1.02.00.00, PN `P2STDMD25603211XSWRXX_1170100177` [R].
* Descriptors: VC interface 0 (interrupt EP 0x83), VS interface 1, YUY2
  format 1 with frames 256×192 and 256×384 at 400000×100 ns (25 fps). Iso EP
  0x81 on alt 1–7. Alt 7 = 3072 B/microframe, which equals
  dwMaxPayloadTransferSize [R].
* Streaming: ~24 fps, frames reassemble cleanly (2 dropped at start‑up) [R].
* `tpd_get_point_temp_info` unit is **1/16 K**: raw 4867 → 31.04 °C, the same
  as the frame value at that pixel (1/64 K) [R].
* **WinUSB quirk:** interface‑recipient requests (0x41/0xC1) have the low
  byte of wIndex replaced with the interface number, so 0x1D08 and 0x1D10
  collapse to 0x1D00. Reads then return the echoed command header and long
  commands fail with status 0x0E. Device‑recipient requests (0x40/0xC0) work,
  so the firmware ignores the recipient bits [R].
* Status 0x0E stays latched after a failed command until the next command
  starts [R].

## Not reproduced / unverified

* macOS (Homebrew libusb) confirmed working by the user, 2026-11-03 [R].
* Linux not yet run on hardware [I].
* Whether streaming stops without the heartbeat has not been tested [I].
* Low-gain correction: `TemperatureCorrectionManager` L80 applies
  `LibIRTemp.tempCompensation(t+273.15, privLow, vtemp)` in low gain using
  `readPrivData` calibration blobs. Not ported [J].
