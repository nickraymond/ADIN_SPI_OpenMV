# s9_hal_native.py -- S9 bite 2 runner: the Alif-native ADI-HAL.
#
# Requires: firmware built with `build_spike.sh --no-prot --hal alif`,
# hat #2 strapped OA (bite-1 state), S4 harness unchanged (P0-P5).
#
# Run from nereus000 (ALWAYS the by-id path -- two OpenMV boards live here):
#   mpremote connect /dev/serial/by-id/usb-OpenMV_OpenMV_Camera_* \
#            run firmware/bm_spike/s9_hal_native.py
#
# What it proves, in order:
#   1. verify(): PHYID=0x0283BC91 through the driver's OA framing with the
#      native SPI0 engine -- zero MicroPython objects in the transfer path
#      (P4 reset and P5 IRQ registration stay machine.Pin scaffolding).
#   2. bench(): PHYID round-trip rate at 5/10/20 MHz, comparable with the
#      --hal mp build's bench (same driver, same chip, only the HAL swaps).
#   3. IRQ: falling edge on P5 (INT_N) reaches the driver's registered
#      callback via bm_spike.irq_trampoline. Armed BEFORE the reset pulse;
#      whether the un-inited chip asserts INT_N after reset is a bench
#      MEASUREMENT (flagged in the plan) -- the P5 level is printed either
#      way so a quiet line is debuggable, and a manual pull test is
#      suggested on failure.

import time
from machine import Pin

import sys

try:
    import bm_spike
except ImportError:
    print("FAIL: no bm_spike module -- flash a build_spike.sh image first")
    raise SystemExit

if getattr(bm_spike, "HAL", None) != "alif":
    print("FAIL: this firmware's bm_spike HAL is %r, need 'alif'"
          % getattr(bm_spike, "HAL", None))
    print("      rebuild with: build_spike.sh --no-prot --hal alif")
    raise SystemExit

print("=" * 64)
print("S9 bite 2: Alif-native ADI-HAL (SPI0 FIFO-burst + INT_N IRQ)")
print(sys.version)
print("=" * 64)

# P4/P5 scaffolding pins (SPI pins + CS are owned by the native HAL --
# do NOT construct machine.SPI(0) in this build).
rst = Pin("P4", Pin.OUT, value=1)
irqpin = Pin("P5", Pin.IN, Pin.PULL_UP)   # D14: board lacks INT_N pull-up

# Arm the IRQ path before anything touches the chip.
irqpin.irq(handler=bm_spike.irq_trampoline, trigger=Pin.IRQ_FALLING, hard=True)

# Hardware reset pulse (bite-1 convention). NOTE measured 2026-08-11:
# this line does NOT reset the chip on the current rig (register scratch
# survives the pulse) -- kept for convention, flagged as a fixture
# question; the IRQ proof below uses the chip's soft reset instead.
rst.value(0)
time.sleep_ms(10)
rst.value(1)
time.sleep_ms(100)

actual = bm_spike.setup(5_000_000)
print("native SPI0 up: requested 5 MHz, controller reports %d Hz" % actual)
print("-" * 64)

r1, phyid, r2 = bm_spike.verify()
print("-" * 64)

ok = phyid == 0x0283BC91
if ok:
    print("VERDICT A: PHYID over OA via NATIVE HAL -- OK")
else:
    print("VERDICT A: FAIL (r1=%d phyid=0x%08X r2=%d)" % (r1, phyid, r2))
    print("  -> same checklist as bite 1: straps, harness, 3V3; then compare")
    print("     against a --hal mp build to split HAL-vs-fixture")

