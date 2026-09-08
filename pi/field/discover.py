#!/usr/bin/env python3
"""Identify the OpenMV boards on this rig by ASKING them, never by USB serial.

Every earlier tool in this repo hard-codes a board's ``/dev/serial/by-id/``
path -- all eleven S25 workbench recipes do, and so do the S8 harness commands.
That was safe while one bench owned one pair of boards. It is not safe here:

1. **The field rig's boards get swapped.** New AE3 and N6 boards arrive this
   week; a by-id path names the *chip*, so every recipe would need editing on
   swap day, and a missed edit fails as "board absent" rather than
   "board changed".
2. **by-id does not even identify a rig.** nereus002's boards were moved off
   nereus000, so BOTH hosts' configs name the same two strings. Measured
   2026-09-06: the N6's by-id serial ``020023000450433547373200`` is its
   STM32 96-bit chip UID -- it reverses byte-for-byte into the ``board_id``
   the chip itself reports, and it matches SPEC's nereus000 entry exactly.

So we do what SPEC §Board identity has said all along and what the S8 mis-run
(which benchmarked the wrong board) paid for: **identify a board by asking it.**
``omv.board_type()`` returns ``"AE3"`` / ``"N6"`` -- verified live on both
boards on this rig, 2026-09-06 -- and any replacement board answers correctly
with zero config edits.

**There is deliberately no cache.** A cached role->port map is stale in exactly
the scenario this module exists for (a board swap), and a stale map points a
recipe at the wrong chip -- the precise failure we are removing. Discovery
costs one bounded attach per port at start-up; that is cheaper than a wrong
answer.
"""

import argparse
import glob
import json
import os
import sys

#: The roles this rig knows how to place. Order is the viewer's left-to-right
#: order downstream, so it is meaningful, not alphabetical.
ROLES = ("AE3", "N6")

#: EVERY by-id entry, deduplicated by the device it actually points at.
#:
#: This used to be ``*-if00``, on the reasoning that an OpenMV board exposes
#: one CDC interface and a bare glob double-lists on some kernels. The second
#: half is true; the first is NOT, and it was falsified by hardware:
#: nereus000's N6 (2026-09-08, a different physical board from nereus002's)
#: enumerates in USB **HS** mode as
#: ``usb-MicroPython_Pyboard_Virtual_Comm_Port_in_HS_Mode_0065345D3643-if01``
#: -- interface **01**. The old glob silently found zero N6 on that rig, which
#: is precisely the hardware-swap case this module exists to survive.
#:
#: So the dedupe is done by the mechanism rather than by a naming heuristic:
#: resolve each symlink and keep one entry per underlying tty. A by-id entry
#: is always a serial device, and a port that is not a board simply fails its
#: probe and lands in ``problems`` -- which is the safe direction.
PORT_GLOB = "/dev/serial/by-id/*"

#: Bounded, and it prints BEFORE anything can fail: if ``omv`` is missing we
#: still learn what the board is from ``sys.version``, instead of getting a
#: bare traceback and no identity at all.
PROBE_SRC = (
    "import sys\n"
    "print('#VER', sys.version)\n"
    "print('#MACH', sys.implementation._machine)\n"
    "try:\n"
    "    import omv\n"
    "    print('#ROLE', omv.board_type())\n"
    "    print('#ID', omv.board_id())\n"
    "except Exception as e:\n"
    "    print('#ROLE', '?')\n"
    "    print('#ERR', e)\n"
)


class ProbeError(Exception):
    """This port did not yield a usable board identity."""


def list_ports(pattern=PORT_GLOB):
    """Serial devices that might be a board, in a stable (sorted) order.

    Deduplicated by the tty each symlink resolves to, so a kernel that
    publishes several by-id aliases for one device is probed ONCE. Probing the
    same board twice is not merely wasteful: repeated raw-REPL attaches are a
    known way to wedge the AE3 (see probe_port).
    """
    seen, out = set(), []
    for path in sorted(glob.glob(pattern)):
        try:
            real = os.path.realpath(path)
        except OSError:
            real = path
        if real in seen:
            continue
        seen.add(real)
        out.append(path)
    return out


