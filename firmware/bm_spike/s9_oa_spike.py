# s9_oa_spike.py -- S9 bite 1 runner: the 1110-vs-2111 verify spike.
#
# Requires: custom firmware built by build_spike.sh (bm_spike usermod),
# hat #2 re-strapped to OA (CFG0/CFG1 bridges REMOVED -> default straps =
# OPEN Alliance with protection), S4 harness unchanged (P0-P5).
#
# Run from nereus000:
#   mpremote run firmware/bm_spike/s9_oa_spike.py
#
# Prints the two verdicts (see firmware/bm_spike/README.md for the
# decision matrix) and drives ADIN reset exactly like the proven
# MicroPython driver does (adin_hal_ae3 pin map, D2/D3).

import time
from machine import Pin, SPI

SPI_HZ = 5_000_000      # bring-up speed, same as S4 first light

try:
    import bm_spike
except ImportError:
    print("FAIL: no bm_spike module -- this firmware was not built with")
    print("      build_spike.sh. Flash the spike build first (S7 ladder).")
    raise SystemExit

print("=" * 64)
print("S9 OA spike: bm_core adin2111 driver (UNMODIFIED) vs our ADIN1110")
import sys
print(sys.version)
print("=" * 64)

# Pin map per SPEC (verified S4): P3=CS manual, P4=RESET out, P5=IRQ in.
cs = Pin("P3", Pin.OUT, value=1)
rst = Pin("P4", Pin.OUT, value=1)
irq = Pin("P5", Pin.IN, Pin.PULL_UP)    # D14: board lacks INT_N pull-up

# Hardware reset pulse (datasheet-conservative timings, as in adin_bringup)
rst.value(0)
time.sleep_ms(10)
rst.value(1)
time.sleep_ms(100)

spi = SPI(0, baudrate=SPI_HZ, polarity=0, phase=0)
print("SPI(0) at %d Hz, CS=P3 manual, RESET pulsed, IRQ=P5 pull-up (unused)"
      % SPI_HZ)
print("-" * 64)

r1, phyid, r2 = bm_spike.verify(spi, cs)

print("-" * 64)
if phyid == 0x0283BC91:
    print("SPIKE RESULT: OA TRANSPORT PROVEN ON ADIN1110 (PHY ID over OA OK)")
    print("  -> 1110 fails only the driver's 2111 identity checks (r2=%d)" % r2)
elif r1 == 0 and r2 == 0:
    print("SPIKE RESULT: full init passed?! check which silicon this is")
else:
    print("SPIKE RESULT: OA transport NOT proven (r1=%d phyid=0x%08X r2=%d)"
          % (r1, phyid, r2))
    print("  -> checklist: straps really default (bridges removed)? harness")
    print("     intact after re-strap? 3V3 present? try s4_bus_probe.py")
