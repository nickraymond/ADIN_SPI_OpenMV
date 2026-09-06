#!/usr/bin/env python3
"""Host tests for the field rig. No hardware, no network, stdlib only.

Run: python3 pi/field/test_field.py
"""

import io
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "bench"))
sys.path.insert(0, _HERE)

import discover                                    # noqa: E402
import sources                                     # noqa: E402
import field_stream                                # noqa: E402


GOOD = ("#VER 3.4.0; OpenMV v5.0.0; MicroPython v1.28.0-49\n"
        "#MACH OpenMV N6 with STM32N657X0\n"
        "#ROLE N6\n"
        "#ID 003237473543500400230002\n")


class TestParseProbe(unittest.TestCase):
    def test_parses_all_fields(self):
        got = discover.parse_probe(GOOD)
        self.assertEqual(got["role"], "N6")
        self.assertEqual(got["board_id"], "003237473543500400230002")
        self.assertIn("OpenMV v5.0.0", got["version"])
        self.assertEqual(got["machine"], "OpenMV N6 with STM32N657X0")

    def test_crlf_tolerated(self):
        # The CDC/raw-REPL path returns CRLF; the CRLF trap has cost this
        # repo a session before.
        self.assertEqual(discover.parse_probe(GOOD.replace("\n", "\r\n"))["role"],
                         "N6")

    def test_unknown_tags_ignored(self):
        got = discover.parse_probe(GOOD + "#FUTURE something\n")
        self.assertEqual(got["role"], "N6")

    def test_no_role_raises(self):
        with self.assertRaises(discover.ProbeError):
            discover.parse_probe("#VER x\n#ROLE ?\n#ERR no module named omv\n")

    def test_empty_raises(self):
        with self.assertRaises(discover.ProbeError):
            discover.parse_probe("")


class TestDiscover(unittest.TestCase):
    def _prober(self, mapping):
        def probe(port):
            if port not in mapping:
                raise RuntimeError("no answer")
            return {"role": mapping[port], "port": port,
                    "version": "v", "machine": "m", "board_id": "i"}
        return probe

    def test_maps_roles_regardless_of_port_order(self):
        # The whole point: role comes from the BOARD, not the path. Swap the
        # by-id strings and the mapping must follow the board.
        found, problems = discover.discover(
            ["/dev/serial/by-id/zzz", "/dev/serial/by-id/aaa"],
            self._prober({"/dev/serial/by-id/zzz": "AE3",
                          "/dev/serial/by-id/aaa": "N6"}))
        self.assertEqual(found["AE3"]["port"], "/dev/serial/by-id/zzz")
        self.assertEqual(found["N6"]["port"], "/dev/serial/by-id/aaa")
        self.assertEqual(problems, [])

    def test_dead_port_reported_not_swallowed(self):
        found, problems = discover.discover(
            ["/dev/a", "/dev/dead"], self._prober({"/dev/a": "AE3"}))
        self.assertIn("AE3", found)
        self.assertEqual(len(problems), 1)
        self.assertIn("/dev/dead", problems[0])

    def test_duplicate_role_refuses_to_choose(self):
        found, problems = discover.discover(
            ["/dev/a", "/dev/b"],
            self._prober({"/dev/a": "N6", "/dev/b": "N6"}))
        self.assertEqual(len(found), 1)
        self.assertTrue(any("two boards report role N6" in p for p in problems))

    def test_require_lists_missing_in_order(self):
        self.assertEqual(discover.require({}), ["AE3", "N6"])
        self.assertEqual(discover.require({"N6": {}}), ["AE3"])
        self.assertEqual(discover.require({"AE3": {}, "N6": {}}), [])


class TestSplitMjpeg(unittest.TestCase):
    def _jpg(self, body):
        return sources.SOI + body + sources.EOI

    def test_two_whole_frames(self):
        a, b = self._jpg(b"aaa"), self._jpg(b"bb")
        frames, rest = sources.split_mjpeg(a + b)
        self.assertEqual(frames, [a, b])
        self.assertEqual(rest, b"")

    def test_partial_frame_is_held_back(self):
        a = self._jpg(b"aaa")
        frames, rest = sources.split_mjpeg(a + sources.SOI + b"partial")
        self.assertEqual(frames, [a])
        self.assertEqual(rest, sources.SOI + b"partial")

    def test_leading_garbage_resyncs(self):
        # Joining a stream mid-frame must discard, never emit a truncated
        # JPEG that decodes to garbage.
        a = self._jpg(b"aaa")
        frames, rest = sources.split_mjpeg(b"\x00\x11junk" + a)
        self.assertEqual(frames, [a])

    def test_no_soi_keeps_only_a_marker_tail(self):
        frames, rest = sources.split_mjpeg(b"\x00" * 10)
        self.assertEqual(frames, [])
        self.assertLessEqual(len(rest), 1)


