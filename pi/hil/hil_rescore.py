#!/usr/bin/env python3
"""Re-score a saved closed-loop run dir at arbitrary IOU / px-floor.

Desk tool, zero board contact (S8 bite E8). Reads <run>/rows.jsonl
(the per-frame dets_cam), the run's own H_<board>.npy, the calib
JPEG's dimensions (= the camera geometry of THAT run), and the stills
labels; rebuilds each frame's GT through the run's homography and
re-runs match_frame — the same function that scored the run.

The homography discriminator (E8 → E11's instrument):

    python3 pi/hil/hil_rescore.py ~/hil_runs/<run> --iou 0.30 0.20

A large recall jump at looser IOU on ONE board is the alignment
signature: matches are being lost to a systematic mapping error (lens
distortion), not to the detector. Re-run after E11's distortion-aware
calibration for the before/after.
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_harness import match_frame                    # noqa: E402


def load_labels(stills_dir):
    path = os.path.join(os.path.expanduser(stills_dir), "labels.jsonl")
    by_name = {}
    with open(path) as fh:
        for ln in fh:
            r = json.loads(ln)
            by_name[r["file"].split("/")[-1]] = r["boxes"]
    return by_name


def rescore(run_dir, stills_dir, ious, floor):
    """-> {(board, iou): {"gt","match","false","frames"}}, boards"""
    run_dir = os.path.expanduser(run_dir)
    labels = load_labels(stills_dir)
    H, cam_wh = {}, {}
    for f in os.listdir(run_dir):
        if f.startswith("H_") and f.endswith(".npy"):
            lb = f[2:-4]
            H[lb] = np.load(os.path.join(run_dir, f))
            with Image.open(os.path.join(run_dir,
                                         "calib_%s.jpg" % lb)) as im:
                cam_wh[lb] = im.size
    if not H:
        raise SystemExit("FAIL: no H_<board>.npy in %s — did the run "
                         "reach calibration?" % run_dir)
    out = {}
    n_skipped = 0
    with open(os.path.join(run_dir, "rows.jsonl")) as fh:
        for ln in fh:
            row = json.loads(ln)
            lb = row["board"]
            if lb not in H:
                continue
            boxes = labels.get(row["still"])
            if boxes is None:
                n_skipped += 1
                continue
            dets = np.array(row.get("dets_cam") or np.zeros((0, 5)),
                            np.float64).reshape(-1, 5)
            w, h = cam_wh[lb]
            for iou in ious:
                mf = match_frame(dets, boxes, H[lb], w, h,
                                 min_gt_px=floor, iou_thr=iou)
                key = (lb, iou)
                t = out.setdefault(key, {"gt": 0, "match": 0,
                                         "false": 0, "frames": 0})
                if floor > 0:
                    t["gt"] += mf["n_gt_floor"]
                    t["match"] += mf["n_match_floor"]
                    t["false"] += mf["n_false_floor"]
                else:
                    t["gt"] += len(mf["boxes"])
                    t["match"] += mf["n_match"]
                    t["false"] += int(len(mf["dets"])) - mf["n_match"]
                t["frames"] += 1
    if n_skipped:
        print("NOTE: %d rows referenced stills missing from %s — "
              "wrong --stills-dir?" % (n_skipped, stills_dir))
    return out, sorted(H)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--stills-dir",
                    default=os.path.expanduser("~/hil_monterey/stills"))
    ap.add_argument("--iou", type=float, nargs="+", default=[0.30, 0.20])
    ap.add_argument("--floor", type=float, default=30.0,
                    help="GT px floor (0 = raw counts)")
    args = ap.parse_args()

    out, boards = rescore(args.run_dir, args.stills_dir, args.iou,
                          args.floor)
    print("%s  (floor %gpx)" % (args.run_dir, args.floor))
    print("  %-6s %-6s %8s %8s %6s %6s %6s"
          % ("board", "IOU", "recall", "prec", "GT", "match", "false"))
    for lb in boards:
        for iou in args.iou:
            t = out.get((lb, iou))
            if not t:
                continue
            rec = t["match"] / t["gt"] if t["gt"] else 0.0
            den = t["match"] + t["false"]
            prc = t["match"] / den if den else 0.0
            print("  %-6s %-6g %8.3f %8.3f %6d %6d %6d"
                  % (lb, iou, rec, prc, t["gt"], t["match"], t["false"]))
    if len(args.iou) >= 2:
        print("  reading: a big recall jump at the looser IOU on ONE "
              "board = alignment losses (the E11 signature), not "
              "detector losses.")


if __name__ == "__main__":
    main()
