# test_workbench.py -- host tests for the S25 workbench (bites 1 + 2).
# No Pi, no hardware, no serial port, no systemd, no browser.
#
# Bite 1's interesting logic is the RECIPE SCHEMA (a typo must be a loud
# error, never a silently dropped field) and the PASSIVE PREFLIGHT (board
# states asserted against fake /dev and /proc trees).
#
# Bite 2's interesting logic is the RUNNER: the single-owner lock, the
# refusal paths (foreign holder, absent board, double start), the health
# poll that gates LIVE, and the stop ladder (SIGINT -> SIGTERM -> stuck,
# NEVER SIGKILL). Children here are real subprocesses -- tiny python
# scripts -- so signal semantics are the real thing, with the grace
# timers shrunk per instance to keep the suite fast.
#
# Run:  python3 pi/workbench/test_workbench.py

import http.client
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import workbench  # noqa: E402
from workbench import (  # noqa: E402
    Runner, StartRefused, board_preflight, load_recipes, preflight,
    validate_recipe)

UNIT = os.path.join(PI, "services", "workbench.service")
INSTALLER = os.path.join(PI, "install_stream_service.sh")


def recipe(**over):
    """A minimal valid recipe dict; override fields per test."""
    base = {
        "name": "demo-test",
        "title": "A demo",
        "boards": [{"label": "AE3", "by_id": "usb-OpenMV_x-if00"}],
    }
    base.update(over)
    return base


def errs_of(obj):
    out, errs = validate_recipe(obj, "t.toml")
    return out, errs


