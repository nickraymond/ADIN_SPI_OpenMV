# test_workbench.py -- host tests for the S25 workbench menu (bite 1).
# No Pi, no hardware, no serial port, no systemd, no browser.
#
# Bite 1's interesting logic is the RECIPE SCHEMA (the format is being
# proven before anything drives hardware, so a typo must be a loud error,
# never a silently dropped field) and the PASSIVE PREFLIGHT (board
# presence and port-holders read from fake /dev and /proc trees, so every
# state -- waiting / ready / held -- is asserted deterministically).
#
# Run:  python3 pi/workbench/test_workbench.py

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import workbench  # noqa: E402
from workbench import (  # noqa: E402
    board_preflight, load_recipes, preflight, validate_recipe)

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
        # A pid dir with no fd/ subdir (raced exit, or another user's
        # process on a locked-down /proc) must be skipped, not fatal.
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

    def test_panel_reports_disk_and_services(self):
        pf = preflight([], dev_dir=self.fake.dev, proc=self.fake.proc,
                       runner=lambda u: "active", disk_path=self.fake.root)
        self.assertIsInstance(pf["disk"]["free_mb"], int)
        self.assertEqual(set(pf["services"]), set(workbench.BENCH_UNITS))
        self.assertTrue(all(v == "active" for v in pf["services"].values()))

    def test_systemctl_absent_reports_unavailable_not_crash(self):
        # On a dev Mac there is no systemctl; the panel must degrade.
        states = workbench.service_states(
            units=("x.service",),
            runner=workbench._systemctl_state
            if shutil.which("systemctl") is None else lambda u: "unavailable")
        self.assertIn(states["x.service"],
                      ("unavailable", "unknown", "inactive"))


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeBench()
        cls.rdir = tempfile.mkdtemp(prefix="wb_http_recipes_")
        with open(os.path.join(cls.rdir, "good.toml"), "w") as fh:
            fh.write(TestRegistry.GOOD)
        with open(os.path.join(cls.rdir, "broken.toml"), "w") as fh:
            fh.write("name = [unclosed\n")
        cfg = {"recipe_dir": cls.rdir, "dev_dir": cls.fake.dev,
               "proc": cls.fake.proc, "runner": lambda u: "inactive",
               "disk_path": cls.fake.root}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                        workbench.make_handler(cfg))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.fake.cleanup()
        shutil.rmtree(cls.rdir, ignore_errors=True)

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_page_served(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"workbench", body)

    def test_recipes_endpoint_carries_both_lists(self):
        code, body = self.get("/api/recipes")
        self.assertEqual(code, 200)
        obj = json.loads(body)
        self.assertEqual([r["name"] for r in obj["recipes"]], ["good-one"])
        self.assertEqual([p["file"] for p in obj["problems"]],
                         ["broken.toml"])

    def test_preflight_endpoint_shape(self):
        code, body = self.get("/api/preflight")
        self.assertEqual(code, 200)
        obj = json.loads(body)
        self.assertEqual({b["by_id"] for b in obj["boards"]}, {"usb-x-if00"})
        self.assertEqual(obj["boards"][0]["state"], "waiting")
        self.assertIn("disk", obj)
        self.assertIn("services", obj)

    def test_unknown_route_404(self):
        code, _ = self.get("/api/nope")
        self.assertEqual(code, 404)

    def test_no_post_routes_exist_in_bite_1(self):
        # Listing only: nothing on this server mutates anything yet.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", "/api/recipes", body=b"{}")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 501)  # BaseHTTPRequestHandler: no do_POST
        finally:
            conn.close()


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
        self.assertIn("workbench) UNIT=workbench.service;         AUTOSTART=yes",
                      text)

    def test_page_file_ships(self):
        self.assertTrue(
            os.path.exists(os.path.join(HERE, "static", "workbench.html")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
