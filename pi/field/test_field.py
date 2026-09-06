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


class TestBoardKeyAndSettleOverlap(unittest.TestCase):
    """Bug 1: the settle window was silently skippable across recipe types."""

    def test_board_key_by_id(self):
        self.assertEqual(workbench.board_key({"by_id": "usb-X-if00"}),
                         "usb-X-if00")

    def test_board_key_role(self):
        self.assertEqual(workbench.board_key({"by_id": None, "role": "AE3"}),
                         "role:AE3")

    def test_role_and_by_id_recipes_overlap(self):
        # THE BUG: stop hil-aiming (by_id), start field-streams (role) --
        # the SAME two chips -- and the raw sets never intersected, so the
        # 35 s window was skipped on a real board.
        self.assertTrue(workbench.keys_overlap({"role:AE3"}, {"usb-OpenMV-if00"}))
        self.assertTrue(workbench.keys_overlap({"usb-OpenMV-if00"}, {"role:AE3"}))

    def test_plain_by_id_sets_still_compare_exactly(self):
        self.assertTrue(workbench.keys_overlap({"usb-A"}, {"usb-A", "usb-B"}))
        self.assertFalse(workbench.keys_overlap({"usb-A"}, {"usb-B"}))

    def test_empty_never_overlaps(self):
        self.assertFalse(workbench.keys_overlap(set(), {"role:AE3"}))
        self.assertFalse(workbench.keys_overlap({"role:AE3"}, set()))


class TestPageHasNoHardcodedHost(unittest.TestCase):
    """Bug 3: the page claimed 'nereus000' while served from nereus002."""

    def _page(self):
        return open(os.path.join(_ROOT, "pi", "workbench", "static",
                                 "workbench.html"), encoding="utf-8").read()

    def test_no_hardcoded_hostname_anywhere(self):
        self.assertNotIn("nereus000", self._page())

    def test_header_is_filled_from_location(self):
        page = self._page()
        self.assertIn('id="hostname"', page)
        self.assertIn("location.hostname", page)

    def test_stuck_hint_names_the_running_demo(self):
        # It used to hardcode n6_stream_host.py, so on any other recipe the
        # "manual" command it handed you would have killed nothing.
        page = self._page()
        self.assertNotIn("n6_stream_host.py", page)
        self.assertIn("stuckPattern", page)


class TestBadgeLookupUsesCanonicalKey(unittest.TestCase):
    """Bug 2: role cards read 'waiting -- not enumerated' while streaming."""

    def test_page_matches_boards_by_key_not_by_id(self):
        page = open(os.path.join(_ROOT, "pi", "workbench", "static",
                                 "workbench.html"), encoding="utf-8").read()
        self.assertIn("function boardKey", page)
        self.assertNotIn("b.by_id === recipeBoard.by_id", page)