class TestSchema(unittest.TestCase):
    def test_minimal_valid(self):
        out, errs = errs_of(recipe())
        self.assertEqual(errs, [])
        self.assertEqual(out["name"], "demo-test")
        self.assertIsNone(out["run"])
        self.assertIsNone(out["health"])
        self.assertIsNone(out["thumbnail"])
        self.assertEqual(out["services"], [])

    def test_missing_required(self):
        for key in ("name", "title", "boards"):
            r = recipe()
            del r[key]
            out, errs = errs_of(r)
            self.assertIsNone(out, key)
            self.assertTrue(any(key in e for e in errs), (key, errs))

    def test_bad_name_rejected(self):
        for bad in ("Has Caps", "under_score", "-leading", ""):
            out, errs = errs_of(recipe(name=bad))
            self.assertIsNone(out, bad)

    def test_unknown_top_key_is_an_error(self):
        out, errs = errs_of(recipe(comand="typo"))
        self.assertIsNone(out)
        self.assertTrue(any("comand" in e for e in errs), errs)

    def test_unknown_board_key_is_an_error(self):
        out, errs = errs_of(recipe(
            boards=[{"label": "AE3", "by_id": "x", "firmwar": "typo"}]))
        self.assertIsNone(out)
        self.assertTrue(any("firmwar" in e for e in errs), errs)

    def test_by_id_must_not_be_a_path(self):
        out, errs = errs_of(recipe(
            boards=[{"label": "AE3", "by_id": "/dev/serial/by-id/usb-x"}]))
        self.assertIsNone(out)
        self.assertTrue(any("not a path" in e for e in errs), errs)

    def test_duplicate_board_labels_rejected(self):
        out, errs = errs_of(recipe(boards=[
            {"label": "AE3", "by_id": "usb-a"},
            {"label": "AE3", "by_id": "usb-b"}]))
        self.assertIsNone(out)
        self.assertTrue(any("unique" in e for e in errs), errs)

    def test_empty_boards_rejected(self):
        out, errs = errs_of(recipe(boards=[]))
        self.assertIsNone(out)

    def test_run_argv_validated(self):
        for bad in ({}, {"argv": []}, {"argv": "python3"},
                    {"argv": ["ok", ""]}, {"argv": ["ok", 3]}):
            out, errs = errs_of(recipe(run=bad))
            self.assertIsNone(out, bad)
        out, errs = errs_of(recipe(run={"argv": ["python3", "x.py"]}))
        self.assertEqual(errs, [])
        self.assertEqual(out["run"]["cwd"], ".")

    def test_unknown_run_key_is_an_error(self):
        out, errs = errs_of(recipe(run={"argv": ["x"], "cmd": "y"}))
        self.assertIsNone(out)

    def test_model_sha256_validated(self):
        def boards(sha):
            return [{"label": "AE3", "by_id": "usb-x", "models": [
                {"name": "m", "path": "/flash/m.tflite", "sha256": sha}]}]
        out, errs = errs_of(recipe(boards=boards("zz")))
        self.assertIsNone(out)
        self.assertTrue(any("hex" in e for e in errs), errs)
        out, errs = errs_of(recipe(boards=boards("ab" * 32)))
        self.assertEqual(errs, [])

    def test_health_validated(self):
        out, errs = errs_of(recipe(health={"http": "http://127.0.0.1:8090/"}))
        self.assertEqual(errs, [])
        out, errs = errs_of(recipe(health={"htp": "typo"}))
        self.assertIsNone(out)

    def test_thumbnail_confined_to_thumbs_dir(self):
        out, errs = errs_of(recipe(thumbnail="thumbs/ball.jpg"))
        self.assertEqual(errs, [])
        for bad in ("../secrets.jpg", "/etc/passwd", "thumbs/../x.jpg",
                    "ball.jpg"):
            out, errs = errs_of(recipe(thumbnail=bad))
            self.assertIsNone(out, bad)

    def test_services_validated(self):
        out, errs = errs_of(recipe(services=["bm-light.service"]))
        self.assertEqual(errs, [])
        for bad in ("bm-light.service", ["bm-light"], [3]):
            out, errs = errs_of(recipe(services=bad))
            self.assertIsNone(out, bad)

    def test_non_table_top_level(self):
        out, errs = errs_of(["not", "a", "table"])
        self.assertIsNone(out)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wb_recipes_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def put(self, fname, text):
        with open(os.path.join(self.dir, fname), "w", encoding="utf-8") as fh:
            fh.write(text)

    GOOD = ('name = "good-one"\ntitle = "Good"\n'
            '[[boards]]\nlabel = "AE3"\nby_id = "usb-x-if00"\n')

    def test_good_file_loads(self):
        self.put("good.toml", self.GOOD)
        recipes, problems = load_recipes(self.dir)
        self.assertEqual(problems, [])
        self.assertEqual([r["name"] for r in recipes], ["good-one"])
        self.assertEqual(recipes[0]["file"], "good.toml")

    def test_broken_toml_becomes_a_problem_card_not_an_absence(self):
        self.put("good.toml", self.GOOD)
        self.put("broken.toml", "name = unclosed [\n")
        recipes, problems = load_recipes(self.dir)
        self.assertEqual(len(recipes), 1)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["file"], "broken.toml")
        self.assertTrue(problems[0]["error"])

    def test_invalid_schema_becomes_a_problem(self):
        self.put("bad.toml", 'name = "bad"\n')  # no title, no boards
        recipes, problems = load_recipes(self.dir)
        self.assertEqual(recipes, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("title", problems[0]["error"])

    def test_duplicate_names_across_files(self):
        self.put("a.toml", self.GOOD)
        self.put("b.toml", self.GOOD)
        recipes, problems = load_recipes(self.dir)
        self.assertEqual(len(recipes), 1)
        self.assertEqual(len(problems), 1)
        self.assertIn("duplicate", problems[0]["error"])
        self.assertIn("a.toml", problems[0]["error"])

    def test_missing_dir_is_a_problem_not_a_crash(self):
        recipes, problems = load_recipes(os.path.join(self.dir, "nope"))
        self.assertEqual(recipes, [])
        self.assertEqual(len(problems), 1)

    def test_non_toml_files_ignored(self):
        self.put("notes.txt", "not a recipe")
        self.put("good.toml", self.GOOD)
        recipes, problems = load_recipes(self.dir)
        self.assertEqual(len(recipes), 1)
        self.assertEqual(problems, [])


class TestShippedRecipes(unittest.TestCase):
    """The repo's own recipes/ must always load clean -- a broken released
    recipe is exactly the silent failure this format exists to prevent."""

    def test_shipped_recipes_have_zero_problems(self):
        recipes, problems = load_recipes(workbench.RECIPE_DIR)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(recipes), 1)

    def test_s8_recipe_matches_spec_board_identities(self):
        # SPEC §Board identity on nereus000, verified live 2026-08-20.
        recipes, _ = load_recipes(workbench.RECIPE_DIR)
        s8 = {r["name"]: r for r in recipes}["s8-two-colour-balls"]
        by_label = {b["label"]: b for b in s8["boards"]}
        self.assertEqual(by_label["AE3"]["by_id"],
                         "usb-OpenMV_OpenMV_Camera_0829c14000000000-if00")
        self.assertEqual(
            by_label["N6"]["by_id"],
            "usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_"
            "020023000450433547373200-if00")
        # The run block must reference the same by-id paths it preflights.
        argv = " ".join(s8["run"]["argv"])
        for b in s8["boards"]:
            self.assertIn(b["by_id"], argv)

    def test_s8_recipe_thumbnail_ships(self):
        recipes, _ = load_recipes(workbench.RECIPE_DIR)
        s8 = {r["name"]: r for r in recipes}["s8-two-colour-balls"]
        self.assertTrue(
            os.path.exists(os.path.join(workbench.RECIPE_DIR,
                                        s8["thumbnail"])))


