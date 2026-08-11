# test_adin_spi.py -- host-side unit tests for the portable ADIN1110
# protocol core (adin_spi.py) and the pure helpers in s4_first_light.py.
# Hardware paths (HAL, reset timing) are covered by the manual S4 run.
#
# Run:  python3 firmware/adin_drv/test_adin_spi.py

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adin_regs as regs
from adin_spi import (build_read_frame, parse_read_value, build_write_frame,
                      AdinSpi)
from s4_first_light import classify_rx, verdict_line, hexdump


# Expected wire bytes below are hand-derived from adin1110.c:195-264
# (CD=0x80, WRITE=0x20, addr split 12:8 / 7:0, value big-endian).

class TestReadFrame(unittest.TestCase):
    def test_phy_id_frame(self):
        self.assertEqual(build_read_frame(regs.PHY_ID),
                         bytes([0x80, 0x01, 0x00, 0, 0, 0, 0]))

    def test_high_address_bits_land_in_first_byte(self):
        self.assertEqual(build_read_frame(0x0A55)[:2], bytes([0x8A, 0x55]))

    def test_length_is_header_plus_reg(self):
        self.assertEqual(len(build_read_frame(0)),
                         regs.RD_HEADER_LEN + regs.REG_LEN)


class TestParseReadValue(unittest.TestCase):
    def test_big_endian_value_after_header(self):
        rx = b"\xFF\xFF\xFF\x02\x83\xBC\x91"
        self.assertEqual(parse_read_value(rx), 0x0283BC91)

    def test_zero(self):
        self.assertEqual(parse_read_value(bytes(7)), 0)


class TestWriteFrame(unittest.TestCase):
    def test_write_sets_write_bit_and_big_endian_value(self):
        self.assertEqual(build_write_frame(regs.PHY_ID, 0x0283BC91),
                         bytes([0xA0, 0x01, 0x02, 0x83, 0xBC, 0x91]))

    def test_length(self):
        self.assertEqual(len(build_write_frame(0, 0)),
                         regs.WR_HEADER_LEN + regs.REG_LEN)


class FakeHal:
    """Records TX frames; answers reads with a canned RX buffer."""

    def __init__(self, rx=None):
        self.sent = []
        self.rx = rx

    def xfer(self, tx):
        self.sent.append(bytes(tx))
        return self.rx if self.rx is not None else bytes(len(tx))


class TestAdinSpi(unittest.TestCase):
    def test_read_reg_round_trip(self):
        hal = FakeHal(rx=b"\x00\x00\x00\x02\x83\xBC\x91")
        val, rx = AdinSpi(hal).read_reg(regs.PHY_ID)
        self.assertEqual(val, regs.PHY_ID_VAL)
        self.assertEqual(hal.sent, [build_read_frame(regs.PHY_ID)])
        self.assertEqual(bytes(rx), b"\x00\x00\x00\x02\x83\xBC\x91")

    def test_write_reg_sends_write_frame(self):
        hal = FakeHal()
        AdinSpi(hal).write_reg(regs.CONFIG1, 0x12345678)
        self.assertEqual(hal.sent, [build_write_frame(regs.CONFIG1, 0x12345678)])


class TestDiagnosticsHelpers(unittest.TestCase):
    def test_all_zero_blames_miso_or_power(self):
        self.assertIn("MISO stuck low", classify_rx(bytes(7)))

    def test_all_ff_blames_cs_or_mosi(self):
        self.assertIn("floating", classify_rx(b"\x00\x00\x00\xFF\xFF\xFF\xFF"))

    def test_header_bytes_do_not_affect_classification(self):
        # Only the 4 value bytes count; junk in the 3 header positions is
        # normal (chip drives status there on real hardware).
        self.assertIn("MISO stuck low",
                      classify_rx(b"\xAA\xBB\xCC\x00\x00\x00\x00"))

    def test_mixed_is_garbled(self):
        self.assertIn("garbled", classify_rx(b"\x00\x00\x00\x02\x83\xBC\x91"))

    def test_verdict_ok(self):
        self.assertEqual(verdict_line(0x0283BC91), "PHY ID: 0x0283BC91 -- OK")

    def test_verdict_mismatch_names_expected(self):
        self.assertIn("MISMATCH", verdict_line(0xDEADBEEF))
        self.assertIn("0x0283BC91", verdict_line(0xDEADBEEF))

    def test_hexdump(self):
        self.assertEqual(hexdump(b"\x00\xAB"), "00 AB")


if __name__ == "__main__":
    unittest.main()
