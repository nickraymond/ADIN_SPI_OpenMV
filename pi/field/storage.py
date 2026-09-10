#!/usr/bin/env python3
"""A bounded recordings store: oldest sessions are evicted so the OS never fills.

Nick's requirement: "the rig can only save videos to a specific memory ring, and
if the memory gets full we start deleting the oldest files - this way the OS
never gets corrupted."

REUSE NOTE. He pointed at `bm_cam_legacy`, expecting this to exist there. It
does NOT -- that repo carries the *spec* (TODO-BM-008) and the reporting half
(`collect_storage_health`), and states plainly "Ring buffer is intentionally not
implemented yet". So the rules below are HIS rules, taken verbatim from
TODO-BM-008 and Sprint03_metadata_sd.md, and the reporting field names match
that repo's so telemetry stays comparable across the two:

    Never delete files from the active run.
    Never delete software/config/log files.
    Only delete old local image artifacts in the recordings directory.
    Prefer deleting oldest complete image groups first.
    Keep at least the newest N captures, configurable.
    Also enforce a minimum free-space threshold.
    Dry-run mode first.
    Include telemetry when cleanup happens.

TWO INDEPENDENT LIMITS, and both are enforced:

  * the RING BUDGET -- how much the recordings directory may occupy. This is
    what stops video from ever consuming the card.
  * a MINIMUM FREE SPACE floor on the filesystem itself. The budget alone does
    not protect the OS: something else (logs, an apt upgrade, a core dump) can
    fill the card while the ring sits politely inside its quota. So eviction
    also triggers on free space, whatever the ring is using.

WHAT IS NEVER TOUCHED: anything outside the recordings root, anything in the
active session, and any session younger than the keep-latest floor. Deletion
is refused rather than attempted if a path escapes the root -- a ring buffer
that can delete the wrong directory is worse than a full disk.
"""

import errno
import os
import shutil
import stat
import time

#: Default budget for the recordings directory. Chosen so a rig recording
#: continuously cannot reach the filesystem.
#:
#: RAISED 50 -> 85 GB (Nick, 2026-09-09, Channel Islands trip): 50 GB was sized
#: for the N6 alone at ~2.3 MB/s. The trip records four streams at once --
#: IMX science 4.94 + N6 2.86 + proxy 0.35 + AE3 0.15 = 8.30 MB/s measured --
#: and there is no bigger card, so leaving 46 GB of a 96 GB card unused would
#: throw away roughly 1.5 hours of dive footage for nothing.
#:
#: The arithmetic, on this rig's 116 GB card with ~16 GB of OS: a full 85 GB
#: ring still leaves ~11 GB free, comfortably clear of the independent
#: min-free floor below. At 8.30 MB/s the ring holds ~2.8 hours of recording,
#: which is about 8 dives of 20 minutes -- and it lands within minutes of the
#: rig's measured ~3 h battery endurance (S29), so storage and power now run
#: out together rather than one wasting the other.
DEFAULT_RING_BYTES = 85 * 1000 ** 3

#: Never let the filesystem go below this, regardless of the ring's own usage.
#: Nick's spec named 2 GB; kept, because the failure it prevents (a full root)
#: is the one that corrupts an OS.
DEFAULT_MIN_FREE_BYTES = 2 * 1000 ** 3

#: Always keep at least this many of the newest sessions, even if that means
#: exceeding the budget. A ring that deletes the recording you just made in
#: order to satisfy a number is not protecting anything.
DEFAULT_KEEP_LATEST = 2


def disk_health(path):
    """Filesystem usage for the volume holding `path`.

    Field names match bm_cam_legacy's `collect_storage_health` so the two rigs'
    telemetry can be read side by side.
    """
    out = {"sd_total_bytes": None, "sd_used_bytes": None,
           "sd_free_bytes": None, "sd_used_pct": None}
    try:
        u = shutil.disk_usage(path)
        out["sd_total_bytes"] = int(u.total)
        out["sd_used_bytes"] = int(u.used)
        out["sd_free_bytes"] = int(u.free)
        if u.total > 0:
            out["sd_used_pct"] = round(u.used / u.total * 100.0, 2)
    except OSError:
        pass                      # report None rather than invent a number
    return out


def dir_size_bytes(path):
    """Bytes on disk under `path`. Unreadable entries are skipped, not fatal.

    lstat, not stat: a symlink must count as the link, never as the size of
    whatever it points at, or a link into the OS would inflate the ring's
    apparent usage and trigger evictions that free nothing.
    """
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return int(total)


