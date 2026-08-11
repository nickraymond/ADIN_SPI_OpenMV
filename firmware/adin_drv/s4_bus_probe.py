# s4_bus_probe.py -- pin-level harness probe, NO SPI (Sprint S4 debug)
#
# For the all-0xFF first-light failure: nothing drives MISO, meaning the
# hat is unpowered, CS never reaches the chip, or the MISO wire is off.
# This script separates those without a logic analyzer, using only the
# hat's own passives:
#
#   1. Rail check: the hat pulls RESET_N to its 3.3V rail through R28
#      100k (DESIGN.md S2 table). Drive P4 low, release it to an input,
#      and read it back: if the hat rail is powered AND the P4 wire is
#      landed, the pull-up lifts the line back to 1 within microseconds.
#      Stays 0 = rail dead or RESET wire not connected.
#   2. MISO float check: read P1 with internal pull-up, then pull-down.
#      Different readings = nobody driving the line (floating). Checked
#      with CS deasserted and asserted.
#
# Run from nereus000:
#   mpremote connect <dev> mount firmware/adin_drv exec "import s4_bus_probe"

import time
import machine


def _read_with_pull(pin_name, pull):
    p = machine.Pin(pin_name, machine.Pin.IN, pull)
    time.sleep_ms(2)
    return p.value()


def _miso_state(label):
    up = _read_with_pull("P1", machine.Pin.PULL_UP)
    dn = _read_with_pull("P1", machine.Pin.PULL_DOWN)
    state = ("DRIVEN %d" % up) if up == dn else "floating"
    print("MISO (P1) %-9s: pull-up=%d pull-down=%d -> %s" %
          (label, up, dn, state))
    return up, dn


def main():
    print("S4 bus probe -- no SPI, pin-level checks only")

    cs = machine.Pin("P3", machine.Pin.OUT, value=1)

    # --- 1. rail check via the hat's RESET_N pull-up -------------------
    rst = machine.Pin("P4", machine.Pin.OUT, value=0)
    time.sleep_ms(5)
    rst = machine.Pin("P4", machine.Pin.IN)          # release, no pull
    time.sleep_ms(2)
    immediate = rst.value()
    time.sleep_ms(10)
    settled = rst.value()
    if immediate == 1 and settled == 1:
        print("RESET_N bounce-back  : 1 -> hat 3.3V rail is UP and the "
              "P4 wire is landed")
    else:
        print("RESET_N bounce-back  : %d/%d -> hat rail UNPOWERED or "
              "RESET wire (P4 -> hat 11) not connected -- meter the "
              "power jumpers / recount header pins" % (immediate, settled))

    # --- 2. MISO drive check -------------------------------------------
    _miso_state("CS high")
    cs.value(0)
    time.sleep_ms(2)
    _miso_state("CS low")
    cs.value(1)

    print("interpretation:")
    print("  rail UP + MISO floating both ways -> suspect CS wire "
          "(P3 -> hat 24) or MISO wire (P1 -> hat 21)")
    print("  rail DOWN -> fix power first; re-meter hat pin 17 vs pin 6")

    # leave the bus in a sane state: CS high, chip held out of reset
    machine.Pin("P4", machine.Pin.OUT, value=1)


main()
