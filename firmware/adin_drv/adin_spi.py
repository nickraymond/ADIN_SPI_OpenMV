# adin_spi.py -- ADIN1110 generic-SPI (no CRC) protocol core (Sprint S4)
#
# PORTABLE LAYER: nothing in this file may import `machine` or any board
# API (DESIGN.md driver architecture). All hardware access goes through a
# HAL object providing:
#
#   hal.xfer(tx: bytes) -> bytes   full-duplex transfer, CS asserted for
#                                  the whole transaction, len(rx)==len(tx)
#
# Wire format (generic SPI without CRC -- the D13 strap config), taken
# from the vendored driver pi/drivers/adin1110/adin1110.c:
#
#   read  (adin1110.c:195-241): one full-duplex transfer of
#          [CD | reg[12:8], reg[7:0], 0x00 turnaround] + 4 clock-out bytes;
#          register value = last 4 RX bytes, big-endian u32.
#   write (adin1110.c:243-264):
#          [CD | WRITE | reg[12:8], reg[7:0]] + value as big-endian u32.

import struct

import adin_regs as regs


# ---------------------------------------------------------------- pure helpers
# (host-unit-tested; keep free of any target dependency)

def build_read_frame(reg):
    """Full 7-byte TX buffer for a register read (header + clock-out zeros)."""
    return bytes((
        regs.CD | ((reg >> 8) & 0x1F),
        reg & 0xFF,
        0x00,                       # turnaround byte
        0x00, 0x00, 0x00, 0x00,     # clocks the 4 value bytes out
    ))


def parse_read_value(rx):
    """Register value from the 7-byte RX buffer of a read transfer."""
    return struct.unpack(">I", rx[regs.RD_HEADER_LEN:regs.RD_HEADER_LEN + regs.REG_LEN])[0]


def build_write_frame(reg, val):
    """6-byte TX buffer for a register write."""
    return bytes((
        regs.CD | regs.WRITE | ((reg >> 8) & 0x1F),
        reg & 0xFF,
    )) + struct.pack(">I", val)


# ---------------------------------------------------------------- driver core

class AdinSpi:
    """Register access over a HAL. One instance per ADIN1110."""

    def __init__(self, hal):
        self.hal = hal

    def read_reg(self, reg):
        """Read a 32-bit MAC register. Returns (value, raw_rx_bytes)."""
        rx = self.hal.xfer(build_read_frame(reg))
        return parse_read_value(rx), rx

    def write_reg(self, reg, val):
        """Write a 32-bit MAC register."""
        self.hal.xfer(build_write_frame(reg, val))
