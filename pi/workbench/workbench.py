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
            "boards", "run", "health"}
BOARD_KEYS = {"label", "by_id", "firmware", "models"}
MODEL_KEYS = {"name", "path", "sha256"}
RUN_KEYS = {"argv", "cwd"}
HEALTH_KEYS = {"http"}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
THUMB_RE = re.compile(r"^thumbs/[A-Za-z0-9._-]+$")


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
        out_models.append({"name": name, "path": path, "sha256": sha})
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
            argv = r.get("argv")
            if (not isinstance(argv, list) or not argv
                    or not all(isinstance(a, str) and a for a in argv)):
                errs.append("%s: argv must be a non-empty array of "
                            "non-empty strings" % where)
            else:
                run = {"argv": argv, "cwd": r.get("cwd", ".")}

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
            "run": run, "health": health}, []


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

    def __init__(self, repo=REPO, pidfile="/tmp/workbench-run.json",
                 logpath="/tmp/workbench-run.log"):
        self.repo = repo
        self.pidfile = pidfile
        self.logpath = logpath
        self._lk = threading.Lock()
        self.state = "idle"
        self.recipe = None      # the recipe dict while not idle
        self.error = None
        self.started = None
        self.proc = None        # Popen when started by us
        self.pid = None         # always set while owning something
        self.settle_until = 0.0  # monotonic; boards quiet until then
        self.settle_boards = set()
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
            if self.state in ("starting", "live", "stopping"):
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
            logf = open(self.logpath, "wb")
            try:
                self.proc = subprocess.Popen(
                    recipe["run"]["argv"],
                    cwd=os.path.join(self.repo, recipe["run"]["cwd"]),
                    stdout=logf, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                logf.close()
                raise StartRefused("cannot exec %s: %s"
                                   % (recipe["run"]["argv"][0], e))
            finally:
                if self.proc:
                    logf.close()  # child holds its own copy
            self.pid = self.proc.pid
            self.recipe = recipe
            self.error = None
            self.started = time.time()
            self.state = "starting"
            self._write_pidfile()
        threading.Thread(target=self._watch_start, daemon=True).start()
        print("runner: started %s (pid %d)" % (recipe["name"], self.pid),
              flush=True)

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
        print("runner: stopping (%s)" % reason, flush=True)
        self._signal_group(signal.SIGINT)
        gone = self._wait_gone(self.GRACE_INT)
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
                              % (self.pid, self.GRACE_INT, self.GRACE_TERM))
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
            self.send_error(404)

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
