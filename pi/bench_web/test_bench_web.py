# test_bench_web.py -- host tests for the S18 bite C1 bench page. No Pi, no
# hardware, no socket, no browser.
#
# The interesting logic in this bite is the CLICK GUARD, and it is worth
# testing on a laptop because the thing it prevents costs a bench session:
# a sensor re-init too soon after a capture wedges the camera until the
# bridge restarts (SPEC section "Open questions", S18 bite B).
#
# The gate takes an injected clock, so every timing property below is
# asserted deterministically rather than by sleeping.
#
# Run:  python3 pi/bench_web/test_bench_web.py

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bench_web  # noqa: E402
from bench_web import Bench, BenchGate, build_camera_cmd, build_light_cmd  # noqa: E402

PAGE = os.path.join(HERE, "static", "bench.html")
UNIT = os.path.join(PI, "services", "bench-web.service")
INSTALLER = os.path.join(PI, "install_stream_service.sh")


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, dt):
        self.t += dt
        return self.t


def status(saved=0, errors=0, **kw):
    st = {"save": {"state": "idle", "saved": saved, "errors": errors}}
    st.update(kw)
    return st


class TestGateBusy(unittest.TestCase):
    """One camera command at a time -- the fast-click half of the guard."""

    def setUp(self):
        self.clk = Clock()
        self.g = BenchGate(settle=8.0, clock=self.clk)

    def test_fresh_gate_allows_anything(self):
        ok, why, retry = self.g.check("qvga", "color")
        self.assertTrue(ok, why)
        self.assertEqual(retry, 0.0)

    def test_capture_in_flight_blocks_the_next_command(self):
        self.g.arm_capture("qvga", "color", status(saved=3))
        ok, why, retry = self.g.check("qvga", "color")
        self.assertFalse(ok)
        self.assertIn("in flight", why)
        self.assertAlmostEqual(retry, BenchGate.CAPTURE_GRACE, places=3)

    def test_a_landed_frame_releases_the_gate(self):
        self.g.arm_capture("qvga", "color", status(saved=3))
        self.clk.tick(0.4)
        self.g.observe(status(saved=4))
        self.assertEqual(self.g.mode, "idle")
        self.assertEqual(self.g.last_release, "saved")

    def test_a_stale_saved_state_does_not_release_early(self):
        # THE TRAP: save.state still reads "saved" from the PREVIOUS capture
        # at the moment we arm. Gating on the string would release the gate
        # on the very next poll, one tick after the click.
        self.g.arm_capture("vga", "mono", status(saved=7))
        self.clk.tick(0.2)
        self.g.observe({"save": {"state": "saved", "saved": 7, "errors": 0}})
        self.assertEqual(self.g.mode, "capture")

    def test_a_save_error_releases_the_gate(self):
        self.g.arm_capture("qvga", "color", status(saved=1, errors=2))
        self.clk.tick(1.0)
        self.g.observe(status(saved=1, errors=3))
        self.assertEqual(self.g.mode, "idle")
        self.assertIn("error", self.g.last_release)

    def test_a_frame_that_never_arrives_releases_on_the_grace(self):
        # Bite B's save gives up at 8 s; the gate must outlive that and then
        # let go, or one lost frame disables the page for the session.
        self.g.arm_capture("hd", "color", status(saved=0))
        self.clk.tick(BenchGate.CAPTURE_GRACE - 0.5)
        self.g.observe(status(saved=0))
        self.assertEqual(self.g.mode, "capture")
        self.clk.tick(1.0)
        self.g.observe(status(saved=0))
        self.assertEqual(self.g.mode, "idle")
        self.assertIn("no frame", self.g.last_release)

    def test_grace_is_longer_than_bite_bs_save_timeout(self):
        self.assertGreater(BenchGate.CAPTURE_GRACE, 8.0)


