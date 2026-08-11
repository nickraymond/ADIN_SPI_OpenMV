# adin_spi.py -- ADIN1110 generic-SPI (no CRC) protocol core (S4 + S5)
#
# PORTABLE LAYER: nothing in this file may import `machine` or any board
# API (DESIGN.md driver architecture). All hardware access goes through a
# HAL object providing:
#
#   hal.xfer(tx: bytes) -> bytes   full-duplex transfer, CS asserted for
#                                  the whole transaction, len(rx)==len(tx)
#   hal.delay_ms(n)                blocking delay, used by poll loops
#
# Wire format (generic SPI without CRC -- the D13 strap config), taken
# from the vendored driver pi/drivers/adin1110/adin1110.c:
#
#   read  (adin1110.c:195-241): one full-duplex transfer of
#          [CD | reg[12:8], reg[7:0], 0x00 turnaround] + 4 clock-out bytes;
#          register value = last 4 RX bytes, big-endian u32.
#   write (adin1110.c:243-264):
#          [CD | WRITE | reg[12:8], reg[7:0]] + value as big-endian u32.
#   frame TX (adin1110.c:369-424): write TX_FSIZE = frame length + 2-byte
#          port header (+ zero-padding so frame+FCS >= 64), then one burst
#          write to reg TX: [2-byte write header][2-byte port header,
#          BE16 port 0][frame bytes][zero pad], burst payload rounded up
#          to a 4-byte multiple (adin1110.c:281-292).
#   MDIO  (adin1110.c:440-502): clause-22 command into MDIOACC, poll
#          TRDONE. Clause-45 MMD registers are reached with the standard
#          C22 MMD-indirect mechanism (regs 13/14) -- see adin_regs.py.

import struct

import adin_regs as regs

MDIO_POLL_TRIES = 100      # x1 ms; Linux polls to 30 ms (adin1110.c:467-469)
PD_POLL_TRIES = 100        # PHY power-down exit; Linux waits (adin1100.c:203)


class AdinError(Exception):
    """Loud driver failure: message names device, action and register."""


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


def round_len(n):
    """Round a FIFO burst payload up to a 4-byte multiple (adin1110.c:281-292)."""
    return (n + 3) & ~3


def tx_padded_len(frame_len):
    """FIFO size of one TX frame: data + pad-to-64 (incl. FCS) + port header.

    This is the value written to TX_FSIZE (adin1110.c:380-394). The MAC
    appends the 4-byte FCS itself (CONFIG2 CRC_APPEND), so padding targets
    frame + FCS >= 64.
    """
    padding = 0
    if frame_len + regs.FEC_LEN < regs.MIN_FRAME_WITH_FCS:
        padding = regs.MIN_FRAME_WITH_FCS - (frame_len + regs.FEC_LEN)
    return frame_len + padding + regs.FRAME_HEADER_LEN


def build_tx_burst(frame):
    """One SPI burst that pushes an Ethernet frame into the TX FIFO.

    Layout per adin1110.c:398-416: [write header to reg TX][BE16 port
    header = 0][frame][zero pad to round_len]. Returns (burst_bytes,
    padded_len) -- padded_len is what TX_FSIZE must be set to first.
    """
    padded = tx_padded_len(len(frame))
    burst_payload = round_len(padded)
    if burst_payload + regs.RD_HEADER_LEN > regs.MAX_BUFF:
        raise AdinError("adin1110 tx: frame %d B exceeds FIFO burst cap "
                        "%d B -- chunk it smaller" % (len(frame), regs.MAX_BUFF))
    buf = bytearray(regs.WR_HEADER_LEN + burst_payload)
    buf[0] = regs.CD | regs.WRITE | ((regs.TX >> 8) & 0x1F)
    buf[1] = regs.TX & 0xFF
    # buf[2:4] stays zero = BE16 port header, port 0 (adin1110.c:408-411)
    buf[4:4 + len(frame)] = frame
    return bytes(buf), padded


def mdio_c22_cmd(op, regad, data=0):
    """MDIOACC clause-22 command word (adin1110.c:449-452, 486-490)."""
    return (regs.MDIO_ST_C22 | op
            | (regs.PHY_MDIO_ADDR << regs.MDIO_PRTAD_SHIFT)
            | ((regad & 0x1F) << regs.MDIO_DEVAD_SHIFT)
            | (data & regs.MDIO_DATA_MASK))


# ---------------------------------------------------------------- driver core

