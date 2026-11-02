import numpy as np
import pytest

from tc01a.frame import HEIGHT, WIDTH
from tc01a.render import display_size, display_to_sensor, rotate_image, sensor_to_display


@pytest.mark.parametrize("deg", [0, 90, 180, 270])
def test_transforms_match_rotated_pixels(deg):
    img = np.arange(WIDTH * HEIGHT, dtype=np.int64).reshape(HEIGHT, WIDTH)
    rot = rotate_image(img, deg)
    dw, dh = display_size(deg)
    assert rot.shape == (dh, dw)
    for x, y in [(0, 0), (255, 0), (0, 191), (255, 191), (17, 120)]:
        dx, dy = sensor_to_display(x, y, deg)
        assert rot[dy, dx] == img[y, x]                     # overlay lands on the right pixel
        assert display_to_sensor(dx, dy, deg) == (x, y)     # click maps back


def test_clockwise_direction():
    # top-left sensor pixel appears top-right after a 90 deg clockwise turn
    assert sensor_to_display(0, 0, 90) == (HEIGHT - 1, 0)


def test_invalid_angle():
    with pytest.raises(ValueError):
        display_size(45)
