"""Host tests for hil_stills_merge (S8 E10). Stdlib only — the merge
tool must be provable on a bare Mac python before it touches the
deployed set."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_stills_merge as m           # noqa: E402


def make_src(root, prefix, indices, reviewed=True, jpeg_byte=b"J"):
    d = os.path.join(root, prefix + "_src")
    os.makedirs(os.path.join(d, "frames"), exist_ok=True)
    rows = []
    for i in indices:
        name = "%s_f%04d.jpg" % (prefix, i)
        with open(os.path.join(d, "frames", name), "wb") as fh:
            fh.write(jpeg_byte + name.encode())
        rows.append({"file": "frames/" + name, "w": 1920, "h": 1080,
                     "classes": ["urchin"],
                     "boxes": [[0, 10, 10, 50, 50, 0]],
                     "reviewed": reviewed})
    with open(os.path.join(d, "labels.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(d, "stills_manifest.json"), "w") as fh:
        json.dump({"created": "t", "ckpt": "c",
                   "clips": [{"clip": "/x/%s.mov" % prefix, "sha256": "s",
                              "n_frames": 99, "fps": 30.0,
                              "sampled_indices": list(indices),
                              "still_prefix": prefix}]}, fh)
    return d


class TestMerge(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="stills_merge_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.out = os.path.join(self.root, "out")

    def test_happy_path_counts_and_order(self):
        a = make_src(self.root, "old", [1, 2])
        b = make_src(self.root, "new", [5, 6, 7])
        n, rev = m.merge(self.out, [a, b])
        self.assertEqual((n, rev), (5, 5))
        man = json.load(open(os.path.join(self.out,
                                          "stills_manifest.json")))
        self.assertEqual([c["still_prefix"] for c in man["clips"]],
                         ["old", "new"])       # SRC order = prior prefix
        rows = [json.loads(ln) for ln in
                open(os.path.join(self.out, "labels.jsonl"))]
        self.assertEqual(len(rows), 5)
        self.assertEqual(len(os.listdir(
            os.path.join(self.out, "frames"))), 5)

    def test_collision_fails_loud(self):
        a = make_src(self.root, "same", [1])
        b_root = os.path.join(self.root, "b")
        os.makedirs(b_root)
        b = make_src(b_root, "same", [1])
        with self.assertRaises(SystemExit) as ctx:
            m.merge(self.out, [a, b])
        self.assertIn("collision", str(ctx.exception))
        self.assertFalse(os.path.exists(self.out))   # nothing written

    def test_missing_frame_fails(self):
        a = make_src(self.root, "old", [1, 2])
        os.remove(os.path.join(a, "frames", "old_f0002.jpg"))
        with self.assertRaises(SystemExit) as ctx:
            m.merge(self.out, [a])
        self.assertIn("not on disk", str(ctx.exception))

    def test_against_accepts_pure_append(self):
        deployed = make_src(self.root, "old", [1, 2])
        a = make_src(os.path.join(self.root, "v1"), "old", [1, 2])
        b = make_src(self.root, "new", [5])
        n, rev = m.merge(self.out, [a, b], against=deployed)
        self.assertEqual(n, 3)

    def test_against_rejects_changed_reviewed_boxes(self):
        deployed = make_src(self.root, "old", [1])
        a = make_src(os.path.join(self.root, "v1"), "old", [1])
        lab = os.path.join(a, "labels.jsonl")
        r = json.loads(open(lab).readline())
        r["boxes"] = [[0, 99, 99, 20, 20, 0]]
        open(lab, "w").write(json.dumps(r) + "\n")
        with self.assertRaises(SystemExit) as ctx:
            m.merge(self.out, [a], against=deployed)
        self.assertIn("DECISION", str(ctx.exception))

    def test_against_rejects_changed_frame_bytes(self):
        deployed = make_src(self.root, "old", [1])
        a = make_src(os.path.join(self.root, "v1"), "old", [1],
                     jpeg_byte=b"X")
        with self.assertRaises(SystemExit) as ctx:
            m.merge(self.out, [a], against=deployed)
        self.assertIn("byte-wise", str(ctx.exception))

    def test_against_rejects_dropped_deployed_still(self):
        deployed = make_src(self.root, "old", [1, 2])
        a = make_src(os.path.join(self.root, "v1"), "old", [1])
        with self.assertRaises(SystemExit) as ctx:
            m.merge(self.out, [a], against=deployed)
        self.assertIn("absent", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
