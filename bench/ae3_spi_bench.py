# ae3_spi_bench.py -- OpenMV AE3 SPI ceiling benchmark (Sprint S0)
#
# Measures the two numbers that decide go/no-go for a MicroPython-level
# ADIN1110 driver on the AE3:
#   1. Sustained machine.SPI(0) loopback throughput at 5/10/20/25 MHz,
#      chunk sizes 64 B - 4 KB  -> effective Mbps (PASS >= 12 Mbps)
#   2. GPIO edge -> Python handler latency on P5 (the ADIN IRQ path)
#
# WIRING (one jumper wire, two phases):
#   Phase A: jumper P0 (MOSI) -> P1 (MISO)
#   Phase B: when prompted, move the same jumper to P4 -> P5.
#            The script detects the move automatically -- no key press.
#
# Run from OpenMV IDE. Copy the printed table into DESIGN.md §Bench results.
#
# Throughput is timed WITHOUT per-iteration verification (timing stays
# clean), then a separate shorter pass verifies data integrity at the same
# settings. A fast point with errors is not a pass.

import time
import gc
import array

try:
    import machine
    ON_TARGET = True
except ImportError:
    machine = None          # host CPython: helpers importable for unit tests
    ON_TARGET = False

FREQS_MHZ = (5, 10, 20, 25)
CHUNK_SIZES = (64, 256, 1024, 4096)
TARGET_BYTES = 512 * 1024       # moved per timed point (time-capped below)
MAX_POINT_SECONDS = 4.0
VERIFY_ITERS = 32               # integrity pass iterations per point
IRQ_SAMPLES = 100
IRQ_TIMEOUT_US = 100_000
# Revised gate (Nick, 2026-08-09): SPI effective >= 2x the T1 stream bitrate
# (QVGA color q35 @ 30 fps). The original 12 Mbps gate is retired -- see
# DESIGN.md D10.
PASS_MBPS = 3.5

PIN_SPI_BUS = 0                 # machine.SPI(0): P0=MOSI P1=MISO P2=SCLK
PIN_DRIVE = "P4"                # phase B edge source (ADIN RESET pin, unused here)
PIN_IRQ = "P5"                  # phase B edge sink (the ADIN IRQ pin)


# ---------------------------------------------------------------- pure helpers
# (no machine/ticks usage -- unit-tested on host CPython)

def fill_pattern(buf, seed):
    """Deterministic non-trivial byte pattern; distinct for distinct seeds."""
    x = (seed * 2654435761 + 1) & 0xFFFFFFFF
    for i in range(len(buf)):
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        buf[i] = (x >> 16) & 0xFF


def first_diff(a, b):
    """Index of first mismatching byte, or -1 if equal (lengths assumed ==)."""
    for i in range(len(a)):
        if a[i] != b[i]:
            return i
    return -1


def mbps(nbytes, elapsed_us):
    """Payload megabits per second (1e6 bits) over a duration in microseconds."""
    if elapsed_us <= 0:
        return 0.0
    return (nbytes * 8.0) / elapsed_us


def percentile(sorted_vals, p):
    """Nearest-rank percentile of an already-sorted non-empty sequence."""
    idx = int((p / 100.0) * len(sorted_vals))
    if idx >= len(sorted_vals):
        idx = len(sorted_vals) - 1
    return sorted_vals[idx]