class TestGateSettle(unittest.TestCase):
    """The settle half: only a genuine geometry delta re-inits the sensor."""

    def setUp(self):
        self.clk = Clock()
        self.g = BenchGate(settle=8.0, clock=self.clk)
        self.g.arm_capture("qvga", "color", status(saved=0))
        self.clk.tick(0.5)
        self.g.observe(status(saved=1))   # landed -> settle window opens

    def test_same_mode_repeat_is_never_held(self):
        # QVGA->QVGA colour repeats need no re-init (S18 bite A), so holding
        # them would slow the bench down for nothing.
        ok, why, _ = self.g.check("qvga", "color")
        self.assertTrue(ok, why)

    def test_resolution_change_is_held_for_the_settle(self):
        ok, why, retry = self.g.check("vga", "color")
        self.assertFalse(ok)
        self.assertIn("settle", why)
        self.assertIn("wedges", why)
        self.assertAlmostEqual(retry, 8.0, places=3)

    def test_pixel_format_change_is_held_too(self):
        ok, _, _ = self.g.check("qvga", "mono")
        self.assertFalse(ok)

    def test_the_hold_expires(self):
        self.clk.tick(7.9)
        self.assertFalse(self.g.check("vga", "color")[0])
        self.clk.tick(0.2)
        self.assertTrue(self.g.check("vga", "color")[0])

    def test_settle_is_at_least_the_measured_passing_gap(self):
        # >=6 s succeeded 3/3; a sub-second gap failed 2/2. A default under
        # 6 s would ship a guard that the measurement says does not work.
        self.assertGreaterEqual(BenchGate().settle, 6.0)

    def test_settle_is_configurable(self):
        g = BenchGate(settle=3.0, clock=self.clk)
        self.assertEqual(g.settle, 3.0)


