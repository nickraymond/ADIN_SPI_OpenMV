#!/usr/bin/env python3
"""Training-curve chart for the stage-1 runs (S8 bite E).

Two stacked panels on a shared epoch axis (different scales never share
one axis): rung-A mAP50 checkpoints on top, epoch-mean training loss
below. Regenerate any time; reads the runs' loss.log files plus the
maintained rung_a_scores.json.

  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/plot_curves.py \
      [--out curves.png] [runs ...]
"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RUNS = Path.home() / "nereus_ml" / "runs" / "stage1_yolox"
# categorical slots 1-3 of the dataviz reference palette, fixed order
COLORS = {"stage1_v1": "#2a78d6", "stage1_v2": "#eb6834",
          "stage1_tiny_v1": "#1baf7a"}
INK, MUTED = "#1a1a19", "#6b6a63"
BASELINES = [(0.243, "yolo11n 0.243"), (0.351, "yolo11x 0.351")]


def epoch_mean_loss(run):
    sums = defaultdict(float)
    counts = defaultdict(int)
    log = RUNS / run / "loss.log"
    if not log.exists():
        return [], []
    for line in open(log):
        m = re.match(r"e(\d+) .*? loss=([0-9.]+)", line)
        if m:
            e, v = int(m.group(1)), float(m.group(2))
            sums[e] += v
            counts[e] += 1
    epochs = sorted(sums)
    return epochs, [sums[e] / counts[e] for e in epochs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*", default=["stage1_v1", "stage1_v2"])
    ap.add_argument("--out", default=str(RUNS / "curves.png"))
    args = ap.parse_args()
    runs = args.runs or ["stage1_v1", "stage1_v2"]

    scores = json.load(open(RUNS / "rung_a_scores.json"))
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(8.5, 6.5), sharex=True, dpi=150,
        gridspec_kw={"height_ratios": [3, 2], "hspace": 0.12})
    fig.patch.set_facecolor("white")

    for ax in (ax1, ax2):
        ax.set_facecolor("white")
        ax.grid(True, color="#e8e7e0", linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("#c9c8bf")
        ax.tick_params(colors=MUTED, labelsize=9)

    # -- top: rung-A mAP50 checkpoints ---------------------------------
    for y, label in BASELINES:
        ax1.axhline(y, color="#c9c8bf", linewidth=1, linestyle=(0, (4, 3)))
        ax1.annotate(label, xy=(1.0, y), xycoords=("axes fraction", "data"),
                     xytext=(4, 0), textcoords="offset points",
                     va="center", fontsize=8, color=MUTED)
    for run in runs:
        pts = scores.get(run, [])
        if not pts:
            continue
        xs, ys = zip(*pts)
        c = COLORS.get(run, INK)
        ax1.plot(xs, ys, color=c, linewidth=2, marker="o", markersize=5)
        ax1.annotate(f"{run.replace('stage1_', '')}  {ys[-1]:.3f}",
                     xy=(xs[-1], ys[-1]), xytext=(6, 4),
                     textcoords="offset points", fontsize=9,
                     color=INK, fontweight="bold")
    ax1.set_ylabel("rung-A mAP50", fontsize=10, color=INK)
    ax1.set_ylim(0, 0.75)
    ax1.set_title("Stage-1 training: rung-A score and training loss by epoch",
                  fontsize=11, color=INK, loc="left", pad=10)

    # -- bottom: epoch-mean training loss ------------------------------
    for run in runs:
        xs, ys = epoch_mean_loss(run)
        if not xs:
            continue
        ax2.plot(xs, ys, color=COLORS.get(run, INK), linewidth=2)
        ax2.annotate(run.replace("stage1_", ""), xy=(xs[-1], ys[-1]),
                     xytext=(6, 0), textcoords="offset points",
                     fontsize=9, color=INK)
    ax2.set_ylabel("train loss (epoch mean)", fontsize=10, color=INK)
    ax2.set_xlabel("epoch", fontsize=10, color=INK)
    ax2.set_ylim(bottom=0)

    handles = [plt.Line2D([], [], color=COLORS[r], linewidth=2,
                          label={"stage1_v1": "v1 nano — 40 ep, no mosaic",
                                 "stage1_v2": "v2 nano — 120 ep, mosaic (final 0.654)",
                                 "stage1_tiny_v1": "tiny — v2 recipe (running)"}
                          .get(r, r))
               for r in runs if r in COLORS]
    ax1.legend(handles=handles, loc="lower right", fontsize=8.5,
               frameon=False, labelcolor=INK)

    fig.savefig(args.out, bbox_inches="tight")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
