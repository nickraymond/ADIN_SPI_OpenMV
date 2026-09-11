#!/usr/bin/env python3
"""Host tests for the S32 recorder. No boards, no ffmpeg, no network.

The parts under test are the ones that fail SILENTLY on hardware: a resync that
accepts a bogus frame, a ring that drops without counting, a ceiling check that
green-lights an impossible request, and a media route that serves a file
outside the recordings directory.

    python3 pi/field/test_recorder.py
"""

import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recorder as R            # noqa: E402
import recorder_web as RW      # noqa: E402
import record_run as RR         # noqa: E402
import storage as ST            # noqa: E402
import transcode as T           # noqa: E402


def frame(seq, payload):
    return R.MAGIC + struct.pack("<II", seq, len(payload)) + payload


JPG = b"\xff\xd8" + b"body-bytes" + b"\xff\xd9"


class TestFrameParser(unittest.TestCase):
    def test_single_frame(self):
        p = R.FrameParser()
        self.assertEqual(p.feed(frame(0, JPG)), [(0, JPG)])

    def test_many_frames_one_chunk(self):
        p = R.FrameParser()
        blob = b"".join(frame(i, JPG) for i in range(5))
        self.assertEqual([s for s, _ in p.feed(blob)], [0, 1, 2, 3, 4])

    def test_split_across_reads(self):
        """A frame arriving in three reads must still come out whole and once."""
        p = R.FrameParser()
        f = frame(7, JPG)
        self.assertEqual(p.feed(f[:5]), [])
        self.assertEqual(p.feed(f[5:9]), [])
        self.assertEqual(p.feed(f[9:]), [(7, JPG)])

    def test_leading_banner_noise_is_skipped(self):
        p = R.FrameParser()
        out = p.feed(b"paste mode echo garbage\r\n" + frame(1, JPG))
        self.assertEqual(out, [(1, JPG)])
        self.assertGreater(p.dropped_bytes, 0)

    def test_absurd_length_is_refused_and_resyncs(self):
        """A corrupt length must not swallow the stream."""
        p = R.FrameParser()
        bad = R.MAGIC + struct.pack("<II", 3, 0xFFFFFFF0)
        out = p.feed(bad + frame(4, JPG))
        self.assertEqual(out, [(4, JPG)])
        self.assertEqual(p.resyncs, 1)

    def test_payload_that_is_not_jpeg_is_refused(self):
        """The SOI check is what makes a plausible length insufficient."""
        p = R.FrameParser()
        notjpeg = b"\x00\x01\x02\x03\x04\x05"
        out = p.feed(frame(9, notjpeg) + frame(10, JPG))
        self.assertEqual(out, [(10, JPG)])
        self.assertEqual(p.resyncs, 1)

    def test_zero_length_refused(self):
        p = R.FrameParser()
        out = p.feed(R.MAGIC + struct.pack("<II", 1, 0) + frame(2, JPG))
        self.assertEqual(out, [(2, JPG)])

    def test_magic_appearing_inside_payload_is_harmless(self):
        """The length field consumes the payload, so embedded magic is data."""
        p = R.FrameParser()
        tricky = b"\xff\xd8" + R.MAGIC + b"more" + b"\xff\xd9"
        self.assertEqual(p.feed(frame(0, tricky)), [(0, tricky)])


class TestRingBuffer(unittest.TestCase):
    def test_put_get_roundtrip(self):
        r = R.RingBuffer(1000)
        self.assertTrue(r.put((1, b"abc")))
        self.assertEqual(r.get(timeout=0.01), (1, b"abc"))

    def test_overflow_drops_and_counts(self):
        """A full ring must never silently discard."""
        r = R.RingBuffer(10)
        self.assertTrue(r.put((1, b"12345")))
        self.assertFalse(r.put((2, b"123456")))
        self.assertEqual(r.dropped_frames, 1)
        self.assertEqual(r.dropped_bytes, 6)

    def test_high_water_tracked(self):
        r = R.RingBuffer(100)
        r.put((1, b"x" * 40))
        r.put((2, b"x" * 30))
        self.assertEqual(r.high_water, 70)
        r.get(timeout=0.01)
        self.assertEqual(r.high_water, 70)     # a peak, not a level

    def test_get_returns_none_when_empty(self):
        self.assertIsNone(R.RingBuffer(10).get(timeout=0.01))

    def test_writer_thread_writes_every_frame(self):
        r = R.RingBuffer(1 << 20)
        state = {"done": False}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.mjpeg")
            t = threading.Thread(target=R.writer_thread, args=(r, path, state))
            t.start()
            for i in range(50):
                r.put((i, JPG))
            state["done"] = True
            r.close()
            t.join(timeout=10)
            self.assertEqual(state["written_frames"], 50)
            self.assertEqual(os.path.getsize(path), 50 * len(JPG))


class TestRingSizing(unittest.TestCase):
    def test_covers_the_measured_stall(self):
        """nereus000 measured a 3.86 s write stall; the ring must cover it."""
        rate = 14.5e6
        self.assertGreaterEqual(R.ring_size_bytes(rate, 8e9), rate * 3.86)

    def test_clamped_by_a_small_host(self):
        """A Pi Zero 2 W has 512 MB and cannot lend 256 MB."""
        small = R.ring_size_bytes(14.5e6, 300 * 1024 * 1024)
        self.assertLessEqual(small, 300 * 1024 * 1024 * 0.25 + 1)

    def test_never_below_the_floor(self):
        self.assertGreaterEqual(R.ring_size_bytes(1000, 8e9), R.RING_MIN_BYTES)

    def test_never_above_the_cap(self):
        self.assertLessEqual(R.ring_size_bytes(1e12, 64e9), R.RING_MAX_BYTES)

    def test_missing_meminfo_does_not_crash(self):
        self.assertGreater(R.ring_size_bytes(14.5e6, None), 0)
        self.assertIsNone(R.mem_available_bytes("/nonexistent/meminfo"))


class TestPacing(unittest.TestCase):
    def test_free_run(self):
        self.assertEqual(R.pace_ms_for(0), 0)
        self.assertEqual(R.pace_ms_for(None), 0)

    def test_thirty_fps(self):
        self.assertEqual(R.pace_ms_for(30), 33)