class FakeBench:
    """A fake /dev + /proc tree for passive-preflight tests."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="wb_fake_")
        self.dev = os.path.join(self.root, "dev", "serial", "by-id")
        self.proc = os.path.join(self.root, "proc")
        os.makedirs(self.dev)
        os.makedirs(self.proc)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add_board(self, by_id, tty="ttyACM0"):
        # Real layout: /dev/serial/by-id/<name> -> ../../ttyACM<n>
        real = os.path.join(self.root, "dev", tty)
        with open(real, "w") as fh:
            fh.write("")
        os.symlink(os.path.join("..", "..", tty),
                   os.path.join(self.dev, by_id))
        return real

    def add_process(self, pid, cmdline, fds=()):
        d = os.path.join(self.proc, str(pid), "fd")
        os.makedirs(d)
        for i, tgt in enumerate(fds):
            os.symlink(tgt, os.path.join(d, str(i)))
        with open(os.path.join(self.proc, str(pid), "cmdline"), "wb") as fh:
            fh.write(cmdline.replace(" ", "\0").encode() + b"\0")


class TestPreflight(unittest.TestCase):
    def setUp(self):
        self.fake = FakeBench()
        self.addCleanup(self.fake.cleanup)

    def test_absent_board_is_waiting_not_an_error(self):
        st = board_preflight("usb-x-if00", self.fake.dev, self.fake.proc)
        self.assertEqual(st["state"], "waiting")
        self.assertIsNone(st["tty"])
        self.assertEqual(st["holders"], [])

    def test_present_board_with_free_port_is_ready(self):
        real = self.fake.add_board("usb-x-if00")
        self.fake.add_process(100, "python3 something_else.py",
                              fds=["/dev/null"])
        st = board_preflight("usb-x-if00", self.fake.dev, self.fake.proc)
        self.assertEqual(st["state"], "ready")
        self.assertEqual(st["tty"], os.path.realpath(real))

    def test_held_port_names_the_holder(self):
        real = self.fake.add_board("usb-x-if00")
        self.fake.add_process(
            4242, "python3 bench/n6_stream_host.py --board AE3=x",
            fds=[os.path.realpath(real)])
        st = board_preflight("usb-x-if00", self.fake.dev, self.fake.proc)
        self.assertEqual(st["state"], "held")
        self.assertEqual(st["holders"][0]["pid"], 4242)
        self.assertIn("n6_stream_host.py", st["holders"][0]["cmd"])

    def test_one_holder_entry_per_process(self):
        real = os.path.realpath(self.fake.add_board("usb-x-if00"))
        self.fake.add_process(7, "double open", fds=[real, real])
        st = board_preflight("usb-x-if00", self.fake.dev, self.fake.proc)
        self.assertEqual([h["pid"] for h in st["holders"]], [7])

    def test_unreadable_proc_entries_skipped(self):
        os.makedirs(os.path.join(self.fake.proc, "999"))
        self.fake.add_board("usb-x-if00")
        st = board_preflight("usb-x-if00", self.fake.dev, self.fake.proc)
        self.assertEqual(st["state"], "ready")

    def test_panel_dedupes_boards_across_recipes(self):
        self.fake.add_board("usb-x-if00")
        r1 = {"boards": [{"label": "AE3", "by_id": "usb-x-if00"}]}
        r2 = {"boards": [{"label": "AE3", "by_id": "usb-x-if00"},
                         {"label": "N6", "by_id": "usb-y-if00"}]}
        pf = preflight([r1, r2], dev_dir=self.fake.dev, proc=self.fake.proc,
                       runner=lambda u: "inactive", disk_path=self.fake.root)
        self.assertEqual(len(pf["boards"]), 2)
        states = {b["label"]: b["state"] for b in pf["boards"]}
        self.assertEqual(states, {"AE3": "ready", "N6": "waiting"})

    def test_services_shown_only_when_a_recipe_declares_them(self):
        # No standing unit list (Nick 2026-08-20): today's CV bench
        # declares none, so the panel shows none.
        r_none = {"boards": [{"label": "A", "by_id": "usb-x"}]}
        pf = preflight([r_none], dev_dir=self.fake.dev, proc=self.fake.proc,
                       runner=lambda u: "active", disk_path=self.fake.root)
        self.assertEqual(pf["services"], {})
        r_decl = {"boards": [{"label": "A", "by_id": "usb-x"}],
                  "services": ["bm-light.service"]}
        pf = preflight([r_decl], dev_dir=self.fake.dev, proc=self.fake.proc,
                       runner=lambda u: "active", disk_path=self.fake.root)
        self.assertEqual(pf["services"], {"bm-light.service": "active"})

    def test_systemctl_absent_reports_unavailable_not_crash(self):
        states = workbench.service_states(
            units=("x.service",),
            runner=workbench._systemctl_state
            if shutil.which("systemctl") is None else lambda u: "unavailable")
        self.assertIn(states["x.service"],
                      ("unavailable", "unknown", "inactive"))


# ---------------------------------------------------------------------------
# The runner.
# ---------------------------------------------------------------------------

READY = [{"label": "AE3", "by_id": "usb-x", "state": "ready", "holders": []}]


def run_recipe(argv, health=None, **over):
    r = {"name": "rt", "title": "RT", "summary": None, "opens": ":9",
         "thumbnail": None, "services": [], "boards":
         [{"label": "AE3", "by_id": "usb-x", "firmware": None, "models": []}],
         "run": {"argv": argv, "cwd": "."}, "health": health}
    r.update(over)
    return r


SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
STUBBORN = [sys.executable, "-c",
            "import signal, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(60)"]


class TestRunner(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="wb_run_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.r = Runner(repo=d, pidfile=os.path.join(d, "run.json"),
                        logpath=os.path.join(d, "run.log"))
        self.r.POLL = 0.05
        self.r.GRACE_INT = 1.0
        self.r.GRACE_TERM = 1.0
        self.r.HEALTH_TIMEOUT = 5.0
        self.addCleanup(self._cleanup_child)

    def _cleanup_child(self):
        # Test hygiene only -- the product never SIGKILLs.
        if self.r.pid:
            try:
                os.killpg(self.r.pid, signal.SIGKILL)
            except OSError:
                pass

    def wait_state(self, *states, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.r.state in states:
                return self.r.state
            time.sleep(0.02)
        self.fail("state %r never reached %r" % (self.r.state, states))

    def test_start_to_live_without_health_then_stop(self):
        self.r.start(run_recipe(SLEEPER), READY)
        self.assertEqual(self.r.state, "starting")
        self.assertTrue(os.path.exists(self.r.pidfile))
        self.wait_state("live")
        self.assertEqual(self.r.stop(), "idle")
        self.assertFalse(os.path.exists(self.r.pidfile))
        self.assertIsNone(self.r.recipe)

    def test_stop_arms_the_settle_window(self):
        # Measured (Nick 2026-08-20): a quick stop->start wedges the AE3
        # into a raw-repl refusal. The runner must hold the boards quiet.
        self.r.start(run_recipe(SLEEPER), READY)
        self.wait_state("live")
        self.r.stop()
        with self.assertRaises(StartRefused) as cm:
            self.r.start(run_recipe(SLEEPER), READY)
        self.assertIn("settling", str(cm.exception))
        self.assertGreater(self.r.snapshot()["settle_s"], 0)
        self.r.settle_until = 0.0  # window elapsed
        self.r.start(run_recipe(SLEEPER), READY)
        self.wait_state("live")
        self.r.stop()

    def test_settle_does_not_block_disjoint_boards(self):
        self.r.start(run_recipe(SLEEPER), READY)
        self.wait_state("live")
        self.r.stop()
        other = [{"label": "N6", "by_id": "usb-OTHER", "state": "ready",
                  "holders": []}]
        r2 = run_recipe(SLEEPER)
        r2["boards"] = [{"label": "N6", "by_id": "usb-OTHER",
                         "firmware": None, "models": []}]
        self.r.start(r2, other)  # must not raise
        self.wait_state("live")
        self.r.stop()

    def test_failure_also_arms_the_settle_window(self):
        self.r.start(run_recipe(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            health={"http": "http://127.0.0.1:1/"}), READY)
        self.wait_state("failed")
        self.assertGreater(self.r.snapshot()["settle_s"], 0)

    def test_child_that_dies_early_is_failed_with_rc(self):
        self.r.start(run_recipe(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            health={"http": "http://127.0.0.1:1/"}), READY)
        self.wait_state("failed")
        self.assertIn("rc=3", self.r.error)
        self.assertFalse(os.path.exists(self.r.pidfile))

    def test_health_gates_live(self):
        # Health URL answers only after the server below starts; until
        # then the runner must sit in "starting".
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        self.r.start(run_recipe(SLEEPER,
                                health={"http": None}), READY)
        # no server yet: pick the port AFTER start so it can't race
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.r.recipe["health"] = {
            "http": "http://127.0.0.1:%d/" % httpd.server_address[1]}
        time.sleep(0.3)
        self.assertEqual(self.r.state, "starting")
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.shutdown)
        self.wait_state("live")
        self.assertEqual(self.r.stop(), "idle")

    def test_double_start_refused(self):
        self.r.start(run_recipe(SLEEPER), READY)
        self.wait_state("live")
        with self.assertRaises(StartRefused) as cm:
            self.r.start(run_recipe(SLEEPER), READY)
        self.assertIn("one demo at a time", str(cm.exception))
        self.r.stop()

    def test_waiting_board_refused(self):
        with self.assertRaises(StartRefused) as cm:
            self.r.start(run_recipe(SLEEPER),
                         [{"label": "AE3", "by_id": "usb-x",
                           "state": "waiting", "holders": []}])
        self.assertIn("not enumerated", str(cm.exception))
        self.assertEqual(self.r.state, "idle")

    def test_foreign_holder_refused_by_name(self):
        with self.assertRaises(StartRefused) as cm:
            self.r.start(run_recipe(SLEEPER),
                         [{"label": "AE3", "by_id": "usb-x", "state": "held",
                           "holders": [{"pid": 999, "cmd": "mpremote"}]}])
        msg = str(cm.exception)
        self.assertIn("999", msg)
        self.assertIn("will not kill", msg)

    def test_bad_exec_is_a_refusal_not_a_crash(self):
        with self.assertRaises(StartRefused):
            self.r.start(run_recipe(["/no/such/binary"]), READY)
        self.assertEqual(self.r.state, "idle")

    def test_recipe_without_run_refused(self):
        with self.assertRaises(StartRefused):
            self.r.start(run_recipe(SLEEPER, run=None), READY)

    def test_stubborn_child_goes_stuck_never_sigkill(self):
        self.r.start(run_recipe(STUBBORN), READY)
        self.wait_state("live")
        time.sleep(0.3)  # let the child install its handlers
        self.assertEqual(self.r.stop(), "stuck")
        self.assertIn("SIGKILL", self.r.error)
        # The child must still be alive: proof no SIGKILL was sent.
        self.assertTrue(self.r._alive())

    def test_child_death_while_live_is_failed(self):
        self.r.start(run_recipe(SLEEPER), READY)
        self.wait_state("live")
        os.killpg(self.r.pid, signal.SIGTERM)  # simulated external death
        self.wait_state("failed")
        self.assertIn("while live", self.r.error)

    def test_stale_pidfile_cleaned_on_init(self):
        d = tempfile.mkdtemp(prefix="wb_stale_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        pidfile = os.path.join(d, "run.json")
        with open(pidfile, "w") as fh:
            json.dump({"pid": 99999999, "recipe": "x"}, fh)
        r2 = Runner(repo=d, pidfile=pidfile,
                    logpath=os.path.join(d, "run.log"))
        self.assertEqual(r2.state, "idle")
        self.assertFalse(os.path.exists(pidfile))

    def test_restart_adopts_running_demo_and_can_stop_it(self):
        self.r.start(run_recipe(SLEEPER), READY)
        self.wait_state("live")
        # A second Runner on the same pidfile = the workbench restarted.
        r2 = Runner(repo=self.r.repo, pidfile=self.r.pidfile,
                    logpath=self.r.logpath)
        r2.POLL = 0.05
        r2.GRACE_INT = 1.0
        r2.GRACE_TERM = 1.0
        self.assertEqual(r2.state, "live")
        self.assertEqual(r2.recipe["name"], "rt")
        self.assertEqual(r2.stop(), "idle")
        self.assertFalse(self.r._alive())


# ---------------------------------------------------------------------------
# HTTP.
# ---------------------------------------------------------------------------

class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeBench()
        cls.rdir = tempfile.mkdtemp(prefix="wb_http_recipes_")
        os.makedirs(os.path.join(cls.rdir, "thumbs"))
        with open(os.path.join(cls.rdir, "thumbs", "t.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8fakejpeg")
        with open(os.path.join(cls.rdir, "good.toml"), "w") as fh:
            fh.write(TestRegistry.GOOD +
                     '\n[run]\nargv = ["python3", "-c", "pass"]\n')
        with open(os.path.join(cls.rdir, "broken.toml"), "w") as fh:
            fh.write("name = [unclosed\n")
        cls.tmp = tempfile.mkdtemp(prefix="wb_http_run_")
        cls.runner = Runner(repo=cls.tmp,
                            pidfile=os.path.join(cls.tmp, "run.json"),
                            logpath=os.path.join(cls.tmp, "run.log"))
        cfg = {"recipe_dir": cls.rdir, "dev_dir": cls.fake.dev,
               "proc": cls.fake.proc, "runner": lambda u: "inactive",
               "disk_path": cls.fake.root}
        cls.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), workbench.make_handler(cfg, cls.runner))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.fake.cleanup()
        shutil.rmtree(cls.rdir, ignore_errors=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path,
                         body=json.dumps(body).encode() if body else None,
                         headers={"Content-Type": "application/json"}
                         if body else {})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_page_served(self):
        code, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"workbench", body)

    def test_recipes_endpoint_carries_both_lists(self):
        code, body = self.req("GET", "/api/recipes")
        self.assertEqual(code, 200)
        obj = json.loads(body)
        self.assertEqual([r["name"] for r in obj["recipes"]], ["good-one"])
        self.assertEqual([p["file"] for p in obj["problems"]],
                         ["broken.toml"])

    def test_preflight_endpoint_shape(self):
        code, body = self.req("GET", "/api/preflight")
        self.assertEqual(code, 200)
        obj = json.loads(body)
        self.assertEqual({b["by_id"] for b in obj["boards"]}, {"usb-x-if00"})
        self.assertEqual(obj["boards"][0]["state"], "waiting")
        self.assertIn("disk", obj)
        self.assertEqual(obj["services"], {})

    def test_runner_endpoint_idle(self):
        code, body = self.req("GET", "/api/runner")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["state"], "idle")

    def test_start_unknown_recipe_404(self):
        code, body = self.req("POST", "/api/start", {"name": "nope"})
        self.assertEqual(code, 404)
        self.assertFalse(json.loads(body)["ok"])

    def test_start_refused_when_board_absent_409(self):
        # good-one's board is not in the fake /dev tree -> waiting.
        code, body = self.req("POST", "/api/start", {"name": "good-one"})
        self.assertEqual(code, 409)
        self.assertIn("not enumerated", json.loads(body)["err"])

    def test_stop_when_idle_is_idempotent(self):
        code, body = self.req("POST", "/api/stop")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["state"], "idle")

    def test_devmode_reports_boards(self):
        code, body = self.req("POST", "/api/devmode")
        self.assertEqual(code, 200)
        obj = json.loads(body)
        # Board absent (waiting) -> not fully in dev mode, and says why.
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["boards"][0]["state"], "waiting")

    def test_thumb_served_and_confined(self):
        code, body = self.req("GET", "/thumbs/t.jpg")
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"\xff\xd8"))
        for evil in ("/thumbs/../good.toml", "/thumbs/..%2Fgood.toml",
                     "/thumbs/no.jpg"):
            code, _ = self.req("GET", evil)
            self.assertEqual(code, 404, evil)

    def test_unknown_routes_404(self):
        for method, path in (("GET", "/api/nope"), ("POST", "/api/nope")):
            code, _ = self.req(method, path)
            self.assertEqual(code, 404, (method, path))


class TestShipsWith(unittest.TestCase):
    """The unit file and installer must agree with the code they launch."""

    def read(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    def test_unit_file_exists_and_points_at_the_module(self):
        text = self.read(UNIT)
        self.assertIn("pi/workbench/workbench.py", text)
        self.assertIn("WantedBy=multi-user.target", text)
        self.assertIn("User=pi", text)

    def test_installer_has_the_workbench_role_enabled(self):
        text = self.read(INSTALLER)
        self.assertIn(
            "workbench) UNIT=workbench.service;         AUTOSTART=yes", text)

    def test_page_file_ships(self):
        self.assertTrue(
            os.path.exists(os.path.join(HERE, "static", "workbench.html")))

    def test_no_sigkill_in_the_runner(self):
        # The rule is load-bearing (a SIGKILLed viewer took the N6 off
        # the USB bus): the product code must never USE SIGKILL. Prose
        # may mention it; the API constant may not appear.
        src = self.read(os.path.join(HERE, "workbench.py"))
        self.assertNotIn("signal.SIGKILL", src)
        self.assertNotIn("kill -9", src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
