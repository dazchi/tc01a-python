"""Interactive OpenCV viewer.

Mouse:  left-click = add measure point, drag = add area, right-click = remove nearest
Keys:   p = next palette   a = toggle AGC image / temperature mapping
        r / R = rotate view 90 deg clockwise / counter-clockwise
        s = shutter (NUC)  g = toggle gain range   c = clear measurements
        f = save snapshot (PNG + .npy raw)        q / Esc = quit

Rotation only changes the display: measure points, areas and saved .npy data
stay in sensor coordinates (x 0..255, y 0..191).
"""
from __future__ import annotations

import time
from typing import Optional

from .render import PALETTES, colorize, display_size, display_to_sensor, rotate_image, sensor_to_display

# Default view orientation, clockwise degrees.
DEFAULT_ROTATION = 270


def _fmt(t: float, unit: str) -> str:
    return f"{t * 9 / 5 + 32:.1f}F" if unit == "F" else f"{t:.1f}C"


def run_viewer(source, scale: int = 3, palette: str = "ironbow", unit: str = "C",
               show_minmax: bool = True, window: str = "TC01A", rotate: Optional[int] = None) -> None:
    import cv2

    palettes = list(PALETTES)
    state = dict(palette=palettes.index(palette), agc=False, drag=None, cur=None, rot=(DEFAULT_ROTATION if rotate is None else rotate) % 360)
    display_size(state["rot"])          # validates the angle
    points, areas = source.points, source.areas

    def to_sensor(x, y):
        dw, dh = display_size(state["rot"])
        dx, dy = min(dw - 1, max(0, x // scale)), min(dh - 1, max(0, y // scale))
        return display_to_sensor(dx, dy, state["rot"])

    def to_screen_rect(x0, y0, x1, y1):
        """Sensor rectangle (inclusive corners) -> screen pixel corners."""
        a = sensor_to_display(x0, y0, state["rot"])
        b = sensor_to_display(x1, y1, state["rot"])
        return ((min(a[0], b[0]) * scale, min(a[1], b[1]) * scale),
                ((max(a[0], b[0]) + 1) * scale, (max(a[1], b[1]) + 1) * scale))

    def on_mouse(event, x, y, flags, _):
        sx, sy = to_sensor(x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            state["drag"] = (sx, sy)
        elif event == cv2.EVENT_MOUSEMOVE and state["drag"]:
            state["cur"] = (sx, sy)
        elif event == cv2.EVENT_LBUTTONUP and state["drag"]:
            x0, y0 = state["drag"]
            state["drag"] = state["cur"] = None
            if abs(sx - x0) + abs(sy - y0) <= 2:
                source.add_point(sx, sy)
            else:
                source.add_area(x0, y0, sx, sy)
        elif event == cv2.EVENT_RBUTTONDOWN:
            cands = [(abs(p.x - sx) + abs(p.y - sy), k) for k, p in points.items()]
            cands += [(0 if a.x0 <= sx <= a.x1 or a.x1 <= sx <= a.x0 else 999, k)
                      for k, a in areas.items()
                      if min(a.y0, a.y1) <= sy <= max(a.y0, a.y1)]
            if cands:
                d, k = min(cands)
                if d < 15:
                    points.pop(k, None)
                    areas.pop(k, None)

    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window, on_mouse)
    fps, t_last, n = 0.0, time.time(), 0
    try:
        while True:
            frame = source.read()
            n += 1
            now = time.time()
            if now - t_last >= 1.0:
                fps, n, t_last = n / (now - t_last), 0, now
            rgb = colorize(frame, palettes[state["palette"]], use_temperature=not state["agc"])
            rot = state["rot"]
            dw, dh = display_size(rot)
            img = cv2.resize(cv2.cvtColor(rotate_image(rgb, rot), cv2.COLOR_RGB2BGR), (dw * scale, dh * scale),
                             interpolation=cv2.INTER_NEAREST if scale > 3 else cv2.INTER_LINEAR)

            def label(text, x, y, color=(255, 255, 255)):
                # Outline with thickness-1 copies offset by 1 px: a thicker stroke
                # would also widen OpenCV's glyph advance and drift to the right.
                for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
                    cv2.putText(img, text, (x + ox, y + oy), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                                (0, 0, 0), 1, cv2.LINE_AA)
                cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

            def cross(x, y, color):
                x, y = sensor_to_display(x, y, rot)
                cx, cy = x * scale + scale // 2, y * scale + scale // 2
                cv2.drawMarker(img, (cx, cy), color, cv2.MARKER_CROSS, 14, 2)
                return cx, cy

            st = frame.stats()
            if show_minmax:
                cx, cy = cross(*st.max_pos, (0, 0, 255))
                label(_fmt(st.max, unit), cx + 6, cy - 6, (80, 80, 255))
                cx, cy = cross(*st.min_pos, (255, 128, 0))
                label(_fmt(st.min, unit), cx + 6, cy - 6, (255, 200, 80))
            for k, p in list(points.items()):
                t = frame.point_avg(p.x, p.y, p.radius) if p.radius else frame.point(p.x, p.y)
                cx, cy = cross(p.x, p.y, (255, 255, 255))
                label(f"{k} {_fmt(t, unit)}", cx + 6, cy - 6)
            for k, a in list(areas.items()):
                s = frame.area(a.x0, a.y0, a.x1, a.y1)
                p0, p1 = to_screen_rect(a.x0, a.y0, a.x1, a.y1)
                cv2.rectangle(img, p0, p1, (255, 255, 255), 1)
                label(f"{k} max {_fmt(s.max, unit)} avg {_fmt(s.mean, unit)} min {_fmt(s.min, unit)}",
                      p0[0] + 2, p0[1] - 4)
            if state["drag"] and state["cur"]:
                p0, p1 = to_screen_rect(*state["drag"], *state["cur"])
                cv2.rectangle(img, p0, p1, (200, 200, 200), 1)
            label(f"{palettes[state['palette']]}  {'AGC' if state['agc'] else 'TEMP'}  "
                  f"{fps:.1f} fps  {rot}deg", 6, dh * scale - 24)
            label(f"span {_fmt(st.min, unit)}..{_fmt(st.max, unit)}", 6, dh * scale - 8)
            cv2.imshow(window, img)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            try:
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except cv2.error:
                break       # window closed by the user
            if key == ord("p"):
                state["palette"] = (state["palette"] + 1) % len(palettes)
            elif key in (ord("r"), ord("R")):
                state["rot"] = (rot + (90 if key == ord("r") else 270)) % 360
                state["drag"] = state["cur"] = None
            elif key == ord("a"):
                state["agc"] = not state["agc"]
            elif key == ord("c"):
                points.clear()
                areas.clear()
            elif key == ord("s") and hasattr(source, "shutter"):
                source.shutter()
            elif key == ord("g") and hasattr(source, "set_gain"):
                from .protocol import Gain
                g = source.get_gain()
                source.set_gain(Gain.LOW if g == Gain.HIGH else Gain.HIGH)
            elif key == ord("f"):
                stamp = time.strftime("%Y%m%d-%H%M%S")
                cv2.imwrite(f"tc01a-{stamp}.png", img)
                frame.save_raw(f"tc01a-{stamp}.npy")
                print(f"saved tc01a-{stamp}.png / .npy")
    finally:
        try:
            cv2.destroyWindow(window)
        except cv2.error:
            pass    # already closed with the window's X button
