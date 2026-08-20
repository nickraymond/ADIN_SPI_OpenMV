#!/usr/bin/env python3
"""workbench.py -- S25 bite 1: the machine-vision workbench menu on nereus000.

Boot the Pi, open a page, pick a test. This module is the MENU half only:
it lists every released test recipe and shows a live preflight panel for
the hardware each one needs. It deliberately does NOT start anything --
the runner and the single-owner board lock are bite 2, and state
reconciliation (verify/repair what is on the boards) is bite 3.

Recipes are declarative TOML files in ``recipes/`` -- one per released
test setup. The registry is re-read on every request, so adding a test is
"drop a file in, refresh the page". A file that fails validation renders
as a red card naming the error; it never silently disappears.

Preflight is PASSIVE by design: this process never opens a serial port.
Two processes on one board wedges it (measured repeatedly, 2026-08-20),
so identity comes from the /dev/serial/by-id path alone and asking a
board questions waits for bite 2's lock. What preflight checks:

  * each board's by-id symlink is present (absent = amber "waiting", a
    NORMAL early state -- USB enumeration lags boot, and the panel is
    recomputed per refresh, so the lag self-heals);
  * whether any process holds the board's tty, by scanning /proc fd
    tables. No sudo: only same-user processes are visible, which covers
    this bench where everything runs as ``pi``. A root-owned holder
    would show as a free port -- bite 2's lock, not this panel, is the
    correctness mechanism;
  * disk free on the repo filesystem, and the ActiveState of the bench
    systemd units (read-only ``systemctl show``).

Run:  python3 pi/workbench/workbench.py     # page at http://<host>:8088/
Unit: pi/services/workbench.service         # enabled at boot -- the point
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
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

# The bench units the panel reports (read-only). workbench.service itself is
# omitted: if this page answered, the unit is self-evidently running.
BENCH_UNITS = (
    "bench-web.service",
    "bm-light.service",
    "bm-telemetry.service",
    "t1l-stream-server.service",
    "t1l-chunk-shim.service",
    "t1l-sender.service",
)

DISK_MIN_FREE_MB = 500

# ---------------------------------------------------------------------------
# Recipe schema. Unknown keys are ERRORS, not ignored: the format is being
# proven in this bite, and a typo that silently drops a field would surface
# much later as a runner doing the wrong thing.
# ---------------------------------------------------------------------------

TOP_KEYS = {"name", "title", "summary", "opens", "boards", "run", "health"}
BOARD_KEYS = {"label", "by_id", "firmware", "models"}
MODEL_KEYS = {"name", "path", "sha256"}
RUN_KEYS = {"argv", "cwd"}
HEALTH_KEYS = {"http"}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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
            "boards": boards, "run": run, "health": health}, []


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


def service_states(units=BENCH_UNITS, runner=_systemctl_state):
    return {u: runner(u) for u in units}


def preflight(recipes, dev_dir=BY_ID_DIR, proc="/proc",
              runner=_systemctl_state, disk_path=REPO):
    """The panel: every board any recipe names, disk, services."""
    boards = {}
    for r in recipes:
        for b in r["boards"]:
            if b["by_id"] not in boards:
                entry = board_preflight(b["by_id"], dev_dir, proc)
                entry["label"] = b["label"]
                boards[b["by_id"]] = entry
    try:
        du = shutil.disk_usage(disk_path)
        free_mb = du.free // (1024 * 1024)
        disk = {"free_mb": free_mb, "ok": free_mb >= DISK_MIN_FREE_MB}
    except OSError as e:
        disk = {"free_mb": None, "ok": False, "error": str(e)}
    return {"boards": list(boards.values()), "disk": disk,
            "services": service_states(runner=runner), "ts": time.time()}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def make_handler(cfg):
    recipe_dir = cfg["recipe_dir"]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "S25Workbench/1"

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

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._page()
            if path == "/api/recipes":
                recipes, problems = load_recipes(recipe_dir)
                return self._json(200, {"recipes": recipes,
                                        "problems": problems})
            if path == "/api/preflight":
                recipes, _ = load_recipes(recipe_dir)
                return self._json(200, preflight(
                    recipes, dev_dir=cfg["dev_dir"], proc=cfg["proc"],
                    runner=cfg["runner"], disk_path=cfg["disk_path"]))
            self.send_error(404)

        def _page(self):
            try:
                with open(os.path.join(STATIC, "workbench.html"), "rb") as fh:
                    body = fh.read()
            except OSError as e:
                return self.send_error(500, "cannot read workbench.html: %s" % e)
            self._send(200, body, "text/html; charset=utf-8")

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
    httpd = ThreadingHTTPServer((args.bind, args.port),
                                make_handler(default_cfg(args.recipes)))
    print("workbench: http://%s:%d/  (%d recipe(s), %d problem(s), dir %s)"
          % (args.bind, args.port, len(recipes), len(problems), args.recipes),
          flush=True)
    if args.bind not in ("127.0.0.1", "localhost"):
        print("WARNING: bound to %s -- this page is reachable by any host "
              "on this network, with no authentication." % args.bind,
              flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
