"""Command line: ``python -m tc01a {list,info,view,snapshot,demo}``."""
from __future__ import annotations

import argparse
import logging
import sys

from .viewer import DEFAULT_ROTATION


def _int(s: str) -> int:
    return int(s, 0)


def _open(args):
    from .camera import TC01A
    cam = TC01A(pid=args.pid, heartbeat=not args.no_heartbeat)
    cam.open()
    return cam


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tc01a", description=__doc__)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--pid", type=_int, default=None, help="force USB product id (e.g. 0xbc)")
    ap.add_argument("--no-heartbeat", action="store_true", help="do not send the SDK handshake/heartbeat")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list connected cameras")
    sub.add_parser("info", help="print firmware / device information")
    v = sub.add_parser("view", help="live viewer with measure points")
    v.add_argument("--scale", type=int, default=3)
    v.add_argument("--palette", default="ironbow")
    v.add_argument("--unit", choices="CF", default="C")
    v.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=DEFAULT_ROTATION,
                   help=f"rotate the view clockwise, default {DEFAULT_ROTATION} (r / R keys rotate live)")
    v.add_argument("--point", action="append", default=[], metavar="X,Y",
                   help="pre-defined measure point in sensor coords (256x192)")
    v.add_argument("--opencv", type=int, metavar="INDEX", default=None,
                   help="use the OS UVC driver via OpenCV (video only, no commands)")
    s = sub.add_parser("snapshot", help="grab one frame, print stats/points, save PNG+NPY")
    s.add_argument("--point", action="append", default=[], metavar="X,Y")
    s.add_argument("-o", "--output", default="tc01a")
    d = sub.add_parser("demo", help="viewer on synthetic data (no camera needed)")
    d.add_argument("--scale", type=int, default=3)
    d.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=DEFAULT_ROTATION)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "list":
        from .camera import list_devices
        devs = list_devices()
        if not devs:
            print("no TC01A found")
            return 1
        for d in devs:
            print(f"bus {d.bus:03d} addr {d.address:03d}  {d.vid:04x}:{d.pid:04x}  {d.product} {d.serial}")
        return 0

    if args.cmd == "info":
        cam = _open(args)
        try:
            for k, val in cam.info().items():
                print(f"{k:24s} {val}")
            try:
                print(f"{'gain':24s} {cam.get_gain().name}")
                print(f"{'emissivity':24s} {cam.get_emissivity():.3f}")
                print(f"{'vtemp(raw)':24s} {cam.sensor_temperature_raw()}")
            except Exception as exc:
                print(f"property read failed: {exc}")
        finally:
            cam.close()
        return 0

    if args.cmd == "demo":
        from .demo import SyntheticSource
        from .viewer import run_viewer
        src = SyntheticSource()
        src.add_point(128, 96, "center")
        run_viewer(src, scale=args.scale, rotate=args.rotate)
        return 0

    if args.cmd == "view":
        from .viewer import run_viewer
        if args.opencv is not None:
            from .opencv_source import OpenCVSource
            src = OpenCVSource(args.opencv)
        else:
            src = _open(args)
            src.start()
        try:
            for i, p in enumerate(args.point):
                x, y = (int(c) for c in p.split(","))
                if hasattr(src, "add_point"):
                    src.add_point(x, y)
            run_viewer(src, scale=args.scale, palette=args.palette, unit=args.unit, rotate=args.rotate)
        finally:
            src.close()
        return 0

    if args.cmd == "snapshot":
        import numpy as np
        from .render import colorize
        cam = _open(args)
        try:
            cam.start()
            for _ in range(5):          # let AGC / stream settle
                frame = cam.read()
            for p in args.point:
                x, y = (int(c) for c in p.split(","))
                cam.add_point(x, y)
            st = frame.stats()
            print(f"min {st.min:.2f}C at {st.min_pos}  max {st.max:.2f}C at {st.max_pos}  mean {st.mean:.2f}C")
            for k, t in cam.measure(frame).points.items():
                print(f"{k} ({cam.points[k].x},{cam.points[k].y}): {t:.2f}C")
            np.save(args.output + ".npy", frame.raw)
            try:
                import cv2
                cv2.imwrite(args.output + ".png", cv2.cvtColor(colorize(frame), cv2.COLOR_RGB2BGR))
                print(f"saved {args.output}.png and {args.output}.npy")
            except ImportError:
                print(f"saved {args.output}.npy (install opencv-python for PNG)")
        finally:
            cam.close()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
