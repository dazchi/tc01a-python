"""Encoders vs. byte layouts read out of libircmd.so (addresses in comments)."""
import pytest

from tc01a import protocol as P


def test_handshake():  # hand_shake_preview @ 0x1294a4: local_40 = 0x5305
    c = P.handshake()
    assert c.header == bytes([0x05, 0x53, 0, 0, 0, 0, 0, 0]) and not c.is_long and c.read_len == 0


def test_device_info_lengths():  # get_device_info @ 0x11ec10 + table DAT_00111550
    c = P.get_device_info(P.DeviceInfoType.PROJECT_INFO)
    assert c.header == bytes([0x05, 0x84, 4, 0, 0, 0, 0, 4])
    assert c.read_reg == P.REG_PARAM and c.read_len == 4
    assert P.get_device_info(P.DeviceInfoType.FW_BUILD_VERSION_INFO).read_len == 0x32


def test_tpd_get_set():  # get @ 0x11fcf0 (0x8514), set @ 0x11f968 (0xc514)
    g = P.get_tpd_param(P.TPDParam.GAIN_SEL)
    assert g.header == bytes([0x14, 0x85, 0, 5, 0, 0, 0, 0])
    assert g.params == bytes([0, 0, 0, 0, 0, 0, 0, 2])
    assert g.read_reg == P.REG_RESULT_LONG and g.read_len == 2
    s = P.set_tpd_param(P.TPDParam.EMS, 0x0180)
    assert s.header == bytes([0x14, 0xC5, 0, 3, 0, 0, 0x01, 0x80]) and s.params == bytes(8)


def test_image_and_shutter_params():
    assert P.set_image_param(P.ImageParam.CONTRAST, 7).header == bytes([0x14, 0xC3, 0, 4, 0, 0, 0, 7])
    assert P.get_image_param(P.ImageParam.DDE).header[:4] == bytes([0x14, 0x83, 0, 2])
    assert P.auto_shutter(True).header == bytes([0x14, 0xC2, 0, 0, 0, 0, 0, 1])  # @ 0x11f09c (0xc214)


def test_shutter_and_vtemp():
    assert P.shutter_update().header == bytes([0x0D, 0xC1, 0, 0, 0, 0, 0, 0])   # ooc_b_update / manualShut
    assert P.get_vtemp().header == bytes([0x0D, 0x8B, 0, 0, 0, 0, 0, 2])        # cur_vtemp_get
    assert P.get_shutter_vtemp().header == bytes([0x0C, 0x84, 0, 0, 0, 0, 0, 2])


def test_point_temp():  # tpd_get_point_temp_info @ 0x121ccc, prefix DAT_00111540 = 11 86 00 00
    c = P.get_point_temp(0x00FE, 0x00BF)
    assert c.header == bytes([0x11, 0x86, 0, 0, 0x00, 0xFE, 0x00, 0xBF])
    with pytest.raises(ValueError):
        P.get_point_temp(256, 0)


@pytest.mark.parametrize("status,expected", [
    (0x00, True), (0x01, None), (0x03, None), (0x02, None), (0x06, False), (0x04, True),
])
def test_status_logic(status, expected):  # check_access_done @ 0x11eb50
    assert P.status_is_done(status) is expected
