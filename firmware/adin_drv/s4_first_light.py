# s4_first_light.py -- Sprint S4 demo: read the ADIN1110 PHY ID over SPI
#
# Rig (D18): AE3 -> AOS hat #2 via 7-wire data harness; hat powered from
# nereus000's 3V3 header. Expected output:
#
#     PHY ID: 0x0283BC91 -- OK
#
# On mismatch this script IS the fallback ladder (no logic analyzer on the
# bench, S2 descope): raw byte dump + failure-signature hint, read
# stability check, raw STATUS0/CONFIG1 dump for comparison against the
# live Linux node (nereus001), and a clock-speed retry sweep.
#
# Run from nereus000:
#   mpremote connect <dev> mount firmware/adin_drv exec "import s4_first_light"
# or open in the OpenMV IDE and press run.

try:
    import machine  # noqa: F401 -- presence check only
    ON_TARGET = True
except ImportError:
    ON_TARGET = False   # host CPython: pure helpers importable for unit tests

import adin_regs as regs
from adin_spi import AdinSpi

RETRY_BAUDS = (2_000_000, 1_000_000)


# ---------------------------------------------------------------- pure helpers

def hexdump(buf):
    return " ".join("%02X" % b for b in buf)


def classify_rx(rx):
    """Map a bad read's RX bytes to the most likely wiring suspect."""
    body = bytes(rx[regs.RD_HEADER_LEN:])
    if all(b == 0x00 for b in body):
        return ("all-0x00: MISO stuck low -- check hat power (3V3/GND "
                "jumpers from the Pi), MISO wire (P1 -> hat 21), or the "
                "chip held in reset (P4 -> hat 11)")
    if all(b == 0xFF for b in body):
        return ("all-0xFF: MISO floating/stuck high -- check CS wire "
                "(P3 -> hat 24) and MOSI wire (P0 -> hat 19)")
    return ("garbled (mixed bytes): activity on MISO but wrong data -- "
            "check SCLK wire (P2 -> hat 23), wire lengths/ground return, "
            "or retry at a lower clock (done below)")


def verdict_line(val):
    ok = val == regs.PHY_ID_VAL
    return "PHY ID: 0x%08X -- %s" % (val, "OK" if ok else
                                     "MISMATCH (expected 0x%08X)" % regs.PHY_ID_VAL)


# ---------------------------------------------------------------- target main

def diagnose(hal, adin):
    print("\n--- diagnostics (no LA on bench; compare against nereus001) ---")
    val, rx = adin.read_reg(regs.PHY_ID)
    print("raw RX bytes : %s" % hexdump(rx))
    print("suspect      : %s" % classify_rx(rx))
    print("INT_N level  : %d (1 = pulled up / idle)" % hal.irq_level())

    # Read stability: same value 5x in a row, or noise?
    vals = set()
    for _ in range(5):
        v, _ = adin.read_reg(regs.PHY_ID)
        vals.add(v)
    print("5x re-read   : %s" %
          ("stable" if len(vals) == 1 else "UNSTABLE %s" %
           ["0x%08X" % v for v in sorted(vals)]))

    # Raw dumps of two more registers -- no expected values asserted here
    # (defaults not verified from a source); diff them against the live
    # Linux node if needed.
    for name, reg in (("STATUS0", regs.STATUS0), ("CONFIG1", regs.CONFIG1)):
        v, _ = adin.read_reg(reg)
        print("%-12s : 0x%08X" % (name, v))

    for baud in RETRY_BAUDS:
        hal.set_baudrate(baud)
        hal.reset_pulse()
        v, _ = adin.read_reg(regs.PHY_ID)
        print("retry @ %d MHz: %s" % (baud // 1_000_000, verdict_line(v)))


def main():
    from adin_hal_ae3 import Ae3Hal

    print("S4 first light -- ADIN1110 PHY ID over generic SPI (no CRC)")
    hal = Ae3Hal()
    print("SPI %d Hz, CS=%s RESET=%s IRQ=%s (pull-up)" %
          (hal.baudrate, "P3", "P4", "P5"))

    hal.reset_pulse()
    adin = AdinSpi(hal)
    val, _ = adin.read_reg(regs.PHY_ID)
    print(verdict_line(val))

    if val != regs.PHY_ID_VAL:
        diagnose(hal, adin)


if ON_TARGET:
    main()
