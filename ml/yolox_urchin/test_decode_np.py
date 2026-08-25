#!/usr/bin/env python3
"""Parity proof for decode_np.py against the training-side torch path.

Run on the Mac (gate venv — torch + torchvision + TF live there):
  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/test_decode_np.py

Three rungs:
  1. decode_all == model.py decode_raw, exactly, on random tensors.
  2. nms == torchvision.ops.nms on random box soups.
  3. the REAL artifact: the int8 tflite the boards run (stage1_v2),
     executed by the TF interpreter on a real Monterey still — decode
     must produce sane, float-model-agreeing detections end to end.

Rungs 1–2 need torch/torchvision; rung 3 additionally needs TF and the
export + stills on disk. Anything missing SKIPS loudly, never passes
silently.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decode_np import (cells_to_dets, decode_all, dequantize,  # noqa: E402
                       detect, merge_tiles, nms)

try:
    import torch
    from model import decode_raw
except ImportError:
    torch = None

EXPORT = Path.home() / "nereus_ml" / "exports" / "stage1_v2"
STILLS = Path.home() / "nereus_ml" / "datasets" / "hil_monterey" / "stills_v1"


def _rand_heads(input_size=256, n_cls=1, seed=8):
    rng = np.random.default_rng(seed)
    heads = []
    for s in (8, 16, 32):                       # decode_raw's level order
        hw = input_size // s
        reg = rng.normal(0, 1.5, (1, 4, hw, hw))
        scores = rng.uniform(0, 1, (1, 1 + n_cls, hw, hw))  # sigmoid-baked
        heads.append(np.concatenate([reg, scores], 1).astype(np.float32))
    return heads


class DecodeMath(unittest.TestCase):

    @unittest.skipIf(torch is None, "torch not in this venv")
    def test_decode_matches_torch_exactly(self):
        heads = _rand_heads()
        ours = decode_all(heads, 256)
        ref = decode_raw([torch.from_numpy(h) for h in heads])[0].numpy()
        # identical formula, identical anchor order; fp32 exp() is the
        # only wiggle room
        np.testing.assert_allclose(ours, ref, rtol=1e-5, atol=1e-4)

    def test_decode_nhwc_equals_nchw(self):
        heads = _rand_heads(seed=9)
        nhwc = [h.transpose(0, 2, 3, 1).copy() for h in heads]
        np.testing.assert_array_equal(decode_all(heads, 256),
                                      decode_all(nhwc, 256))

    def test_decode_tile_sizes(self):
        # native-px tiles are also 256 — but prove other sizes decode too
        heads = _rand_heads(input_size=192, seed=10)
        out = decode_all(heads, 192)
        self.assertEqual(out.shape, (24 ** 2 + 12 ** 2 + 6 ** 2, 6))
        with self.assertRaises(ValueError):
            decode_all(heads, 250)              # stride must divide

    @unittest.skipIf(torch is None, "torch not in this venv")
    def test_nms_matches_torchvision(self):
        from torchvision.ops import nms as tv_nms
        for seed in range(5):
            rng = np.random.default_rng(seed)
            n = 200
            xy = rng.uniform(0, 220, (n, 2)).astype(np.float32)
            wh = rng.uniform(4, 60, (n, 2)).astype(np.float32)
            boxes = np.concatenate([xy, xy + wh], 1)
            scores = rng.uniform(0.01, 1, n).astype(np.float32)
            ours = nms(boxes, scores, 0.45)
            ref = tv_nms(torch.from_numpy(boxes), torch.from_numpy(scores),
                         0.45).tolist()
            self.assertEqual(ours, ref, f"seed {seed}")

    def test_detect_thresholds_and_shapes(self):
        heads = _rand_heads(seed=11)
        dets = detect(heads, 256, conf=0.5, nms_iou=0.45)
        self.assertEqual(dets.shape[1], 6)
        self.assertTrue((dets[:, 4] > 0.5).all())
        self.assertTrue((np.diff(dets[:, 4]) <= 1e-6).all())  # descending
        self.assertEqual(detect(heads, 256, conf=1.1).shape, (0, 6))

    def test_sparse_cells_equal_full_decode(self):
        """The board's sparse-cell wire (obj-thresholded, indexed, rounded
        to 4dp) must reproduce detect()'s output: same boxes, same scores,
        to rounding tolerance. Mirrors hil_board.py extract_cells."""
        heads = _rand_heads(seed=13)
        full = detect(heads, 256, conf=0.30, nms_iou=0.45)
        cells = []
        for o in heads:                    # NCHW (1,6,H,W)
            _, _, hh, ww = o.shape
            for y in range(hh):
                for x in range(ww):
                    c = o[0, :, y, x]
                    if c[4] >= 0.10:       # board OBJ_THR
                        cells.append([hh, y, x] +
                                     [round(float(v), 4) for v in c])
        sparse = cells_to_dets(cells, 256, conf=0.30, nms_iou=0.45)
        # rounding can swap NMS order between near-equal scores, so compare
        # as SETS: counts within 1, every full det has a sparse twin
        self.assertLessEqual(abs(len(sparse) - len(full)), 1)
        def _iou(a, b):
            iw = max(0, min(a[2], b[2]) - max(a[0], b[0]))
            ih = max(0, min(a[3], b[3]) - max(a[1], b[1]))
            inter = iw * ih
            ua = ((a[2] - a[0]) * (a[3] - a[1])
                  + (b[2] - b[0]) * (b[3] - b[1]) - inter)
            return inter / max(ua, 1e-9)
        unmatched = sum(
            not any(_iou(f, s) > 0.9 and abs(f[4] - s[4]) < 0.01
                    for s in sparse) for f in full)
        self.assertLessEqual(unmatched, 1,
                             f"{unmatched}/{len(full)} full dets unmatched")

    def test_merge_tiles_dedups_seam_straddler(self):
        # same box seen by two overlapping tiles at different local coords
        a = np.array([[100, 50, 140, 90, 0.9, 0]], np.float32)  # tile (0,0)
        b = np.array([[8, 50, 48, 90, 0.8, 0]], np.float32)     # tile (92,0)
        merged = merge_tiles([a, b], [(0, 0), (92, 0)], nms_iou=0.45)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0, 4], np.float32(0.9))
        # disjoint boxes both survive
        c = np.array([[200, 200, 240, 240, 0.7, 0]], np.float32)
        self.assertEqual(len(merge_tiles([a, c], [(0, 0), (0, 0)])), 2)
        empty = merge_tiles([np.zeros((0, 6), np.float32)], [(0, 0)])
        self.assertEqual(empty.shape, (0, 6))


class Int8EndToEnd(unittest.TestCase):

    @staticmethod
    def _densest_still():
        """The pre-labeled still with the most boxes + its record.
        A whole-frame 256 letterbox shrinks these urchins to ~14 px —
        below the model's size floor, zero detections, correctly (the
        artifact the HIL exists to measure). So the int8-vs-float
        comparison runs on a NATIVE-px 256 crop: the tiled-mode regime,
        where the model actually sees targets."""
        import json
        labels = STILLS / "labels.jsonl"
        if not labels.exists():
            raise unittest.SkipTest(
                "hil stills_v1 missing — run hil_stills.py first")
        recs = [json.loads(ln) for ln in open(labels)]
        return max(recs, key=lambda r: len(r["boxes"]))

    @unittest.skipIf(torch is None, "torch not in this venv")
    def test_float_at_eval_protocol_sees_the_scene(self):
        """Guards the real-frame preprocessing + decode: the 640-px eval
        protocol (mAP50 0.654) must find a dense urchin scene."""
        import cv2
        from model import RawExport, build_model
        rec = self._densest_still()
        img = cv2.imread(str(STILLS / rec["file"]))
        s = 640 / max(img.shape[:2])
        rs = cv2.resize(img, (round(img.shape[1] * s),
                              round(img.shape[0] * s)),
                        interpolation=cv2.INTER_AREA)
        canvas = np.full((640, 640, 3), 114, np.uint8)
        canvas[:rs.shape[0], :rs.shape[1]] = rs
        ckpt = (Path.home()
                / "nereus_ml/runs/stage1_yolox/stage1_v2/ema.pt")
        model = build_model(num_classes=1, arch="yolox-nano", stem="conv")
        ck = torch.load(str(ckpt), map_location="cpu")
        model.load_state_dict(ck.get("model", ck))
        raw = RawExport(model).eval()
        with torch.no_grad():
            heads = [o.numpy() for o in
                     raw(torch.from_numpy(np.ascontiguousarray(
                         canvas[:, :, ::-1][None]))
                         .permute(0, 3, 1, 2).float())]
        dets = detect(heads, 640, conf=0.30)
        print(f"\nfloat@640 on {rec['file']}: {len(dets)} dets "
              f"({len(rec['boxes'])} pre-label boxes)")
        self.assertGreaterEqual(len(dets), 5)

    @unittest.skipIf(torch is None, "torch not in this venv")
    def test_int8_tflite_agrees_with_float(self):
        """The exact tensors a board emits: int8 tflite -> dequant ->
        detect, vs the float torch model on the same NATIVE-px 256 crop
        (see _densest_still for why not a whole-frame letterbox)."""
        tfl = EXPORT / "stage1_v2_256_int8.tflite"
        if not tfl.exists():
            self.skipTest(f"{tfl} not on this machine")
        try:
            import tensorflow as tf
        except ImportError:
            self.skipTest("tensorflow not in this venv")
        import cv2
        from model import RawExport, build_model

        rec = self._densest_still()
        img = cv2.imread(str(STILLS / rec["file"]))
        h0, w0 = img.shape[:2]
        y, x0 = (h0 - 256) // 2, (w0 - 256) // 2
        rgb = np.ascontiguousarray(
            img[y:y + 256, x0:x0 + 256, ::-1])

        it = tf.lite.Interpreter(model_path=str(tfl))
        it.allocate_tensors()
        inp = it.get_input_details()[0]
        x = rgb[None].astype(np.float32)
        if inp["dtype"] in (np.int8, np.uint8):  # full-integer io
            sc, zp = inp["quantization"]
            info = np.iinfo(inp["dtype"])
            x = np.clip(np.round(x / sc + zp),
                        info.min, info.max).astype(inp["dtype"])
        it.set_tensor(inp["index"], x)
        it.invoke()
        heads = []
        for od in it.get_output_details():
            t = it.get_tensor(od["index"])
            if od["dtype"] in (np.int8, np.uint8):
                t = dequantize(t, *od["quantization"])
            heads.append(t)
        int8_dets = detect(heads, 256, conf=0.30)

        ckpt = (Path.home()
                / "nereus_ml/runs/stage1_yolox/stage1_v2/ema.pt")
        model = build_model(num_classes=1, arch="yolox-nano", stem="conv")
        ck = torch.load(str(ckpt), map_location="cpu")
        model.load_state_dict(ck.get("model", ck))
        raw = RawExport(model).eval()
        with torch.no_grad():
            f_heads = [o.numpy() for o in
                       raw(torch.from_numpy(
                           np.ascontiguousarray(rgb[None]))
                           .permute(0, 3, 1, 2).float())]
        float_dets = detect(f_heads, 256, conf=0.30)

        self.assertTrue(len(int8_dets),
                        "int8 model found nothing on a dense urchin still")

        def iou(a, b):
            iw = max(0, min(a[2], b[2]) - max(a[0], b[0]))
            ih = max(0, min(a[3], b[3]) - max(a[1], b[1]))
            inter = iw * ih
            ua = ((a[2] - a[0]) * (a[3] - a[1])
                  + (b[2] - b[0]) * (b[3] - b[1]) - inter)
            return inter / max(ua, 1e-9)

        matched = sum(any(iou(d, f) >= 0.5 for f in float_dets)
                      for d in int8_dets)
        frac = matched / len(int8_dets)
        print(f"\nint8 dets={len(int8_dets)} float dets={len(float_dets)} "
              f"matched={matched} ({frac:.0%})")
        # int8@256 is a weaker model (0.202 vs 0.654 mAP50) — demand
        # agreement, not identity
        self.assertGreaterEqual(frac, 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
