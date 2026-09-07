#!/usr/bin/env python3
"""Log the field rig's power and thermals to CSV, for endurance testing.

Answers "how long can we leave this running", which is the question that
decides deployment duration. Samples every 10 s by default:

    iso_utc, uptime_s, vin_mv, vbat_mv, vout_mv, iout_ma, load_w,
    energy_wh, cpu_temp_c, load1, throttled

Design notes that matter for an overnight run:

* APPEND-ONLY, flushed every sample. A drawdown test that dies with the
  battery must still have every row it took -- buffering would lose the
  most interesting minutes, which are the last ones.
* energy_wh integrates load_w over the ACTUAL elapsed time between
  samples, not the nominal interval; a stalled sample must not invent
  energy that was never drawn.
* A failed read writes an EMPTY field, never a zero. Zero volts is a
  measurement; a missing reading is not, and averaging invented zeros is
  how an endurance curve gets quietly wrong.
* `throttled` is the Pi's own thermal/undervoltage word -- an endurance
  run that silently throttles is measuring a different machine.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

FIELDS = ["iso_utc", "uptime_s", "vin_mv", "vbat_mv", "vout_mv", "iout_ma",
          "load_w", "energy_wh", "cpu_temp_c", "load1", "throttled"]


def _run(argv, timeout=5):
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def lifepo4(name):
    """One Pi+ register, or None. None means unknown -- never 0."""
    out = _run(["lifepo4wered-cli", "get", name])
    try:
        return int(out.split()[-1])
    except (ValueError, IndexError):
        return None


def cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            return round(int(fh.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    out = _run(["vcgencmd", "measure_temp"])
    try:
        return float(out.split("=")[1].split("'")[0])
    except (IndexError, ValueError):
        return None


def uptime_s():
    try:
        with open("/proc/uptime") as fh:
            return int(float(fh.read().split()[0]))
    except (OSError, ValueError):
        return None


def load1():
    try:
        return float(open("/proc/loadavg").read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def throttled():
    out = _run(["vcgencmd", "get_throttled"])
    return out.split("=")[-1] if "=" in out else ""


def sample(prev_t=None, energy_wh=0.0, clock=time.monotonic):
    """One row plus the running energy total."""
    now = clock()
    vin, vbat = lifepo4("VIN"), lifepo4("VBAT")
    vout, iout = lifepo4("VOUT"), lifepo4("IOUT")
    load_w = (vout / 1000.0) * (iout / 1000.0) if (vout and iout) else None
    if load_w is not None and prev_t is not None:
        # Integrate over the REAL gap, so a stalled sample cannot invent
        # energy that was never drawn.
        energy_wh += load_w * (now - prev_t) / 3600.0
    row = {
        "iso_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uptime_s": uptime_s(), "vin_mv": vin, "vbat_mv": vbat,
        "vout_mv": vout, "iout_ma": iout,
        "load_w": round(load_w, 3) if load_w is not None else None,
        "energy_wh": round(energy_wh, 4),
        "cpu_temp_c": cpu_temp_c(), "load1": load1(),
        "throttled": throttled(),
    }
    return row, now, energy_wh


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--out", default=os.path.expanduser("~/power_logs"))
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "power_%s.csv"
                        % time.strftime("%Y%m%d_%H%M%S"))
    fresh = not os.path.exists(path)
    fh = open(path, "a", buffering=1)          # line buffered
    if fresh:
        fh.write(",".join(FIELDS) + "\n")
    print("power log -> %s (every %gs)" % (path, args.interval), flush=True)

    prev_t, energy = None, 0.0
    while True:
        row, prev_t, energy = sample(prev_t, energy)
        fh.write(",".join("" if row[k] is None else str(row[k])
                          for k in FIELDS) + "\n")
        fh.flush()
        os.fsync(fh.fileno())                  # survive an abrupt power loss
        if args.once:
            print(row, flush=True)
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
