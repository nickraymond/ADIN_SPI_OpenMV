# test_bench_web.py -- host tests for the S18 bench page (bites C1 + C2).
# No Pi, no hardware, no socket, no browser.
#
# C1's interesting logic is the CLICK GUARD, worth testing on a laptop
# because the thing it prevents costs a bench session: a sensor re-init too
# soon after a capture wedges the camera until the bridge restarts (SPEC
# section "Open questions", S18 bite B). The gate takes an injected clock, so
# every timing property below is asserted deterministically, not by sleeping.
#
# C2's interesting logic is CONFINEMENT: the gallery gave this server a
# file-reading route, and the tests below try to walk out of the capture
# directory by every route the browser can express.
#
# Run:  python3 pi/bench_web/test_bench_web.py

import json
import os
import re
import shutil
import sys
import tempfile
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
        # q50, not q90: hd mono q90 is now (correctly) refused by the
        # finding-1 burst guard -- see TestFindingOneGuardrails.
        cmd, res, pf, secs = build_camera_cmd(
            "capture", {"q": 50, "res": "hd", "pf": "mono"})
        self.assertEqual(cmd, {"cmd": "capture", "q": 50, "res": "hd", "pf": "mono"})
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
        # 5 fps, not 15: vga color at 15 fps predicts 945 rpmsg msg/s and
        # is now (correctly) refused -- see TestFindingOneGuardrails.
        cmd, _, _, secs = build_camera_cmd("stream", {
            "q": 50, "res": "vga", "pf": "color",
            "mbps": 2.0, "fps": 5, "secs": 60})
        self.assertEqual(cmd["mbps"], 2.0)
        self.assertEqual(cmd["fps"], 5.0)
        self.assertEqual(cmd["secs"], 60)
        self.assertEqual(secs, 60)

    def test_stream_without_a_duration_is_refused(self):
        # An unbounded stream is the S19 contaminator: it outlives the run
        # that asked for it.
        with self.assertRaises(ValueError):
            build_camera_cmd("stream", {"q": 50, "res": "vga", "pf": "color",
                                        "mbps": 2.0, "fps": 15})

    def test_finding1_guard_chunk_predictions_are_exact(self):
        # The same arithmetic as the page model: reef bytes @q50 x qFactor.
        # Color rows moved to the forced-4:2:0 bytes (S23 bite 0): HD
        # color q50 68 -> 62 chunks, now clear of SAFE_BURST_CHUNKS=68.
        self.assertEqual(bench_web.predicted_chunks("qvga", "color", 50), 7)
        self.assertEqual(bench_web.predicted_chunks("hd", "mono", 50), 55)
        self.assertEqual(bench_web.predicted_chunks("hd", "color", 50), 62)

    def test_finding1_measured_safe_commands_pass(self):
        # Every one of these delivered ledger-exact on the bench 2026-08-18.
        build_camera_cmd("capture", {"q": 50, "res": "hd", "pf": "mono"})
        build_camera_cmd("capture", {"q": 50, "res": "hd", "pf": "color"})
        # The 15 fps regression IS the 315 msg/s point: at the limit, allowed.
        build_camera_cmd("stream", {"q": 50, "res": "qvga", "pf": "color",
                                    "mbps": 2.0, "fps": 15, "secs": 60})
        build_camera_cmd("stream", {"q": 50, "res": "hd", "pf": "mono",
                                    "mbps": 4.0, "fps": 1.5, "secs": 60})
        build_camera_cmd("stream", {"q": 50, "res": "hd", "pf": "color",
                                    "mbps": 4.0, "fps": 1.5, "secs": 30})

    def test_finding1_the_burst_that_broke_the_matrix_is_refused(self):
        # hd mono q90 published completely and the relay lost 54 chunks
        # (matrix 2026-08-18). Stills above 68 predicted chunks refuse.
        with self.assertRaises(ValueError) as e:
            build_camera_cmd("capture", {"q": 90, "res": "hd", "pf": "mono"})
        self.assertIn("chunks", str(e.exception))

    def test_wrapfix_the_stream_that_wedged_the_demo_is_now_accepted(self):
        # ~27 fps QVGA color ~= 560 msg/s wedged the HE live pre-fix. The
        # wedge was the u16 vring wrap (S22 bite 1, FIXED): this exact
        # command then ran a 10-min soak at 28.23 fps, ledger exact — so
        # the server must accept it now.
        cmd, _, _, _ = build_camera_cmd(
            "stream", {"q": 50, "res": "qvga", "pf": "color",
                       "mbps": 2.0, "fps": 27, "secs": 600})
        self.assertEqual(cmd["fps"], 27.0)

    def test_wrapfix_measured_clean_vga_mono_15_is_accepted(self):
        # VGA mono 15 fps commands ~810 msg/s (reef model) and was
        # measured CLEAN post-fix (13.27 fps delivered, 0 gaps). A cap
        # that refuses a measured-clean command is the wrong cap.
        cmd, _, _, _ = build_camera_cmd(
            "stream", {"q": 50, "res": "vga", "pf": "mono",
                       "mbps": 4.0, "fps": 15, "secs": 60})
        self.assertEqual(cmd["fps"], 15.0)

    def test_envelope_beyond_every_measured_rate_is_refused(self):
        # VGA mono 30 fps predicts ~1620 msg/s — past the 1200 msg/s
        # measured-clean envelope; unmeasured territory still refuses.
        with self.assertRaises(ValueError) as e:
            build_camera_cmd("stream", {"q": 50, "res": "vga", "pf": "mono",
                                        "mbps": 4.0, "fps": 30, "secs": 60})
        self.assertIn("envelope", str(e.exception))

    def test_finding1_page_mirrors_the_server_constants(self):
        # The page only makes the refusal visible; if its numbers drift
        # from the server's, the visible warning lies about the enforcement.
        page = read(PAGE)
        self.assertIn("SAFE_STREAM_MSGS: %d" % bench_web.SAFE_STREAM_MSGS, page)
        self.assertIn("SAFE_BURST_CHUNKS: %d" % bench_web.SAFE_BURST_CHUNKS, page)
        self.assertIn("MSGS_PER_CHUNK: %d" % bench_web.MSGS_PER_CHUNK, page)
        self.assertIn("wedgeStream", page)
        self.assertIn("wedgeBurst", page)

    def test_danger_zone_force_bypasses_both_guards(self):
        # The override is per command, never sticky: the same bodies
        # without force refuse (covered above), with force they build.
        cmd, _, _, _ = build_camera_cmd(
            "capture", {"q": 90, "res": "hd", "pf": "mono", "force": True})
        self.assertEqual(cmd["cmd"], "capture")
        cmd, _, _, _ = build_camera_cmd(
            "stream", {"q": 50, "res": "vga", "pf": "mono",
                       "mbps": 4.0, "fps": 30, "secs": 60, "force": True})
        self.assertEqual(cmd["fps"], 30.0)

    def test_danger_zone_force_never_reaches_the_socket(self):
        # The control-socket schema does not know 'force'; leaking it
        # would make the bridge's parser the enforcement point.
        cmd, _, _, _ = build_camera_cmd(
            "capture", {"q": 90, "res": "hd", "pf": "mono", "force": True})
        self.assertNotIn("force", cmd)

    def test_danger_zone_force_false_still_refuses(self):
        with self.assertRaises(ValueError):
            build_camera_cmd(
                "capture", {"q": 90, "res": "hd", "pf": "mono", "force": False})

    def test_danger_zone_is_on_the_page(self):
        page = read(PAGE)
        self.assertIn("Danger Zone", page)
        self.assertIn('id="dzForce"', page)
        self.assertIn("force: true", page)   # camBody carries the flag

    def test_light_and_strobe_are_range_checked(self):
        self.assertEqual(build_light_cmd("light", {"level": 0})["level"], 0)
        with self.assertRaises(ValueError):
            build_light_cmd("light", {"level": 101})
        s = build_light_cmd("strobe", {"on_ms": 200, "off_ms": 200, "count": 5})
        self.assertEqual(s, {"cmd": "strobe", "on_ms": 200, "off_ms": 200,
                             "count": 5})


