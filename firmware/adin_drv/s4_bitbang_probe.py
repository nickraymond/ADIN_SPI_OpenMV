# s4_bitbang_probe.py -- PHY ID read with BIT-BANGED SPI (Sprint S4 debug)
#
# Pure-GPIO mode-0 SPI at a few kHz: no machine.SPI anywhere. Separates
# "harness/chip problem" from "machine.SPI peripheral problem":
#
#   reads 0x0283BC91  -> harness + chip + protocol all good; the bug is
#                        in how machine.SPI drives the bus (e.g. the SSI
#                        peripheral pulsing its own hardware SS on P3)
#   still garbage     -> chip-side: strap mode, SCLK/MOSI wires
#
# Run from nereus000:
#   mpremote connect <dev> mount firmware/adin_drv exec "import s4_bitbang_probe"

import time
import machine

from adin_spi import build_read_frame, parse_read_value
import adin_regs as regs


def xfer_byte(sclk, mosi, miso, b):
    """Mode 0, MSB first: data set before rising edge, sampled while high."""
    rx = 0
    for i in range(7, -1, -1):
        mosi.value((b >> i) & 1)
        sclk.value(1)
        rx = (rx << 1) | miso.value()
        sclk.value(0)
    return rx


def read_reg_bitbang(pins, reg):
    sclk, mosi, miso, cs = pins
    tx = build_read_frame(reg)
    cs.value(0)
    rx = bytes(xfer_byte(sclk, mosi, miso, b) for b in tx)
    cs.value(1)
    time.sleep_ms(1)
    return parse_read_value(rx), rx


def main():
    print("S4 bit-bang probe -- pure GPIO SPI, no machine.SPI")
    sclk = machine.Pin("P2", machine.Pin.OUT, value=0)
    mosi = machine.Pin("P0", machine.Pin.OUT, value=0)
    miso = machine.Pin("P1", machine.Pin.IN)            # no pull: true drive
    cs = machine.Pin("P3", machine.Pin.OUT, value=1)
    rst = machine.Pin("P4", machine.Pin.OUT, value=1)

    # reset per adin1110.c timing, bus quiet
    rst.value(0)
    time.sleep_ms(10)
    rst.value(1)
    time.sleep_ms(90)

    for name, reg in (("PHY_ID", regs.PHY_ID), ("PHY_ID again", regs.PHY_ID),
                      ("STATUS0", regs.STATUS0), ("CONFIG1", regs.CONFIG1)):
        val, rx = read_reg_bitbang((sclk, mosi, miso, cs), reg)
        print("%-13s: 0x%08X  raw %s" %
              (name, val, " ".join("%02X" % b for b in rx)))

    val, _ = read_reg_bitbang((sclk, mosi, miso, cs), regs.PHY_ID)
    print("verdict: %s" %
          ("PHY ID OK over bit-bang -> suspect machine.SPI peripheral"
           if val == regs.PHY_ID_VAL else
           "still wrong over bit-bang -> chip-side: straps or SCLK/MOSI wires"))


main()