class TestStreamStats(unittest.TestCase):
    def _stats(self):
        self.now = 0.0
        return sources.StreamStats(clock=lambda: self.now)

    def test_fps_over_window(self):
        st = self._stats()
        for _ in range(5):
            st.note({}, 1000)
            self.now += 0.1          # 10 fps
        self.assertAlmostEqual(st.fps(), 10.0, places=5)

    def test_mbps_pairs_intervals_with_payloads(self):
        st = self._stats()
        for _ in range(3):
            st.note({}, 1250)        # bytes
            self.now += 0.5
        # 2 intervals of 0.5 s = 1.0 s span, 2 payloads counted.
        self.assertAlmostEqual(st.mbps(), 1250 * 2 * 8 / 1.0 / 1e6, places=6)

    def test_stale_none_before_any_frame(self):
        self.assertIsNone(self._stats().snapshot()["stale_s"])

    def test_stale_grows_with_clock(self):
        st = self._stats()
        st.note({}, 10)
        self.now += 4.0
        self.assertEqual(st.snapshot()["stale_s"], 4.0)

    def test_window_is_bounded(self):
        st = self._stats()
        for _ in range(sources.StreamStats.WINDOW * 3):
            st.note({}, 10)
            self.now += 0.01
        self.assertLessEqual(len(st._times), sources.StreamStats.WINDOW)


class TestCsiReaderLoop(unittest.TestCase):
    def test_feeds_frames_and_counts(self):
        a = sources.SOI + b"one" + sources.EOI
        b = sources.SOI + b"two" + sources.EOI
        view = sources.SourceView("IMX708", "csi")
        sources.csi_reader_loop(io.BytesIO(a + b), view.latest, view.stats,
                                view.state, chunk=3)
        frame, seq = view.latest.get()
        self.assertEqual(frame, b)
        self.assertEqual(seq, 2)
        self.assertEqual(view.stats.frames, 2)
        self.assertFalse(view.state["alive"])


class TestRpicamArgv(unittest.TestCase):
    def test_mjpeg_to_stdout(self):
        argv = sources.rpicam_argv(640, 480, 15, 50)
        self.assertEqual(argv[0], "rpicam-vid")
        self.assertIn("--codec", argv)
        self.assertEqual(argv[argv.index("--codec") + 1], "mjpeg")
        self.assertEqual(argv[argv.index("-o") + 1], "-")
        self.assertEqual(argv[argv.index("-t") + 1], "0")
        self.assertEqual(argv[argv.index("--width") + 1], "640")


class TestSupervisorCsiRestart(unittest.TestCase):
    def test_restarts_and_counts_reconnect(self):
        class FakeProc:
            def __init__(self, data):
                self.stdout = io.BytesIO(data)
            def terminate(self): pass
            def wait(self, timeout=None): pass

        view = sources.SourceView("IMX708", "csi")
        frame = sources.SOI + b"x" + sources.EOI
        spawned = []

        def spawn(argv):
            spawned.append(argv)
            if len(spawned) >= 2:
                view.state["quit"] = True     # stop after the restart
            return FakeProc(frame)

        sources.supervise_csi(view, 640, 480, 15, 50, spawn=spawn,
                              sleep=lambda s: None)
        self.assertEqual(len(spawned), 2)
        self.assertEqual(view.stats.reconnects, 1)


class TestBuildViews(unittest.TestCase):
    def test_layout_order_is_imx_ae3_n6(self):
        found = {"AE3": {"port": "/dev/a", "machine": "OpenMV-AE3"},
                 "N6": {"port": "/dev/n", "machine": "OpenMV N6"}}
        views = field_stream.build_views(found)
        self.assertEqual([v.label for v in views], ["IMX708", "AE3", "N6"])
        self.assertEqual([v.kind for v in views], ["csi", "serial", "serial"])

    def test_missing_board_keeps_its_panel_and_says_why(self):
        views = field_stream.build_views({"N6": {"port": "/dev/n", "machine": "m"}})
        ae3 = [v for v in views if v.label == "AE3"][0]
        self.assertEqual(len(views), 3)
        self.assertIn("not found", ae3.stats.status)
        self.assertFalse(ae3.state["alive"])

    def test_page_renders_all_panels(self):
        html = field_stream.page(field_stream.build_views({}))
        for label in ("IMX708", "AE3", "N6"):
            self.assertIn(label, html)
        self.assertIn("/s/2/stream", html)


