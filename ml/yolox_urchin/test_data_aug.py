#!/usr/bin/env python3
"""Blur-augmentation tests (S8 bite E2 fix path).

Run on the Mac (gate venv):
  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/test_data_aug.py

Pins the three properties the fine-tune depends on:
  1. LABEL PRESERVATION — enabling blur cannot change the boxes a given
     seed produces (blur draws rng LAST, after every placement draw).
  2. The blur actually softens (Laplacian variance drops) and stays in
     the configured sigma range's effect zone.
  3. Eval-side: no blur at sigma 0; train=False never blurs.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import CorpusDataset  # noqa: E402


def lap_var(gray):
    g = gray.astype(np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1]
           + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def make_corpus(root):
    """Two 320x320 high-frequency JPEGs + labels.jsonl, boxes
    [ci,x0,y0,w,h,pixels] absolute px (data.py convention)."""
    rng = np.random.default_rng(7)
    recs = []
    for i in range(2):
        img = rng.integers(0, 256, (320, 320, 3), np.uint8)
        p = root / f"im{i}.jpg"
        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        recs.append({"file": str(p), "w": 320, "h": 320,
                     "boxes": [[0, 40 + 60 * i, 50, 48, 44, 2112],
                               [0, 180, 160 + 20 * i, 36, 40, 1440]]})
    lab = root / "labels.jsonl"
    lab.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return lab


class TestBlurAug(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.jsonl = make_corpus(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _pair(self, **kw):
        """Two datasets, identical seed, differing only in kw."""
        base = dict(canvas=256, train=True, seed=123)
        a = CorpusDataset(self.jsonl, **base)
        b = CorpusDataset(self.jsonl, **{**base, **kw})
        return a, b

    def test_labels_invariant_under_blur(self):
        """Same seed: blur on/off -> identical targets, every sample.
        This is the label-preservation contract (blur draws rng last)."""
        off, on = self._pair(blur_prob=1.0)
        changed_pixels = 0
        for i in range(len(off)):
            img0, t0 = off[i]
            img1, t1 = on[i]
            self.assertTrue((t0 == t1).all(),
                            f"targets changed by blur at idx {i}")
            if not np.array_equal(img0.numpy(), img1.numpy()):
                changed_pixels += 1
        self.assertGreater(changed_pixels, 0,
                           "blur_prob=1.0 never altered an image")

    def test_blur_softens(self):
        """blur_prob=1.0 output is measurably softer than blur off."""
        off, on = self._pair(blur_prob=1.0, blur_sigma=(1.5, 1.5))
        v_off = np.mean([lap_var(off[i][0].numpy().mean(0))
                         for i in range(len(off))])
        v_on = np.mean([lap_var(on[i][0].numpy().mean(0))
                        for i in range(len(on))])
        self.assertLess(v_on, v_off * 0.5,
                        f"sigma 1.5 should halve lap_var "
                        f"(off {v_off:.0f} -> on {v_on:.0f})")

    def test_eval_mode_never_blurs(self):
        """train=False ignores blur_prob entirely (eval determinism)."""
        a = CorpusDataset(self.jsonl, canvas=256, train=False, seed=1)
        b = CorpusDataset(self.jsonl, canvas=256, train=False, seed=1,
                          blur_prob=1.0)
        for i in range(len(a)):
            self.assertTrue(
                np.array_equal(a[i][0].numpy(), b[i][0].numpy()),
                f"eval-mode image changed by blur_prob at idx {i}")

    def test_same_seed_determinism(self):
        """Blur path is rng-driven, not wall-clock: same seed, same out."""
        a = CorpusDataset(self.jsonl, canvas=256, train=True, seed=9,
                          blur_prob=0.7)
        b = CorpusDataset(self.jsonl, canvas=256, train=True, seed=9,
                          blur_prob=0.7)
        for i in range(len(a)):
            self.assertTrue(np.array_equal(a[i][0].numpy(),
                                           b[i][0].numpy()))
            self.assertTrue((a[i][1] == b[i][1]).all())


if __name__ == "__main__":
    unittest.main(verbosity=2)
