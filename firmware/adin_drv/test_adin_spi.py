# test_adin_spi.py -- host-side unit tests for the portable ADIN1110
# protocol core (adin_spi.py) and the pure helpers in s4_first_light.py /
# s5_frame_tx.py. Hardware paths (HAL, reset timing) are covered by the
# manual sprint runs.
#
# Run:  python3 firmware/adin_drv/test_adin_spi.py

import struct
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adin_regs as regs
from adin_spi import (build_read_frame, parse_read_value, build_write_frame,
                      round_len, tx_padded_len, build_tx_burst, mdio_c22_cmd,
                      AdinSpi, AdinError)
from s4_first_light import classify_rx, verdict_line, hexdump
from s5_frame_tx import build_eth_frame, DST_MAC, SRC_MAC, ETHERTYPE, MAGIC


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

    def delay_ms(self, ms):
        pass


class SeqHal:
    """Records all TX; answers each 7-byte register READ from a queue of
    u32 values (writes and bursts get a zero buffer back)."""

    def __init__(self, reads=()):
        self.sent = []
        self.reads = list(reads)
        self.delays = 0

    def xfer(self, tx):
        self.sent.append(bytes(tx))
        if len(tx) == regs.RD_HEADER_LEN + regs.REG_LEN and not (tx[0] & regs.WRITE):
            return b"\x00" * 3 + struct.pack(">I", self.reads.pop(0))
        return bytes(len(tx))

    def delay_ms(self, ms):
        self.delays += 1


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

    def test_all_ff_blames_power_cs_or_miso(self):
        msg = classify_rx(b"\x00\x00\x00\xFF\xFF\xFF\xFF")
        self.assertIn("nothing driving MISO", msg)
        self.assertIn("unpowered", msg)

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


# --- S5: TX FIFO burst building (expected values hand-derived from
# adin1110.c:281-292 round_len and :369-424 write_fifo) ---------------------

class TestTxGeometry(unittest.TestCase):
    def test_round_len_multiples_of_four(self):
        self.assertEqual([round_len(n) for n in (1, 4, 61, 62, 64)],
                         [4, 4, 64, 64, 64])

    def test_padded_len_no_padding_at_60_bytes(self):
        # 60 B frame + 4 B FCS = 64 -> no padding; + 2 B port header = 62
        self.assertEqual(tx_padded_len(60), 62)

    def test_padded_len_short_frame_pads_to_64_with_fcs(self):
        # 10 B frame: pad 50 so frame+FCS=64; 10+50+2 = 62
        self.assertEqual(tx_padded_len(10), 62)

    def test_padded_len_large_frame_just_adds_header(self):
        self.assertEqual(tx_padded_len(100), 102)


class TestBuildTxBurst(unittest.TestCase):
    def test_header_and_layout(self):
        frame = bytes(range(100))
        burst, padded = build_tx_burst(frame)
        self.assertEqual(padded, 102)
        self.assertEqual(len(burst), regs.WR_HEADER_LEN + 104)  # round4(102)
        self.assertEqual(burst[0], 0xA0)            # CD|WRITE, TX addr 0x031
        self.assertEqual(burst[1], 0x31)
        self.assertEqual(burst[2:4], b"\x00\x00")   # BE16 port header, port 0
        self.assertEqual(burst[4:104], frame)
        self.assertEqual(burst[104:], b"\x00\x00")  # rounding pad

    def test_short_frame_zero_padded(self):
        frame = b"\xFF" * 10
        burst, padded = build_tx_burst(frame)
        self.assertEqual(padded, 62)
        self.assertEqual(len(burst), regs.WR_HEADER_LEN + 64)
        self.assertEqual(burst[4:14], frame)
        self.assertEqual(burst[14:], bytes(52))     # padding is zeros

    def test_oversize_frame_raises(self):
        with self.assertRaises(AdinError):
            build_tx_burst(bytes(2100))


class TestMdioCmd(unittest.TestCase):
    # Hand-derived from adin1110.c FIELD_PREP layout: ST=1<<28, OP<<26,
    # PRTAD(=1)<<21, DEVAD<<16, DATA[15:0].
    def test_read_cmd(self):
        self.assertEqual(mdio_c22_cmd(regs.MDIO_OP_RD, 0x0D), 0x1C2D0000)

    def test_write_cmd_carries_data(self):
        self.assertEqual(mdio_c22_cmd(regs.MDIO_OP_WR, 0x0E, 0xBEEF),
                         0x142EBEEF)


TRDONE = regs.MDIO_TRDONE


