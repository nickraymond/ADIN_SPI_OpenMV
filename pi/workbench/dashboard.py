#!/usr/bin/env python3
"""Splash-page dashboard: card ring, battery, and dives remaining.

The workbench menu became the thing Nick actually lands on between dives, so
it has to answer the two questions he will have standing on a boat with wet
hands: how much card is left, and how much battery. Both answers are given in
DIVES, because that is the unit the decision is made in -- "37 GB free" is not
a decision, "3 more dives" is.

EVERY NUMBER HERE IS EITHER READ FROM HARDWARE OR DERIVED FROM A MEASUREMENT
THAT IS NAMED. The estimates are deliberately crude and say so; what they must
never be is confidently wrong, because a rig that claims four dives of battery
and dies on the second costs a dive site that does not come round again.

WHY THE BATTERY MODEL IS A CURVE AND NOT A PERCENTAGE
-----------------------------------------------------
A LiFePO4 cell's discharge curve is famously FLAT -- it sits near 3.2-3.3 V
for most of its usable capacity and then falls off a cliff. So the obvious
implementation, linear interpolation between the shutdown voltage and full,
is not merely imprecise, it is wrong in the dangerous direction: it reads
"half full" across almost the entire discharge and then collapses without
warning. Instead this uses two points MEASURED on this rig class in S29 --
3.20 V ran 78 minutes to the 2950 mV cutoff at a 2.73 W mean, and a full
charge ran ~3 hours -- and interpolates between them, which reproduces the
knee rather than pretending it is not there.

The remaining time is then scaled by the LIVE power draw (VOUT x IOUT), so
recording four streams reports a shorter battery than sitting idle, which is
the whole point of showing it.
"""

import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIELD = os.path.join(os.path.dirname(_HERE), "field")
if _FIELD not in sys.path:
    sys.path.insert(0, _FIELD)

CLI = "lifepo4wered-cli"
CLI_TIMEOUT_S = 5

#: (millivolts, minutes of runtime left) measured on nereus002 in S29 at a
#: 2.73 W mean draw. The endpoints are the Pi+'s own cutoff and the observed
#: full-charge voltage on this rig (VBAT read 3618 mV on charge, 2026-09-09).
BATTERY_CURVE = ((2950, 0.0), (3200, 78.0), (3600, 180.0))

#: The draw those minutes were measured at. Live power is divided by this.
CURVE_REFERENCE_WATTS = 2.73

#: The pinned Channel Islands recipe, measured on nereus002 2026-09-09:
#: IMX science sw JPEG q90 @1280x800 4.94 + H.264 proxy 0.35
#: + N6 HD q70 2.86 + AE3 VGA q50 0.15.
#: 2026-09-10 night: science JPEG dropped (Nick). IMX is one hardware H.264
#: at 8 Mbps (~1.0 MB/s) + N6 HD q70 2.86 + AE3 VGA q50 0.15 = ~4.0 MB/s.
#: The H.264 figure is the bitrate setting, not yet a measured file rate.
PINNED_RATE_BYTES_S = int(4.0 * 1e6)

#: Nick: "15-20 min per dive at most." The pessimistic end is used, because
#: over-reporting dives remaining is the failure that costs a site.
DIVE_MINUTES = 20.0

REGISTERS = ("VIN", "VBAT", "VOUT", "IOUT", "VBAT_SHDN", "AUTO_BOOT")


# -- pure functions (no hardware, no disk) ---------------------------------

def interp_minutes(vbat_mv, curve=BATTERY_CURVE):
    """Minutes left at the reference draw, from the measured knee curve."""
    if vbat_mv is None:
        return None
    pts = sorted(curve)
    if vbat_mv <= pts[0][0]:
        return 0.0
    if vbat_mv >= pts[-1][0]:
        return float(pts[-1][1])
    for (v0, m0), (v1, m1) in zip(pts, pts[1:]):
        if v0 <= vbat_mv <= v1:
            if v1 == v0:
                return float(m1)
            f = (vbat_mv - v0) / float(v1 - v0)
            return m0 + f * (m1 - m0)
    return None


def minutes_remaining(vbat_mv, watts=None, curve=BATTERY_CURVE,
                      reference_watts=CURVE_REFERENCE_WATTS):
    """Scale the curve by how hard the rig is actually working right now."""
    base = interp_minutes(vbat_mv, curve)
    if base is None:
        return None
    if not watts or watts <= 0:
        return base
    return base * (reference_watts / float(watts))