class TestEveryRunnableRecipeHasAThumbnail(unittest.TestCase):
    """Nick's rule (2026-09-06): a new card must show what the UI looks like.

    The README already listed a thumbnail as a release step and it got
    skipped anyway, so it is a test now rather than a discipline. Guide
    cards are exempt: they have no demo to photograph.
    """

    def test_runnable_recipes_declare_a_thumbnail_that_exists(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs python 3.11+")
        rdir = os.path.join(_ROOT, "pi", "workbench", "recipes")
        # Pre-existing gaps from earlier sprints. The rule is enforced for
        # everything else from now on; listing these by name makes the debt
        # visible instead of silently weakening the gate. Delete an entry as
        # its thumbnail lands -- an entry that becomes untrue also fails.
        LEGACY_NO_THUMB = {"hil_aiming.toml", "s28_stack_compare.toml",
                           "s8_hil_review.toml", "s8_hil_urchin.toml"}
        missing, fixed = [], []
        for fn in sorted(os.listdir(rdir)):
            if not fn.endswith(".toml"):
                continue
            with open(os.path.join(rdir, fn), "rb") as fh:
                obj = tomllib.load(fh)
            if obj.get("guide"):
                continue                      # cookbook chapter, no demo
            thumb = obj.get("thumbnail")
            has = bool(thumb) and os.path.exists(os.path.join(rdir, thumb))
            if fn in LEGACY_NO_THUMB:
                if has:
                    fixed.append(fn)
                continue
            if not thumb:
                missing.append("%s: no thumbnail" % fn)
            elif not has:
                missing.append("%s: thumbnail %s not found" % (fn, thumb))
        self.assertEqual(missing, [], "runnable recipes must ship a thumbnail")
        self.assertEqual(fixed, [], "these now HAVE thumbnails -- remove them "
                                    "from LEGACY_NO_THUMB")


# --- S29 bite 2: UI features from Nick's review -----------------------------

import netinfo                                     # noqa: E402


class TestNetInfoParsing(unittest.TestCase):
    IW = """Connected to 3c:37:86:12:34:56 (on wlan0)
\tSSID: Ford
\tfreq: 2437
\tRX: 128374 bytes (901 packets)
\tTX: 39284 bytes (410 packets)
\tsignal: -71 dBm
\trx bitrate: 65.0 MBit/s
\ttx bitrate: 72.2 MBit/s
"""

    def test_parses_link(self):
        got = netinfo.parse_iw_link(self.IW)
        self.assertTrue(got["connected"])
        self.assertEqual(got["ssid"], "Ford")
        self.assertEqual(got["freq_mhz"], 2437)
        self.assertEqual(got["signal_dbm"], -71)
        self.assertEqual(got["tx_bitrate_mbps"], 72.2)

    def test_not_connected(self):
        got = netinfo.parse_iw_link("Not connected.")
        self.assertFalse(got["connected"])
        self.assertIsNone(got["signal_dbm"])

    def test_missing_fields_are_none_not_zero(self):
        # A dead radio must not read as a quiet one.
        got = netinfo.parse_iw_link("Connected to aa:bb (on wlan0)\n")
        self.assertIsNone(got["signal_dbm"])
        self.assertIsNone(got["ssid"])

    def test_signal_grades(self):
        self.assertEqual(netinfo.signal_grade(-45), "excellent")
        self.assertEqual(netinfo.signal_grade(-60), "good")
        self.assertEqual(netinfo.signal_grade(-71), "weak")
        self.assertEqual(netinfo.signal_grade(-85), "poor")
        self.assertEqual(netinfo.signal_grade(None), "unknown")

    def test_default_iface_from_route(self):
        self.assertEqual(netinfo.default_iface(
            "default via 192.168.1.1 dev wlan0 proto dhcp metric 600"), "wlan0")
        self.assertIsNone(netinfo.default_iface("10.0.0.0/8 dev eth0"))

    def test_proc_wireless_fallback(self):
        txt = ("Inter-| sta-|   Quality\n face | link | level\n"
               " wlan0: 0000   58.  -68.  -256\n")
        got = netinfo.parse_proc_wireless(txt)
        self.assertEqual(got["wlan0"]["signal_dbm"], -68.0)

    def test_wired_reports_no_rssi(self):
        got = netinfo.net_status(
            run=lambda a, timeout=3: "default via 1.1.1.1 dev eth0\n"
            if a[:2] == ["ip", "route"] else "",
            read=lambda p: "")
        self.assertTrue(got["wired"])
        self.assertEqual(got["grade"], "wired")
        self.assertIsNone(got["signal_dbm"])

    def test_wifi_status_end_to_end(self):
        def run(argv, timeout=3):
            if argv[:2] == ["ip", "route"]:
                return "default via 192.168.1.1 dev wlan0 proto dhcp\n"
            if argv[:2] == ["iw", "dev"]:
                return self.IW
            return ""
        got = netinfo.net_status(run=run, read=lambda p: "")
        self.assertEqual(got["iface"], "wlan0")
        self.assertEqual(got["grade"], "weak")
        self.assertEqual(got["ssid"], "Ford")


class TestFramesizeDrivesAllThreeCameras(unittest.TestCase):
    def test_hd_matches_the_n6_rectangle(self):
        # The IMX is matched to the N6's HD so the panels frame the same
        # scene -- not dumbed down to the AE3.
        self.assertEqual(field_stream.CSI_SIZES["HD"], (1280, 720))

    def test_vga_default(self):
        self.assertEqual(field_stream.CSI_SIZES["VGA"], (640, 480))

    def test_csi_overrides_default_to_follow_framesize(self):
        args = field_stream.parse_args(["--framesize", "HD"])
        self.assertEqual(args.csi_width, 0)
        self.assertEqual(args.csi_height, 0)

    def test_explicit_override_wins(self):
        args = field_stream.parse_args(["--csi-width", "800"])
        self.assertEqual(args.csi_width, 800)


class TestSetVsActualReporting(unittest.TestCase):
    def test_snapshot_carries_set_fps_and_resolution(self):
        v = sources.SourceView("AE3", "serial", "/dev/x")
        v.want = {"fps": 15.0, "framesize": "HD"}
        v.stats.info_fields = {"w": 1280, "h": 800}
        s = v.snapshot()
        self.assertEqual(s["set_fps"], 15.0)
        self.assertEqual(s["res"], "1280x800")
        self.assertEqual(s["framesize"], "HD")

    def test_board_banner_wins_over_what_we_asked_for(self):
        # The sensor letterboxes; its own report is authoritative.
        v = sources.SourceView("AE3", "serial")
        v.want = {"w": 1280, "h": 720, "fps": 15.0}
        v.stats.info_fields = {"w": 1280, "h": 800}
        self.assertEqual(v.snapshot()["res"], "1280x800")

    def test_res_is_none_before_the_board_answers(self):
        v = sources.SourceView("N6", "serial")
        self.assertIsNone(v.snapshot()["res"])


class TestPageUiFeatures(unittest.TestCase):
    def _html(self):
        return field_stream.page(field_stream.build_views({}))

    def test_click_to_fullscreen_present(self):
        h = self._html()
        self.assertIn("focusPanel", h)
        self.assertIn("body.zoom", h)
        self.assertIn("Escape", h)

    def test_stats_are_a_fixed_table_not_a_text_blob(self):
        h = self._html()
        self.assertIn("<table class=\"st\">", h)
        for row in ("resolution", "frame rate", "stream rate", "status"):
            self.assertIn(row, h)

    def test_target_row_was_removed(self):
        # Nick: the device path is noise mid-test.
        self.assertNotIn("<th>target</th>", self._html())

    def test_link_indicator_is_top_right(self):
        h = self._html()
        self.assertIn('id="net"', h)
        self.assertIn("/api/net", h)
        self.assertIn("text-align:right", h)

    def test_set_and_actual_both_rendered(self):
        h = self._html()
        self.assertIn("actual", h)
        self.assertIn("set", h)


class TestRecipeParams(unittest.TestCase):
    def test_framesize_and_fps_are_card_toggles(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs python 3.11+")
        path = os.path.join(_ROOT, "pi", "workbench", "recipes",
                            "field_streams.toml")
        with open(path, "rb") as fh:
            obj = tomllib.load(fh)
        rec, errs = workbench.validate_recipe(obj, "field_streams.toml")
        self.assertEqual(errs, [])
        self.assertEqual(rec["params"]["framesize"], ["VGA", "HD"])
        self.assertEqual(rec["params"]["fps"][0], "15")

    def test_params_become_argv_flags(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs python 3.11+")
        path = os.path.join(_ROOT, "pi", "workbench", "recipes",
                            "field_streams.toml")
        with open(path, "rb") as fh:
            rec, _ = workbench.validate_recipe(tomllib.load(fh), "x.toml")
        extra, picks, err = workbench.resolve_params(rec, {"framesize": "HD"})
        self.assertIsNone(err)
        self.assertIn("--framesize", extra)
        self.assertEqual(extra[extra.index("--framesize") + 1], "HD")
        self.assertEqual(picks["framesize"], "HD")

    def test_undeclared_value_is_refused(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs python 3.11+")
        path = os.path.join(_ROOT, "pi", "workbench", "recipes",
                            "field_streams.toml")
        with open(path, "rb") as fh:
            rec, _ = workbench.validate_recipe(tomllib.load(fh), "x.toml")
        _, _, err = workbench.resolve_params(rec, {"framesize": "4K"})
        self.assertIsNotNone(err)

    def test_the_flags_the_card_sends_are_all_accepted_by_the_viewer(self):
        # Guards the card<->viewer contract: a toggle that the child cannot
        # parse would fail only at click time, on the bench.
        for fs in ("VGA", "HD"):
            for fps in ("15", "10", "5", "30"):
                for q in ("50", "75", "90"):
                    a = field_stream.parse_args(
                        ["--framesize", fs, "--fps", fps, "--quality", q])
                    self.assertEqual(a.framesize, fs)
                    self.assertEqual(a.fps, float(fps))


class TestMonoOption(unittest.TestCase):
    """Nick: 'I want to see the max fps we can do for AE3 when mono'."""

    def test_default_is_colour_so_existing_runs_are_unchanged(self):
        self.assertEqual(field_stream.board_cfg("VGA", 50, 66)["pixfmt"],
                         "RGB565")

    def test_mono_maps_to_grayscale(self):
        cfg = field_stream.board_cfg("HD", 50, 66, "GRAYSCALE")
        self.assertEqual(cfg["pixfmt"], "GRAYSCALE")

    def test_flag_maps_to_pixfmt(self):
        self.assertEqual(field_stream.parse_args(["--colour", "mono"]).colour,
                         "mono")
        self.assertEqual(field_stream.parse_args([]).colour, "color")

    def test_board_script_defaults_to_colour(self):
        src = open(os.path.join(_ROOT, "bench", "n6_stream_board.py")).read()
        self.assertIn('PIXFMT = _CFG.get("pixfmt", "RGB565")', src)

    def test_board_banner_reports_the_real_pixfmt(self):
        # It used to print the literal "RGB565" regardless, which would have
        # made a mono run look like a colour one in every artifact.
        src = open(os.path.join(_ROOT, "bench", "n6_stream_board.py")).read()
        self.assertNotIn('\\"pixfmt\\":\\"RGB565\\"', src)
        self.assertIn('_json_str(PIXFMT)', src)

    def test_card_offers_the_toggle(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs python 3.11+")
        path = os.path.join(_ROOT, "pi", "workbench", "recipes",
                            "field_streams.toml")
        with open(path, "rb") as fh:
            rec, errs = workbench.validate_recipe(tomllib.load(fh), "x.toml")
        self.assertEqual(errs, [])
        self.assertEqual(rec["params"]["colour"], ["color", "mono"])
        extra, _, err = workbench.resolve_params(rec, {"colour": "mono"})
        self.assertIsNone(err)
        self.assertEqual(extra[extra.index("--colour") + 1], "mono")

if __name__ == "__main__":
    unittest.main(verbosity=2)