class AdinSpi:
    """Register access over a HAL. One instance per ADIN1110."""

    def __init__(self, hal):
        self.hal = hal
        # Software TX counters, mirroring the mainline driver's approach
        # (adin1110.c:420-421 -- it never reads hardware count registers).
        self.tx_frames = 0
        self.tx_bytes = 0

    def read_reg(self, reg):
        """Read a 32-bit MAC register. Returns (value, raw_rx_bytes)."""
        rx = self.hal.xfer(build_read_frame(reg))
        return parse_read_value(rx), rx

    def write_reg(self, reg, val):
        """Write a 32-bit MAC register."""
        self.hal.xfer(build_write_frame(reg, val))

    def set_bits(self, reg, mask, val):
        """Read-modify-write, mirroring adin1110_set_bits (adin1110.c:266-279)."""
        cur, _ = self.read_reg(reg)
        self.write_reg(reg, (cur & ~mask) | (val & mask))

    # ------------------------------------------------------------- MDIO / PHY

    def _mdio_poll(self):
        for _ in range(MDIO_POLL_TRIES):
            v, _ = self.read_reg(regs.MDIOACC)
            if v & regs.MDIO_TRDONE:
                return v
            self.hal.delay_ms(1)
        raise AdinError("adin1110 MDIOACC: TRDONE timeout after %d ms -- "
                        "MAC unresponsive; re-check SPI link (PHY ID read)"
                        % MDIO_POLL_TRIES)

    def mdio_read(self, regad):
        """Clause-22 PHY register read via MDIOACC (adin1110.c:440-474)."""
        self.write_reg(regs.MDIOACC, mdio_c22_cmd(regs.MDIO_OP_RD, regad))
        return self._mdio_poll() & regs.MDIO_DATA_MASK

    def mdio_write(self, regad, val):
        """Clause-22 PHY register write via MDIOACC (adin1110.c:476-502)."""
        self.write_reg(regs.MDIOACC, mdio_c22_cmd(regs.MDIO_OP_WR, regad, val))
        self._mdio_poll()

    def mmd_read(self, devad, reg):
        """Clause-45 MMD register read via C22 regs 13/14 (see adin_regs.py)."""
        self.mdio_write(regs.MII_MMD_CTRL, devad)
        self.mdio_write(regs.MII_MMD_DATA, reg)
        self.mdio_write(regs.MII_MMD_CTRL, regs.MMD_FUNC_DATA_NOINC | devad)
        return self.mdio_read(regs.MII_MMD_DATA)

    def mmd_write(self, devad, reg, val):
        """Clause-45 MMD register write via C22 regs 13/14."""
        self.mdio_write(regs.MII_MMD_CTRL, devad)
        self.mdio_write(regs.MII_MMD_DATA, reg)
        self.mdio_write(regs.MII_MMD_CTRL, regs.MMD_FUNC_DATA_NOINC | devad)
        self.mdio_write(regs.MII_MMD_DATA, val)

    def phy_power_up(self):
        """Exit PHY software power-down; returns final CRSM_STAT.

        Mirrors adin_set_powerdown(false) + ready poll (adin1100.c:195-206).
        Done unconditionally so we never have to assume the post-reset
        power-down state.
        """
        self.mmd_write(regs.MMD_VEND1, regs.CRSM_SFT_PD_CNTRL, 0)
        for _ in range(PD_POLL_TRIES):
            st = self.mmd_read(regs.MMD_VEND1, regs.CRSM_STAT)
            if not (st & regs.CRSM_SFT_PD_RDY):
                return st
            self.hal.delay_ms(1)
        raise AdinError("adin1100 PHY: stuck in software power-down "
                        "(CRSM_STAT=0x%04X after %d ms) -- power-cycle the "
                        "hat and retry" % (st, PD_POLL_TRIES))

    def link_up(self):
        """Current PMA link state. PMA_STAT1 is latched-low: read twice."""
        self.mmd_read(regs.MMD_PMAPMD, regs.PMA_STAT1)
        st = self.mmd_read(regs.MMD_PMAPMD, regs.PMA_STAT1)
        return bool(st & regs.PMA_STAT1_LINK)

    def wait_link(self, timeout_ms=10_000, poll_ms=100):
        """Poll for link-up; returns wait in ms or raises loudly."""
        waited = 0
        while True:
            if self.link_up():
                return waited
            if waited >= timeout_ms:
                raise AdinError("adin1100 PHY: no link after %d ms -- check "
                                "the pair is plugged at both J1s and the "
                                "partner interface is up" % timeout_ms)
            self.hal.delay_ms(poll_ms)
            waited += poll_ms

    def status_summary(self):
        """(STATUS0, STATUS1, spi_err) for end-of-run health reporting."""
        s0, _ = self.read_reg(regs.STATUS0)
        s1, _ = self.read_reg(regs.STATUS1)
        return s0, s1, bool(s1 & regs.STATUS1_SPI_ERR)

    # ------------------------------------------------------------- frame TX

    def mac_init(self):
        """Post-reset MAC config for frame TX; returns TX FIFO free bytes.

        Sequence from adin1110_net_open (adin1110.c:884-926), minus IRQ
        unmasking (S5 bite 1 polls, no IRQs) and minus RX MAC filters
        (TX-only bite). CONFIG1 SYNC last, per the same source.
        """
        self.write_reg(regs.CONFIG2, regs.CONFIG2_CRC_APPEND)
        space = self.tx_space_bytes()
        self.set_bits(regs.CONFIG1, regs.CONFIG1_SYNC, regs.CONFIG1_SYNC)
        return space

    def tx_space_bytes(self):
        """Free TX FIFO space in bytes (TX_SPACE value x2, adin1110.c:915)."""
        v, _ = self.read_reg(regs.TX_SPACE)
        return 2 * v

    def send_frame(self, frame, max_wait_ms=100):
        """Push one Ethernet frame (no FCS) into the TX FIFO.

        Polls TX_SPACE until the frame fits (space needed per
        adin1110.c:995: len + port header + internal size header), then
        writes TX_FSIZE and bursts the frame. Returns the number of
        poll stalls (0 = FIFO had room immediately).
        """
        burst, padded = build_tx_burst(frame)
        needed = (len(frame) + regs.FRAME_HEADER_LEN
                  + regs.INTERNAL_SIZE_HEADER_LEN)
        stalls = 0
        while self.tx_space_bytes() < needed:
            stalls += 1
            if stalls > max_wait_ms:
                raise AdinError("adin1110 tx: FIFO full for %d ms (need %d B)"
                                " -- is the link up / partner draining?"
                                % (max_wait_ms, needed))
            self.hal.delay_ms(1)
        self.write_reg(regs.TX_FSIZE, padded)
        self.hal.xfer(burst)
        self.tx_frames += 1
        self.tx_bytes += len(frame)
        return stalls
