#!/usr/bin/env python3
"""Reboot this rig by POWER CYCLING it through the LiFePO4wered/Pi+.

Usage:
    python3 pi/field/power_cycle.py --check            # verify, change nothing
    python3 pi/field/power_cycle.py --wake-in 25       # schedule + shut down

`systemctl reboot` is the wrong tool on this rig, twice over:

1. It is not reliable here. The Pi+ detects shutdown on the UART TX line and
   cuts power after SHDN_DELAY; a Pi Zero 2 W can miss that window, turning a
   reboot into a power-off that needs a physical button. Measured 2026-09-06.
2. It does not cut USB VBUS, so it cannot clear the two AE3 states whose only
   documented cure is a power cycle -- the raw-REPL refusal, and falling off
   the bus entirely (both seen this session).

Scheduling RTC_WAKE_TIME first makes the shutdown deliberate: the Pi+ removes
power, waits, and restores it. USB included. That is a safe remote reboot AND
the AE3's only remote cure.

SAFETY: the wake is programmed and VERIFIED BEFORE anything shuts down. If it
cannot be verified, this refuses and leaves the rig running -- a rig that is
up is always recoverable, one that powered off without an armed wake is not.
"""

import argparse
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import lifepo4                                          # noqa: E402

#: Enough for the Pi+ to settle after power is cut. The vendor's own shutdown
#: delay is ~12 s here (SHDN_DELAY=96 at 8 ticks/s), so a wake inside that
#: window can land before power has actually dropped and be missed.
MIN_WAKE_SEC = 20


def preflight(st):
    """Reasons this rig must NOT be power cycled right now."""
    problems = []
    if st.get("AUTO_BOOT") != lifepo4.AUTO_BOOT_VBAT_SMART:
        problems.append("AUTO_BOOT is %r, must be %d or the rig will not come "
                        "back" % (st.get("AUTO_BOOT"),
                                  lifepo4.AUTO_BOOT_VBAT_SMART))
    vbat, shdn = st.get("VBAT"), st.get("VBAT_SHDN")
    vin = st.get("VIN")
    # Refuse on a flat battery with no charger: the Pi+ will not re-boot below
    # VBAT_BOOT, so a cycle here is a one-way trip to a dark rig.
    if vbat is not None and shdn is not None and vbat < shdn + 250:
        if not vin or vin < 4000:
            problems.append("VBAT %s mV is close to VBAT_SHDN %s mV and there "
                            "is no input power (VIN %s mV) -- it may not come "
                            "back" % (vbat, shdn, vin))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wake-in", type=int, default=25,
                    help="seconds until the Pi+ restores power (min %d)"
                         % MIN_WAKE_SEC)
    ap.add_argument("--check", action="store_true",
                    help="verify and report; schedule nothing, shut down nothing")
    ap.add_argument("--yes", action="store_true",
                    help="actually shut down (without this it is a dry run)")
    args = ap.parse_args(argv)

    if args.wake_in < MIN_WAKE_SEC:
        print("refusing: --wake-in %d is below the %d s minimum"
              % (args.wake_in, MIN_WAKE_SEC), file=sys.stderr)
        return 2

    st = lifepo4.status()
    print("power: VIN=%s mV  VBAT=%s mV  IOUT=%s mA  AUTO_BOOT=%s"
          % (st.get("VIN"), st.get("VBAT"), st.get("IOUT"),
             st.get("AUTO_BOOT")), flush=True)

    problems = preflight(st)
    if problems:
        for p in problems:
            print("REFUSED: %s" % p, file=sys.stderr)
        return 3

    if args.check:
        print("check only: preflight clean, nothing scheduled")
        return 0

    armed = lifepo4.program_wake(args.wake_in)
    print("wake VERIFIED for %s (unix=%s, attempt %d)"
          % (armed["wake_utc"], armed["wake_unix"], armed["attempt"]), flush=True)

    if not args.yes:
        print("dry run: wake is armed but NOT shutting down; pass --yes to cycle")
        return 0

    print("shutting down now; power returns in ~%d s" % args.wake_in, flush=True)
    # Clean shutdown, never `halt -f`: the filesystem and the boards both
    # deserve an orderly stop, and the Pi+ is what restores power.
    subprocess.run(["sudo", "-n", "systemctl", "poweroff"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