def list_sessions(root):
    """Recording directories, OLDEST FIRST, with their size and mtime.

    Ordered by the session directory's mtime rather than by name, so a clock
    change or a hand-renamed directory cannot invert the eviction order.
    """
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in sorted(names):
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            continue
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        out.append({"name": name, "path": p, "bytes": dir_size_bytes(p),
                    "mtime": mtime,
                    "complete": os.path.isfile(os.path.join(p, "manifest.json"))})
    out.sort(key=lambda s: s["mtime"])
    return out


def plan_eviction(sessions, ring_bytes, free_bytes=None,
                  min_free_bytes=DEFAULT_MIN_FREE_BYTES,
                  keep_latest=DEFAULT_KEEP_LATEST, active=None,
                  need_bytes=0):
    """Decide what to delete. PURE -- no filesystem writes, fully testable.

    Returns (victims, report). Victims are oldest-first and never include the
    active session or the newest `keep_latest`.

    Two triggers, either sufficient:
      * the store exceeds `ring_bytes` (optionally plus `need_bytes` headroom
        for the recording about to start), or
      * the filesystem's free space is below `min_free_bytes`.
    """
    total = sum(s["bytes"] for s in sessions)
    protected = set()
    if active:
        protected.add(active)
    # Newest N are protected. `sessions` is oldest-first, so that is the tail.
    for s in sessions[len(sessions) - keep_latest:] if keep_latest > 0 else []:
        protected.add(s["name"])

    over_budget = max(0, (total + need_bytes) - ring_bytes)
    short_on_free = 0
    if free_bytes is not None:
        short_on_free = max(0, (min_free_bytes + need_bytes) - free_bytes)
    must_free = max(over_budget, short_on_free)

    victims, freed = [], 0
    if must_free > 0:
        for s in sessions:                       # oldest first
            if freed >= must_free:
                break
            if s["name"] in protected:
                continue
            victims.append(s)
            freed += s["bytes"]

    report = {
        "ring_bytes": ring_bytes,
        "used_bytes": total,
        "used_pct": round(total / ring_bytes * 100.0, 2) if ring_bytes else None,
        "sessions": len(sessions),
        "need_bytes": need_bytes,
        "over_budget_bytes": over_budget,
        "short_on_free_bytes": short_on_free,
        "must_free_bytes": must_free,
        "will_free_bytes": freed,
        "victims": [s["name"] for s in victims],
        "protected": sorted(protected),
        # If this is set, the ring CANNOT satisfy the request without deleting
        # something it is forbidden to delete. Reported, never worked around.
        "shortfall_bytes": max(0, must_free - freed),
    }
    return victims, report


def _safe_under(root, path):
    """True only if `path` really lives under `root`. Guards every delete."""
    root_r = os.path.realpath(root)
    path_r = os.path.realpath(path)
    return path_r.startswith(root_r + os.sep) and path_r != root_r