class TestBoardCfg(unittest.TestCase):
    def test_streaming_only_no_model_no_blobs(self):
        cfg = field_stream.board_cfg("VGA", 50, 66)
        self.assertFalse(cfg["detect"])
        self.assertFalse(cfg["blobs"])
        self.assertFalse(cfg["overlay"])
        self.assertEqual(cfg["pace_ms"], 66)

    def test_fps_becomes_pace_ms(self):
        args = field_stream.parse_args(["--fps", "15"])
        self.assertEqual(int(1000.0 / args.fps), 66)

    def test_board_script_builds_with_our_cfg(self):
        # Guards the contract with the shared board script: if a required
        # key is dropped from board_cfg, this fails here rather than on a
        # board at 2 a.m.
        text = field_stream.build_board_script_text(
            field_stream.board_cfg("VGA", 50, 66))
        self.assertIn("_CFG = ", text)
        self.assertIn("PACE_MS", text)


class TestPaceKnobDefaultsOff(unittest.TestCase):
    def test_shared_board_script_defaults_to_free_run(self):
        # S8/S28 runs must be byte-identical: pacing is opt-in.
        path = os.path.join(_ROOT, "bench", "n6_stream_board.py")
        src = open(path).read()
        self.assertIn('PACE_MS = _CFG.get("pace_ms", 0)', src)



# --- workbench integration: role-named boards -------------------------------

sys.path.insert(0, os.path.join(_ROOT, "pi", "workbench"))
import workbench                                   # noqa: E402


def _recipe(**over):
    obj = {"name": "r", "title": "T", "summary": "s",
           "boards": [{"label": "AE3", "role": "AE3"}],
           "run": {"argv": ["python3", "x.py"], "cwd": "."},
           "health": {"http": "http://127.0.0.1:8090/"}}
    obj.update(over)
    return obj


class TestRoleSchema(unittest.TestCase):
    def test_role_only_board_is_valid(self):
        rec, errs = workbench.validate_recipe(_recipe(), "t.toml")
        self.assertEqual(errs, [])
        self.assertEqual(rec["boards"][0]["role"], "AE3")
        # _str returns None for an absent optional key -- the repo's
        # existing convention (firmware behaves the same way), and the
        # preflight dispatch treats it as falsy.
        self.assertIsNone(rec["boards"][0]["by_id"])

    def test_by_id_still_valid(self):
        rec, errs = workbench.validate_recipe(
            _recipe(boards=[{"label": "AE3", "by_id": "usb-OpenMV_x-if00"}]),
            "t.toml")
        self.assertEqual(errs, [])
        self.assertEqual(rec["boards"][0]["by_id"], "usb-OpenMV_x-if00")

    def test_both_is_an_error(self):
        _, errs = workbench.validate_recipe(
            _recipe(boards=[{"label": "A", "by_id": "usb-x-if00",
                             "role": "AE3"}]), "t.toml")
        self.assertTrue(any("not both" in e for e in errs))

    def test_neither_is_an_error(self):
        _, errs = workbench.validate_recipe(
            _recipe(boards=[{"label": "A"}]), "t.toml")
        self.assertTrue(any("by_id" in e and "role" in e for e in errs))


class TestRolePreflight(unittest.TestCase):
    def _dev(self, names):
        import tempfile
        d = tempfile.mkdtemp()
        for n in names:
            target = os.path.join(d, n.replace("-if00", ".tty"))
            open(target, "w").close()
            os.symlink(target, os.path.join(d, n))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return d

    def test_waiting_when_no_ports(self):
        got = workbench.role_preflight("AE3", dev_dir=self._dev([]))
        self.assertEqual(got["state"], "waiting")
        self.assertEqual(got["candidates"], 0)

    def test_ready_counts_candidates_without_naming_them(self):
        d = self._dev(["usb-A-if00", "usb-B-if00"])
        got = workbench.role_preflight("AE3", dev_dir=d, proc="/nonexistent")
        self.assertEqual(got["state"], "ready")
        self.assertEqual(got["candidates"], 2)
        # It must NOT claim a tty for a role -- that needs a port open.
        self.assertIsNone(got["tty"])

    def test_key_is_namespaced_so_roles_and_by_ids_cannot_collide(self):
        got = workbench.role_preflight("N6", dev_dir=self._dev([]))
        self.assertEqual(got["by_id"], "role:N6")