def battery_bars(vbat_mv, curve=BATTERY_CURVE):
    """0-4 bars, from the CURVE rather than raw voltage.

    Bars track usable capacity, so on a flat LiFePO4 plateau the icon does not
    sit at 'full' until the moment it dies.
    """
    if vbat_mv is None:
        return None
    full = float(max(m for _, m in curve)) or 1.0
    frac = (interp_minutes(vbat_mv, curve) or 0.0) / full
    for threshold, bars in ((0.75, 4), (0.5, 3), (0.25, 2), (0.05, 1)):
        if frac >= threshold:
            return bars
    return 0


def dives_from_bytes(free_bytes, rate_bytes_s=PINNED_RATE_BYTES_S,
                     dive_minutes=DIVE_MINUTES):
    if free_bytes is None or rate_bytes_s <= 0:
        return None
    per_dive = rate_bytes_s * dive_minutes * 60.0
    return int(max(0.0, free_bytes) // per_dive) if per_dive else None


def dives_from_minutes(minutes, dive_minutes=DIVE_MINUTES):
    if minutes is None or dive_minutes <= 0:
        return None
    return int(max(0.0, minutes) // dive_minutes)


def watts(vout_mv, iout_ma):
    if vout_mv is None or iout_ma is None:
        return None
    return round((vout_mv / 1000.0) * (iout_ma / 1000.0), 2)


# -- hardware / disk -------------------------------------------------------

def read_pi_plus(runner=None):
    """Read the Pi+ registers. A missing CLI is REPORTED, never faked."""
    runner = runner or subprocess.run
    out = {"available": False, "error": None}
    for reg in REGISTERS:
        try:
            r = runner([CLI, "get", reg], capture_output=True, text=True,
                       timeout=CLI_TIMEOUT_S)
        except FileNotFoundError:
            out["error"] = ("%s not installed -- no battery telemetry"
                            % CLI)
            return out
        except (OSError, subprocess.SubprocessError) as exc:
            out["error"] = "%s: %s" % (type(exc).__name__, exc)
            return out
        if r.returncode != 0:
            out["error"] = "%s get %s failed: %s" % (
                CLI, reg, (r.stderr or "").strip()[:120])
            return out
        try:
            out[reg] = int((r.stdout or "").strip().split()[-1])
        except (ValueError, IndexError):
            out[reg] = None
    out["available"] = any(out.get(r) is not None for r in REGISTERS)
    return out


def battery(runner=None):
    raw = read_pi_plus(runner=runner)
    if not raw.get("available"):
        return {"available": False, "error": raw.get("error"),
                "bars": None, "vbat_mv": None}
    vbat, vin = raw.get("VBAT"), raw.get("VIN")
    w = watts(raw.get("VOUT"), raw.get("IOUT"))
    mins = minutes_remaining(vbat, w)
    return {
        "available": True, "error": None,
        "vbat_mv": vbat, "vin_mv": vin,
        "vout_mv": raw.get("VOUT"), "iout_ma": raw.get("IOUT"),
        "vbat_shdn_mv": raw.get("VBAT_SHDN"),
        "watts": w,
        # VIN above the cell means external power is present; the Pi+ is
        # charging or running from it, so a falling bar is not a warning.
        "on_external_power": bool(vin and vbat and vin > vbat + 200),
        "bars": battery_bars(vbat),
        "minutes_remaining": round(mins) if mins is not None else None,
        "dives_remaining": dives_from_minutes(mins),
    }


def ring(root, ring_bytes=None, min_free_bytes=None):
    """Recording-ring occupancy, via the storage module that owns the rule."""
    try:
        import storage as ST
    except ImportError as exc:
        return {"available": False, "error": "storage module: %s" % exc}
    rb = ST.DEFAULT_RING_BYTES if ring_bytes is None else ring_bytes
    mf = ST.DEFAULT_MIN_FREE_BYTES if min_free_bytes is None else min_free_bytes
    try:
        st = ST.status(root, rb, mf)
    except Exception as exc:
        return {"available": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    used = st.get("ring_used_bytes")
    # What can still be RECORDED is the smaller of the ring's own headroom and
    # the card's real free space minus the floor -- a ring with room on a full
    # card records nothing.
    ring_head = None if used is None else max(0, rb - used)
    free = st.get("sd_free_bytes")
    card_head = None if free is None else max(0, free - mf)
    heads = [h for h in (ring_head, card_head) if h is not None]
    headroom = min(heads) if heads else None
    return {
        "available": True, "error": None,
        "ring_bytes": rb, "ring_used_bytes": used,
        "sd_free_bytes": free, "min_free_bytes": mf,
        "sessions": st.get("sessions"),
        "pct": (round(100.0 * used / rb, 1) if (used is not None and rb) else None),
        "headroom_bytes": headroom,
        "dives_remaining": dives_from_bytes(headroom),
    }


#: A status file older than this is from a dead run, not a live one. Without
#: the guard a crashed recorder would leave the card counting down forever.
STATUS_STALE_S = 30.0


def recording(root, now=None):
    """Progress of the segment being recorded right now, if there is one."""
    import glob
    import json
    now = time.time() if now is None else now
    best, best_mtime = None, -1.0
    for path in glob.glob(os.path.join(root, "*", "status.json")):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = path, mtime
    if best is None:
        return {"active": False, "reason": "no recording status on disk"}
    try:
        with open(best) as f:
            st = json.load(f)
    except (OSError, ValueError) as exc:
        return {"active": False, "reason": "unreadable status: %s" % exc}
    age = now - best_mtime
    ends = st.get("ends_unix")
    # Live means: the file is fresh, OR the segment it describes has not run
    # out yet. A 5-minute segment only touches the file at its ends, so
    # freshness alone would blink the countdown off mid-segment.
    live = (age <= STATUS_STALE_S
            or (ends is not None and now < ends + STATUS_STALE_S))
    if st.get("closing") and age > STATUS_STALE_S:
        live = False
    if not live:
        return {"active": False, "reason": "last status is %.0f s old" % age}
    left = None if ends is None else max(0.0, ends - now)
    return {
        "active": True,
        "session": st.get("session"),
        "segment": st.get("segment"),
        "segment_s": st.get("segment_s"),
        "seconds_left": None if left is None else round(left, 1),
        "closing": bool(st.get("closing")),
        "recipe": st.get("recipe"),
        "wb_mode": st.get("wb_mode"),
    }


#: CPU temperature (Nick, 2026-09-10 night: "the dashboard needs a CPU
#: temperature readout, and a warning if it's getting too hot"). Recording
#: measured 52-70 C in open air on nereus002; the Pi begins soft-throttling
#: at 80 C and hard-throttles at 85. A sealed housing on a deck in the sun
#: is the case nobody has measured, hence the warning band starts at 70.
THERMAL_WARN_C = 70.0
THERMAL_BAD_C = 80.0
THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"


def _read_temp_c():
    with open(THERMAL_ZONE) as f:
        return int(f.read().strip()) / 1000.0


def _read_throttled():
    import subprocess
    out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                         text=True, timeout=3).stdout.strip()
    return out.split("=", 1)[1] if "=" in out else out


def thermal(read_temp=_read_temp_c, read_throttled=_read_throttled):
    try:
        t = float(read_temp())
    except Exception as exc:
        return {"available": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        thr = read_throttled() or "0x0"
    except Exception:
        thr = None
    try:
        bits = int(thr, 16) if thr else 0
    except ValueError:
        bits = 0
    # bit 0 under-voltage now, 1 arm freq capped now, 2 throttled now,
    # 3 soft temp limit now (the 16-19 bits are "has happened since boot")
    throttled_now = bool(bits & 0xF)
    if throttled_now or t >= THERMAL_BAD_C:
        level, note = "bad", "throttling -- footage rate will drop; get it out of the sun"
    elif t >= THERMAL_WARN_C:
        level, note = "warn", "hot -- close to the 80 C soft limit"
    else:
        level, note = "ok", None
    return {"available": True, "temp_c": round(t, 1), "throttled": thr,
            "throttled_now": throttled_now, "level": level, "note": note,
            "warn_c": THERMAL_WARN_C, "bad_c": THERMAL_BAD_C}


def snapshot(root, ring_bytes=None, min_free_bytes=None, runner=None):
    b = battery(runner=runner)
    r = ring(root, ring_bytes, min_free_bytes)
    th = thermal()
    try:
        rec = recording(root)
    except Exception as exc:
        rec = {"active": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    return {
        "battery": b,
        "ring": r,
        "thermal": th,
        "recording": rec,
        "assumptions": {
            "dive_minutes": DIVE_MINUTES,
            "rate_bytes_s": PINNED_RATE_BYTES_S,
            "rate_note": ("IMX H.264 8 Mbps ~1.0 (bitrate setting) + N6 HD q70 "
                          "2.86 + AE3 VGA q50 0.15 (measured 2026-09-09) = ~4.0 MB/s"),
            "battery_note": ("crude: S29 curve (3.20 V -> 78 min, full -> "
                             "~180 min at 2.73 W), scaled by live draw"),
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(os.path.expanduser("~/recordings")), indent=2))