class TestCeilingCheck(unittest.TestCase):
    CEIL = {"cameras": {"N6": {"cells": {"HD_q90": 29.3, "HD_q85": 34.9,
                                         "VGA_q90": 63.1}}}}

    def test_impossible_is_named(self):
        v, msg = RR.check_request(self.CEIL, "N6", "HD", 90, 30)
        self.assertEqual(v, "impossible")
        self.assertIn("29.3", msg)

    def test_tight_when_inside_ten_percent(self):
        v, _ = RR.check_request(self.CEIL, "N6", "HD", 85, 33)
        self.assertEqual(v, "tight")

    def test_ok_with_margin(self):
        v, _ = RR.check_request(self.CEIL, "N6", "HD", 85, 30)
        self.assertEqual(v, "ok")

    def test_unmeasured_is_not_silently_ok(self):
        """A cell nobody measured must never be reported as fine."""
        v, _ = RR.check_request(self.CEIL, "AE3", "HD", 90, 30)
        self.assertEqual(v, "unmeasured")

    # The encoder number excludes the board's own USB write and is always the
    # optimistic one. Measured on nereus000: HD q85 encodes at 34.9 fps and
    # DELIVERS 23.8. Guarding on 34.9 promises 30 and hands back 24.
    BOTH = {"cameras": {"N6": {"cells": {"HD_q85": 34.9},
                               "delivered": {"HD_q85": 23.81}}}}

    def test_delivered_is_preferred_over_encoder(self):
        val, kind = RR.ceiling_for(self.BOTH, "N6", "HD", 85)
        self.assertEqual(kind, "delivered")
        self.assertAlmostEqual(val, 23.81)

    def test_delivered_turns_a_false_ok_into_impossible(self):
        """The exact regression: 30 fps looks fine on 34.9, but is not."""
        enc_only = {"cameras": {"N6": {"cells": {"HD_q85": 34.9}}}}
        self.assertEqual(RR.check_request(enc_only, "N6", "HD", 85, 30)[0], "ok")
        self.assertEqual(RR.check_request(self.BOTH, "N6", "HD", 85, 30)[0],
                         "impossible")

    def test_encoder_verdict_says_the_real_rate_is_lower(self):
        _, msg = RR.check_request({"cameras": {"N6": {"cells": {"HD_q85": 34.9}}}},
                                  "N6", "HD", 85, 20)
        self.assertIn("lower", msg)


class TestPerCameraSettings(unittest.TestCase):
    """The cameras are not equals, so one global setting cannot serve them.

    HD q70 gives the N6 30.24 fps and the AE3 2.29. Measured, this rig.
    """

    def test_each_camera_gets_its_own_measured_default(self):
        self.assertEqual(RR.settings_for("N6", "HD", 85), ("HD", 70))
        self.assertEqual(RR.settings_for("AE3", "HD", 85), ("VGA", 50))

    def test_explicit_override_beats_the_default(self):
        got = RR.settings_for("AE3", "HD", 85,
                              {"AE3": {"framesize": "QVGA", "quality": 50}})
        self.assertEqual(got, ("QVGA", 50))

    def test_partial_override_keeps_the_rest_of_the_default(self):
        self.assertEqual(RR.settings_for("AE3", "HD", 85,
                                         {"AE3": {"quality": 70}}),
                         ("VGA", 70))

    def test_none_values_do_not_clobber(self):
        """A form that sends nothing for a field must not erase the default."""
        self.assertEqual(RR.settings_for("N6", "HD", 85,
                                         {"N6": {"framesize": None,
                                                 "quality": None}}),
                         ("HD", 70))

    def test_the_imx_has_its_own_default_too(self):
        self.assertEqual(RR.settings_for("IMX", "QVGA", 10), ("HD", 70))

    def test_a_genuinely_unknown_camera_falls_back_to_the_global_ask(self):
        self.assertEqual(RR.settings_for("SOMETHING_ELSE", "VGA", 50),
                         ("VGA", 50))

    def test_quality_is_coerced_to_int(self):
        """The form sends strings; a str would break the "%s_q%d" cell key."""
        fs, q = RR.settings_for("N6", "HD", 85, {"N6": {"quality": "90"}})
        self.assertIsInstance(q, int)
        self.assertEqual(q, 90)


class TestSessions(unittest.TestCase):
    def test_broken_manifest_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "rec_bad"))
            with open(os.path.join(d, "rec_bad", "manifest.json"), "w") as f:
                f.write("{not json")
            got = R.load_sessions(d)
            self.assertEqual(len(got), 1)
            self.assertIn("broken", got[0])

    def test_sizes_are_read_from_disk(self):
        with tempfile.TemporaryDirectory() as d:
            sd = os.path.join(d, "rec_x")
            os.makedirs(sd)
            with open(os.path.join(sd, "N6.mjpeg"), "wb") as f:
                f.write(b"x" * 1234)
            with open(os.path.join(sd, "manifest.json"), "w") as f:
                json.dump({"name": "rec_x", "cameras": [
                    {"label": "N6", "mjpeg": "N6.mjpeg", "mp4": "N6.mp4"}]}, f)
            got = R.load_sessions(d)[0]
            self.assertEqual(got["cameras"][0]["mjpeg_bytes"], 1234)
            self.assertEqual(got["cameras"][0]["mp4_bytes"], 0)

    def test_missing_root_is_empty_not_an_error(self):
        self.assertEqual(R.load_sessions("/nonexistent/dir"), [])


class TestBoardScript(unittest.TestCase):
    def test_cfg_is_prepended_and_parses(self):
        src = R.build_board_script({"framesize": "HD", "quality": 85},
                                   board_src="print(_CFG)\n")
        self.assertTrue(src.startswith("_CFG = "))
        ns = {}
        exec(compile(src, "<t>", "exec"), ns)          # noqa: S102 - fixture
        self.assertEqual(ns["_CFG"]["quality"], 85)

    def test_real_board_script_compiles(self):
        """The file we push to the board must at least be valid Python."""
        with open(os.path.join(_HERE, "record_board.py")) as f:
            compile(f.read(), "record_board.py", "exec")


class TestEncoderPick(unittest.TestCase):
    def test_software_fallback_when_no_device(self):
        real = T._v4l2_encoder_present
        T._v4l2_encoder_present = lambda: None
        try:
            name, args = T.pick_encoder()
            self.assertEqual(name, "libx264")
            self.assertIn("libx264", args)
        finally:
            T._v4l2_encoder_present = real

    def test_hardware_used_when_device_exists(self):
        real = T._v4l2_encoder_present
        T._v4l2_encoder_present = lambda: "/dev/video11"
        try:
            self.assertEqual(T.pick_encoder()[0], "h264_v4l2m2m")
        finally:
            T._v4l2_encoder_present = real

    def test_explicit_software_preference_wins(self):
        real = T._v4l2_encoder_present
        T._v4l2_encoder_present = lambda: "/dev/video11"
        try:
            self.assertEqual(T.pick_encoder(prefer="software")[0], "libx264")
        finally:
            T._v4l2_encoder_present = real


class TestNoCameraPath(unittest.TestCase):
    """A missing camera must fail loudly and locally, not hang or crash.

    This is not hypothetical: on 2026-09-08 the AE3 went off the USB bus
    mid-session, and a recording asking for it had to still record the N6 and
    say plainly what was missing.
    """

    def test_no_cameras_found_reports_and_records_nothing(self):
        real = R.find_boards
        R.find_boards = lambda roles=(), **kw: ({}, ["AE3 did not answer"], {})
        try:
            with tempfile.TemporaryDirectory() as d:
                res = RR.run_recording(root=d, cameras=["AE3"], log=lambda m: None)
            self.assertFalse(res["ok"])
            self.assertIn("no cameras found", res["summary"])
            self.assertTrue(any("did not answer" in w for w in res["warnings"]))
        finally:
            R.find_boards = real

    def test_a_wedged_board_is_a_warning_not_an_exception(self):
        """The bounded probe reports; it must never raise into the caller."""
        real = R.find_boards
        R.find_boards = lambda roles=(), **kw: (
            {}, ["/dev/x did not answer within 25 s -- the board may be wedged"],
            {})
        try:
            with tempfile.TemporaryDirectory() as d:
                res = RR.run_recording(root=d, cameras=["N6"], log=lambda m: None)
            self.assertIn("wedged", " ".join(res["warnings"]))
        finally:
            R.find_boards = real


