#!/usr/bin/env python3
"""Objectness heat maps over the source stills (S8 E4 follow-on, Nick's ask).

Reads a closed-loop run's cells.jsonl (raw sparse head cells per scored
frame), maps every candidate cell's decoded CENTER from tile → camera px
→ still fractions through that board's saved homography (H_<board>.npy),
deposits score = obj·cls into a coarse grid per (board, phase, still),
and renders the accumulated heat over the still. Where a model "looks"
vs where the urchins are — false-positive clusters and blind regions are
visible at a glance in a way count tables can't show.

  python3 pi/hil/hil_heatmap.py ~/hil_runs/<run> \
      [--stills-dir ~/hil_monterey/stills] [--conf-floor 0.05]

Outputs under <run>/heatmaps/: <board>_<phase>_<still>.jpg per still and
an index.html gallery (self-contained, Pi-served or scp-able). Heat is
normalized per (board, phase) at the p99 deposit so stills within a leg
are comparable; the caption carries the scale. PIL + numpy only.
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_harness import load_cam_maps               # noqa: E402

STILL_W, STILL_H = 1920, 1080
IN_W = 256
GRID_W, GRID_H = 240, 135          # deposit grid (8 px still-cells)


def heat_lut():
    """Black→deep violet→red→amber→white, 256 RGB rows (urchin-ish)."""
    stops = [(0.00, (0, 0, 0)), (0.25, (58, 20, 90)),
             (0.55, (168, 44, 66)), (0.80, (232, 148, 42)),
             (1.00, (255, 244, 214))]
    lut = np.zeros((256, 3), np.float32)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        i0, i1 = int(p0 * 255), int(p1 * 255)
        t = np.linspace(0, 1, i1 - i0 + 1)[:, None]
        lut[i0:i1 + 1] = np.array(c0) * (1 - t) + np.array(c1) * t
    return lut.astype(np.uint8)


def accumulate(rec, M, grid, conf_floor):
    """Deposit one frame's cells into `grid` (GRID_H×GRID_W, float).
    M = the board's CamMap (camera px -> still fractions, k1-aware)."""
    for tile_xy, cells in zip(rec["tiles"], rec["cells"]):
        tx0, ty0 = tile_xy
        for hh, y, x, tx, ty, _tw, _th, ob, cl in cells:
            score = ob * cl
            if score < conf_floor:
                continue
            stride = IN_W / hh
            # decode_np semantics: center = (t + grid_idx) * stride
            cx = tx0 + (tx + x) * stride
            cy = ty0 + (ty + y) * stride
            fx, fy = M.cam_to_frac(np.array([[cx, cy]]))[0]
            gx, gy = int(fx * GRID_W), int(fy * GRID_H)
            if 0 <= gx < GRID_W and 0 <= gy < GRID_H:
                grid[gy, gx] += score


def render(still_path, grid, norm, out_path, lut, fov_poly=None):
    img = Image.open(still_path).convert("RGB").resize((960, 540))
    base = (np.asarray(img, np.float32) * 0.45)     # dim the still
    h = np.clip(grid / norm, 0, 1) if norm > 0 else grid * 0
    h = np.asarray(Image.fromarray((h * 255).astype(np.uint8))
                   .resize((960, 540), Image.BILINEAR), np.float32) / 255
    heat = lut[(h * 255).astype(np.uint8)].astype(np.float32)
    a = (h ** 0.7)[..., None]                       # alpha from intensity
    out = np.clip(base * (1 - a) + heat * a, 0, 255).astype(np.uint8)
    im = Image.fromarray(out)
    if fov_poly is not None:
        # the camera sees only part of the still — without this line a
        # cold region outside the FOV reads as model blindness
        d = ImageDraw.Draw(im)
        d.polygon([(x * 960, y * 540) for x, y in fov_poly],
                  outline=(0, 180, 255), width=2)
    im.save(out_path, quality=85)