# --- bench ladder ------------------------------------------------------
# Only the 5 MHz rung gates the bite (the OA-proven bring-up speed).
# 10/20 MHz are the FIRST OA-mode runs at speed on this rig -- reported
# as data; failures there are findings for DESIGN, not bite failures.
N = 2000
print("-" * 64)
print("bench: %d PHYID round trips per rung (init excluded)" % N)
for hz in (5_000_000, 10_000_000, 20_000_000):
    actual = bm_spike.setup(hz)
    us, fails, ph = bm_spike.bench(N)
    rate = N * 1_000_000 // us if us else 0
    print("  %2d MHz (actual %8d): %7d us  %5d reads/s  fails=%d  phyid=0x%08X"
          % (hz // 1_000_000, actual, us, rate, fails, ph))
    if fails or ph != 0x0283BC91:
        if hz == 5_000_000:
            ok = False
            print("  ^ FAIL (gating rung)")
        else:
            print("  ^ finding: OA at %d MHz not clean on this rig -- record"
                  " in DESIGN (RX sample delay is the first suspect)"
                  % (hz // 1_000_000))
xf, by, stalls, _ = bm_spike.stats()
print("hal stats: %d transfers, %d bytes, %d stalls" % (xf, by, stalls))
if stalls:
    ok = False
    print("FAIL: SPI stalls counted -- wiring/clocking suspect")

# --- IRQ proof ---------------------------------------------------------
# Back at the proven speed first: one variable at a time.
#
# MEASURED 2026-08-11 on this rig (raw OA probes, then encoded here):
#   - INT_N is asserted from power-up (RESETC pending, unmasked by
#     default -- post-reset IMASK0 = 0x1FBF) and W1C of STATUS0 is the
#     only way to raise it.
#   - STATUS0.LOFE (bit 4) relatches continuously on this bench -- it
#     must be MASKED or INT_N never rises.
#   - The P4 hardware reset line is INEFFECTIVE on this rig (register
#     scratch survives a 50 ms pulse -- flagged fixture question), so the
#     edge source is the driver's own soft reset (ADDR_MAC_RESET=0x003,
#     SWRESET=1, exactly what MAC_Reset writes) -> RESETC relatches ->
#     INT_N falls -> hard IRQ -> trampoline -> driver callback.
ST0, IMASK0, RESET_REG = 0x008, 0x00C, 0x003
LOFE_M = 0x10
bm_spike.setup(5_000_000)
print("-" * 64)
bm_spike.write_reg(RESET_REG, 1)           # known state
time.sleep_ms(20)
im = bm_spike.read_reg(IMASK0)
bm_spike.write_reg(IMASK0, im | LOFE_M)    # mask the relatching LOFE
st0 = bm_spike.read_reg(ST0)
bm_spike.write_reg(ST0, st0)               # W1C everything latched
time.sleep_ms(1)
lvl_cleared = irqpin.value()
print("IRQ proof: IMASK0 0x%08X -> LOFE masked; STATUS0 was 0x%08X;"
      % (im, st0))
print("           after W1C -> P5 level %d (want 1)" % lvl_cleared)
bm_spike.stats_clear()
bm_spike.write_reg(RESET_REG, 1)           # soft reset -> RESETC -> edge
time.sleep_ms(50)
_, _, _, irqs = bm_spike.stats()
level = irqpin.value()
print("           soft reset -> %d IRQ callback(s); P5 level now %d" %
      (irqs, level))
if lvl_cleared == 1 and irqs >= 1:
    print("VERDICT B: INT_N -> native IRQ path -> driver callback -- OK")
elif lvl_cleared == 0:
    print("VERDICT B: INCONCLUSIVE -- INT_N did not deassert after W1C;")
    try:
        print("  STATUS0=0x%08X -- new unmasked cause pending?"
              % bm_spike.read_reg(ST0))
    except Exception as e:
        print("  (post-mortem read failed: %s)" % e)
    ok = False
else:
    print("VERDICT B: FAIL -- edge existed (P5 rose, soft reset re-asserted)")
    print("  but no callback fired: dispatch is broken (this IS a bite bug)")
    ok = False

print("=" * 64)
print("BITE 2 RESULT: %s" % ("PASS (see 20 MHz finding)" if ok else "FAIL"))