def summarize(vals):
    """(n, min, median, p99, max) of a non-empty sequence."""
    s = sorted(vals)
    n = len(s)
    if n % 2:
        med = s[n // 2]
    else:
        med = (s[n // 2 - 1] + s[n // 2]) / 2.0
    return (n, s[0], med, percentile(s, 99), s[-1])


def pass_fail(points, threshold):
    """points: [(mhz, chunk, mbps, errors), ...] ->
    (passed, best_clean_point_or_None). Only error-free points can pass."""
    best = None
    for pt in points:
        if pt[3] != 0:
            continue
        if best is None or pt[2] > best[2]:
            best = pt
    return (best is not None and best[2] >= threshold, best)


# ------------------------------------------------------------- phase A: SPI

def make_spi(freq_hz):
    return machine.SPI(PIN_SPI_BUS, baudrate=freq_hz, polarity=0, phase=0)


def timed_pass(spi, tx, rx):
    """Move up to TARGET_BYTES through the loopback; return (nbytes, us)."""
    chunk = len(tx)
    iters = TARGET_BYTES // chunk
    max_us = int(MAX_POINT_SECONDS * 1_000_000)
    done = 0
    t0 = time.ticks_us()
    for i in range(iters):
        spi.write_readinto(tx, rx)
        done += chunk
        if i & 0x3F == 0x3F and time.ticks_diff(time.ticks_us(), t0) > max_us:
            break
    return done, time.ticks_diff(time.ticks_us(), t0)


def verify_pass(spi, tx, rx):
    """Integrity check at the same settings; returns error count (prints
    the first failure in detail)."""
    errors = 0
    for it in range(VERIFY_ITERS):
        fill_pattern(tx, it)
        for i in range(len(rx)):
            rx[i] = 0
        spi.write_readinto(tx, rx)
        d = first_diff(tx, rx)
        if d >= 0:
            errors += 1
            if errors == 1:
                print("      INTEGRITY FAIL iter %d: offset %d wrote 0x%02X "
                      "read 0x%02X" % (it, d, tx[d], rx[d]))
    return errors


def run_throughput():
    print("PHASE A -- SPI(%d) loopback throughput  (jumper P0 -> P1)"
          % PIN_SPI_BUS)
    hdr = ("%-8s %-8s %10s %9s %9s %7s"
           % ("MHz", "chunk", "moved", "eff Mbps", "us/chunk", "errors"))
    print(hdr)
    print("-" * len(hdr))

    points = []
    for mhz in FREQS_MHZ:
        try:
            spi = make_spi(mhz * 1_000_000)
        except Exception as e:
            print("%-8d SPI init failed: %s" % (mhz, e))
            continue
        print("  [%d MHz] %s" % (mhz, spi))
        for chunk in CHUNK_SIZES:
            tx = bytearray(chunk)
            rx = bytearray(chunk)
            fill_pattern(tx, chunk)
            gc.collect()
            nbytes, us = timed_pass(spi, tx, rx)
            errors = verify_pass(spi, tx, rx)
            rate = mbps(nbytes, us)
            points.append((mhz, chunk, rate, errors))
            print("%-8d %-8d %10d %9.2f %9.1f %7d"
                  % (mhz, chunk, nbytes, rate,
                     us / max(1, nbytes // chunk), errors))
        spi.deinit()
        gc.collect()
    print("-" * len(hdr))
    return points


# ------------------------------------------------------------- phase B: IRQ

def wait_for_jumper_move(drive, sense):
    """Block until `sense` tracks `drive` (jumper moved to P4 -> P5)."""
    print()
    print("PHASE B -- move the jumper: P0 -> P1  ==>  P4 -> P5")
    print("Waiting for P5 to follow P4 ...")
    while True:
        ok = True
        for level in (0, 1, 0, 1):
            drive.value(level)
            time.sleep_ms(2)
            if sense.value() != level:
                ok = False
                break
        drive.value(0)
        if ok:
            print("Jumper detected on P4 -> P5.")
            return
        time.sleep_ms(500)


def measure_irq(drive, sense, hard):
    """Return (deltas_us list, missed count) for IRQ_SAMPLES rising edges."""
    stamp = array.array("i", [0])

    def _isr(pin):
        stamp[0] = time.ticks_us()

    try:
        sense.irq(trigger=machine.Pin.IRQ_RISING, handler=_isr, hard=hard)
    except (TypeError, ValueError, NotImplementedError):
        if hard:
            return None, 0          # hard ISR unsupported on this port
        sense.irq(trigger=machine.Pin.IRQ_RISING, handler=_isr)

    deltas = []
    missed = 0
    for _ in range(IRQ_SAMPLES):
        drive.value(0)
        time.sleep_ms(1)
        stamp[0] = -1
        t0 = time.ticks_us()
        drive.value(1)
        while stamp[0] == -1:
            if time.ticks_diff(time.ticks_us(), t0) > IRQ_TIMEOUT_US:
                break
        if stamp[0] == -1:
            missed += 1
        else:
            deltas.append(time.ticks_diff(stamp[0], t0))
    sense.irq(handler=None)
    drive.value(0)
    return deltas, missed


def run_irq_latency():
    drive = machine.Pin(PIN_DRIVE, machine.Pin.OUT, value=0)
    sense = machine.Pin(PIN_IRQ, machine.Pin.IN, machine.Pin.PULL_DOWN)
    wait_for_jumper_move(drive, sense)

    results = []
    for label, hard in (("soft", False), ("hard", True)):
        deltas, missed = measure_irq(drive, sense, hard)
        if deltas is None:
            print("IRQ (%s): not supported on this port -- skipped" % label)
            continue
        if not deltas:
            print("IRQ (%s): NO edges seen in %d tries -- check jumper"
                  % (label, IRQ_SAMPLES))
            continue
        n, lo, med, p99, hi = summarize(deltas)
        print("IRQ (%s) us over %d edges (%d missed): "
              "min %d  median %.0f  p99 %d  max %d"
              % (label, n, missed, lo, med, p99, hi))
        results.append((label, med))
    return results


# -------------------------------------------------------------------- main

def main():
    print("=" * 64)
    print("OpenMV AE3 SPI ceiling benchmark -- Sprint S0")
    gc.collect()
    print("free heap at start: %d bytes" % gc.mem_free())
    print("=" * 64)

    points = run_throughput()
    irq = run_irq_latency()

    print()
    print("=" * 64)
    passed, best = pass_fail(points, PASS_MBPS)
    if best:
        print("best error-free point: %d MHz / %d B chunks -> %.2f Mbps"
              % (best[0], best[1], best[2]))
    else:
        print("no error-free throughput point measured")
    for label, med in irq:
        print("IRQ median latency (%s): %.0f us" % (label, med))
    if passed:
        print("VERDICT: PASS (>= %.0f Mbps effective)" % PASS_MBPS)
    else:
        print("VERDICT: FAIL (< %.0f Mbps) -> decision note in DESIGN.md"
              % PASS_MBPS)
    print("Copy this output into DESIGN.md §Bench results (S0).")
    print("=" * 64)


if __name__ == "__main__":
    if ON_TARGET:
        main()
    else:
        print("ae3_spi_bench: no `machine` module -- run on the AE3. "
              "Host use is import-only (unit tests).")