class TestStorageRing(unittest.TestCase):
    """The bounded store. Each test pins one of Nick's stated rules
    (bm_cam_legacy TODO-BM-008), because a ring buffer that deletes the wrong
    thing is worse than a full disk.
    """

    def _store(self, d, names, size=1000):
        for i, name in enumerate(names):
            p = os.path.join(d, name)
            os.makedirs(p)
            with open(os.path.join(p, "N6.mjpeg"), "wb") as f:
                f.write(b"x" * size)
            with open(os.path.join(p, "manifest.json"), "w") as f:
                f.write("{}")
            os.utime(p, (1000 + i, 1000 + i))       # deterministic age order
        return ST.list_sessions(d)

    def test_sessions_come_back_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            ss = self._store(d, ["c", "a", "b"])     # names out of age order
            self.assertEqual([s["name"] for s in ss], ["c", "a", "b"])

    def test_evicts_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            ss = self._store(d, ["r1", "r2", "r3", "r4"])
            v, rep = ST.plan_eviction(ss, ring_bytes=2500, free_bytes=10 ** 12,
                                      keep_latest=0)
            self.assertEqual(rep["victims"][0], "r1")

    def test_newest_n_are_never_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            ss = self._store(d, ["r1", "r2", "r3", "r4"])
            _, rep = ST.plan_eviction(ss, ring_bytes=1, free_bytes=10 ** 12,
                                      keep_latest=2)
            self.assertNotIn("r3", rep["victims"])
            self.assertNotIn("r4", rep["victims"])
            self.assertGreater(rep["shortfall_bytes"], 0)   # reported, not hidden

    def test_the_active_recording_is_never_deleted(self):
        """It must not evict the clip it is in the middle of writing."""
        with tempfile.TemporaryDirectory() as d:
            ss = self._store(d, ["r1", "r2", "r3"])
            _, rep = ST.plan_eviction(ss, ring_bytes=1, free_bytes=10 ** 12,
                                      keep_latest=0, active="r1")
            self.assertNotIn("r1", rep["victims"])

    def test_low_free_space_triggers_eviction_even_inside_budget(self):
        """The budget alone does not protect the OS -- something else can fill
        the card while the ring sits politely inside its quota."""
        with tempfile.TemporaryDirectory() as d:
            ss = self._store(d, ["r1", "r2", "r3"])
            _, rep = ST.plan_eviction(ss, ring_bytes=10 ** 12,      # miles of budget
                                      free_bytes=1000,              # but no disk
                                      min_free_bytes=5000, keep_latest=0)
            self.assertGreater(rep["short_on_free_bytes"], 0)
            self.assertTrue(rep["victims"])

    def test_headroom_for_the_next_clip_is_reserved(self):
        with tempfile.TemporaryDirectory() as d:
            ss = self._store(d, ["r1", "r2"])
            _, a = ST.plan_eviction(ss, ring_bytes=3000, free_bytes=10 ** 12,
                                    keep_latest=0, need_bytes=0)
            _, b = ST.plan_eviction(ss, ring_bytes=3000, free_bytes=10 ** 12,
                                    keep_latest=0, need_bytes=2000)
            self.assertEqual(a["victims"], [])
            self.assertTrue(b["victims"])

    def test_dry_run_deletes_nothing(self):
        """Nick's spec: dry-run mode first."""
        with tempfile.TemporaryDirectory() as d:
            self._store(d, ["r1", "r2", "r3"])
            rep = ST.enforce(d, ring_bytes=1500, min_free_bytes=0, keep_latest=0,
                             dry_run=True)
            self.assertTrue(rep["deleted"])
            self.assertEqual(sorted(os.listdir(d)), ["r1", "r2", "r3"])

    def test_enforce_actually_frees_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            self._store(d, ["r1", "r2", "r3"])
            rep = ST.enforce(d, ring_bytes=1500, min_free_bytes=0, keep_latest=0)
            self.assertIn("r1", rep["deleted"])
            self.assertNotIn("r1", os.listdir(d))
            self.assertIn("r3", os.listdir(d))       # newest survives

    def test_reported_usage_matches_disk_after_eviction(self):
        """The summary must describe the store AFTER the action, not before.

        It did not: the plan's used_bytes is the pre-eviction total, so the
        live test printed "ring 2.83 / 2.50 GB used" in the same breath as
        having just freed 0.95 GB.
        """
        with tempfile.TemporaryDirectory() as d:
            self._store(d, ["r1", "r2", "r3"])
            rep = ST.enforce(d, ring_bytes=1500, min_free_bytes=0, keep_latest=0)
            on_disk = sum(ST.dir_size_bytes(os.path.join(d, n))
                          for n in os.listdir(d))
            self.assertEqual(rep["used_bytes"], on_disk)
            self.assertGreater(rep["freed_bytes"], 0)

    def test_dry_run_reports_no_freed_bytes(self):
        """A dry run frees nothing, so it must not claim to have."""
        with tempfile.TemporaryDirectory() as d:
            before = self._store(d, ["r1", "r2", "r3"])
            rep = ST.enforce(d, ring_bytes=1500, min_free_bytes=0, keep_latest=0,
                             dry_run=True)
            self.assertEqual(rep["freed_bytes"], 0)
            self.assertEqual(rep["used_bytes"], sum(s["bytes"] for s in before))

    def test_a_full_filesystem_evicts_even_when_the_budget_is_fine(self):
        """Found by running the suite on nereus002, whose /tmp is a small
        tmpfs: the 2 GB free-space floor dominated and evicted everything.
        That is CORRECT -- the floor exists to protect the OS -- so pin it."""
        with tempfile.TemporaryDirectory() as d:
            ss = self._store(d, ["r1", "r2", "r3"])
            _, rep = ST.plan_eviction(ss, ring_bytes=10 ** 12, free_bytes=1000,
                                      min_free_bytes=2 * 10 ** 9, keep_latest=0)
            self.assertEqual(len(rep["victims"]), 3)
            self.assertEqual(rep["over_budget_bytes"], 0)
            self.assertGreater(rep["short_on_free_bytes"], 0)

    def test_nothing_outside_the_root_is_ever_deletable(self):
        with tempfile.TemporaryDirectory() as outer:
            root = os.path.join(outer, "recordings")
            os.makedirs(root)
            self.assertFalse(ST._safe_under(root, outer))
            self.assertFalse(ST._safe_under(root, "/etc"))
            self.assertFalse(ST._safe_under(root, root))
            self.assertTrue(ST._safe_under(root, os.path.join(root, "rec_x")))

    def test_a_symlink_is_counted_as_a_link_not_its_target(self):
        """A link into the OS must not inflate the ring's apparent usage."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "rec_1")
            os.makedirs(p)
            with open(os.path.join(p, "real.bin"), "wb") as f:
                f.write(b"x" * 100)
            try:
                os.symlink("/etc/services", os.path.join(p, "link"))
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            self.assertEqual(ST.dir_size_bytes(p), 100)

    def test_status_has_what_the_dashboard_draws(self):
        with tempfile.TemporaryDirectory() as d:
            self._store(d, ["r1"])
            st = ST.status(d, ring_bytes=10000)
            for key in ("ring_bytes", "ring_used_bytes", "ring_used_pct",
                        "sd_total_bytes", "sd_used_pct", "sessions"):
                self.assertIn(key, st)
            self.assertEqual(st["sessions"], 1)

    def test_missing_root_is_not_a_crash(self):
        self.assertEqual(ST.list_sessions("/nonexistent/x"), [])


class TestPagesRender(unittest.TestCase):
    """The pages must actually build.

    They did not, once: a bare "%" inside the page's JavaScript ("% margin")
    was consumed by Python's %-formatting and the index page raised
    "not enough arguments for format string" -- while /healthz and the JSON API
    kept answering 200, so nothing looked wrong until a browser asked for the
    page. Rendering both pages in the suite is the cheap guard.
    """

    CEIL = {"cameras": {"N6": {"cells": {"HD_q85": 34.9},
                               "delivered": {"HD_q85": 23.81},
                               "note": "n"}}}
    SESSION = {
        "name": "rec_20260908T073433", "created_iso": "2026-09-08T07:34:33",
        "host": "nereus000",
        "settings": {"framesize": "HD", "quality": 85, "fps_requested": 30,
                     "duration_s": 5, "cameras": ["N6", "AE3"]},
        "cameras": [
            {"label": "N6", "written_frames": 120, "delivered_fps": 20.05,
             "capture_fps": 23.81, "mb_per_s": 3.94, "seq_gaps": 0,
             "ring_dropped_frames": 0, "mjpeg": "N6.mjpeg", "mp4": "N6.mp4",
             "mjpeg_bytes": 23402568, "mp4_bytes": 6857740,
             "start_offset_s": 0.0, "banner": {"w": 1280, "h": 800}},
            {"label": "AE3", "written_frames": 11, "delivered_fps": 2.1,
             "capture_fps": 2.1, "mb_per_s": 0.4, "seq_gaps": 3,
             "ring_dropped_frames": 0, "mjpeg": "AE3.mjpeg",
             "mjpeg_bytes": 900000, "mp4_bytes": 0,
             "start_offset_s": 0.42, "banner": {"w": 1280, "h": 800}},
        ],
    }

    def test_index_renders(self):
        import recorder_web as W
        with tempfile.TemporaryDirectory() as d:
            page = W.index_page(W.RecorderState(d), [self.SESSION], self.CEIL)
        self.assertIn("Video recorder", page)
        self.assertIn("rec_20260908T073433", page)
        self.assertNotIn("@@", page)          # every token substituted

    def test_index_renders_with_no_recordings_and_no_ceilings(self):
        import recorder_web as W
        with tempfile.TemporaryDirectory() as d:
            page = W.index_page(W.RecorderState(d), [], {})
        self.assertIn("No recordings yet", page)
        self.assertNotIn("@@", page)

    def test_viewer_renders_a_player_per_converted_camera(self):
        import recorder_web as W
        page = W.viewer_page(self.SESSION)
        self.assertNotIn("@@", page)
        # Only the converted camera gets a <video>; the other gets an offer.
        self.assertEqual(page.count("<video"), 1)
        self.assertEqual(page.count('id=scrub'), 1)        # ONE scrubber
        self.assertIn("22.3 MB", page)                     # file size shown
        self.assertIn("HD", page)                          # settings shown
        self.assertIn("data-offset='0.0'", page)

    def test_viewer_renders_two_players_when_both_are_converted(self):
        import recorder_web as W
        import copy
        sess = copy.deepcopy(self.SESSION)
        sess["cameras"][1]["mp4"] = "AE3.mp4"
        sess["cameras"][1]["mp4_bytes"] = 900000
        page = W.viewer_page(sess)
        self.assertEqual(page.count("<video"), 2)
        self.assertEqual(page.count('id=scrub'), 1)
        self.assertIn("data-offset='0.42'", page)

    def test_viewer_offers_conversion_for_an_unconverted_clip(self):
        """Not an error state: clips are kept as MJPEG until asked for.

        The standing "Not converted yet ..." paragraph was dropped on
        2026-09-09 (Nick): it repeated under every unconverted clip, three
        times a page, and the cost it explained is now stated once in the
        confirm dialog that names the actual minutes. The BUTTON is the part
        that must not disappear, so that is what this asserts.
        """
        import recorder_web as W
        page = W.viewer_page(self.SESSION)
        self.assertIn("Make playable", page)
        self.assertNotIn("Not converted yet", page)


class TestImxIsOneCamera(unittest.TestCase):
    """The IMX is one camera on the page, and its science file is download-only.

    Nick, 2026-09-10: the viewer drew "IMX" (an empty tile with a Make
    playable button) beside "IMX_proxy" (the tile that actually played), and
    the index offered to convert the 1.5 GB science JPEG. One tile, playing
    the proxy; the science file stays a download; nothing on either page or
    in the server converts it.
    """

    DIVE = {
        "name": "dive_20260910T190137Z_s0000",
        "created_iso": "2026-09-10T12:01:45", "host": "nereus002",
        "settings": {"framesize": "HD", "quality": 85, "fps_requested": 30,
                     "duration_s": 178, "cameras": ["N6", "AE3"],
                     "per_camera": {"N6": {"framesize": "HD", "quality": 70}}},
        "cameras": [
            {"label": "N6", "written_frames": 5387, "delivered_fps": 30.26,
             "mb_per_s": 2.86, "seq_gaps": 0, "ring_dropped_frames": 0,
             "mjpeg": "N6.mjpeg", "thumb": "N6_thumb.jpg",
             "mjpeg_bytes": 511000000, "mp4_bytes": 0,
             "start_offset_s": 0.0, "banner": {"w": 1280, "h": 800}},
            {"label": "IMX", "mjpeg": "IMX.mjpeg", "thumb": "IMX_thumb.jpg",
             "capture_fps": 29.94, "delivered_fps": 29.94, "frames": 5407,
             "bytes": 890000000, "mb_per_s": 4.94, "w": 1280, "h": 800,
             "kind": "science (JPEG q90)"},
            {"label": "IMX_proxy", "mp4": "IMX_proxy.mp4", "capture_fps": 29.94,
             "bytes": 63000000, "mb_per_s": 0.35, "w": 640, "h": 400,
             "kind": "iPad proxy (H.264, plays as-is)"},
        ],
    }

    def test_fold_gives_one_imx_that_plays_the_proxy(self):
        import recorder_web as W
        cams = W.fold_imx(self.DIVE["cameras"])
        labels = [c["label"] for c in cams]
        self.assertEqual(labels.count("IMX"), 1)
        self.assertNotIn("IMX_proxy", labels)
        imx = next(c for c in cams if c["label"] == "IMX")
        self.assertEqual(imx["mp4"], "IMX_proxy.mp4")
        self.assertEqual(imx["mjpeg"], "IMX.mjpeg")
        self.assertEqual(imx["thumb"], "IMX_thumb.jpg")
        self.assertTrue(imx["download_only"])
        self.assertEqual(W._frames(imx), 5407)

    def test_fold_leaves_a_board_only_session_alone(self):
        import recorder_web as W
        cams = [c for c in self.DIVE["cameras"] if c["label"] == "N6"]
        self.assertEqual(W.fold_imx(cams), cams)

    def test_viewer_has_one_imx_tile_and_it_is_a_player(self):
        import recorder_web as W
        page = W.viewer_page(self.DIVE)
        self.assertNotIn("@@", page)
        self.assertEqual(page.count("<video"), 1)             # the proxy
        self.assertIn("IMX_proxy.mp4", page)
        self.assertEqual(page.count("<b>IMX</b>"), 1)          # ONE tile
        self.assertNotIn("<b>IMX_proxy</b>", page)
        # The N6 is unconverted and keeps its button; the IMX never gets one.
        self.assertEqual(page.count("Make playable"), 1)
        self.assertIn("mkv(event,'dive_20260910T190137Z_s0000','N6')", page)
        self.assertNotIn("mkv(event,'dive_20260910T190137Z_s0000','IMX')", page)

    def test_viewer_offers_the_science_file_as_a_download(self):
        import recorder_web as W
        page = W.viewer_page(self.DIVE)
        self.assertIn("/download/dive_20260910T190137Z_s0000/IMX.mjpeg", page)
        self.assertIn("science mjpeg", page)
        self.assertIn("/download/dive_20260910T190137Z_s0000/IMX_proxy.mp4", page)
        self.assertIn("proxy mp4", page)
        self.assertIn("1280x800 science", page)

    def test_viewer_without_a_proxy_still_never_offers_conversion(self):
        import recorder_web as W
        import copy
        sess = copy.deepcopy(self.DIVE)
        sess["cameras"] = [c for c in sess["cameras"] if c["label"] != "IMX_proxy"]
        page = W.viewer_page(sess)
        self.assertEqual(page.count("<video"), 0)
        self.assertIn("no playable proxy", page)
        self.assertIn("IMX_thumb.jpg", page)
        self.assertNotIn("mkv(event,'dive_20260910T190137Z_s0000','IMX')", page)

    def test_index_never_offers_to_convert_the_imx(self):
        import recorder_web as W
        with tempfile.TemporaryDirectory() as d:
            page = W.index_page(W.RecorderState(d), [self.DIVE], {})
        self.assertNotIn("Make IMX playable", page)
        self.assertNotIn("IMX_proxy", page.split("<h2>Recordings</h2>")[1]
                         .split("<script>")[0])
        self.assertIn("IMX mp4", page)                         # plays as-is
        self.assertIn("IMX 5407 fr @ 29.9 fps", page)          # not "0 fr"
        self.assertIn("Make N6 playable", page)
        self.assertNotIn("Make all", page)     # only one convertible camera
        self.assertIn("IMX_thumb.jpg", page)

    def test_viewer_offers_a_toggle_per_camera_all_on_by_default(self):
        """Nick, 2026-09-10: choose which streams are being compared; default
        all three; switching the AE3 off lets the N6 and IMX grow."""
        import recorder_web as W
        page = W.viewer_page(self.DIVE)
        for cam in ("IMX", "N6"):
            self.assertIn("data-cam='%s'" % cam, page)
            self.assertIn("togCam('%s')" % cam, page)
        self.assertNotIn("togCam('IMX_proxy')", page)
        self.assertEqual(page.count("class='sec camtog'"), 2)
        self.assertNotIn("class=vid data-cam='IMX' hidden", page)  # default on
        self.assertIn("never hide the last one", page)

    def test_server_refuses_to_convert_the_imx(self):
        """A stale page or a hand-typed request must not start the transcode."""
        import recorder_web as W
        import http.client
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "dive_a"))
            for n in ("IMX.mjpeg", "N6.mjpeg"):
                with open(os.path.join(d, "dive_a", n), "wb") as f:
                    f.write(b"\xff\xd8x\xff\xd9")
            state = W.RecorderState(d)
            tq = W.TranscodeQueue(d)
            httpd = W.ThreadingHTTPServer(
                ("127.0.0.1", 0), W.make_handler(state, d, tq, review_only=True))
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                def post(cam):
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    c.request("POST", "/api/transcode",
                              body=json.dumps({"session": "dive_a", "camera": cam}))
                    r = c.getresponse()
                    return r.status, json.loads(r.read())
                code, body = post("IMX")
                self.assertEqual(code, 409)
                self.assertFalse(body["ok"])
                self.assertIn("download-only", body["err"])
                code, body = post("IMX_proxy")
                self.assertEqual(code, 409)
                self.assertEqual(tq.snapshot().get("queued"), [])
            finally:
                httpd.shutdown()
                httpd.server_close()


class TestMediaPathSafety(unittest.TestCase):
    """The media route takes user-supplied names; traversal must be refused."""

    def test_safe_regex_rejects_traversal(self):
        import recorder_web as W
        for bad in ("..", ".", "../etc", "a/b", "", "x\x00", "/etc/passwd",
                    ".hidden", "..\\win"):
            self.assertIsNone(W.SAFE.match(bad), "accepted %r" % bad)

    def test_safe_path_serves_real_files_and_refuses_escapes(self):
        """Containment is the gate that must not be wrong, so exercise it."""
        import recorder_web as W
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "rec_a"))
            with open(os.path.join(d, "rec_a", "N6.mp4"), "wb") as f:
                f.write(b"x")
            with open(os.path.join(d, "secret.txt"), "w") as f:
                f.write("no")
            H = W.make_handler(W.RecorderState(d), d, None)
            # _safe_path never touches `self`, so an unbound call is a fair test
            # and avoids standing up a socket server to check a path rule.
            sp = H._safe_path
            self.assertIsNotNone(sp(None, "rec_a", "N6.mp4"))
            self.assertIsNone(sp(None, "..", "secret.txt"))
            self.assertIsNone(sp(None, "rec_a", "missing.mp4"))
            self.assertIsNone(sp(None, "rec_a", "../secret.txt"))

    def test_safe_regex_accepts_normal_names(self):
        import recorder_web as W
        for good in ("rec_20260908T014500", "N6.mp4", "AE3.mjpeg"):
            self.assertIsNotNone(W.SAFE.match(good))



class TestImxStallGuard(unittest.TestCase):
    """2026-09-10: 'Camera frontend has timed out!' 55 s after boot; the
    sensor stopped and the recorder hung in capture_metadata() forever with
    problems=[] while the boards recorded on. Every camera wait is bounded,
    a stall closes the segment with a PROBLEM and exits 3, and the launcher
    relaunches at the next segment."""

    def setUp(self):
        import importlib
        self.M = importlib.import_module("imx_dive_recorder")

    def test_call_with_timeout_gives_up_on_a_hang(self):
        import time as _t
        t0 = _t.time()
        got = self.M.call_with_timeout(lambda: _t.sleep(5), 0.2, "gave up")
        self.assertEqual(got, "gave up")
        self.assertLess(_t.time() - t0, 2.0)
        self.assertEqual(self.M.call_with_timeout(lambda: 7, 1.0), 7)
        self.assertIsNone(self.M.call_with_timeout(lambda: 1 / 0, 1.0))

    def test_stall_watch_fires_only_after_frames_stop(self):
        w = self.M.StallWatch(stall_s=20)
        self.assertIsNone(w.update(0, 0))         # starting up
        self.assertIsNone(w.update(0, 10))        # still inside the grace
        self.assertIsNone(w.update(30, 21))       # frames arriving
        self.assertIsNone(w.update(60, 30))
        self.assertIsNone(w.update(60, 45))       # 15 s quiet: not yet
        why = w.update(60, 51)                    # 21 s quiet: stalled
        self.assertIn("no IMX frame for 21 s after 60 frames", why)

    def test_stall_watch_reports_a_camera_that_never_started(self):
        w = self.M.StallWatch(stall_s=20)
        w.update(0, 0)
        self.assertIsNotNone(w.update(0, 25))

    def test_parser_takes_first_segment_and_stall_window(self):
        a = self.M.build_parser().parse_args(["--first-segment", "7", "--stall-s", "12"])
        self.assertEqual(a.first_segment, 7)
        self.assertEqual(a.stall_s, 12.0)
        self.assertEqual(self.M.build_parser().parse_args([]).first_segment, 0)
        self.assertEqual(self.M.EXIT_STALLED, 3)

    def test_launcher_relaunches_on_the_stall_exit_code(self):
        src = open(os.path.join(_HERE, "run_channel_islands.sh")).read()
        self.assertIn('--first-segment "$next"', src)
        self.assertIn('[ "$rc" -eq 3 ]', src)
        self.assertIn("relaunching at segment", src)
        self.assertIn('kill -INT "$pid"', src)      # Stop still reaches the recorder


class TestDurableJson(unittest.TestCase):
    """A manifest is either the old one or the new one, never empty.

    2026-09-10: a hard reset seconds after a segment closed left
    manifest.json at zero bytes. The writer now fsyncs before the rename.
    """

    def test_writes_the_file_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "manifest.json")
            R.write_json_durable(p, {"a": 1, "cameras": []}, indent=1)
            with open(p) as f:
                self.assertEqual(json.load(f), {"a": 1, "cameras": []})
            self.assertEqual(sorted(os.listdir(d)), ["manifest.json"])

    def test_replaces_an_existing_file_whole(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "manifest.json")
            R.write_json_durable(p, {"v": 1})
            R.write_json_durable(p, {"v": 2})
            with open(p) as f:
                self.assertEqual(json.load(f), {"v": 2})

    def test_session_save_uses_it(self):
        src = open(os.path.join(_HERE, "recorder.py")).read()
        save = src[src.index("    def save(self):"):]
        self.assertIn("write_json_durable(self.path(\"manifest.json\")", save)
        self.assertNotIn("os.replace(tmp, self.path(\"manifest.json\"))", save)


class TestThumbnails(unittest.TestCase):
    """A thumbnail must cost a read and a write, never a transcode.

    Nick's rule for this rig is that energy is spent on conversion only when he
    asks for it, so the thumbnail is one frame copied verbatim out of the
    .mjpeg -- no decode, no re-encode.
    """

    def _mjpeg(self, path, n):
        with open(path, "wb") as f:
            for i in range(n):
                f.write(b"\xff\xd8" + bytes([i]) * 40 + b"\xff\xd9")

    def test_reads_the_nth_frame_byte_exact(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "N6.mjpeg")
            self._mjpeg(p, 30)
            got = R.read_frame(p, 7)
            self.assertEqual(got, b"\xff\xd8" + bytes([7]) * 40 + b"\xff\xd9")

    def test_past_the_end_is_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "N6.mjpeg")
            self._mjpeg(p, 3)
            self.assertIsNone(R.read_frame(p, 99))

    def test_missing_file_is_none(self):
        self.assertIsNone(R.read_frame("/nonexistent/x.mjpeg", 0))

    def test_thumbnail_is_a_verbatim_copy(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "N6.mjpeg")
            self._mjpeg(p, 40)
            out = os.path.join(d, "N6_thumb.jpg")
            n = R.write_thumbnail(p, out)
            self.assertGreater(n, 0)
            with open(out, "rb") as f:
                data = f.read()
            self.assertEqual(data, R.read_frame(p, R.THUMB_FRAME))
            self.assertTrue(data.startswith(b"\xff\xd8"))

    def test_short_clip_falls_back_to_an_earlier_frame(self):
        """A 3 frame clip has no frame 20; it must still get a thumbnail."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "N6.mjpeg")
            self._mjpeg(p, 3)
            out = os.path.join(d, "t.jpg")
            self.assertGreater(R.write_thumbnail(p, out), 0)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), R.read_frame(p, 0))

    def test_no_frames_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "empty.mjpeg")
            open(p, "wb").close()
            out = os.path.join(d, "t.jpg")
            self.assertEqual(R.write_thumbnail(p, out), 0)
            self.assertFalse(os.path.exists(out))


