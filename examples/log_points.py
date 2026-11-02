"""Log measure-point temperatures to CSV once per second.

    python examples/log_points.py 128,96 40,150 > temps.csv
"""
import sys
import time

from tc01a import TC01A


def main():
    pts = [tuple(int(v) for v in a.split(",")) for a in sys.argv[1:]] or [(128, 96)]
    with TC01A() as cam:
        labels = [cam.add_point(x, y, radius=1) for x, y in pts]
        print("time," + ",".join(labels) + ",min,max")
        while True:
            frame = cam.read()
            m = cam.measure(frame)
            st = frame.stats()
            print(f"{time.time():.1f}," + ",".join(f"{m.points[k]:.2f}" for k in labels)
                  + f",{st.min:.2f},{st.max:.2f}", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