class TestShippedFieldRecipeLoads(unittest.TestCase):
    def test_field_streams_recipe_is_valid(self):
        # Mirrors the workbench's own shipped-recipes gate: a broken released
        # recipe must fail CI on any machine, not just on the bench.
        path = os.path.join(_ROOT, "pi", "workbench", "recipes",
                            "field_streams.toml")
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs python 3.11+")
        with open(path, "rb") as fh:
            obj = tomllib.load(fh)
        rec, errs = workbench.validate_recipe(obj, "field_streams.toml")
        self.assertEqual(errs, [])
        self.assertEqual({b["role"] for b in rec["boards"]}, {"AE3", "N6"})
        self.assertIn("run_field_stream.sh", " ".join(rec["run"]["argv"]))

    def test_launcher_is_executable_and_has_no_host_specific_path(self):
        sh = os.path.join(_ROOT, "pi", "field", "run_field_stream.sh")
        self.assertTrue(os.access(sh, os.X_OK), "wrapper must be executable")
        body = open(sh).read()
        # A repo file the other rigs also read must not hardcode this rig's
        # home directory; $HOME is resolved at run time instead. Comments may
        # mention the path (they explain why it is avoided), so only the
        # executable lines are checked.
        code = "\n".join(l for l in body.splitlines()
                          if not l.lstrip().startswith("#"))
        self.assertNotIn("/home/pi", code)
        self.assertIn("$HOME/mpv/bin/python", body)
        self.assertIn("FIELD_PYTHON", body)


class TestMaxSecondsFitsMicropythonTicks(unittest.TestCase):
    """Regression for a bug that only hardware could show.

    max_seconds=1e9 crashed both boards with OverflowError inside
    time.ticks_add(). MicroPython's ticks delta must fit +/- 2**29 ms.
    """

    TICKS_HALF_PERIOD_MS = 2 ** 29

    def test_cap_is_within_ticks_range(self):
        ms = field_stream.MAX_STREAM_SECONDS * 1000
        self.assertLess(ms, self.TICKS_HALF_PERIOD_MS)

    def test_cap_keeps_real_margin(self):
        # Not merely under the limit -- comfortably under it, so a port with
        # a smaller ticks_period does not rediscover this at 2 a.m.
        ms = field_stream.MAX_STREAM_SECONDS * 1000
        self.assertLess(ms, self.TICKS_HALF_PERIOD_MS / 2)

    def test_board_cfg_uses_the_cap(self):
        self.assertEqual(field_stream.board_cfg("VGA", 50, 66)["max_seconds"],
                         field_stream.MAX_STREAM_SECONDS)

    def test_cap_is_long_enough_to_not_bite_a_session(self):
        self.assertGreaterEqual(field_stream.MAX_STREAM_SECONDS, 24 * 3600)


class TestDiscoverRetryOnRefusal(unittest.TestCase):
    """A refused port gets exactly ONE retry after real silence."""

    def test_retries_once_after_refusal_and_recovers(self):
        calls = []
        slept = []

        def fake():
            calls.append(1)
            if len(calls) == 1:
                return ({"N6": {"port": "/dev/n"}},
                        ["/dev/a: could not enter raw repl"])
            return ({"N6": {"port": "/dev/n"}, "AE3": {"port": "/dev/a"}}, [])

        found, problems = field_stream.discover_boards(
            settle_s=60.0, sleep=slept.append, discover=fake)
        self.assertEqual(len(calls), 2)
        self.assertEqual(slept, [60.0])
        self.assertIn("AE3", found)

    def test_no_retry_when_all_roles_found(self):
        calls = []

        def fake():
            calls.append(1)
            return ({"AE3": {"port": "/dev/a"}, "N6": {"port": "/dev/n"}}, [])

        field_stream.discover_boards(sleep=lambda s: None, discover=fake)
        self.assertEqual(len(calls), 1)

    def test_no_retry_when_board_is_simply_absent(self):
        # An absent device is never probed, so it yields no problem line --
        # waiting 60 s for a board that is unplugged is pure delay.
        calls = []

        def fake():
            calls.append(1)
            return ({"N6": {"port": "/dev/n"}}, [])

        field_stream.discover_boards(sleep=lambda s: None, discover=fake)
        self.assertEqual(len(calls), 1)

    def test_retry_happens_at_most_once(self):
        calls = []

        def fake():
            calls.append(1)
            return ({}, ["/dev/a: could not enter raw repl"])

        field_stream.discover_boards(sleep=lambda s: None, discover=fake)
        self.assertEqual(len(calls), 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
