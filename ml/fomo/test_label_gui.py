"""S8 B3 host tests: label_gui's data layer + relabel's reviewed guard.

Run:  python3 -m unittest ml.fomo.test_label_gui
No server is started; the GUI's HTTP layer is a thin shell over these
functions, which is the point of keeping them pure.
"""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import label_gui as G  # noqa: E402


def write_set(root, run, board, records):
    d = os.path.join(root, run, board)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "labels.jsonl"), "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return d


def rec(file="frame_000.jpg", boxes=None, classes=("pink", "purple"),
        **extra):
    r = {"file": file, "w": 640, "h": 400, "classes": list(classes),
         "boxes": boxes if boxes is not None else [[0, 10, 10, 20, 20, 300]]}
    r.update(extra)
    return r


class TestSetDiscovery(unittest.TestCase):
    def test_finds_run_board_sets_sorted(self):
        with tempfile.TemporaryDirectory() as root:
            write_set(root, "run2", "N6", [rec()])
            write_set(root, "run1", "AE3", [rec()])
            os.makedirs(os.path.join(root, "run1", "empty"))  # no labels
            self.assertEqual(G.find_sets(root),
                             ["run1/AE3", "run2/N6"])

    def test_finds_flat_single_level_sets_too(self):
        # The S26 urchin corpora: labels.jsonl directly in <source>/.
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "urchinbot")
            os.makedirs(d)
            with open(os.path.join(d, "labels.jsonl"), "w") as fh:
                fh.write(json.dumps(rec()) + "\n")
            write_set(root, "roboflow", "rf100", [rec()])
            self.assertEqual(G.find_sets(root),
                             ["roboflow/rf100", "urchinbot"])

    def test_missing_root_is_empty_not_an_error(self):
        self.assertEqual(G.find_sets("/nonexistent/nowhere"), [])

    def test_set_dir_accepts_one_or_two_segments(self):
        self.assertEqual(G.set_dir("/root", "urchinbot"), "/root/urchinbot")
        self.assertEqual(G.set_dir("/root", "run1/AE3"), "/root/run1/AE3")

    def test_set_dir_refuses_escapes(self):
        for bad in ("../etc", "a/../../b", "a/b/c", "run/..", "..", ".",
                    "/abs/x"):
            with self.assertRaises(ValueError, msg=bad):
                G.set_dir("/root", bad)


class TestResolveImage(unittest.TestCase):
    """S26 sources ship NESTED file paths (images/x.JPG, train/images/x.jpg);
    the resolver must serve them and still refuse escapes."""

    def test_nested_paths_resolve_inside_the_set(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "urchinbot", "images")
            os.makedirs(d)
            with open(os.path.join(d, "im1.JPG"), "wb") as fh:
                fh.write(b"\xff\xd8")
            p = G.resolve_image(root, "urchinbot", "images/im1.JPG")
            self.assertEqual(p, os.path.realpath(os.path.join(d, "im1.JPG")))

    def test_escapes_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "s"))
            for bad in ("../other/x.jpg", "a/../../x.jpg", "..", "",
                        "/etc/passwd", "a//b.jpg"):
                with self.assertRaises(ValueError, msg=bad):
                    G.resolve_image(root, "s", bad)

    def test_symlink_out_of_the_set_is_refused(self):
        # realpath-based confinement: a symlink pointing outside the set
        # must not serve, even though its literal path looks clean.
        with tempfile.TemporaryDirectory() as root:
            sdir = os.path.join(root, "s")
            os.makedirs(sdir)
            outside = os.path.join(root, "secret.jpg")
            with open(outside, "wb") as fh:
                fh.write(b"\xff\xd8")
            os.symlink(outside, os.path.join(sdir, "link.jpg"))
            with self.assertRaises(ValueError):
                G.resolve_image(root, "s", "link.jpg")


class TestSaveRoundTrip(unittest.TestCase):
    def test_round_trip_preserves_format(self):
        with tempfile.TemporaryDirectory() as root:
            original = [rec("a.jpg"), rec("b.jpg", boxes=[[1, 1, 2, 3, 4, 12]])]
            write_set(root, "r", "AE3", original)
            recs = G.load_records(root, "r/AE3")
            G.save_records(root, "r/AE3", recs)
            self.assertEqual(G.load_records(root, "r/AE3"), original)

    def test_no_tmp_file_left_behind(self):
        with tempfile.TemporaryDirectory() as root:
            d = write_set(root, "r", "AE3", [rec()])
            G.save_records(root, "r/AE3", G.load_records(root, "r/AE3"))
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")],
                             [])


