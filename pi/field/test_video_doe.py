#!/usr/bin/env python3
"""Tests for the S30 video DOE. No hardware, no network."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import video_doe as v                                            # noqa: E402


class TestSizeMath(unittest.TestCase):
    def test_clip_bytes_is_linear_in_fps(self):
        self.assertEqual(v.clip_bytes(1000, 30, 5), 150000)
        self.assertEqual(v.clip_bytes(1000, 15, 5), 75000)

    def test_link_bitrate_is_over_the_hour_not_the_clip(self):
        # 1.8 MB once an hour is a 4 kbps link, not a 2.9 Mbps one. Getting
        # this backwards would overstate the budget by 720x.
        self.assertAlmostEqual(v.link_bitrate_bps(1800000), 4000.0, places=1)

    def test_link_bitrate_rejects_zero_period(self):
        with self.assertRaises(ValueError):
            v.link_bitrate_bps(1000, 0)


class TestReachable(unittest.TestCase):
    def test_ae3_hd_reaches_nothing(self):
        self.assertEqual(v.reachable_fps(2.6), [])

    def test_tolerance_admits_a_near_miss(self):
        # 29.4 fps against a 30 target is a pass; 26 is not.
        self.assertIn(30, v.reachable_fps(29.4))
        self.assertNotIn(30, v.reachable_fps(26.0))

    def test_zero_and_none_are_safe(self):
        self.assertEqual(v.reachable_fps(0), [])
        self.assertEqual(v.reachable_fps(None), [])


class TestGridOrder(unittest.TestCase):
    def test_cameras_are_adjacent_within_a_block(self):
        """The camera comparison must not be confounded with scene drift."""
        g = v.build_grid()
        for i in range(0, len(g), len(v.CAMERAS)):
            block = g[i:i + len(v.CAMERAS)]
            self.assertEqual(len({(c["resolution"], c["quality"]) for c in block}), 1)
            self.assertEqual({c["camera"] for c in block}, set(v.CAMERAS))

    def test_grid_size(self):
        self.assertEqual(len(v.build_grid()), 3 * 3 * len(v.QUALITIES))


class TestBufferFits(unittest.TestCase):
    def test_refuses_without_a_heap_reading(self):
        self.assertFalse(v.buffer_fits(150, 12000, None))
        self.assertFalse(v.buffer_fits(150, None, 3000000))

    def test_leaves_working_room(self):
        # Exactly-fits must FAIL: the encoder needs room on top of the frames,
        # and an OOM wedges the board rather than raising.
        self.assertFalse(v.buffer_fits(10, 100, 1000))
        self.assertTrue(v.buffer_fits(10, 100, 10000))


class TestBoardScripts(unittest.TestCase):
    """The rendered board scripts must be valid Python.

    S29 shipped a burst script with an unescaped quote inside a %-format --
    it captured 0 frames while the host reported a clean run. A compile check
    is cheap and catches exactly that class.
    """

    def test_probe_compiles(self):
        src = v.BOARD_PROBE % {"SIZE": "VGA", "Q": 30, "MS": 3000}
        compile(src, "probe", "exec")
        self.assertNotIn("%%", src)
        self.assertIn("csi.VGA", src)

    def test_clip_compiles_in_both_modes(self):
        for buffered in (0, 1):
            src = v.BOARD_CLIP % {"SIZE": "HD", "Q": 45, "N": 150,
                                  "PERIOD": 33, "BUFFER": buffered}
            compile(src, "clip", "exec")
            self.assertNotIn("%%", src)

    def test_probe_never_transfers_a_frame(self):
        """The probe measures the CAMERA, not the USB link -- if it ever ships
        a frame it is measuring transfer and calling it frame rate."""
        src = v.BOARD_PROBE % {"SIZE": "VGA", "Q": 30, "MS": 3000}
        self.assertNotIn("b2a_base64", src)


class TestArgv(unittest.TestCase):
    def test_transcode_sets_input_framerate(self):
        """MJPEG carries no timing; without -r on the INPUT every clip plays
        at ffmpeg's default 25 and the motion is wrong."""
        a = v.transcode_argv("/a.mjpeg", "/b.mp4", 30)
        self.assertLess(a.index("-r"), a.index("-i"))
        self.assertEqual(a[a.index("-r") + 1], "30")

    def test_transcode_uses_the_hardware_encoder(self):
        a = v.transcode_argv("/a.mjpeg", "/b.mp4", 30)
        self.assertIn("h264_v4l2m2m", a)

    def test_imx_argv_is_mjpeg_and_bounded(self):
        a = v.imx_argv(1280, 720, 30, 45, "/o.mjpeg", 5000)
        self.assertIn("mjpeg", a)
        self.assertEqual(a[a.index("-t") + 1], "5000")
        self.assertEqual(a[a.index("--quality") + 1], "45")


