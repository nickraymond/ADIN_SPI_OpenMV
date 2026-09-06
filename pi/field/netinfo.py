#!/usr/bin/env python3
"""How this rig is actually connected -- interface, link and signal.

Nick's ask, 2026-09-06, and it comes from a real suspicion: the streams
looked worse than the boards should be capable of, and nereus002 dropped off
the network three times in one session while still powered and running. If
the limiting factor is a weak 2.4 GHz link rather than the cameras, then
every "the N6 looks bad" conclusion drawn from this page is wrong.

So the link is MEASURED and shown next to the streams, not assumed.

One thing the numbers do NOT mean: the per-camera fps on the page is counted
ON THE PI, at the moment each frame arrives from the sensor. A weak link
makes the BROWSER's view stutter while those counters stay at 15. That is
the useful split -- capture health and delivery health are different
questions, and conflating them is how a camera gets blamed for a router.
"""

import re
import subprocess

#: Rough signal bands for 2.4 GHz. A Pi Zero 2 W is 2.4 GHz only.
#: Boundaries are the conventional ones used by wifi tooling.
def signal_grade(dbm):
    """Human verdict for an RSSI in dBm. None when there is no reading."""
    if dbm is None:
        return "unknown"
    if dbm >= -55:
        return "excellent"
    if dbm >= -67:
        return "good"          # the usual floor for reliable video
    if dbm >= -75:
        return "weak"
    return "poor"


def parse_iw_link(text):
    """Pull SSID / freq / signal / bitrates out of `iw dev <if> link` output.

    Returns a dict with None for anything absent -- a missing field must read
    as unknown, never as zero, or a dead radio looks like a quiet one.
    """
    out = {"connected": False, "ssid": None, "freq_mhz": None,
           "signal_dbm": None, "rx_bitrate_mbps": None, "tx_bitrate_mbps": None}
    if not text or "Not connected" in text:
        return out
    out["connected"] = bool(re.search(r"Connected to", text))
    m = re.search(r"^\s*SSID:\s*(.+?)\s*$", text, re.M)
    if m:
        out["ssid"] = m.group(1)
    m = re.search(r"freq:\s*([0-9.]+)", text)
    if m:
        # iw prints MHz on older kernels and may print GHz-ish values on some
        # builds; normalise so the page never shows "5.18 MHz".
        val = float(m.group(1))
        out["freq_mhz"] = val * 1000 if val < 100 else val
    m = re.search(r"signal:\s*(-?\d+)", text)
    if m:
        out["signal_dbm"] = int(m.group(1))
    m = re.search(r"rx bitrate:\s*([0-9.]+)\s*MBit/s", text)
    if m:
        out["rx_bitrate_mbps"] = float(m.group(1))
    m = re.search(r"tx bitrate:\s*([0-9.]+)\s*MBit/s", text)
    if m:
        out["tx_bitrate_mbps"] = float(m.group(1))
    return out


def parse_proc_wireless(text):
    """Fallback signal read from /proc/net/wireless (no iw needed)."""
    out = {}
    for line in (text or "").splitlines()[2:]:
        if ":" not in line:
            continue
        iface, _, rest = line.partition(":")
        cols = rest.split()
        if len(cols) < 4:
            continue
        try:
            # Columns after "<iface>:" are STATUS, link, level, noise -- the
            # leading status field is easy to miss and cost a wrong sign here
            # (58 read as the RSSI instead of -68). Values carry a trailing
            # dot, e.g. "-68.".
            out[iface.strip()] = {
                "quality": float(cols[1].rstrip(".")),
                "signal_dbm": float(cols[2].rstrip(".")),
            }
        except ValueError:
            continue
    return out


def default_iface(route_text):
    """The interface carrying the default route -- what we are actually on."""
    m = re.search(r"^default .*?\bdev\s+(\S+)", route_text or "", re.M)
    return m.group(1) if m else None


#: `iw` and `ip` live in /sbin and /usr/sbin, which are NOT on a normal
#: user's PATH on Debian -- measured on nereus002, where a bare "iw" call
#: returned "command not found" while /sbin/iw worked fine. A viewer running
#: as `pi` would therefore have reported "unknown" signal forever and looked
#: like a missing radio. Resolve the absolute path instead of trusting PATH.
_SBIN = ("/sbin", "/usr/sbin", "/bin", "/usr/bin")


def _which(name):
    import os
    for d in _SBIN:
        cand = os.path.join(d, name)
        if os.access(cand, os.X_OK):
            return cand
    return name          # last resort: let PATH try


def _run(argv, timeout=3):
    argv = [_which(argv[0])] + list(argv[1:])
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def net_status(run=_run, read=_read):
    """One dict describing the live link. Injected IO so it is testable."""
    iface = default_iface(run(["ip", "route"])) or "wlan0"
    wired = not iface.startswith(("wl", "wlan"))
    info = {"iface": iface, "wired": wired, "ssid": None, "freq_mhz": None,
            "signal_dbm": None, "grade": "n/a" if wired else "unknown",
            "rx_bitrate_mbps": None, "tx_bitrate_mbps": None}
    if wired:
        # Ethernet: no RSSI to report, and saying "unknown" would imply a
        # radio problem where there is no radio.
        info["grade"] = "wired"
        return info
    link = parse_iw_link(run(["iw", "dev", iface, "link"]))
    info.update({k: link[k] for k in
                 ("ssid", "freq_mhz", "signal_dbm",
                  "rx_bitrate_mbps", "tx_bitrate_mbps")})
    if info["signal_dbm"] is None:
        fallback = parse_proc_wireless(read("/proc/net/wireless")).get(iface)
        if fallback:
            info["signal_dbm"] = fallback["signal_dbm"]
    info["grade"] = signal_grade(info["signal_dbm"])
    return info