class TestOnDemandTranscode(unittest.TestCase):
    def test_recording_does_not_transcode_by_default(self):
        """Nick's rule: do not spend energy on a clip the ring may delete."""
        import inspect
        sig = inspect.signature(RR.run_recording)
        self.assertIs(sig.parameters["transcode"].default, False)

    def test_default_duration_is_three_minutes(self):
        """Nick's call after measuring the transfer wall: shorter segments mean
        less to lose if the rig is killed, and a smaller unit to move."""
        import inspect
        sig = inspect.signature(RR.run_recording)
        self.assertEqual(sig.parameters["duration_s"].default, 180.0)

    def test_library_trusts_the_filesystem_for_playability(self):
        """A manifest may name an mp4 that was never made, or predate one."""
        with tempfile.TemporaryDirectory() as d:
            sd = os.path.join(d, "rec_x")
            os.makedirs(sd)
            for n in ("N6.mjpeg", "N6_thumb.jpg"):
                with open(os.path.join(sd, n), "wb") as f:
                    f.write(b"x" * 10)
            with open(os.path.join(sd, "manifest.json"), "w") as f:
                json.dump({"name": "rec_x", "cameras": [
                    {"label": "N6", "mjpeg": "N6.mjpeg", "mp4": "N6.mp4"}]}, f)
            cam = R.load_sessions(d)[0]["cameras"][0]
            self.assertNotIn("mp4", cam)           # claimed but absent
            self.assertEqual(cam["thumb"], "N6_thumb.jpg")
            with open(os.path.join(sd, "N6.mp4"), "wb") as f:
                f.write(b"y" * 20)
            cam = R.load_sessions(d)[0]["cameras"][0]
            self.assertEqual(cam["mp4"], "N6.mp4")  # now really there
            self.assertEqual(cam["mp4_bytes"], 20)