class TestApplyFrameUpdate(unittest.TestCase):
    def test_update_stamps_reviewed_and_saves_boxes(self):
        recs = [rec("a.jpg"), rec("b.jpg")]
        out = G.apply_frame_update(recs, "b.jpg",
                                   [[1, 5, 6, 7, 8, 56]], ["pink", "purple"])
        self.assertEqual(out["boxes"], [[1, 5, 6, 7, 8, 56]])
        self.assertTrue(out["reviewed"])
        self.assertNotIn("reviewed", recs[0])   # only the edited frame

    def test_unknown_frame_is_an_error(self):
        with self.assertRaises(ValueError):
            G.apply_frame_update([rec("a.jpg")], "zz.jpg", [], ["pink"])

    def test_class_extension_reaches_every_record(self):
        recs = [rec("a.jpg"), rec("b.jpg")]
        G.apply_frame_update(recs, "a.jpg", [], ["pink", "purple", "urchin"])
        for r in recs:
            self.assertEqual(r["classes"], ["pink", "purple", "urchin"])

    def test_class_removal_or_rename_is_refused(self):
        recs = [rec("a.jpg")]
        for bad in (["pink"], ["purple", "pink"], ["pink", "mauve"]):
            with self.assertRaises(ValueError, msg=bad):
                G.apply_frame_update(recs, "a.jpg", [], bad)

    def test_box_class_out_of_range_is_refused(self):
        recs = [rec("a.jpg")]
        with self.assertRaises(ValueError):
            G.apply_frame_update(recs, "a.jpg",
                                 [[2, 1, 1, 5, 5, 25]], ["pink", "purple"])

    def test_malformed_boxes_are_refused(self):
        recs = [rec("a.jpg")]
        for bad in ([[0, 1, 2, 3, 4]],       # 5 fields
                    [[0, 1, 2, 0, 4, 0]],    # zero width
                    ["nope"], "nope"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                G.apply_frame_update(recs, "a.jpg", bad, ["pink", "purple"])

    def test_trainer_still_reads_a_reviewed_file(self):
        """The bite's format contract: corrections round-trip through the
        trainer's reader unchanged -- 6-field boxes, extra keys ignored."""
        with tempfile.TemporaryDirectory() as root:
            recs = [rec("a.jpg")]
            G.apply_frame_update(recs, "a.jpg",
                                 [[0, 9, 9, 30, 30, 900]], ["pink", "purple"])
            write_set(root, "r", "AE3", recs)
            for line in open(os.path.join(root, "r", "AE3", "labels.jsonl")):
                r = json.loads(line)
                # exactly train.py's unpack -- raises if the shape drifts
                for ci, x, yy, w, h, px in r["boxes"]:
                    self.assertIsInstance(ci, int)


class TestRelabelGuard(unittest.TestCase):
    """relabel.py must refuse to flatten hand-reviewed labels (no --force)."""

    @staticmethod
    def _load_relabel():
        # relabel imports numpy/PIL/scipy at module top; stub them so the
        # guard is testable anywhere (the bench-test stub pattern).
        for name in ("numpy", "scipy", "scipy.ndimage", "PIL", "PIL.Image"):
            if name not in sys.modules:
                sys.modules[name] = types.ModuleType(name)
        sys.modules["scipy"].ndimage = sys.modules["scipy.ndimage"]
        sys.modules["PIL"].Image = sys.modules["PIL.Image"]
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "relabel.py")
        spec = importlib.util.spec_from_file_location("relabel_under_test",
                                                      path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_has_reviewed_detects_the_stamp(self):
        R = self._load_relabel()
        with tempfile.TemporaryDirectory() as root:
            d = write_set(root, "r", "AE3",
                          [rec("a.jpg"), rec("b.jpg", reviewed=True)])
            self.assertTrue(R.has_reviewed(os.path.join(d, "labels.jsonl")))
            d2 = write_set(root, "r", "N6", [rec("a.jpg")])
            self.assertFalse(R.has_reviewed(os.path.join(d2, "labels.jsonl")))
            self.assertFalse(R.has_reviewed("/nonexistent"))


if __name__ == "__main__":
    unittest.main()