class TestClipPlan(unittest.TestCase):
    def _p(self, cam, res, q, reach, ok=True):
        return {"ok": ok, "camera": cam, "resolution": res, "quality": q,
                "reachable": reach}

    def test_picks_the_fastest_sustainable_rate(self):
        plan = v.clip_plan([self._p("N6", "VGA", 60, [30, 25, 20, 15, 10, 5])],
                           v.QUALITIES, v.FPS_TARGETS)
        self.assertEqual(plan[0]["fps"], 30)

    def test_skips_cells_that_reach_nothing(self):
        self.assertEqual(v.clip_plan([self._p("AE3", "HD", 60, [])],
                                     v.QUALITIES, v.FPS_TARGETS), [])

    def test_skips_failed_probes(self):
        self.assertEqual(v.clip_plan([self._p("AE3", "VGA", 60, [10], ok=False)],
                                     v.QUALITIES, v.FPS_TARGETS), [])


class TestQualityBand(unittest.TestCase):
    """Nick set the operating band by LOOKING at frames, 2026-09-07:
    "q30 is not impressive, anything around q50-90 is where we want to
    operate." An earlier sweep sat at q10-q45 and was measuring the wrong
    region entirely, so this is pinned rather than left to a comment."""

    def test_band_covers_50_to_90(self):
        self.assertTrue({50, 60, 70, 80, 90}.issubset(set(v.QUALITIES)))

    def test_one_rung_below_the_band_is_kept(self):
        """The report must SHOW what leaving the band costs, not assert it."""
        self.assertTrue(any(q < 50 for q in v.QUALITIES))

    def test_nothing_down_in_the_old_region(self):
        self.assertFalse([q for q in v.QUALITIES if q < 40])

    def test_clip_plan_filters_by_the_active_band(self):
        """A probe at a quality outside the sweep must not produce a clip --
        this is what caught the stale fixtures when the band moved."""
        probe = {"ok": True, "camera": "N6", "resolution": "VGA",
                 "quality": 20, "reachable": [30]}
        self.assertEqual(v.clip_plan([probe], v.QUALITIES, v.FPS_TARGETS), [])


class TestImxProbeAsksHighEnough(unittest.TestCase):
    """The IMX probe must ask ABOVE the highest target it is scored against.

    Shipped wrong once: asking exactly 30 measured 25.6 (rpicam's -t window
    includes sensor start-up), which failed the 30 fps target and would have
    reported that the IMX708 cannot do 30 fps. It does 55.6.
    """

    def test_probe_request_exceeds_every_target(self):
        self.assertGreater(v.IMX_PROBE_FPS, max(v.FPS_TARGETS))

    def test_probe_request_clears_the_top_target_with_margin(self):
        # The deficit is a fixed slice of the window, so the request must
        # exceed the target by more than the tolerance can absorb.
        self.assertGreaterEqual(v.IMX_PROBE_FPS,
                                max(v.FPS_TARGETS) / v.FPS_TOLERANCE)


class TestMotionReference(unittest.TestCase):
    """A STREAMED clip cannot price H.264.

    Its frames are spaced by the USB link, not the requested rate, so they
    share far less than 33 ms apart would -- H.264 compresses it badly and
    understates its own benefit. That is the number the custom-firmware
    decision rests on, so it must come from a true-motion capture.
    """

    def test_floor_admits_the_n6_hd_q90_cell(self):
        # 25.6 MB heap, 385 KB/frame -> 29 frames -> 0.97 s at 30 fps.
        fit = v.buffered_frames(385 * 1024, 25607984)
        self.assertGreaterEqual(fit / 30.0, v.MIN_MOTION_REF_S)

    def test_buffered_frames_is_zero_without_a_reading(self):
        self.assertEqual(v.buffered_frames(None, 100), 0)
        self.assertEqual(v.buffered_frames(100, None), 0)

    def test_page_marks_a_streamed_ratio_invalid(self):
        self.assertIn("NOT valid: clip streamed", v.PAGE)

    def test_page_shows_before_and_after(self):
        self.assertIn("<b>before</b>", v.PAGE)
        self.assertIn("<b>after</b>", v.PAGE)