def fov_polygon(M, cam_w, cam_h):
    pts = np.array([[0, 0], [cam_w, 0], [cam_w, cam_h], [0, cam_h]],
                   np.float64)
    p = M.cam_to_frac(pts)
    return [(float(x), float(y)) for x, y in p]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--stills-dir",
                    default=os.path.expanduser("~/hil_monterey/stills"))
    ap.add_argument("--conf-floor", type=float, default=0.05,
                    help="min obj*cls for a cell to deposit (below this "
                         "is int8 noise, not attention)")
    args = ap.parse_args()
    run = os.path.expanduser(args.run_dir)
    cells_path = os.path.join(run, "cells.jsonl")
    if not os.path.exists(cells_path):
        raise SystemExit(f"FAIL: no cells.jsonl under {run} — heat maps "
                         f"need a closed-loop run from the cells-saving "
                         f"harness (re-run the leg)")
    maps = load_cam_maps(run)      # k1-aware json, bare-H npy fallback
    warned = set()
    fovs = {}                      # board -> still-fraction FOV polygon
    grids = {}                     # (board, phase, still) -> grid
    n_frames = {}
    for ln in open(cells_path):
        rec = json.loads(ln)
        b = rec["board"]
        if b not in maps:
            if b not in warned:
                warned.add(b)
                print(f"WARN: no calibration for {b} — skipping its cells")
            continue
        if b not in fovs:
            fovs[b] = fov_polygon(maps[b], rec["cam_w"], rec["cam_h"])
        key = (b, rec["phase"], rec["still"])
        grids.setdefault(key, np.zeros((GRID_H, GRID_W), np.float32))
        n_frames[key] = n_frames.get(key, 0) + 1
        accumulate(rec, maps[b], grids[key], args.conf_floor)

    out_dir = os.path.join(run, "heatmaps")
    os.makedirs(out_dir, exist_ok=True)
    lut = heat_lut()
    # per-(board,phase) p99 normalization: stills within a leg share a
    # scale, so a hot still IS hotter, not just auto-scaled
    norms = {}
    for (b, ph, _s), g in grids.items():
        norms.setdefault((b, ph), []).append(g)
    norms = {k: max(1e-6, float(np.percentile(
        np.concatenate([g.ravel() for g in v]), 99.5)))
        for k, v in norms.items()}
    entries = []
    for (b, ph, still), g in sorted(grids.items()):
        name = f"{b}_{ph}_{os.path.splitext(still)[0]}.jpg"
        render(os.path.join(args.stills_dir, "frames", still),
               g / max(1, n_frames[(b, ph, still)]), norms[(b, ph)],
               os.path.join(out_dir, name), lut, fov_poly=fovs.get(b))
        entries.append((b, ph, still, name))
    print(f"{len(entries)} heat maps -> {out_dir}")

    rows = "\n".join(
        f'<figure><img src="{n}" loading="lazy">'
        f"<figcaption>{b} · {ph} · {s}</figcaption></figure>"
        for b, ph, s, n in entries)
    open(os.path.join(out_dir, "index.html"), "w").write(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>HIL heat maps</title><style>
 body{{background:#14171a;color:#dde3e8;font:14px system-ui;margin:1rem}}
 .g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(460px,1fr));
    gap:10px}}
 figure{{margin:0}} img{{width:100%;border-radius:4px}}
 figcaption{{color:#8fa3b3;font-size:12px;padding:2px 0 6px}}
</style></head><body>
<h2>Objectness heat maps — where each model looks</h2>
<p style="color:#8fa3b3">score = obj·cls per candidate cell, ≥ {args.conf_floor};
heat normalized per board×model leg (p99.5), so brightness is comparable
across stills within a leg. Source: cells.jsonl of {os.path.basename(run)}.</p>
<div class="g">{rows}</div></body></html>""")
    print(f"gallery: {os.path.join(out_dir, 'index.html')}")


if __name__ == "__main__":
    main()