class TestCsiRecorder(unittest.TestCase):
    """The IMX708 path. It shares the ring and the writer with the boards, but
    it differs in one way that must be reported rather than hidden: rpicam-vid
    gives no per-frame sequence numbers, so lost frames are UNDETECTABLE there.
    """

    def test_splitter_finds_concatenated_frames(self):
        sp = R.JpegSplitter()
        blob = b"".join(b"\xff\xd8" + bytes([i]) * 20 + b"\xff\xd9"
                        for i in range(4))
        got = sp.feed(blob)
        self.assertEqual([n for n, _ in got], [0, 1, 2, 3])
        self.assertTrue(all(f.startswith(b"\xff\xd8") and f.endswith(b"\xff\xd9")
                            for _, f in got))

    def test_splitter_handles_a_frame_split_across_reads(self):
        sp = R.JpegSplitter()
        f = b"\xff\xd8" + b"z" * 30 + b"\xff\xd9"
        self.assertEqual(sp.feed(f[:10]), [])
        self.assertEqual(sp.feed(f[10:20]), [])
        self.assertEqual(sp.feed(f[20:]), [(0, f)])

    def test_splitter_skips_leading_junk(self):
        sp = R.JpegSplitter()
        f = b"\xff\xd8" + b"a" * 8 + b"\xff\xd9"
        self.assertEqual(sp.feed(b"noise-before-any-frame" + f), [(0, f)])

    def test_argv_is_bounded_and_writes_to_stdout(self):
        argv = R.rpicam_record_argv(1280, 720, 30, 70, 5.0, camera=0)
        self.assertIn("rpicam-vid", argv[0])
        self.assertIn("--codec", argv)
        self.assertEqual(argv[argv.index("--codec") + 1], "mjpeg")
        # bounded in TIME, so a wedged host cannot record forever
        self.assertEqual(argv[argv.index("-t") + 1], "5000")
        self.assertEqual(argv[argv.index("-o") + 1], "-")
        self.assertEqual(argv[argv.index("--width") + 1], "1280")

    def test_csi_sizes_are_not_the_boards_rectangle(self):
        """The IMX is free to pick any size; the boards letterbox 16:10."""
        self.assertEqual(R.CSI_SIZES["HD"], (1280, 720))
        self.assertEqual(R.CSI_SIZES["VGA"], (640, 480))

    def test_seq_gaps_is_unknown_not_zero(self):
        """rpicam gives no sequence numbers, so 0 would claim a check that was
        never performed. None means unknown."""
        rec = R.CsiRecorder("IMX", {"framesize": "VGA", "duration_s": 1}, "/tmp/x")
        rec.started_at = rec.finished_at = 1.0
        self.assertIsNone(rec.stats()["seq_gaps"])

    def test_no_frames_reports_an_error_rather_than_a_clean_zero(self):
        rec = R.CsiRecorder("IMX", {"framesize": "VGA", "duration_s": 1}, "/tmp/x")
        rec.started_at = rec.finished_at = 1.0
        self.assertIn("no frames", rec.stats()["error"])

    def test_csi_roles_are_never_probed_as_serial_boards(self):
        self.assertIn("IMX", RR.CSI_ROLES)
        self.assertNotIn("N6", RR.CSI_ROLES)
        self.assertNotIn("AE3", RR.CSI_ROLES)



