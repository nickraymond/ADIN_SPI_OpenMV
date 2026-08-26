#!/usr/bin/env python3
"""workbench.py -- S25: the machine-vision workbench on nereus000.

Boot the Pi, open a page, pick a test. Bite 1 shipped the MENU: recipes
listed from TOML files, passive preflight (by-id presence, port holders,
disk). Bite 2 adds the RUNNER and the single-owner board lock: a Start
button per recipe, a live state machine (idle -> starting -> live /
failed), a global Stop, and a "dev mode" verdict that says when the
boards are free for mpremote/flashing again.

The lock rules, born from a day of wedged boards (2026-08-20):

  * ONE demo at a time, enforced server-side; a second Start is refused
    loudly (409), never queued.
  * Start re-runs preflight and refuses if any needed port is held.
  * The workbench only ever signals processes IT started. A foreign
    port holder is named, with the manual command -- never killed: a
    kill button aimed at an arbitrary holder could shoot an mpremote
    flash mid-write.
  * Stop = SIGINT to the process group (the viewer's clean-teardown
    path), grace, then SIGTERM, grace -- NEVER SIGKILL (measured: a
    SIGKILLed viewer left the N6 streaming into a closed endpoint and
    took it off the USB bus; physical replug required).
  * A pidfile survives a workbench restart, so a restarted server
    re-adopts (or reports) the demo instead of double-starting it.

"Live" is not inferred: the runner polls the recipe's [health] http URL
until it answers 200, and only then does the page flip to LIVE and show
the demo link. A child that dies first is reported failed, with rc and
its log tail.

Run:  python3 pi/workbench/workbench.py     # page at http://<host>:8088/
Unit: pi/services/workbench.service         # enabled at boot -- the point
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RECIPE_DIR = os.path.join(HERE, "recipes")
STATIC = os.path.join(HERE, "static")
BY_ID_DIR = "/dev/serial/by-id"

DISK_MIN_FREE_MB = 500

# ---------------------------------------------------------------------------
# Recipe schema. Unknown keys are ERRORS, not ignored: the format is being
# proven, and a typo that silently drops a field would surface much later
# as a runner doing the wrong thing.
# ---------------------------------------------------------------------------

TOP_KEYS = {"name", "title", "summary", "opens", "thumbnail", "services",
            "boards", "run", "health", "guide"}
BOARD_KEYS = {"label", "by_id", "firmware", "models"}
MODEL_KEYS = {"name", "path", "sha256", "src"}
RUN_KEYS = {"argv", "cwd", "stop_grace"}
STOP_GRACE_MAX = 120
HEALTH_KEYS = {"http"}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
THUMB_RE = re.compile(r"^thumbs/[A-Za-z0-9._-]+$")
GUIDE_RE = re.compile(r"^guides/[A-Za-z0-9._-]+\.html$")


def _unknown(obj, allowed, where, errs):
    extra = sorted(set(obj) - allowed)
    if extra:
        errs.append("%s: unknown key(s) %s" % (where, ", ".join(extra)))


def _str(obj, key, where, errs, required=False):
    v = obj.get(key)
    if v is None:
        if required:
            errs.append("%s: missing required key '%s'" % (where, key))
        return None
    if not isinstance(v, str) or not v.strip():
        errs.append("%s: '%s' must be a non-empty string" % (where, key))
        return None
    return v


def _validate_board(b, i, errs):
    where = "boards[%d]" % i
    if not isinstance(b, dict):
        errs.append("%s: must be a table" % where)
        return None
    _unknown(b, BOARD_KEYS, where, errs)
    label = _str(b, "label", where, errs, required=True)
    by_id = _str(b, "by_id", where, errs, required=True)
    if by_id and "/" in by_id:
        errs.append("%s: by_id is a name under %s, not a path"
                    % (where, BY_ID_DIR))
    firmware = _str(b, "firmware", where, errs)
    models = b.get("models", [])
    if not isinstance(models, list):
        errs.append("%s: models must be an array of tables" % where)
        models = []
    out_models = []
    for j, m in enumerate(models):
        mw = "%s.models[%d]" % (where, j)
        if not isinstance(m, dict):
            errs.append("%s: must be a table" % mw)
            continue
        _unknown(m, MODEL_KEYS, mw, errs)
        name = _str(m, "name", mw, errs, required=True)
        path = _str(m, "path", mw, errs, required=True)
        sha = _str(m, "sha256", mw, errs, required=True)
        if sha and not SHA256_RE.match(sha):
            errs.append("%s: sha256 must be 64 lowercase hex chars" % mw)
        # src = repo-relative artifact for the copy-route repair (AE3
        # /flash). Absent -> verify-only: drift is reported, not repaired
        # (the N6's models live in a ROMFS partition; that flash stays a
        # deliberate human/agent act, never a page side effect).
        src = _str(m, "src", mw, errs)
        if src and (os.path.isabs(src) or ".." in src.split("/")):
            errs.append("%s: src must be a repo-relative path without '..'"
                        % mw)
        out_models.append({"name": name, "path": path, "sha256": sha,
                           "src": src})
    return {"label": label, "by_id": by_id, "firmware": firmware,
            "models": out_models}


def validate_recipe(obj, source):
    """Return (recipe, errors). recipe is None when errors is non-empty."""
    errs = []
    if not isinstance(obj, dict):
        return None, ["%s: top level must be a table" % source]
    _unknown(obj, TOP_KEYS, source, errs)

    name = _str(obj, "name", source, errs, required=True)
    if name and not NAME_RE.match(name):
        errs.append("%s: name %r must match %s" % (source, name,
                                                   NAME_RE.pattern))
    title = _str(obj, "title", source, errs, required=True)
    summary = _str(obj, "summary", source, errs)
    opens = _str(obj, "opens", source, errs)
    thumbnail = _str(obj, "thumbnail", source, errs)
    if thumbnail and not THUMB_RE.match(thumbnail):
        errs.append("%s: thumbnail must look like thumbs/<file> (an image "
                    "shipped in the recipes dir)" % source)

    services = obj.get("services", [])
    if (not isinstance(services, list)
            or not all(isinstance(s, str) and s.endswith(".service")
                       for s in services)):
        errs.append("%s: services must be an array of '*.service' names"
                    % source)
        services = []

    # A "guide card" (S8 B3): a documentation chapter in the menu. It runs
    # nothing and owns no boards -- clicking it opens a page served from
    # recipes/guides/. Mutually exclusive with run/health/boards/services so
    # a half-and-half recipe cannot exist ambiguously.
    guide = _str(obj, "guide", source, errs)
    if guide:
        if not GUIDE_RE.match(guide):
            errs.append("%s: guide must look like guides/<file>.html "
                        "(shipped in the recipes dir)" % source)
        for k in ("run", "health", "boards", "services"):
            if k in obj:
                errs.append("%s: a guide card cannot carry '%s' -- it is "
                            "documentation, not a runnable demo" % (source, k))
        if errs:
            return None, errs
        return {"name": name, "title": title, "summary": summary,
                "opens": None, "thumbnail": thumbnail, "services": [],
                "boards": [], "run": None, "health": None,
                "guide": guide}, []

    boards_raw = obj.get("boards")
    boards = []
    if not isinstance(boards_raw, list) or not boards_raw:
        errs.append("%s: at least one [[boards]] table is required" % source)
    else:
        for i, b in enumerate(boards_raw):
            out = _validate_board(b, i, errs)
            if out:
                boards.append(out)
        labels = [b["label"] for b in boards if b["label"]]
        if len(labels) != len(set(labels)):
            errs.append("%s: board labels must be unique" % source)

    run = None
    if "run" in obj:
        r, where = obj["run"], "%s [run]" % source
        if not isinstance(r, dict):
            errs.append("%s: must be a table" % where)
        else:
            _unknown(r, RUN_KEYS, where, errs)
            # stop_grace: seconds the runner waits after SIGINT before
            # escalating (default Runner.GRACE_INT). For children whose
            # clean stop does real work -- the HIL review wrapper scores
            # collected frames and writes overlays before exiting.
            sg = r.get("stop_grace")
            if sg is not None and (isinstance(sg, bool)
                                   or not isinstance(sg, int)
                                   or not 1 <= sg <= STOP_GRACE_MAX):
                errs.append("%s: stop_grace must be an integer 1..%d "
                            "(seconds)" % (where, STOP_GRACE_MAX))
                sg = None
            argv = r.get("argv")
            if (not isinstance(argv, list) or not argv
                    or not all(isinstance(a, str) and a for a in argv)):
                errs.append("%s: argv must be a non-empty array of "
                            "non-empty strings" % where)
            else:
                run = {"argv": argv, "cwd": r.get("cwd", "."),
                       "stop_grace": sg}

    health = None
    if "health" in obj:
        h, where = obj["health"], "%s [health]" % source
        if not isinstance(h, dict):
            errs.append("%s: must be a table" % where)
        else:
            _unknown(h, HEALTH_KEYS, where, errs)
            health = {"http": _str(h, "http", where, errs)}

    if errs:
        return None, errs
    return {"name": name, "title": title, "summary": summary, "opens": opens,
            "thumbnail": thumbnail, "services": services, "boards": boards,
            "run": run, "health": health, "guide": None}, []


def load_recipes(dirpath=RECIPE_DIR):
    """Read every recipes/*.toml -> (recipes, problems).

    problems is a list of {file, error} -- a broken file becomes a red
    card on the page, never a silent absence.
    """
    recipes, problems, seen = [], [], {}
    if tomllib is None:  # pragma: no cover
        return [], [{"file": dirpath,
                     "error": "python >= 3.11 required (tomllib missing)"}]
    try:
        names = sorted(n for n in os.listdir(dirpath) if n.endswith(".toml"))
    except OSError as e:
        return [], [{"file": dirpath, "error": "cannot list: %s" % e}]
    for fname in names:
        path = os.path.join(dirpath, fname)
        try:
            with open(path, "rb") as fh:
                obj = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as e:
            problems.append({"file": fname, "error": str(e)})
            continue
        recipe, errs = validate_recipe(obj, fname)
        if errs:
            problems.append({"file": fname, "error": "; ".join(errs)})
            continue
        if recipe["name"] in seen:
            problems.append({"file": fname,
                             "error": "duplicate recipe name %r (also in %s)"
                             % (recipe["name"], seen[recipe["name"]])})
            continue
        seen[recipe["name"]] = fname
        recipe["file"] = fname
        recipes.append(recipe)
    return recipes, problems


# ---------------------------------------------------------------------------
# Preflight -- all passive, no serial-port contact (see module docstring).
# ---------------------------------------------------------------------------

def scan_port_holders(dev_real, proc="/proc"):
    """Which same-user processes hold the tty at ``dev_real`` open."""
    holders = []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return holders
    for pid in pids:
        fddir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fddir)
        except OSError:
            continue  # not ours, or the process is gone
        for fd in fds:
            try:
                tgt = os.readlink(os.path.join(fddir, fd))
            except OSError:
                continue
            if tgt == dev_real:
                try:
                    with open(os.path.join(proc, pid, "cmdline"), "rb") as fh:
                        cmd = fh.read().replace(b"\0", b" ").decode(
                            "utf-8", "replace").strip()
                except OSError:
                    cmd = "?"
                holders.append({"pid": int(pid), "cmd": cmd[:200]})
                break
    return holders


def board_preflight(by_id, dev_dir=BY_ID_DIR, proc="/proc"):
    """One board's passive state: waiting (absent) / ready / held."""
    link = os.path.join(dev_dir, by_id)
    if not os.path.exists(link):
        return {"by_id": by_id, "state": "waiting", "tty": None,
                "holders": []}
    real = os.path.realpath(link)
    holders = scan_port_holders(real, proc)
    return {"by_id": by_id, "state": "held" if holders else "ready",
            "tty": real, "holders": holders}


def _systemctl_state(unit):
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, "-p", "ActiveState", "--value"],
            capture_output=True, text=True, timeout=5)
        state = out.stdout.strip()
        return state if state else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def service_states(units, runner=_systemctl_state):
    return {u: runner(u) for u in units}


def preflight(recipes, dev_dir=BY_ID_DIR, proc="/proc",
              runner=_systemctl_state, disk_path=REPO):
    """The panel: every board any recipe names, disk, and only the
    services a recipe DECLARES it needs (there is no standing unit list;
    today's CV bench needs none -- Nick, 2026-08-20)."""
    boards, units = {}, []
    for r in recipes:
        for b in r["boards"]:
            if b["by_id"] not in boards:
                entry = board_preflight(b["by_id"], dev_dir, proc)
                entry["label"] = b["label"]
                boards[b["by_id"]] = entry
        for u in r.get("services") or []:
            if u not in units:
                units.append(u)
    try:
        du = shutil.disk_usage(disk_path)
        free_mb = du.free // (1024 * 1024)
        disk = {"free_mb": free_mb, "ok": free_mb >= DISK_MIN_FREE_MB}
    except OSError as e:
        disk = {"free_mb": None, "ok": False, "error": str(e)}
    return {"boards": list(boards.values()), "disk": disk,
            "services": service_states(units, runner=runner),
            "ts": time.time()}


# ---------------------------------------------------------------------------
# The runner -- one demo at a time, owned start to stop.
# ---------------------------------------------------------------------------

class StartRefused(Exception):
    """A refusal the operator must read (409 on the wire)."""


class ReconcileFailed(Exception):
    """Drift or a probe failure that stops the bring-up (state -> failed)."""


# One mpremote operation per invocation (ae3-board-access rule: no
# chaining). The probe is a single exec that prints one JSON line.
_PROBE_SCRIPT = r"""
import sys, json, binascii
try:
    import hashlib
except ImportError:
    import uhashlib as hashlib
out = {"version": sys.version, "models": {}}
for p in %s:
    try:
        s = hashlib.sha256()
        f = open(p, "rb")
        while True:
            b = f.read(4096)
            if not b:
                break
            s.update(b)
        f.close()
        out["models"][p] = [binascii.hexlify(s.digest()).decode(), None]
    except Exception as e:
        out["models"][p] = [None, repr(e)]
print("RECON:" + json.dumps(out))
"""


def _mpremote_bin():
    return (shutil.which("mpremote")
            or os.path.expanduser("~/.local/bin/mpremote"))


def mpremote_probe(by_id, model_paths, timeout=30):
    """One serialized board read: sys.version + sha256 of each path.

    Returns {"version": str, "models": {path: (sha_or_None, err_or_None)}}.
    Raises ReconcileFailed with the board's own words on any failure.
    """
    dev = os.path.join(BY_ID_DIR, by_id)
    script = _PROBE_SCRIPT % json.dumps(list(model_paths))
    try:
        out = subprocess.run(
            [_mpremote_bin(), "connect", dev, "exec", script],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ReconcileFailed("probe of %s timed out after %ds" % (by_id,
                                                                   timeout))
    except OSError as e:
        raise ReconcileFailed("cannot run mpremote: %s" % e)
    for line in out.stdout.splitlines():
        if line.startswith("RECON:"):
            obj = json.loads(line[len("RECON:"):])
            return {"version": obj["version"],
                    "models": {p: tuple(v) for p, v in
                               obj["models"].items()}}
    raise ReconcileFailed(
        "probe of %s failed (rc=%d): %s" % (
            by_id, out.returncode,
            (out.stderr or out.stdout or "no output").strip()[-300:]))


def mpremote_cp(by_id, src_abs, board_path, timeout=120):
    """One serialized copy to the board (the AE3 /flash repair route)."""
    dev = os.path.join(BY_ID_DIR, by_id)
    try:
        out = subprocess.run(
            [_mpremote_bin(), "connect", dev, "cp", src_abs,
             ":" + board_path],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ReconcileFailed("repair copy to %s timed out" % by_id)
    except OSError as e:
        raise ReconcileFailed("cannot run mpremote: %s" % e)
    if out.returncode != 0:
        raise ReconcileFailed("repair copy to %s failed: %s" % (
            by_id, (out.stderr or out.stdout or "?").strip()[-300:]))


class Runner:
    """Single-owner demo runner.

    States: idle -> starting -> live -> stopping -> idle
                       \\-> failed (child died / health timeout)
            stopping -> stuck (ignored SIGINT and SIGTERM; manual step)

    Never SIGKILL: a SIGKILLed viewer skipped board teardown and took
    the N6 off the USB bus entirely (S8, measured). If SIGINT+SIGTERM
    both fail the state goes to "stuck" and the page shows the manual
    command instead.
    """

    GRACE_INT = 10.0
    GRACE_TERM = 5.0
    HEALTH_TIMEOUT = 60.0
    POLL = 0.5
    # Measured 2026-08-20 (Nick's stop->start test): the AE3 tolerates NO
    # quick reattach -- its teardown/quiet-exit needs ~30 s of silence, and
    # an immediate restart lands in a raw-repl refusal that then takes a
    # recovery ladder to clear. So a stopped board cannot be started again
    # until this many seconds have passed with the port untouched.
    SETTLE = 35.0

    # Gap between the last reconcile board op and the app's own attach --
    # sequential clean sessions are fine, but give the board air.
    RECON_GAP = 3.0

    def __init__(self, repo=REPO, pidfile="/tmp/workbench-run.json",
                 logpath="/tmp/workbench-run.log",
                 probe=mpremote_probe, repair=mpremote_cp):
        self.repo = repo
        self.pidfile = pidfile
        self.logpath = logpath
        self._probe = probe
        self._repair = repair
        self._lk = threading.Lock()
        self.state = "idle"
        self.recipe = None      # the recipe dict while not idle
        self.error = None
        self.started = None
        self.proc = None        # Popen when started by us
        self.pid = None         # always set while owning something
        self.settle_until = 0.0  # monotonic; boards quiet until then
        self.settle_boards = set()
        self.reconcile_report = []
        self._adopt()

    # -- pidfile ----------------------------------------------------------
    def _write_pidfile(self):
        tmp = self.pidfile + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"pid": self.pid, "recipe": self.recipe["name"],
                       "started": self.started}, fh)
        os.replace(tmp, self.pidfile)

    def _clear_pidfile(self):
        try:
            os.unlink(self.pidfile)
        except OSError:
            pass

    def _adopt(self):
        """After a workbench restart: re-own a still-running demo (so Stop
        keeps working) or clean up a stale pidfile."""
        try:
            with open(self.pidfile) as fh:
                rec = json.load(fh)
            pid = int(rec["pid"])
            os.kill(pid, 0)  # raises if gone
        except (OSError, ValueError, KeyError):
            self._clear_pidfile()
            return
        self.pid = pid
        self.started = rec.get("started")
        self.recipe = {"name": rec.get("recipe", "?"),
                       "title": rec.get("recipe", "?"), "opens": None,
                       "health": None, "boards": []}
        self.state = "live"  # adopted: it was running; health rebinds below
        threading.Thread(target=self._watch_adopted, daemon=True).start()

    def rebind(self, recipes):
        """Give an adopted runner its full recipe back (called per request;
        cheap, idempotent)."""
        with self._lk:
            if self.recipe and self.recipe.get("boards") == []:
                for r in recipes:
                    if r["name"] == self.recipe["name"]:
                        self.recipe = r
                        break

    # -- lifecycle --------------------------------------------------------
    def start(self, recipe, board_states):
        with self._lk:
            if self.state in ("reconciling", "starting", "live", "stopping"):
                raise StartRefused(
                    "'%s' is already %s -- one demo at a time; stop it first"
                    % (self.recipe["name"], self.state))
            if self.state == "stuck":
                raise StartRefused(
                    "the previous demo ignored SIGINT and SIGTERM and is "
                    "still holding its boards -- clear it manually first")
            if not recipe.get("run"):
                raise StartRefused("recipe '%s' has no [run] block"
                                   % recipe["name"])
            needed = {b["by_id"] for b in recipe["boards"]}
            remaining = self.settle_until - time.monotonic()
            if remaining > 0 and needed & self.settle_boards:
                raise StartRefused(
                    "the boards are settling after the last stop -- the AE3 "
                    "needs ~%.0f s of silence before a reattach or it wedges. "
                    "Try again in %d s." % (self.SETTLE, int(remaining) + 1))
            for b in board_states:
                if b["state"] == "waiting":
                    raise StartRefused(
                        "board %s (%s) is not enumerated -- plug it in or "
                        "wait a few seconds after boot"
                        % (b.get("label", "?"), b["by_id"]))
                if b["state"] == "held":
                    h = b["holders"][0]
                    raise StartRefused(
                        "board %s's port is held by pid %d (%s), which the "
                        "workbench did not start and will not kill. Free it "
                        "yourself, then start again."
                        % (b.get("label", "?"), h["pid"], h["cmd"]))
            self.recipe = recipe
            self.error = None
            self.started = time.time()
            self.proc = None
            self.pid = None
            self.reconcile_report = []
            self.state = "reconciling"
        threading.Thread(target=self._bringup, args=(recipe,),
                         daemon=True).start()
        print("runner: bring-up of %s (reconcile -> spawn)"
              % recipe["name"], flush=True)

    # -- reconcile (bite 3): verify declared state, repair only drift ----
    def _reconcile(self, recipe):
        """Returns (report, touched_a_board). Raises ReconcileFailed on
        drift it cannot repair, a failed repair, or a failed probe.
        Repair route is the file copy (src declared) ONLY -- firmware and
        partition-flash drift are reported with the manual step, never
        performed by this page."""
        report, touched = [], False
        for b in recipe["boards"]:
            expect_fw = b.get("firmware")
            models = b.get("models") or []
            if not expect_fw and not models:
                continue
            touched = True
            probe = self._probe(b["by_id"], [m["path"] for m in models])
            entry = {"label": b.get("label"), "checks": []}
            report.append(entry)
            self.reconcile_report = report  # partial visibility on failure
            if expect_fw:
                ok = expect_fw in probe["version"]
                entry["checks"].append(
                    {"kind": "firmware", "expected": expect_fw,
                     "found": probe["version"], "ok": ok, "repaired": False})
                if not ok:
                    raise ReconcileFailed(
                        "board %s firmware drift: recipe expects %r, board "
                        "reports %r. Firmware flashing is never a page side "
                        "effect -- reflash via the S7 ladder "
                        "(pi/ae3_flash/README.md) or update the recipe."
                        % (b.get("label"), expect_fw, probe["version"]))
            for m in models:
                sha, err = probe["models"].get(m["path"], (None, "not probed"))
                if sha == m["sha256"]:
                    entry["checks"].append(
                        {"kind": "model", "path": m["path"], "ok": True,
                         "repaired": False})
                    continue
                found = sha or ("missing: %s" % err)
                if not m.get("src"):
                    raise ReconcileFailed(
                        "board %s model %s drifted (found %s, expected %s) "
                        "and the recipe declares no src to repair from -- "
                        "this route (e.g. N6 ROMFS) is a deliberate manual "
                        "deploy, see ml/README.md."
                        % (b.get("label"), m["path"], found, m["sha256"]))
                src_abs = os.path.join(self.repo, m["src"])
                try:
                    with open(src_abs, "rb") as fh:
                        src_sha = hashlib.sha256(fh.read()).hexdigest()
                except OSError as e:
                    raise ReconcileFailed("repair source %s unreadable: %s"
                                          % (m["src"], e))
                if src_sha != m["sha256"]:
                    raise ReconcileFailed(
                        "repo artifact %s hashes to %s, not the declared "
                        "%s -- fix the recipe before it repairs boards "
                        "with the wrong bytes." % (m["src"], src_sha,
                                                   m["sha256"]))
                print("runner: DRIFT on %s %s (found %s) -- repairing from "
                      "%s" % (b.get("label"), m["path"], found, m["src"]),
                      flush=True)
                self._repair(b["by_id"], src_abs, m["path"])
                # Trust the bytes, not the copy's rc: read the sha back.
                re_probe = self._probe(b["by_id"], [m["path"]])
                sha2, err2 = re_probe["models"].get(m["path"], (None, "?"))
                if sha2 != m["sha256"]:
                    raise ReconcileFailed(
                        "repair of %s did NOT verify: read back %s"
                        % (m["path"], sha2 or err2))
                entry["checks"].append(
                    {"kind": "model", "path": m["path"], "ok": True,
                     "repaired": True})
        return report, touched

    def _bringup(self, recipe):
        try:
            report, touched = self._reconcile(recipe)
        except ReconcileFailed as e:
            with self._lk:
                if self.state == "reconciling":
                    self.state = "failed"
                    self.error = str(e)
                    self._arm_settle()
            print("runner: RECONCILE FAILED -- %s" % e, flush=True)
            return
        with self._lk:
            if self.state != "reconciling":
                return
            self.reconcile_report = report
        if touched and self.RECON_GAP:
            time.sleep(self.RECON_GAP)  # air between probe and app attach
        with self._lk:
            if self.state != "reconciling":
                return
            logf = open(self.logpath, "wb")
            try:
                self.proc = subprocess.Popen(
                    recipe["run"]["argv"],
                    cwd=os.path.join(self.repo, recipe["run"]["cwd"]),
                    stdout=logf, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                logf.close()
                self.state = "failed"
                self.error = ("cannot exec %s: %s"
                              % (recipe["run"]["argv"][0], e))
                self._arm_settle()
                return
            logf.close()  # child holds its own copy
            self.pid = self.proc.pid
            self.state = "starting"
            self._write_pidfile()
        print("runner: started %s (pid %d)" % (recipe["name"], self.pid),
              flush=True)
        self._watch_start()

    def _arm_settle(self):
        """Called (under the lock) whenever the demo releases its boards."""
        if self.recipe:
            self.settle_boards = {b["by_id"]
                                  for b in self.recipe.get("boards", [])}
            self.settle_until = time.monotonic() + self.SETTLE

    def _health_url(self):
        h = self.recipe.get("health") if self.recipe else None
        return h.get("http") if h else None

    def _health_ok(self):
        url = self._health_url()
        if not url:
            return True  # nothing to poll -- running is the best we know
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 200
        except OSError:
            return False

    def _watch_start(self):
        deadline = time.monotonic() + self.HEALTH_TIMEOUT
        while True:
            time.sleep(self.POLL)
            with self._lk:
                if self.state != "starting":
                    return
                rc = self.proc.poll() if self.proc else None
                if rc is not None:
                    self.state = "failed"
                    self.error = ("exited rc=%d after %.1f s"
                                  % (rc, time.time() - self.started))
                    self._arm_settle()
                    self._clear_pidfile()
                    print("runner: FAILED -- %s" % self.error, flush=True)
                    return
            if self._health_ok():
                with self._lk:
                    if self.state == "starting":
                        self.state = "live"
                        print("runner: LIVE -- %s"
                              % self.recipe["name"], flush=True)
                threading.Thread(target=self._watch_live,
                                 daemon=True).start()
                return
            if time.monotonic() > deadline:
                print("runner: health timeout -- stopping the child",
                      flush=True)
                self.stop(reason="health check never answered -- stopped")
                with self._lk:
                    if self.state == "idle":
                        self.state = "failed"
                        self.error = ("health check %s never answered "
                                      "within %.0f s" % (self._health_url(),
                                                         self.HEALTH_TIMEOUT))
                return

    def _watch_live(self):
        while True:
            time.sleep(self.POLL * 2)
            with self._lk:
                if self.state != "live":
                    return
                rc = self.proc.poll() if self.proc else None
                if rc is not None:
                    self.state = "failed"
                    self.error = "exited rc=%d while live" % rc
                    self._arm_settle()
                    self._clear_pidfile()
                    print("runner: FAILED -- %s" % self.error, flush=True)
                    return

    def _watch_adopted(self):
        while True:
            time.sleep(1.0)
            with self._lk:
                if self.state != "live":
                    return
                try:
                    os.kill(self.pid, 0)
                except OSError:
                    self.state = "idle"
                    self.recipe = None
                    self._clear_pidfile()
                    return

    def _alive(self):
        if self.proc is not None:
            return self.proc.poll() is None
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def _signal_group(self, sig):
        try:
            os.killpg(self.pid, sig)  # start_new_session=True -> pgid==pid
        except OSError:
            try:
                os.kill(self.pid, sig)
            except OSError:
                pass

    def _wait_gone(self, secs):
        deadline = time.monotonic() + secs
        while time.monotonic() < deadline:
            if not self._alive():
                return True
            time.sleep(self.POLL / 2)
        return not self._alive()

    def stop(self, reason="stopped by operator"):
        """SIGINT -> grace -> SIGTERM -> grace -> stuck. Returns final state."""
        with self._lk:
            if self.state not in ("starting", "live"):
                return self.state
            self.state = "stopping"
            # a recipe whose clean stop does real work (the HIL review
            # wrapper scores + writes overlays) declares its own grace
            run = (self.recipe or {}).get("run") or {}
            grace_int = run.get("stop_grace") or self.GRACE_INT
        print("runner: stopping (%s; SIGINT grace %.0f s)"
              % (reason, grace_int), flush=True)
        self._signal_group(signal.SIGINT)
        gone = self._wait_gone(grace_int)
        if not gone:
            self._signal_group(signal.SIGTERM)
            gone = self._wait_gone(self.GRACE_TERM)
        with self._lk:
            if gone:
                if self.proc:
                    self.proc.wait()  # reap
                self.state = "idle"
                self._arm_settle()
                self.recipe = None
                self.error = None
                self._clear_pidfile()
                print("runner: stopped, ports released; boards settle %.0f s"
                      % self.SETTLE, flush=True)
            else:
                self.state = "stuck"
                self.error = ("pid %d ignored SIGINT (%.0f s) and SIGTERM "
                              "(%.0f s); the workbench never sends SIGKILL "
                              "(it can take a board off the USB bus)"
                              % (self.pid, grace_int, self.GRACE_TERM))
                print("runner: STUCK -- %s" % self.error, flush=True)
            return self.state

    def log_tail(self, max_bytes=4096):
        try:
            with open(self.logpath, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - max_bytes))
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def snapshot(self):
        with self._lk:
            r = self.recipe
            return {"state": self.state,
                    "settle_s": max(0, int(self.settle_until
                                           - time.monotonic() + 0.999)),
                    "recipe": r["name"] if r else None,
                    "title": r.get("title") if r else None,
                    "opens": r.get("opens") if r else None,
                    "error": self.error,
                    "pid": self.pid if self.state not in ("idle",) else None,
                    "started": self.started,
                    "reconcile": self.reconcile_report
                    if self.state not in ("idle",) else [],
                    "log_tail": self.log_tail()
                    if self.state in ("starting", "failed", "stuck") else ""}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def make_handler(cfg, runner: Runner):
    recipe_dir = cfg["recipe_dir"]

    def fresh_preflight():
        recipes, _ = load_recipes(recipe_dir)
        runner.rebind(recipes)
        return recipes, preflight(
            recipes, dev_dir=cfg["dev_dir"], proc=cfg["proc"],
            runner=cfg["runner"], disk_path=cfg["disk_path"])

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "S25Workbench/2"

        def log_message(self, fmt, *args):
            pass  # the page polls; logging that buries what matters

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _body(self):
            n = self.headers.get("Content-Length")
            try:
                n = int(n)
            except (TypeError, ValueError):
                n = 0
            if n <= 0:
                return {}
            if n > 65536:
                raise ValueError("body too large")
            try:
                obj = json.loads(self.rfile.read(n).decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError("body is not JSON") from None
            if not isinstance(obj, dict):
                raise ValueError("body must be a JSON object")
            return obj

        # -- GET ----------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._page()
            if path == "/api/recipes":
                recipes, problems = load_recipes(recipe_dir)
                return self._json(200, {"recipes": recipes,
                                        "problems": problems})
            if path == "/api/preflight":
                _, pf = fresh_preflight()
                return self._json(200, pf)
            if path == "/api/runner":
                return self._json(200, runner.snapshot())
            if path.startswith("/thumbs/"):
                return self._thumb(path[len("/thumbs/"):])
            if path.startswith("/guides/"):
                return self._guide(path[len("/guides/"):])
            self.send_error(404)

        def _guide(self, name):
            # Guide chapters (S8 B3): HTML shipped in recipes/guides/, same
            # confinement rule as thumbnails.
            if not re.match(r"^[A-Za-z0-9._-]+\.html$", name) or ".." in name:
                return self.send_error(404, "no such guide")
            path = os.path.join(recipe_dir, "guides", name)
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
            except OSError:
                return self.send_error(404, "no such guide")
            self._send(200, body, "text/html; charset=utf-8")

        def _thumb(self, name):
            # Confinement: same rule as bench_web's captures -- 404 for
            # every refusal, reason in the body only.
            if not re.match(r"^[A-Za-z0-9._-]+$", name) or ".." in name:
                return self.send_error(404, "no such thumbnail")
            path = os.path.join(recipe_dir, "thumbs", name)
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
            except OSError:
                return self.send_error(404, "no such thumbnail")
            ext = name.rsplit(".", 1)[-1].lower()
            ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "png": "image/png", "gif": "image/gif"}.get(
                         ext, "application/octet-stream")
            self._send(200, body, ctype)

        def _page(self):
            try:
                with open(os.path.join(STATIC, "workbench.html"), "rb") as fh:
                    body = fh.read()
            except OSError as e:
                return self.send_error(500,
                                       "cannot read workbench.html: %s" % e)
            self._send(200, body, "text/html; charset=utf-8")

        # -- POST ---------------------------------------------------------
        def do_POST(self):
            path = self.path.split("?", 1)[0]
            try:
                body = self._body()
            except ValueError as e:
                return self._json(400, {"ok": False, "err": str(e)})
            if path == "/api/start":
                return self._start(body)
            if path == "/api/stop":
                state = runner.stop()
                return self._json(200 if state in ("idle",) else 409,
                                  {"ok": state == "idle", "state": state,
                                   "err": runner.error})
            if path == "/api/devmode":
                return self._devmode()
            self.send_error(404)

        def _start(self, body):
            name = body.get("name")
            recipes, pf = fresh_preflight()
            by_name = {r["name"]: r for r in recipes}
            if name not in by_name:
                return self._json(404, {"ok": False,
                                        "err": "no recipe named %r" % name})
            recipe = by_name[name]
            if recipe.get("guide"):
                return self._json(409, {"ok": False,
                                        "err": "%r is a guide card -- it "
                                        "documents a procedure, there is "
                                        "nothing to run" % name})
            needed = {b["by_id"] for b in recipe["boards"]}
            states = [b for b in pf["boards"] if b["by_id"] in needed]
            try:
                runner.start(recipe, states)
            except StartRefused as e:
                print("runner: REFUSED %s -- %s" % (name, e), flush=True)
                return self._json(409, {"ok": False, "err": str(e)})
            return self._json(200, {"ok": True, "state": "starting"})

        def _devmode(self):
            state = runner.stop(reason="dev mode requested")
            _, pf = fresh_preflight()
            free = all(b["state"] == "ready" for b in pf["boards"])
            return self._json(200, {
                "ok": state in ("idle", "failed") and free,
                "state": state, "err": runner.error,
                "boards": pf["boards"]})

    return Handler


def default_cfg(recipe_dir=RECIPE_DIR):
    return {"recipe_dir": recipe_dir, "dev_dir": BY_ID_DIR, "proc": "/proc",
            "runner": _systemctl_state, "disk_path": REPO}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0",
                    help="HTTP bind address (default 0.0.0.0 -- Nick's S25 "
                         "call: LAN-visible, loud banner, no auth)")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--recipes", default=RECIPE_DIR)
    args = ap.parse_args(argv)

    recipes, problems = load_recipes(args.recipes)
    runner = Runner()
    httpd = ThreadingHTTPServer((args.bind, args.port),
                                make_handler(default_cfg(args.recipes),
                                             runner))
    print("workbench: http://%s:%d/  (%d recipe(s), %d problem(s), dir %s)"
          % (args.bind, args.port, len(recipes), len(problems), args.recipes),
          flush=True)
    if runner.state != "idle":
        print("workbench: re-adopted running demo %r (pid %s)"
              % (runner.recipe["name"], runner.pid), flush=True)
    if args.bind not in ("127.0.0.1", "localhost"):
        print("WARNING: bound to %s -- this page can START AND STOP bench "
              "demos for any host on this network, with no authentication."
              % args.bind, flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