class CaptureDir(unittest.TestCase):
    """A throwaway ~/bench_captures, written the way bite B writes one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s18caps")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = bench_web.CaptureStore(self.tmp)

    def write(self, stem, jpeg=True, side=True, **over):
        if jpeg:
            with open(os.path.join(self.tmp, stem + ".jpg"), "wb") as fh:
                fh.write(b"\xff\xd8" + b"\x00" * 40 + b"\xff\xd9")
        if side:
            doc = {"schema": 1, "file": stem + ".jpg", "utc": stem[4:20],
                   "source": "socket", "req": {"q": 50, "res": "qvga", "pf": "color"},
                   "reply": {"seen": True, "ok": 1, "pub_errs": 0},
                   "frame": {"seq": 1, "size_bytes": 3936, "chunks": 3},
                   "ledger": {"gaps_delta": 0, "dropped_delta": 0}}
            doc.update(over)
            with open(os.path.join(self.tmp, stem + ".json"), "w") as fh:
                json.dump(doc, fh)
        return stem


A = "cap_20260816T223049Z_seq000004"
B = "cap_20260816T223136Z_seq000007"


class TestCaptureListing(CaptureDir):
    """The gallery enumerates SIDECARS: the JPEG is renamed first, so the
    sidecar is the commit record and a bare .jpg may be half-written."""

    def test_newest_first_and_only_captures(self):
        self.write(A)
        self.write(B)
        open(os.path.join(self.tmp, "notes.json"), "w").write("{}")
        open(os.path.join(self.tmp, "holiday.jpg"), "wb").write(b"x")
        out = self.store.listing()
        self.assertTrue(out["ok"])
        self.assertEqual([i["stem"] for i in out["items"]], [B, A])

    def test_a_jpeg_with_no_sidecar_is_not_listed(self):
        # It may still be mid-write. The sidecar is what commits it.
        self.write(A, side=False)
        self.assertEqual(self.store.listing()["items"], [])

    def test_a_sidecar_with_no_jpeg_is_listed_and_marked(self):
        # Evidence, not clutter: bite B writes the image first, so this means
        # something removed it.
        self.write(A, jpeg=False)
        item = self.store.listing()["items"][0]
        self.assertIsNone(item["on_disk"])
        self.assertEqual(item["frame"]["size_bytes"], 3936)

    def test_a_malformed_sidecar_is_skipped_and_counted(self):
        self.write(A)
        with open(os.path.join(self.tmp, B + ".json"), "w") as fh:
            fh.write("{not json")
        out = self.store.listing()
        self.assertEqual([i["stem"] for i in out["items"]], [A])
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["total"], 2)

    def test_the_file_field_is_reported_but_never_followed(self):
        # Path construction must not come from file CONTENTS.
        self.write(A, file="../../etc/passwd")
        item = self.store.listing()["items"][0]
        self.assertEqual(item["jpeg"], A + ".jpg")
        self.assertTrue(item["name_mismatch"])
        self.assertEqual(item["file_field"], "../../etc/passwd")

    def test_limit_is_applied_but_the_total_is_still_reported(self):
        self.write(A)
        self.write(B)
        out = self.store.listing(limit=1)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["total"], 2)

    def test_a_missing_directory_is_an_honest_error(self):
        store = bench_web.CaptureStore(os.path.join(self.tmp, "nope"))
        out = store.listing()
        self.assertFalse(out["ok"])
        self.assertIn("cannot read", out["err"])
        self.assertEqual(out["items"], [])


class TestCaptureConfinement(CaptureDir):
    """Nothing an operator types becomes a path. Three fences, tested apart."""

    def test_a_committed_capture_resolves(self):
        self.write(A)
        self.assertEqual(self.store.resolve(A + ".jpg"),
                         os.path.join(self.store.root, A + ".jpg"))
        self.assertTrue(self.store.read(A + ".jpg").startswith(b"\xff\xd8"))

    def test_traversal_is_refused(self):
        self.write(A)
        for bad in ("../../etc/passwd",
                    "..%2f..%2fetc%2fpasswd",          # decoded before matching
                    "%2e%2e/%2e%2e/etc/passwd",
                    "/etc/passwd",
                    "..\\..\\windows\\win.ini",
                    A + ".jpg\x00.txt",
                    "sub/" + A + ".jpg",
                    A + ".json",                        # sidecars are not images
                    "cap_2026_seq1.jpg",                # wrong shape
                    ""):
            with self.assertRaises(KeyError, msg=bad):
                self.store.resolve(bad)

    def test_an_image_without_a_sidecar_is_unreachable(self):
        # Fence 2: the reachable set is exactly the set of committed captures.
        self.write(A, side=False)
        with self.assertRaises(KeyError):
            self.store.resolve(A + ".jpg")

    def test_a_symlink_planted_inside_the_directory_is_refused(self):
        # Fence 3, and the only fence that catches this one: the name is
        # perfectly well-formed and the sidecar exists.
        secret = os.path.join(self.tmp, "secret.txt")
        with open(secret, "w") as fh:
            fh.write("not yours")
        self.write(A, jpeg=False)
        os.symlink(secret, os.path.join(self.tmp, A + ".jpg"))
        with self.assertRaises(KeyError):
            self.store.resolve(A + ".jpg")

    def test_every_refusal_reason_is_ascii(self):
        # Measured, not theoretical: the reason was going into the HTTP status
        # line, which encodes as latin-1, so a single em dash dropped the
        # connection and the client saw no response at all instead of a 404.
        # The reason now travels in the body, and this keeps it safe anyway.
        for bad in ("../etc/passwd", "nope.jpg", A + ".jpg", A + ".json"):
            try:
                self.store.resolve(bad)
            except KeyError as e:
                str(e.args[0]).encode("ascii")

    def test_an_implausibly_large_image_is_refused(self):
        self.write(A)
        self.store.JPEG_MAX = 8
        with self.assertRaises(KeyError):
            self.store.resolve(A + ".jpg")


class TestLiveFrameProxy(unittest.TestCase):
    """Same-origin copy of the frozen S3 server's cached frame. It exists
    because a canvas fed from :8080 is tainted and getImageData throws."""

    def patch(self, fn):
        real = bench_web.urllib.request.urlopen
        bench_web.urllib.request.urlopen = fn
        self.addCleanup(setattr, bench_web.urllib.request, "urlopen", real)

    def test_it_returns_the_jpeg(self):
        class R:
            def read(self, n): return b"\xff\xd8body"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        self.patch(lambda url, timeout=None: R())
        code, body = bench_web.fetch_live_frame("127.0.0.1", 8080)
        self.assertEqual((code, body), (200, b"\xff\xd8body"))

    def test_no_frame_yet_is_passed_through_not_invented(self):
        def boom(url, timeout=None):
            raise bench_web.urllib.error.HTTPError(
                url, 503, "no frame received yet", None, None)
        self.patch(boom)
        code, body = bench_web.fetch_live_frame("127.0.0.1", 8080)
        self.assertEqual(code, 503)
        self.assertIn(b"no frame received yet", body)

    def test_a_dead_stream_server_names_the_unit(self):
        def boom(url, timeout=None):
            raise bench_web.urllib.error.URLError("connection refused")
        self.patch(boom)
        code, body = bench_web.fetch_live_frame("127.0.0.1", 8080)
        self.assertEqual(code, 503)
        self.assertIn(b"t1l-stream-server", body)


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

    # -- S18 reef matrix: measured fps beats the derate --------------------
    def test_measured_fps_table_covers_every_mode(self):
        # MEAS_FPS carries one slot per (res, pf); null = not measured yet.
        self.assertIn("MEAS_FPS", self.html)
        block = self.html.split("const MEAS_FPS")[1].split("};")[0]
        self.assertEqual(block.count("color:"), 3, "three res rows (color)")
        self.assertEqual(block.count("mono:"), 3, "three res rows (mono)")
        # the S22 wrap-fix ceilings (2026-08-18, 10-min soaks + ceiling
        # rows on the fixed HE, receiver-ledger exact)
        self.assertIn("28.23", block)
        # VGA color re-measured on the S23 MVE build (7.41 on 4:2:2,
        # 7.93 on 4:2:0 scalar, 9.03 on 4:2:0 + Helium color convert)
        self.assertIn("9.50", block)
        self.assertIn("30.30", block)
        self.assertIn("13.27", block)
        self.assertIn("3.10", block)

    def test_the_label_comes_from_the_model_not_a_constant(self):
        # Provenance rides the model (m.src), so a measured mode and an
        # extrapolated mode label themselves differently on one page.
        self.assertIn("const srcTag", self.html)
        self.assertNotIn("${EST}", self.html)
        for s in ('"measured"', '"measured @q50, q-scaled"',
                  '"extrapolated"'):
            self.assertIn(s, self.html, s)

    def test_measured_fps_wins_over_the_derate(self):
        self.assertIn("mfps !== null ? mfps * (enc50 / encMs)", self.html)
        self.assertIn("encCeil * K.BRIDGE_DERATE", self.html)

    def test_the_measured_constants_carried_over_unchanged(self):
        # Color MEAS rows are the S23 forced-4:2:0 numbers (s22_enc_matrix);
        # mono rows and the derate anchor carried over from the S18 matrix.
        for const in ("5.262e6", "15.0 / (1000 / 19.7)", "1400", "492",
                      "8728, 13.8", "86120, 197.7", "75324, 117.6"):
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

    # -- bite C2 -----------------------------------------------------------
    def test_the_histograms_are_computed_from_pixels(self):
        for fn in ("histogramOf", "renderHistPanel", "plotChannel", "binStats",
                   "getImageData", "0.299*r + 0.587*g + 0.114*b"):
            self.assertIn(fn, self.html, fn)

    def test_the_gallery_reads_sidecars_through_this_server(self):
        # Same-origin, or the canvas is tainted and there is no histogram.
        for path in ("/api/captures", "/captures/", "/api/frame.jpg"):
            self.assertIn(path, self.html, path)

    def test_greyscale_is_decided_by_the_pixels_not_the_command(self):
        # H.colored comes from the decoded image, so a mono JPEG that arrived
        # as colour would show up rather than be described away.
        self.assertIn("H.colored", self.html)
        self.assertNotIn('S.pf === "mono" ? CH', self.html)

    def test_compare_drops_the_right_column(self):
        self.assertIn('getElementById("rightCol").classList.toggle("hidden", cmp)',
                      self.html)
        self.assertIn('classList.toggle("cmp", cmp)', self.html)

    def test_a_bad_camera_reply_raises_a_banner_keyed_on_state(self):
        # The C1 demo's failure: state=timeout while the socket answered 200,
        # with cam_seen TRUE and every other field stale from the last good
        # reply -- so seen/ok are the wrong things to key on.
        self.assertIn("renderBanner", self.html)
        self.assertIn('cam.state !== "ok"', self.html)
        self.assertIn("STALE", self.html)
        self.assertIn("save.state", self.html.replace("sv.state", "save.state"))


class TestPageWiring(unittest.TestCase):
    """There is no JS runtime on this bench and the sandboxed browsers cannot
    reach the page, so the cheap structural faults are caught here instead:
    a getElementById for an element that does not exist throws on the null,
    and takes the whole poll loop with it."""

    def setUp(self):
        self.html = read(PAGE)

    def test_every_element_the_script_reaches_for_exists(self):
        wanted = set(re.findall(r'getElementById\("([^"]+)"\)', self.html))
        have = set(re.findall(r'\bid="([^"]+)"', self.html))
        self.assertTrue(wanted, "no ids found -- the regex is wrong")
        self.assertEqual(wanted - have, set())

    def test_the_ids_bite_c2_added_are_all_present(self):
        for i in ("banner", "bannerTxt", "histPanel", "histRes", "histsrc",
                  "strip", "galHint", "galDir", "btnCompare", "btnRefresh",
                  "compareView", "compareGrid", "btnBackLive", "cmpNote"):
            self.assertIn('id="%s"' % i, self.html, i)

    def test_html_and_script_tags_are_balanced(self):
        for tag in ("script", "style", "table", "div"):
            self.assertEqual(self.html.count("<%s" % tag) - self.html.count("</%s>" % tag),
                             0, tag)

    def test_braces_and_backticks_balance_in_the_script(self):
        js = self.html.split("<script>")[1].split("</script>")[0]
        self.assertEqual(js.count("{") - js.count("}"), 0)
        self.assertEqual(js.count("`") % 2, 0)
        self.assertEqual(js.count("(") - js.count(")"), 0)


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
        allowed = ("__future__", "argparse", "json", "math", "os", "re", "sys",
                   "threading", "time", "urllib", "http", "bench_ctl")
        for line in read(os.path.join(HERE, "bench_web.py")).splitlines():
            if line.startswith("import ") or line.startswith("from "):
                self.assertIn(line.split()[1].split(".")[0], allowed, line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