def enforce(root, ring_bytes=DEFAULT_RING_BYTES,
            min_free_bytes=DEFAULT_MIN_FREE_BYTES,
            keep_latest=DEFAULT_KEEP_LATEST, active=None, need_bytes=0,
            dry_run=False, log=None):
    """Bring the store within its limits. Returns a telemetry report.

    `dry_run` honours Nick's "dry-run mode first": it computes and reports the
    exact same plan and deletes nothing.
    """
    log = log or (lambda _m: None)
    sessions = list_sessions(root)
    health = disk_health(root)
    victims, report = plan_eviction(
        sessions, ring_bytes, health.get("sd_free_bytes"), min_free_bytes,
        keep_latest, active, need_bytes)
    report.update(health)
    report["dry_run"] = bool(dry_run)
    report["deleted"] = []
    report["failed"] = []
    report["when"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    if report["shortfall_bytes"] > 0:
        log("storage: cannot free %.1f GB without touching protected sessions "
            "(active or newest %d) -- recording anyway, but the store is over "
            "its limit" % (report["shortfall_bytes"] / 1e9, keep_latest))

    for s in victims:
        if not _safe_under(root, s["path"]):
            # Cannot happen from list_sessions, and is refused anyway: a ring
            # buffer that deletes outside its root is worse than a full disk.
            report["failed"].append({"name": s["name"], "err": "outside the root"})
            continue
        if dry_run:
            log("storage: WOULD delete %s (%.2f GB)" % (s["name"], s["bytes"] / 1e9))
            report["deleted"].append(s["name"])
            continue
        try:
            shutil.rmtree(s["path"])
            log("storage: evicted %s (%.2f GB, oldest)"
                % (s["name"], s["bytes"] / 1e9))
            report["deleted"].append(s["name"])
        except OSError as e:
            if e.errno == errno.ENOENT:
                report["deleted"].append(s["name"])
            else:
                log("storage: FAILED to evict %s: %s" % (s["name"], e))
                report["failed"].append({"name": s["name"], "err": str(e)})

    # Report the store as it is NOW, not as it was before evicting. The plan's
    # `used_bytes` is the pre-eviction total, and printing that after a
    # successful eviction said "ring 2.83 / 2.50 GB used" immediately after
    # freeing 0.95 GB -- a number that contradicted the action just taken.
    if report["deleted"] and not dry_run:
        freed = sum(s["bytes"] for s in victims
                    if s["name"] in report["deleted"])
        report["freed_bytes"] = freed
        report["used_bytes"] = max(0, report["used_bytes"] - freed)
        if ring_bytes:
            report["used_pct"] = round(report["used_bytes"] / ring_bytes * 100.0, 2)
    else:
        report["freed_bytes"] = 0
    return report


def wipe_all(root, active=None, dry_run=False, log=None):
    """Delete EVERY recording session under `root`. Nick's clean-slate button.

    Deliberately separate from enforce(): eviction protects the newest
    sessions and the active one because its job is to make room without
    losing work. This one is the opposite intent -- the operator has decided
    the card should be empty -- so keep-latest does NOT apply.

    What it will still refuse, because these are mistakes and not choices:
      * the session currently being RECORDED, which would delete a file that
        is open and leave a half-written clip claiming to be a recording;
      * anything that does not resolve to a directory under `root`, checked
        with the same realpath guard eviction uses.

    Returns the same shape of report as enforce(), so the page can render one
    and the operator sees exactly what went and what did not.
    """
    log = log or (lambda _m: None)
    sessions = list_sessions(root)
    report = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "dry_run": bool(dry_run), "deleted": [], "failed": [],
              "skipped": [], "freed_bytes": 0,
              "candidates": len(sessions)}
    for s in sessions:
        if active and s["name"] == active:
            log("storage: NOT wiping %s -- it is being recorded" % s["name"])
            report["skipped"].append({"name": s["name"],
                                      "why": "currently recording"})
            continue
        if not _safe_under(root, s["path"]):
            report["failed"].append({"name": s["name"],
                                     "err": "outside the root"})
            continue
        if dry_run:
            report["deleted"].append(s["name"])
            report["freed_bytes"] += s["bytes"]
            continue
        try:
            shutil.rmtree(s["path"])
            log("storage: wiped %s (%.2f GB)" % (s["name"], s["bytes"] / 1e9))
            report["deleted"].append(s["name"])
            report["freed_bytes"] += s["bytes"]
        except OSError as e:
            if e.errno == errno.ENOENT:
                report["deleted"].append(s["name"])
            else:
                log("storage: FAILED to wipe %s: %s" % (s["name"], e))
                report["failed"].append({"name": s["name"], "err": str(e)})
    report.update(disk_health(root))
    return report


def status(root, ring_bytes=DEFAULT_RING_BYTES,
           min_free_bytes=DEFAULT_MIN_FREE_BYTES):
    """Everything the dashboard needs, with no side effects at all."""
    sessions = list_sessions(root)
    used = sum(s["bytes"] for s in sessions)
    out = {"root": root, "ring_bytes": ring_bytes,
           "ring_used_bytes": used,
           "ring_free_bytes": max(0, ring_bytes - used),
           "ring_used_pct": round(used / ring_bytes * 100.0, 2) if ring_bytes else None,
           "sessions": len(sessions),
           "min_free_bytes": min_free_bytes,
           "oldest": sessions[0]["name"] if sessions else None,
           "newest": sessions[-1]["name"] if sessions else None}
    out.update(disk_health(root))
    return out


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.expanduser("~/recordings"))
    ap.add_argument("--ring-gb", type=float, default=DEFAULT_RING_BYTES / 1e9)
    ap.add_argument("--min-free-gb", type=float,
                    default=DEFAULT_MIN_FREE_BYTES / 1e9)
    ap.add_argument("--keep-latest", type=int, default=DEFAULT_KEEP_LATEST)
    ap.add_argument("--enforce", action="store_true",
                    help="actually delete; without this it only reports")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --enforce, print what WOULD be deleted")
    a = ap.parse_args()
    if a.enforce:
        print(json.dumps(enforce(a.root, int(a.ring_gb * 1e9),
                                 int(a.min_free_gb * 1e9), a.keep_latest,
                                 dry_run=a.dry_run, log=print), indent=1))
    else:
        print(json.dumps(status(a.root, int(a.ring_gb * 1e9),
                                int(a.min_free_gb * 1e9)), indent=1))