class TestMdioOps(unittest.TestCase):
    def test_mdio_read_returns_low_16_bits(self):
        hal = SeqHal(reads=[TRDONE | 0x1234])
        self.assertEqual(AdinSpi(hal).mdio_read(0x0D), 0x1234)
        self.assertEqual(hal.sent[0],
                         build_write_frame(regs.MDIOACC,
                                           mdio_c22_cmd(regs.MDIO_OP_RD, 0x0D)))
        self.assertEqual(hal.sent[1], build_read_frame(regs.MDIOACC))

    def test_mdio_poll_timeout_raises(self):
        from adin_spi import MDIO_POLL_TRIES
        hal = SeqHal(reads=[0] * MDIO_POLL_TRIES)
        with self.assertRaises(AdinError):
            AdinSpi(hal).mdio_read(0x0D)

    def test_mmd_read_indirect_sequence(self):
        # 3 MDIO writes (CTRL=devad, DATA=reg, CTRL=data-noinc|devad) then
        # one MDIO read; each op polls TRDONE once here.
        hal = SeqHal(reads=[TRDONE, TRDONE, TRDONE, TRDONE | 0xCAFE])
        val = AdinSpi(hal).mmd_read(regs.MMD_VEND1, regs.CRSM_STAT)
        self.assertEqual(val, 0xCAFE)
        cmds = [hal.sent[i] for i in (0, 2, 4, 6)]  # MDIOACC writes
        self.assertEqual(cmds, [
            build_write_frame(regs.MDIOACC,
                              mdio_c22_cmd(regs.MDIO_OP_WR, regs.MII_MMD_CTRL,
                                           regs.MMD_VEND1)),
            build_write_frame(regs.MDIOACC,
                              mdio_c22_cmd(regs.MDIO_OP_WR, regs.MII_MMD_DATA,
                                           regs.CRSM_STAT)),
            build_write_frame(regs.MDIOACC,
                              mdio_c22_cmd(regs.MDIO_OP_WR, regs.MII_MMD_CTRL,
                                           regs.MMD_FUNC_DATA_NOINC
                                           | regs.MMD_VEND1)),
            build_write_frame(regs.MDIOACC,
                              mdio_c22_cmd(regs.MDIO_OP_RD, regs.MII_MMD_DATA)),
        ])

    def test_link_up_reads_twice_latched_low(self):
        # First PMA_STAT1 read returns 0 (latched-low), second shows link.
        hal = SeqHal(reads=[TRDONE, TRDONE, TRDONE, TRDONE | 0x0000,
                            TRDONE, TRDONE, TRDONE,
                            TRDONE | regs.PMA_STAT1_LINK])
        self.assertTrue(AdinSpi(hal).link_up())

    def test_phy_power_up_returns_crsm_stat(self):
        # mmd_write (4 ops) then mmd_read of CRSM_STAT with SFT_PD_RDY clear
        hal = SeqHal(reads=[TRDONE] * 4
                     + [TRDONE, TRDONE, TRDONE,
                        TRDONE | regs.CRSM_SYS_RDY])
        self.assertEqual(AdinSpi(hal).phy_power_up(), regs.CRSM_SYS_RDY)


class TestMacInit(unittest.TestCase):
    def test_sequence_and_tx_space(self):
        # reads: TX_SPACE=0x3FA (-> 2036 B), CONFIG1=0 (for set_bits RMW)
        hal = SeqHal(reads=[0x3FA, 0x0000])
        space = AdinSpi(hal).mac_init()
        self.assertEqual(space, 2036)
        self.assertEqual(hal.sent, [
            build_write_frame(regs.CONFIG2, regs.CONFIG2_CRC_APPEND),
            build_read_frame(regs.TX_SPACE),
            build_read_frame(regs.CONFIG1),
            build_write_frame(regs.CONFIG1, regs.CONFIG1_SYNC),
        ])


class TestSendFrame(unittest.TestCase):
    def test_immediate_send(self):
        frame = bytes(100)
        hal = SeqHal(reads=[0x800])            # 4096 B free
        stalls = AdinSpi(hal).send_frame(frame)
        self.assertEqual(stalls, 0)
        burst, padded = build_tx_burst(frame)
        self.assertEqual(hal.sent, [
            build_read_frame(regs.TX_SPACE),
            build_write_frame(regs.TX_FSIZE, padded),
            burst,
        ])

    def test_stalls_until_space(self):
        hal = SeqHal(reads=[0, 0, 0x800])
        stalls = AdinSpi(hal).send_frame(bytes(100))
        self.assertEqual(stalls, 2)
        self.assertEqual(hal.delays, 2)

    def test_fifo_full_timeout_raises(self):
        hal = SeqHal(reads=[0, 0, 0, 0])
        with self.assertRaises(AdinError):
            AdinSpi(hal).send_frame(bytes(100), max_wait_ms=3)


class TestBuildEthFrame(unittest.TestCase):
    def test_layout(self):
        f = build_eth_frame(7)
        self.assertEqual(len(f), 500)
        self.assertEqual(f[0:6], DST_MAC)
        self.assertEqual(f[6:12], SRC_MAC)
        self.assertEqual(f[12:14], struct.pack(">H", ETHERTYPE))
        self.assertEqual(f[14:18], MAGIC)
        self.assertEqual(f[18:22], struct.pack(">I", 7))

    def test_pad_is_deterministic(self):
        self.assertEqual(build_eth_frame(1)[22:], build_eth_frame(2)[22:])

    def test_seq_changes_only_seq_field(self):
        a, b = build_eth_frame(0), build_eth_frame(0xFFFFFFFF)
        self.assertNotEqual(a[18:22], b[18:22])
        self.assertEqual(a[:18], b[:18])


if __name__ == "__main__":
    unittest.main()
