#!/usr/bin/env python3
"""LiFePO4wered/Pi+ control: verified wake scheduling and a real power cycle.

VENDORED (copied, not imported) from Nick's `nereus-vision-dev` project,
`device/system_agent/src/system_agent/lifepo4wered_controller.py`, which is
field-deployed and already hardened. That repo is NOT modified and NOT
depended on: this file stands alone so S29 builds without it and so the two
can diverge without touching units that are in the water. The verification
rules below are its rules, kept deliberately intact -- they were earned on
deployed hardware and this is not the place to re-derive them.

WHY THIS EXISTS -- the problem it solves, measured on nereus002 2026-09-06:

* `systemctl reboot` is NOT safe on this rig. The Pi+ watches the UART TX
  line to detect shutdown and cuts power after SHDN_DELAY; a Pi Zero 2 W can
  be slower to bring the line back than that window, so a "reboot" becomes a
  power-off that needs a physical button press. It happened.
* The AE3 has two failure states whose ONLY documented cure is a power
  cycle: the raw-REPL refusal (board present, refuses below Python) and
  falling off the USB bus entirely (measured this session -- `lsusb` lost
  it). `systemctl reboot` does not cut USB VBUS, so it cannot fix either.

Scheduling RTC_WAKE_TIME before shutting down turns both problems into one
solution: the Pi+ removes power, then restores it at the wake time. That is
a TRUE power cycle -- USB included -- so it is both a safe remote reboot and
the AE3's cure, which the rig otherwise has no remote access to at all.
"""

import subprocess
import time
from datetime import datetime, timedelta, timezone

CLI = "lifepo4wered-cli"
CLI_TIMEOUT_SEC = 10

#: AUTO_BOOT must be 2 (AUTO_BOOT_VBAT_SMART) for a scheduled wake to bring
#: the Pi back. Verified before every schedule rather than assumed -- a wake
#: programmed against the wrong AUTO_BOOT silently never fires, and the rig
#: stays dark until someone walks to it.
AUTO_BOOT_VBAT_SMART = 2

#: Tolerances from the source project.
RTC_TIME_TOLERANCE_SEC = 3
WAKE_TOLERANCE_SEC = 1
MAX_WAKE_AHEAD_SEC = 24 * 3600


class LiFePO4Error(RuntimeError):
    """A Pi+ operation failed or could not be verified."""


def _cli(args, runner=None):
    runner = runner or subprocess.run
    try:
        r = runner([CLI, *args], capture_output=True, text=True,
                   timeout=CLI_TIMEOUT_SEC)
    except FileNotFoundError as exc:
        raise LiFePO4Error("lifepo4wered-cli not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise LiFePO4Error("lifepo4wered-cli timed out: %s" % " ".join(args)) from exc
    if r.returncode != 0:
        raise LiFePO4Error("lifepo4wered-cli %s failed: %s"
                           % (" ".join(args), (r.stderr or "").strip()))
    return (r.stdout or "").strip()


def get_int(name, runner=None):
    out = _cli(["get", name], runner=runner)
    try:
        return int(out.split()[-1])
    except (ValueError, IndexError) as exc:
        raise LiFePO4Error("unparsable %s: %r" % (name, out)) from exc


def set_int(name, value, runner=None):
    _cli(["set", name, str(int(value))], runner=runner)
    return get_int(name, runner=runner)


def status(runner=None, keys=("VIN", "VBAT", "VOUT", "IOUT", "AUTO_BOOT",
                              "RTC_TIME", "RTC_WAKE_TIME", "PI_RUNNING",
                              "SHDN_DELAY", "VBAT_SHDN")):
    out = {}
    for k in keys:
        try:
            out[k] = get_int(k, runner=runner)
        except LiFePO4Error:
            out[k] = None          # report the gap, never invent a value
    return out


def verify_clock_pair(requested_wake_unix, rtc_time, wake_time, auto_boot,
                      now_unix,
                      rtc_tol=RTC_TIME_TOLERANCE_SEC,
                      wake_tol=WAKE_TOLERANCE_SEC,
                      max_ahead=MAX_WAKE_AHEAD_SEC):
    """Pure validation of a programmed wake. Returns (ok, reason).

    Vendored rules, unchanged. Each one exists because its absence leaves the
    rig dark: a wrong AUTO_BOOT never fires; a wake in the past never fires;
    a wake implausibly far ahead is a corrupt write that reads as valid; and
    an RTC that disagrees with Linux means the wake lands at the wrong time.
    """
    if auto_boot != AUTO_BOOT_VBAT_SMART:
        return False, "AUTO_BOOT expected %d got %r" % (AUTO_BOOT_VBAT_SMART,
                                                        auto_boot)
    if rtc_time is None or wake_time is None:
        return False, "RTC_TIME/RTC_WAKE_TIME unreadable"
    if abs(int(rtc_time) - int(now_unix)) > rtc_tol:
        return False, ("RTC_TIME outside tolerance: rtc=%s linux=%s delta=%s"
                       % (rtc_time, now_unix, int(rtc_time) - int(now_unix)))
    if abs(int(wake_time) - int(requested_wake_unix)) > wake_tol:
        return False, ("RTC_WAKE_TIME outside tolerance: want=%s got=%s"
                       % (requested_wake_unix, wake_time))
    if int(wake_time) <= int(now_unix):
        return False, ("RTC_WAKE_TIME is not in the future: wake=%s now=%s"
                       % (wake_time, now_unix))
    if int(wake_time) - int(now_unix) > max_ahead:
        return False, ("RTC_WAKE_TIME implausibly far ahead: %s s"
                       % (int(wake_time) - int(now_unix)))
    return True, "ok"


def program_wake(seconds, runner=None, retries=5, base_delay=1.0,
                 sleep=time.sleep, now=None):
    """Write RTC_TIME then RTC_WAKE_TIME, then re-read and VERIFY both.

    Order matters: RTC_TIME first so the wake is programmed against a clock
    that agrees with Linux. Both are re-read afterwards -- a write that
    returns success is not proof the register holds the value (this repo's
    rule 4, and the source project's retry loop exists for the same reason).
    """
    if seconds <= 0:
        raise LiFePO4Error("seconds must be > 0, got %r" % (seconds,))
    last = "no attempt made"
    for attempt in range(1, retries + 1):
        now_dt = (now or (lambda: datetime.now(timezone.utc)))()
        now_unix = int(now_dt.timestamp())
        wake_unix = int((now_dt + timedelta(seconds=seconds)).timestamp())
        set_int("RTC_TIME", now_unix, runner=runner)
        set_int("RTC_WAKE_TIME", wake_unix, runner=runner)
        rtc_time = get_int("RTC_TIME", runner=runner)
        wake_time = get_int("RTC_WAKE_TIME", runner=runner)
        auto_boot = get_int("AUTO_BOOT", runner=runner)
        ok, reason = verify_clock_pair(wake_unix, rtc_time, wake_time,
                                       auto_boot, int(time.time())
                                       if now is None else now_unix)
        if ok:
            return {"wake_unix": wake_unix, "rtc_time": rtc_time,
                    "attempt": attempt,
                    "wake_utc": datetime.fromtimestamp(
                        wake_unix, timezone.utc).isoformat()}
        last = reason
        if attempt < retries:
            sleep(base_delay * attempt)
    raise LiFePO4Error("could not program a verified wake after %d attempts: %s"
                       % (retries, last))
