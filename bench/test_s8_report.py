"""S8 bite C host tests: row aggregation + report rendering, on synthetic
rows shaped exactly like the recorder's output."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s8_report as R  # noqa: E402


def hdr(seq, bc, mc, bb=(), mb=(), **stages):
    base = {"seq": seq, "w": 640, "h": 400, "b64": 100, "jpeg": 75,
            "cap_us": 10000, "inf_us": 6000, "blob_us": 12000,
            "enc_us": 40000, "mdec_us": 14000,
            "det": sum(mc), "blobs": sum(bc), "bc": list(bc),
            "mc": list(mc), "amb": 0, "bb": [list(b) for b in bb],
            "mb": [list(b) for b in mb], "lab": [0, 0, 0]}
    base.update(stages)
    return base


def row(ts, run, board, truth, h):
    return {"ts": ts, "run": run, "board": board, "truth": truth, "hdr": h}


def rows_two_runs():
    truth = {"pink": 2, "purple": 1}
    out = []
    for i in range(4):
        # arr1/AE3: blob exact every frame; model misses one purple in frame 0
        mc = [2, 0] if i == 0 else [2, 1]
        out.append(row(100.0 + i, "arr1", "AE3", truth,
                       hdr(i, [2, 1], mc,
                           bb=[[0, 10, 10, 30, 40, 900], [0, 60, 60, 20, 20, 300],
                               [1, 200, 100, 26, 28, 500]],
                           mb=[[0, 8, 8, 30, 40, 91], [1, 198, 99, 27, 27, 62]])))
    for i in range(4):
        out.append(row(200.0 + i, "arr2", "AE3", {"pink": 0, "purple": 3},
                       hdr(i, [0, 3], [0, 3],
                           bb=[[1, 10, 10, 12, 14, 100]] * 3)))
    return out


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.agg = R.aggregate(rows_two_runs())

    def test_keys_are_run_board(self):
        self.assertEqual(set(self.agg), {("arr1", "AE3"), ("arr2", "AE3")})

    def test_mean_counts(self):
        s = self.agg[("arr1", "AE3")]
        self.assertEqual(s["mean_bc"], [2.0, 1.0])
        self.assertAlmostEqual(s["mean_mc"][1], 0.75)   # 3 of 4 frames saw it

    def test_exactness_counts_all_classes_together(self):
        s = self.agg[("arr1", "AE3")]
        self.assertEqual(s["b_exact"], 4)
        self.assertEqual(s["m_exact"], 3)               # frame 0 missed purple

    def test_fps_from_span(self):
        s = self.agg[("arr1", "AE3")]
        self.assertAlmostEqual(s["fps"], 1.0)           # 4 frames over 3 s

    def test_stage_ms_are_means(self):
        s = self.agg[("arr1", "AE3")]
        self.assertAlmostEqual(s["stage_ms"]["inf_us"], 6.0)

    def test_frame_px_is_median_blob_min_side(self):
        s = self.agg[("arr1", "AE3")]
        # min sides 30, 20, 26 -> median 26
        self.assertEqual(s["frame_px"][0], 26)

    def test_model_points_carry_conf(self):
        s = self.agg[("arr1", "AE3")]
        self.assertIn((30, 0.91), s["model_pts"])

    def test_info_rows_are_ignored(self):
        rows = rows_two_runs() + [{"ts": 1, "run": "arr1", "board": "AE3",
                                   "truth": {}, "hdr": {"labels": ["x"]}}]
        agg = R.aggregate(rows)
        self.assertEqual(agg[("arr1", "AE3")]["n"], 4)


class TestPxBins(unittest.TestCase):
    def test_bins_split_blob_and_model(self):
        binned = R.px_bin_accuracy(R.aggregate(rows_two_runs()), "AE3")
        d = {lab: (n, bf, mf) for lab, n, bf, mf in binned}
        self.assertEqual(d["24-32"][0], 4)              # arr1 frames, px 26
        self.assertEqual(d["24-32"][1], 1.0)            # blob always exact
        self.assertEqual(d["24-32"][2], 0.75)
        self.assertEqual(d["<16"][0], 4)                # arr2 frames, px 12

    def test_empty_bins_report_none(self):
        binned = R.px_bin_accuracy(R.aggregate(rows_two_runs()), "AE3")
        d = {lab: (n, bf, mf) for lab, n, bf, mf in binned}
        self.assertEqual(d["64+"], (0, None, None))


class TestParseKv(unittest.TestCase):
    def test_parses_watts(self):
        self.assertEqual(R.parse_kv_floats("AE3=0.21,N6=1.02"),
                         {"AE3": 0.21, "N6": 1.02})

    def test_none_is_empty(self):
        self.assertEqual(R.parse_kv_floats(None), {})


class TestRender(unittest.TestCase):
    def _html(self, **kw):
        return R.render(R.aggregate(rows_two_runs()),
                        kw.get("power", {}), kw.get("infer", {}),
                        "meter swapped; constant-load assumption")

    def test_all_five_views_present(self):
        h = self._html()
        for sec in ("Does it count right", "pixels-on-target",
                    "frame budget", "Confidence vs size", "board scorecard"):
            self.assertIn(sec, h)

    def test_truth_reaches_the_table(self):
        h = self._html()
        self.assertIn("<td>pink</td><td>2</td>", h.replace("\n", ""))

    def test_energy_not_measured_without_power(self):
        self.assertIn("not measured", self._html())

    def test_energy_computed_with_power(self):
        h = self._html(power={"AE3": 0.5})
        self.assertIn("3.00 mJ", h)                     # 0.5 W * 6 ms

    def test_blob_asymmetry_documented(self):
        self.assertIn("NO confidence by construction", self._html())

    def test_self_contained_no_external_refs(self):
        h = self._html()
        for bad in ("http://", "https://", "src="):
            self.assertNotIn(bad, h)

    def test_dark_mode_variables_present(self):
        h = self._html()
        self.assertIn("prefers-color-scheme: dark", h)
        self.assertIn('[data-theme="dark"]', h)

    def test_t2_floor_shaded(self):
        self.assertIn("T2 floor", self._html())


class TestMain(unittest.TestCase):
    def test_writes_report_and_names_the_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            rows_path = os.path.join(d, "rows.jsonl")
            with open(rows_path, "w") as fh:
                for r in rows_two_runs():
                    fh.write(json.dumps(r) + "\n")
            out = os.path.join(d, "report.html")
            rc = R.main([rows_path, "--out", out])
            self.assertEqual(rc, 0)
            body = open(out).read()
            self.assertIn("board scorecard", body)

    def test_no_rows_is_a_loud_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "empty.jsonl")
            open(p, "w").close()
            self.assertEqual(R.main([p, "--out", os.path.join(d, "r.html")]),
                             1)


if __name__ == "__main__":
    unittest.main()