class TestRatioIsQualityTargeted(unittest.TestCase):
    """The SIZE comparison must not be bitrate-controlled.

    Shipped wrong once: every clip was encoded at -b:v 4M, so every output was
    ~2 MB regardless of input and the "ratio" measured the bitrate I chose,
    not H.264. A bigger MJPEG mechanically scored a bigger ratio -- N6 VGA q90
    read 7.36x against the same cell's true 2.4x-class behaviour.
    """

    def test_ratio_encode_uses_crf_not_bitrate(self):
        a = v.ratio_argv("/a.mjpeg", "/b.mp4", 30)
        self.assertIn("-crf", a)
        self.assertNotIn("-b:v", a)

    def test_viewing_encode_may_still_use_bitrate(self):
        a = v.transcode_argv("/a.mjpeg", "/b.mp4", 30, bitrate="4M")
        self.assertIn("-b:v", a)

    def test_ratio_encode_sets_input_framerate(self):
        a = v.ratio_argv("/a.mjpeg", "/b.mp4", 30)
        self.assertLess(a.index("-r"), a.index("-i"))


class TestEncoderAsymmetryIsDisclosed(unittest.TestCase):
    """The N6/AE3 fps gap must not read as one camera simply being faster.

    Per the S31 desk session's source reading (unverified in this checkout):
    the N6's JPEG runs on the VC8000 hardware encoder, the AE3's in software.
    A card that prints 68 fps beside 12 fps without saying so invites the
    wrong conclusion about the boards.
    """

    def test_page_discloses_the_asymmetry(self):
        self.assertIn("not like-for-like", v.PAGE)
        self.assertIn("VC8000", v.PAGE)

    def test_page_marks_the_claim_unverified(self):
        """Attributed, not established -- this repo has been burned by
        plausible second-hand hardware facts before."""
        self.assertIn("not verified here", v.PAGE)


class TestState(unittest.TestCase):
    def test_snapshot_is_json_serialisable(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            s = v.DoeState(d)
            s.probes.append({"ok": True, "camera": "N6"})
            json.dumps(s.snapshot())
            s.save()
            self.assertTrue(os.path.exists(os.path.join(d, "results.json")))

    def test_save_is_atomic(self):
        """The page polls while the run writes; a half-written file is a
        parse error on the operator's screen."""
        import inspect
        src = inspect.getsource(v.DoeState.save)
        self.assertIn("os.replace", src)


class TestPage(unittest.TestCase):
    def test_page_does_not_claim_the_board_makes_h264(self):
        self.assertIn("the boards cannot make H.264", v.PAGE)

    def test_page_has_no_hardcoded_hostname(self):
        for host in ("nereus000", "nereus001", "nereus002"):
            self.assertNotIn(host, v.PAGE)




class TestParseList(unittest.TestCase):
    def test_dash_form_is_what_recipes_use(self):
        self.assertEqual(v.parse_list("10-20-30"), ["10", "20", "30"])

    def test_comma_form_still_works_for_humans(self):
        self.assertEqual(v.parse_list("QVGA,VGA,HD"), ["QVGA", "VGA", "HD"])

    def test_empty_is_empty(self):
        self.assertEqual(v.parse_list(""), [])
        self.assertEqual(v.parse_list(None), [])

    def test_recipe_params_actually_parse(self):
        """The recipe's own choices must survive the parser -- the S29 bug was
        a recipe that rendered broken because the two disagreed."""
        import re
        here = os.path.dirname(os.path.abspath(__file__))
        toml = os.path.join(here, "..", "workbench", "recipes", "video_doe.toml")
        body = open(toml).read()
        for line, cast in (("qualities", int), ("resolutions", str)):
            m = re.search(r'^%s = \[(.*)\]$' % line, body, re.M)
            self.assertTrue(m, "%s missing from recipe" % line)
            for choice in re.findall(r'"([^"]+)"', m.group(1)):
                parts = v.parse_list(choice)
                self.assertTrue(parts, choice)
                for part in parts:
                    cast(part)



class TestHeadlineIsTheFastestRate(unittest.TestCase):
    """The page's "hourly link @max" must price the FASTEST sustainable rate.

    Shipped wrong once: fps_targets is descending and the render loop assigned
    `best` on every reachable cell, so it ended on the SLOWEST one and
    understated the link budget by up to 6x. A budget that is wrong low is
    the dangerous direction -- it sizes a radio that cannot keep up.
    """

    def test_fps_targets_are_descending(self):
        self.assertEqual(list(v.FPS_TARGETS), sorted(v.FPS_TARGETS, reverse=True))

    def test_render_keeps_the_first_reachable(self):
        self.assertIn("if(!best)best=c", v.PAGE)
        self.assertNotIn("{best=c;h+=", v.PAGE)

if __name__ == "__main__":
    unittest.main(verbosity=1)