class TestGateStream(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.g = BenchGate(settle=8.0, clock=self.clk)

    def test_a_running_stream_blocks_camera_commands(self):
        self.g.arm_stream("vga", "color", 60)
        ok, why, retry = self.g.check("vga", "color")
        self.assertFalse(ok)
        self.assertIn("Stop", why)
        self.assertAlmostEqual(retry, 60.0, places=3)

    def test_a_stream_releases_when_its_seconds_elapse(self):
        self.g.arm_stream("vga", "color", 30)
        self.clk.tick(29.0)
        self.g.observe(status())
        self.assertEqual(self.g.mode, "stream")
        self.clk.tick(2.0)
        self.g.observe(status())
        self.assertEqual(self.g.mode, "idle")

    def test_stop_releases_and_still_applies_the_settle(self):
        # A stop ends the command but leaves a hot sensor.
        self.g.arm_stream("hd", "mono", 600)
        self.clk.tick(5.0)
        self.g.note_stop()
        self.assertEqual(self.g.mode, "idle")
        self.assertFalse(self.g.check("qvga", "color")[0])

    def test_snapshot_reports_what_the_page_needs(self):
        self.g.arm_stream("vga", "color", 45)
        self.clk.tick(5.0)
        snap = self.g.snapshot()
        self.assertEqual(snap["mode"], "stream")
        self.assertTrue(snap["busy"])
        self.assertEqual(snap["res"], "vga")
        self.assertEqual(snap["pf"], "color")
        self.assertAlmostEqual(snap["stream_left"], 40.0, places=1)
        for key in ("settle_in", "retry_in", "reason", "ready", "last_release"):
            self.assertIn(key, snap)


class FakeBench(Bench):
    """Bench with the socket replaced by a script. Keeps the real gate."""

    def __init__(self, gate, replies=None):
        super().__init__(gate)
        self.sent = []
        self.replies = replies or {}

    def _request(self, obj):
        self.sent.append(obj)
        if obj["cmd"] == "status":
            return self.replies.get("status", status(saved=0))
        return self.replies.get(obj["cmd"], {"ok": True})


class TestBenchEnforcement(unittest.TestCase):
    """The server is what enforces the guard -- a reload or a second tab
    must not be able to get past it."""

    def setUp(self):
        self.clk = Clock()
        self.bench = FakeBench(BenchGate(settle=8.0, clock=self.clk))

    def _capture(self, res="qvga", pf="color"):
        cmd, r, p, secs = build_camera_cmd("capture", {"q": 50, "res": res, "pf": pf})
        return self.bench.camera(cmd, r, p, secs)

    def test_a_second_click_is_refused_and_never_reaches_the_socket(self):
        code, _ = self._capture()
        self.assertEqual(code, 200)
        self.bench.sent.clear()
        code, out = self._capture()
        self.assertEqual(code, 409)
        self.assertFalse(out["ok"])
        self.assertNotIn("capture", [c["cmd"] for c in self.bench.sent])

    def test_a_refused_command_still_reports_the_gate(self):
        self._capture()
        _, out = self._capture()
        self.assertEqual(out["gate"]["mode"], "capture")
        self.assertGreater(out["retry_in"], 0)

    def test_the_gate_does_not_arm_on_a_refused_reply(self):
        # The node refusing (ok=0, e.g. an out-of-range geometry) is not a
        # capture: holding the page afterwards would be a lie.
        self.bench.replies["capture"] = {"ok": False, "err": "nope"}
        code, out = self._capture()
        self.assertEqual(code, 200)
        self.assertFalse(out["ok"])
        self.assertEqual(self.bench.gate.mode, "idle")

    def test_arming_reads_a_fresh_status_first(self):
        # The save counters are the completion signal; arming from a cached
        # count can release the gate before the frame lands.
        self._capture()
        self.assertEqual(self.bench.sent[0]["cmd"], "status")
        self.assertEqual(self.bench.sent[1]["cmd"], "capture")

    def test_stop_is_never_gated(self):
        self.bench.gate.arm_stream("vga", "color", 600)
        code, out = self.bench.stop()
        self.assertEqual(code, 200)
        self.assertEqual(self.bench.gate.mode, "idle")
        self.assertIn("stop", [c["cmd"] for c in self.bench.sent])

    def test_light_is_not_gated(self):
        # A different node, no sensor: gating it would be superstition.
        self.bench.gate.arm_capture("qvga", "color", status())
        code, out = self.bench.passthrough(build_light_cmd("light", {"level": 50}))
        self.assertEqual(code, 200)
        self.assertTrue(out["ok"])


class TestRequestValidation(unittest.TestCase):
    """Everything operator-supplied is checked here, so nothing unvalidated
    reaches the socket -- and the refusal is ours, which is legible, rather
    than the bridge's, which can be a wedged sensor."""

    def test_a_valid_capture_builds(self):
        cmd, res, pf, secs = build_camera_cmd(
            "capture", {"q": 90, "res": "hd", "pf": "mono"})
        self.assertEqual(cmd, {"cmd": "capture", "q": 90, "res": "hd", "pf": "mono"})
        self.assertEqual((res, pf, secs), ("hd", "mono", None))

    def test_only_the_measured_geometries_are_offered(self):
        # QQVGA/SVGA/WXGA are unsupported on sensor 0x7936 and nothing above
        # HD has ever been tested (DESIGN section S0).
        self.assertEqual(bench_web.RES_OK, ("qvga", "vga", "hd"))
        for bad in ("720p", "qqvga", "wxga", "", "QVGA; rm -rf"):
            with self.assertRaises(ValueError):
                build_camera_cmd("capture", {"q": 50, "res": bad, "pf": "color"})

    def test_bad_pixel_format_is_refused(self):
        with self.assertRaises(ValueError):
            build_camera_cmd("capture", {"q": 50, "res": "vga", "pf": "rgb565"})

    def test_quality_is_range_checked(self):
        for bad in (0, 9, 96, 1000):
            with self.assertRaises(ValueError):
                build_camera_cmd("capture", {"q": bad, "res": "vga", "pf": "color"})

    def test_missing_field_is_a_clear_refusal(self):
        with self.assertRaises(ValueError) as e:
            build_camera_cmd("capture", {"res": "vga", "pf": "color"})
        self.assertIn("q", str(e.exception))

    def test_stream_carries_its_own_three_numbers(self):
        cmd, _, _, secs = build_camera_cmd("stream", {
            "q": 50, "res": "vga", "pf": "color",
            "mbps": 2.0, "fps": 15, "secs": 60})
        self.assertEqual(cmd["mbps"], 2.0)
        self.assertEqual(cmd["fps"], 15.0)
        self.assertEqual(cmd["secs"], 60)
        self.assertEqual(secs, 60)

    def test_stream_without_a_duration_is_refused(self):
        # An unbounded stream is the S19 contaminator: it outlives the run
        # that asked for it.
        with self.assertRaises(ValueError):
            build_camera_cmd("stream", {"q": 50, "res": "vga", "pf": "color",
                                        "mbps": 2.0, "fps": 15})

    def test_light_and_strobe_are_range_checked(self):
        self.assertEqual(build_light_cmd("light", {"level": 0})["level"], 0)
        with self.assertRaises(ValueError):
            build_light_cmd("light", {"level": 101})
        s = build_light_cmd("strobe", {"on_ms": 200, "off_ms": 200, "count": 5})
        self.assertEqual(s, {"cmd": "strobe", "on_ms": 200, "off_ms": 200,
                             "count": 5})


class TestPage(unittest.TestCase):
    """The page is carried from the approved mockup; these assert that what
    had to CHANGE actually changed (docs/mockups/README.md)."""

    def setUp(self):
        self.html = read(PAGE)

    def test_the_simulation_is_gone(self):
        for gone in ("REEF_B64", "encodeJpegSync", "drawSynthetic", "toGrey",
                     "applyLight", "Math.random", "mockbar", "MOCKUP"):
            self.assertNotIn(gone, self.html, gone)

    def test_it_drives_the_real_endpoints(self):
        for path in ("/api/status", "/api/config", '"capture"', '"stream"',
                     '"stop"', '"light"', '"strobe"'):
            self.assertIn(path, self.html, path)

    def test_predictions_are_labelled_extrapolated(self):
        self.assertIn("EXTRAPOLATED", self.html)
        self.assertIn("extrapolated", self.html)
        self.assertNotIn(">estimate<", self.html)

    def test_the_measured_constants_carried_over_unchanged(self):
        for const in ("5.262e6", "15.0 / (1000 / 19.7)", "1400", "492",
                      "9198, 19.7", "93253, 299.2", "75324, 117.6"):
            self.assertIn(const, self.html, const)

    def test_the_live_view_is_the_frozen_s3_server(self):
        # An <img> straight at :8080 -- no bytes copied through this server,
        # and the frozen ingest on :8081 is not touched at all.
        self.assertIn("stream_port", self.html)
        self.assertIn("/stream", self.html)
        self.assertNotIn("8081", self.html)

    def test_the_gate_is_mirrored_not_reimplemented(self):
        # The page reads the server's gate; it must not invent its own timer.
        self.assertIn("GATE.settle_in", self.html)
        self.assertIn("GATE.busy", self.html)


class TestUnitAndInstaller(unittest.TestCase):
    def setUp(self):
        self.unit = read(UNIT)
        self.inst = read(INSTALLER)

    def test_unit_runs_the_script_from_the_repo_checkout(self):
        self.assertIn("/home/pi/ADIN_SPI_OpenMV/pi/bench_web/bench_web.py",
                      self.unit)
        self.assertIn("User=pi", self.unit)

    def test_unit_is_not_enabled_at_boot(self):
        self.assertIn("bench-web) UNIT=bench-web.service;         AUTOSTART=no",
                      self.inst)

    def test_installer_usage_lists_the_role(self):
        self.assertIn("bench-web", self.inst.split("usage:")[1][:80])

    def test_unit_does_not_require_telemetry(self):
        # A page that refuses to start when the socket is missing leaves the
        # operator with a browser error and no explanation; this one starts
        # and says what to do.
        self.assertNotIn("Requires=bm-telemetry", self.unit)

    def test_server_is_stdlib_only(self):
        # Same rule as the frozen S3 stream server: nothing to install.
        allowed = ("__future__", "argparse", "json", "os", "sys", "threading",
                   "time", "http", "bench_ctl")
        for line in read(os.path.join(HERE, "bench_web.py")).splitlines():
            if line.startswith("import ") or line.startswith("from "):
                self.assertIn(line.split()[1].split(".")[0], allowed, line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