class TestPerCombinationCeilings(unittest.TestCase):
    """Cameras contend, so a ceiling measured solo overstates a group session.

    Measured on nereus002: the N6 delivered 28.75 fps alone, 26.8 beside the
    IMX708 and 25.12 with both others. Guarding a three-camera request against
    28.75 promises 3.6 fps that will not arrive.
    """

    CEIL = {"cameras": {"N6": {
        "cells": {"HD_q70": 37.2},
        "delivered": {"HD_q70": 28.75},
        "delivered_by_combo": {"AE3+IMX+N6": {"HD_q70": 25.12},
                               "IMX+N6": {"HD_q70": 26.8}},
    }}}

    def test_combo_key_is_order_independent(self):
        self.assertEqual(RR.combo_key(["N6", "IMX", "AE3"]),
                         RR.combo_key(["AE3", "N6", "IMX"]))
        self.assertEqual(RR.combo_key(["N6", "AE3"]), "AE3+N6")

    def test_exact_combination_wins(self):
        val, kind = RR.ceiling_for(self.CEIL, "N6", "HD", 70, "AE3+IMX+N6")
        self.assertAlmostEqual(val, 25.12)
        self.assertEqual(kind, "delivered together")

    def test_a_different_combination_is_used_when_measured(self):
        val, _ = RR.ceiling_for(self.CEIL, "N6", "HD", 70, "IMX+N6")
        self.assertAlmostEqual(val, 26.8)

    def test_unmeasured_combination_falls_back_to_solo_and_says_so(self):
        val, kind = RR.ceiling_for(self.CEIL, "N6", "HD", 70, "N6+SOMETHING")
        self.assertAlmostEqual(val, 28.75)
        self.assertEqual(kind, "delivered")
        _, msg = RR.check_request(self.CEIL, "N6", "HD", 70, 20, "N6+SOMETHING")
        self.assertIn("alone", msg)

    def test_the_regression_this_exists_for(self):
        """26 fps passes against the solo 28.75 but is NOT achievable with all
        three running. The solo verdict lets it through; the combination one
        refuses it and names the real number."""
        solo = RR.check_request(self.CEIL, "N6", "HD", 70, 26, None)
        self.assertIn(solo[0], ("ok", "tight"))       # not refused
        combo = RR.check_request(self.CEIL, "N6", "HD", 70, 26, "AE3+IMX+N6")
        self.assertEqual(combo[0], "impossible")
        self.assertIn("25.1", combo[1])

    def test_no_combo_given_behaves_as_before(self):
        val, kind = RR.ceiling_for(self.CEIL, "N6", "HD", 70)
        self.assertAlmostEqual(val, 28.75)
        self.assertEqual(kind, "delivered")