def parse_probe(text):
    """Turn the probe's stdout into a dict. Pure parsing, so it is testable.

    Unknown ``#`` lines are ignored rather than fatal: a board running a
    newer probe can add fields without breaking an older reader.
    """
    out = {"role": "", "version": "", "machine": "", "board_id": "", "error": ""}
    keys = {"#VER": "version", "#MACH": "machine", "#ROLE": "role",
            "#ID": "board_id", "#ERR": "error"}
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        tag, _, rest = line.partition(" ")
        if tag in keys:
            out[keys[tag]] = rest.strip()
    if not out["role"] or out["role"] == "?":
        raise ProbeError("board did not report a role (%s)"
                         % (out["error"] or "no omv module"))
    return out


def probe_port(port, baudrate=115200):
    """Attach once, ask the board what it is, detach. One bounded operation.

    ONE attach, no retry loop: repeated raw-REPL attaches are themselves a
    known way to wedge the AE3 (roughly 4-6 after a teardown and it refuses
    below the Python level, curable only by a power cycle -- TRACKER S23
    bite R). Callers that want resilience back off between whole discovery
    passes; they never hammer a single port.
    """
    from mpremote.transport_serial import SerialTransport
    transport = SerialTransport(port, baudrate=baudrate)
    try:
        transport.enter_raw_repl(soft_reset=True)
        try:
            raw = transport.exec(PROBE_SRC)
        finally:
            # Leaving a board in the raw REPL makes the NEXT attach look
            # wedged, which is how a clean board acquires a false reputation.
            transport.exit_raw_repl()
    finally:
        transport.close()
    info = parse_probe(raw.decode("utf-8", "replace"))
    info["port"] = port
    return info


def discover(ports=None, prober=probe_port):
    """Map role -> board info for every port that answers.

    ``prober`` is injected so the whole placement policy is testable without
    hardware. Returns ``(found, problems)``; a port that fails to answer is
    reported, never silently dropped -- a rig running on one camera because
    the other quietly failed to probe is exactly the "plausible still image"
    class of bug this repo keeps paying for.
    """
    if ports is None:
        ports = list_ports()
    found, problems = {}, []
    for port in ports:
        try:
            info = prober(port)
        except Exception as exc:            # noqa: BLE001 - report, never raise
            problems.append("%s: %s" % (port, exc))
            continue
        role = info.get("role", "")
        if role in found:
            # Two boards claiming one role is a real hardware situation
            # (someone plugged in a spare). Refusing to choose is correct:
            # picking one silently would benchmark an unknown chip.
            problems.append(
                "two boards report role %s (%s and %s) -- unplug one"
                % (role, found[role]["port"], port))
            continue
        found[role] = info
    return found, problems


def require(found, roles=ROLES):
    """Names of the roles that are missing, in ROLES order."""
    return [r for r in roles if r not in found]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable")
    args = ap.parse_args(argv)

    ports = list_ports()
    found, problems = discover(ports)
    if args.json:
        print(json.dumps({"found": found, "problems": problems,
                          "missing": require(found)}, indent=2))
    else:
        print("ports seen: %d" % len(ports))
        for role in ROLES:
            info = found.get(role)
            if info is None:
                print("  %-4s MISSING" % role)
            else:
                print("  %-4s %s\n       %s" % (role, info["port"],
                                                info["version"]))
        for extra in sorted(set(found) - set(ROLES)):
            print("  %-4s %s (unexpected role)" % (extra, found[extra]["port"]))
        for problem in problems:
            print("  ! %s" % problem)
    return 0 if not require(found) else 1


if __name__ == "__main__":
    sys.exit(main())