class PerHostCeilings(unittest.TestCase):
    """S33 bite 1: ceilings are a per-RIG measurement, not shared source.

    The bug this pins actually happened: nereus002 held the only copy of its
    per-combination delivered rates (and the only IMX708 numbers anywhere) in
    an untracked camera_ceilings.json, and the repo's tracked file held
    nereus000's. A checkout would have replaced one rig's measurements with
    another's, and the recorder would have gone on guarding confidently
    against numbers from a board it does not have.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _write(self, name, doc):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            json.dump(doc, f)
        return path

    def test_prefers_this_hosts_file(self):
        self._write("camera_ceilings.rigA.json", {"host": "rigA", "cameras": {"N6": {}}})
        self._write("camera_ceilings.rigB.json", {"host": "rigB", "cameras": {"AE3": {}}})
        doc = RR.load_ceilings(host="rigB", here=self.dir)
        self.assertEqual(doc["host"], "rigB")
        self.assertEqual(sorted(doc["cameras"]), ["AE3"])

    def test_another_rigs_file_is_refused_not_used(self):
        """The whole point. A legacy shared file measured elsewhere must NOT
        be adopted just because it is the only one present."""
        self._write("camera_ceilings.json", {"host": "someone_else",
                                             "cameras": {"N6": {"delivered": {"HD_q70": 99.0}}}})
        doc = RR.load_ceilings(host="rigB", here=self.dir)
        self.assertEqual(doc["cameras"], {})
        self.assertIn("no measured ceilings", doc["note"])

    def test_legacy_file_without_a_host_field_is_still_accepted(self):
        """An old artifact that never recorded where it came from is trusted,
        because refusing it would silently disarm a guard that used to work."""
        self._write("camera_ceilings.json", {"cameras": {"N6": {}}})
        doc = RR.load_ceilings(host="rigB", here=self.dir)
        self.assertEqual(sorted(doc["cameras"]), ["N6"])

    def test_missing_everything_makes_no_claims(self):
        doc = RR.load_ceilings(host="rigB", here=self.dir)
        self.assertEqual(doc["cameras"], {})

    def test_explicit_path_still_wins(self):
        p = self._write("odd_name.json", {"host": "rigB", "cameras": {"IMX": {}}})
        doc = RR.load_ceilings(path=p, host="rigB", here=self.dir)
        self.assertEqual(sorted(doc["cameras"]), ["IMX"])

    def test_the_shipped_files_match_their_own_filenames(self):
        """A per-host file whose `host` field disagrees with its NAME would be
        refused on the very rig it was measured on -- silently, and the page
        would just stop making claims. Cheap to pin, expensive to discover."""
        import glob
        found = glob.glob(os.path.join(_HERE, "camera_ceilings.*.json"))
        self.assertTrue(found, "no per-host ceilings shipped")
        for path in found:
            host = os.path.basename(path).split(".")[1]
            with open(path) as f:
                doc = json.load(f)
            self.assertEqual(doc.get("host"), host, path)
            self.assertEqual(RR.load_ceilings(host=host, here=_HERE).get("host"), host)



class TestWipeAll(unittest.TestCase):
    """Nick's clean-slate button. Destructive, so its refusals are tested."""

    def _rig(self):
        root = tempfile.mkdtemp()
        for name in ("rec_a", "rec_b", "dive_c"):
            d = os.path.join(root, name)
            os.makedirs(d)
            with open(os.path.join(d, "N6.mjpeg"), "wb") as f:
                f.write(b"x" * 1024)
        return root

    def test_wipes_every_session(self):
        root = self._rig()
        try:
            rep = ST.wipe_all(root)
            self.assertEqual(3, len(rep["deleted"]))
            self.assertEqual([], rep["failed"])
            self.assertEqual([], os.listdir(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_refuses_the_session_being_recorded(self):
        """Deleting the open session would leave a half-written clip that
        still looks like a recording."""
        root = self._rig()
        try:
            rep = ST.wipe_all(root, active="rec_b")
            self.assertNotIn("rec_b", rep["deleted"])
            self.assertEqual(["rec_b"], [s["name"] for s in rep["skipped"]])
            self.assertTrue(os.path.isdir(os.path.join(root, "rec_b")))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_dry_run_deletes_nothing(self):
        root = self._rig()
        try:
            rep = ST.wipe_all(root, dry_run=True)
            self.assertEqual(3, len(rep["deleted"]))
            self.assertEqual(3, len(os.listdir(root)))
            self.assertGreater(rep["freed_bytes"], 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_never_follows_a_symlink_out_of_the_root(self):
        """A ring that can delete outside its root is worse than a full disk."""
        root = self._rig()
        outside = tempfile.mkdtemp()
        keep = os.path.join(outside, "precious")
        os.makedirs(keep)
        with open(os.path.join(keep, "data.bin"), "wb") as f:
            f.write(b"keep me")
        try:
            os.symlink(outside, os.path.join(root, "escape"))
            ST.wipe_all(root)
            self.assertTrue(os.path.isfile(os.path.join(keep, "data.bin")),
                            "wipe followed a symlink out of the root")
        finally:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)


class TestRenderedPageScripts(unittest.TestCase):
    """The page's JavaScript must actually PARSE.

    Twice now a Python-side change has emitted broken JS and every Python test
    still passed: once literal % characters in a %-formatted template, and
    once a "\\n" that Python decoded into a REAL newline, splitting a JS
    string literal across two lines. The second one took the whole <script>
    block down, so "Make playable", the player controls and full-screen were
    all silently dead while the page looked perfectly normal -- exactly the
    plausible-but-wrong artifact this repo keeps paying for.

    A quote that opens and never closes on a line is the signature of both.
    """

    def _scripts(self, html_text):
        return re.findall(r"<script>(.*?)</script>", html_text, re.S)

    def _assert_parses(self, html_text, where):
        scripts = self._scripts(html_text)
        self.assertTrue(scripts, "%s has no script block" % where)
        for js in scripts:
            for n, line in enumerate(js.splitlines(), 1):
                if line.strip().startswith("//"):
                    continue
                for q in ("'", '"'):
                    self.assertEqual(
                        0, line.count(q) % 2,
                        "%s line %d has an unterminated %s string -- the whole "
                        "script will fail to parse: %r" % (where, n, q, line[:120]))

    def test_viewer_script_parses(self):
        man = {"name": "t", "created_iso": "x", "settings": {},
               "cameras": [
                   {"label": "IMX", "mjpeg": "IMX.mjpeg",
                    "written_frames": 10, "delivered_fps": 30,
                    "capture_fps": 30},
                   {"label": "IMX_proxy", "mp4": "IMX_proxy.mp4"}]}
        self._assert_parses(RW.viewer_page(man), "viewer_page")

    def test_index_script_parses_in_both_modes(self):
        class _S:
            def snapshot(self):
                return {"busy": False, "log": "", "last": {}}
        for review_only in (False, True):
            html_text = RW.index_page(_S(), [], {"cameras": {}},
                                      review_only=review_only)
            self._assert_parses(html_text,
                                "index_page(review_only=%s)" % review_only)

    def test_review_only_page_has_no_record_button(self):
        class _S:
            def snapshot(self):
                return {"busy": False, "log": "", "last": {}}
        ro = RW.index_page(_S(), [], {"cameras": {}}, review_only=True)
        rec = RW.index_page(_S(), [], {"cameras": {}}, review_only=False)
        self.assertNotIn("id=go", ro)
        self.assertIn("Review only", ro)
        self.assertIn("id=go", rec, "the recording page must keep its button")

    def test_imx_sorts_above_the_boards(self):
        """Nick: the IMX is his reference video, so it leads the session."""
        man = {"name": "t", "created_iso": "x", "settings": {}, "cameras": [
            {"label": "AE3", "mjpeg": "AE3.mjpeg"},
            {"label": "N6", "mjpeg": "N6.mjpeg"},
            {"label": "IMX_proxy", "mp4": "IMX_proxy.mp4"},
            {"label": "IMX", "mjpeg": "IMX.mjpeg"},
            {"label": "MYSTERY", "mjpeg": "MYSTERY.mjpeg"}]}
        html_text = RW.viewer_page(man)
        # Since 2026-09-10 the proxy is folded INTO the IMX tile (one camera,
        # one tile), so IMX_proxy is no longer a heading of its own.
        pos = [html_text.index("<b>%s</b>" % lbl)
               for lbl in ("IMX", "N6", "AE3")]
        self.assertEqual(pos, sorted(pos), "camera order is not IMX-first")
        self.assertNotIn("<b>IMX_proxy</b>", html_text)
        self.assertIn("MYSTERY", html_text,
                      "an unlisted camera must still be shown, not dropped")

if __name__ == "__main__":
    unittest.main(verbosity=2)
