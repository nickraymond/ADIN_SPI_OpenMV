#!/usr/bin/env python3
"""S28 bite 2 — the stacking compare tool (the sprint's deliverable).

Turns locked bursts into a self-contained HTML report: for each board
(AE3 and/or N6), single vs mean vs median vs sigma-clip side by side with
zoom crops, a sqrt(N) NOISE LADDER, JPEG size at equal quality, and a
FLICKER check. Handles BAYER (the AE3's raw linear domain) and RGB565
(the deployed path, and the only format both boards share — the N6's
stock firmware can't emit BAYER).

Noise is measured the group-means way: split the burst into disjoint
groups of k, merge each, and take the per-pixel std ACROSS the merges.
Fixed scene structure (print/lighting/lens) cancels, leaving PURE
temporal noise — the thing stacking removes. Scene-independent.

    # both boards (each --board LABEL=run_dir):
    python3 pi/s28/s28_compare.py \
        --board AE3=~/s28_runs/ae3 --board N6=~/s28_runs/n6
    # or one board (back-compat):
    python3 pi/s28/s28_compare.py --run ~/s28_runs/ae3 --label AE3
"""
import argparse
import base64
import io
import json
import os
import sys

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from s28_stack import (green_plane, jpeg_size, load_burst, merge_mean,  # noqa
                       merge_median, merge_sigma_clip, noise_ladder,
                       temporal_sigma, to_view)
from s28_session import flicker_verdict                                # noqa

MERGE_FNS = {"mean": merge_mean, "median": merge_median,
             "sigma_clip": merge_sigma_clip}


def gray_world(rgb):
    f = rgb.astype(np.float64)
    m = f.reshape(-1, 3).mean(0)
    return np.clip(f * (m.mean() / np.maximum(m, 1.0)), 0, 255).astype(
        np.uint8)


def burst_flicker(stack, pixformat):
    """SAFE (clean sensor noise, stackable) vs ALIASED (a light source
    wobbling frame-to-frame — stacking can't remove it)."""
    gp = np.stack([green_plane(f, pixformat) for f in stack])
    means = [float(f.mean()) for f in gp]
    return flicker_verdict(means, temporal_sigma(stack), gp[0].size)


def png_data_uri(rgb, scale=1.0):
    im = Image.fromarray(rgb)
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)))
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def analyze_board(label, run_dir, stage):
    stack, rows, pf = load_burst(os.path.expanduser(run_dir), stage)
    n = len(stack)
    merges = {"single": stack[0], "mean_%d" % n: merge_mean(stack),
              "median_%d" % n: merge_median(stack),
              "sigma_clip_%d" % n: merge_sigma_clip(stack)}
    order = list(merges)
    ks = [k for k in (1, 2, 4, 8, 16) if n // k >= 2]
    ladders = {m: noise_ladder(stack, fn, pf, ks)
               for m, fn in MERGE_FNS.items()}
    sizes = {mk: jpeg_size(to_view(v, pf)) for mk, v in merges.items()}
    fv, fdet = burst_flicker(stack, pf)

    # views (RGB565 shown as the real deployed output; BAYER gets a
    # gray-world WB since raw has no white balance)
    def view(frame):
        rgb = to_view(frame, pf)
        return gray_world(rgb) if pf != "RGB565" else rgb

    h, w = stack.shape[1:3]
    cx0, cy0 = int(w * 0.24), int(h * 0.28)
    crop = (cx0, cy0, cx0 + 130, cy0 + 95)
    panels = []
    for mk in order:
        rgb = view(merges[mk])
        cr = rgb[crop[1]:crop[3], crop[0]:crop[2]]
        panels.append((mk, png_data_uri(rgb, 0.55),
                       png_data_uri(cr, 3.0), sizes[mk]))
    return {"label": label, "pixformat": pf, "n": n, "ks": ks,
            "ladders": ladders, "sizes": sizes,
            "temporal_sigma": temporal_sigma(stack),
            "flicker": fv, "flicker_detail": fdet, "order": order,
            "panels": panels}


def _redux(base, v):
    """Reduction factor as a string; '—' when there is no noise to reduce
    (base==0: a flat/quantized channel — stacking has nothing to do)."""
    if base <= 0:
        return "—"
    return "%.2fx" % (base / v) if v > 0 else "∞"


def board_section(b):
    base = b["ladders"]["mean"][1]
    flick = b["flicker"] + (" (%.0fx over floor)" % b["flicker_detail"]["ratio"]
                            if "ratio" in b["flicker_detail"] else "")
    pans = "".join(
        "<div class=p><h4>%s</h4><img src='%s'><div class=z><img src='%s'>"
        "</div><div class=cap>%.1f KB</div></div>"
        % (mk, full, cr, sz / 1024) for mk, full, cr, sz in b["panels"])
    head = "".join("<th>k=%d</th>" % k for k in b["ks"])
    lad = ""
    for m in ("mean", "sigma_clip", "median"):
        cells = "".join("<td>%.3f<br><span class=r>%s</span></td>"
                        % (b["ladders"][m][k], _redux(base,
                                                      b["ladders"][m][k]))
                        for k in b["ks"])
        lad += "<tr><td>%s</td>%s</tr>" % (m, cells)
    ideal = "".join("<td>%.2fx</td>" % (k ** 0.5) for k in b["ks"])
    shead = "".join("<th>%s</th>" % mk for mk in b["order"])
    srow = "".join("<td>%.1f KB</td>" % (b["sizes"][mk] / 1024)
                   for mk in b["order"])
    flag = "ok" if b["flicker"] == "SAFE" else "warn"
    return """<div class=board><h3>{label} &middot; {pf} &middot; {n} frames
&middot; single-frame &sigma; {tsig:.2f} &middot;
<span class={flag}>flicker: {flick}</span></h3>
<div class=row>{pans}</div>
<table><tr><th>merge</th>{head}</tr>{lad}
<tr class=ideal><td>&radic;N ideal</td>{ideal}</tr></table>
<table><tr>{shead}</tr><tr>{srow}</tr></table></div>""".format(
        label=b["label"], pf=b["pixformat"], n=b["n"],
        tsig=b["temporal_sigma"], flag=flag, flick=flick, pans=pans,
        head=head, lad=lad, ideal=ideal, shead=shead, srow=srow)


def render(boards, out):
    secs = "".join(board_section(b) for b in boards)
    html = """<!doctype html><meta charset=utf-8><title>S28 stacking compare</title>
<style>body{{font:13px system-ui;margin:16px;background:#111;color:#eee}}
h2{{margin:0 0 4px}} .sub{{color:#999;margin-bottom:8px;max-width:940px}}
.board{{border-top:1px solid #333;padding-top:8px;margin-top:14px}}
.board h3{{margin:2px 0 8px}}
.row{{display:flex;flex-wrap:wrap;gap:9px}}
.p{{background:#1c1c1c;padding:6px;border-radius:6px}}
.p img{{display:block;border-radius:3px}} .p h4{{margin:2px 0;font-size:12px}}
.z{{margin-top:4px;image-rendering:pixelated}} .z img{{width:360px}}
.cap{{color:#999;font-size:11px;margin-top:2px}}
table{{border-collapse:collapse;margin:10px 12px 4px 0;display:inline-table;vertical-align:top}}
td,th{{border:1px solid #333;padding:4px 9px;text-align:right}}
td:first-child,th:first-child{{text-align:left}}
.r{{color:#5c8;font-size:11px}} .ideal td{{color:#89f}}
.ok{{color:#5c8}} .warn{{color:#fb6}}</style>
<h2>S28 frame-stacking compare</h2>
<div class=sub>Single vs mean/median/sigma-clip per board, with the
&radic;N noise ladder (temporal &sigma;, green channel; disjoint
group-means so fixed structure cancels) and a flicker check. Zoom crops
are 3&times; nearest-neighbour. RGB565 = the deployed path (both boards);
BAYER = the AE3 raw domain. Color is uncorrected (the AE3 needs a CCM —
separate finding); this tool measures NOISE.</div>
{secs}
<p class=sub>flicker <span class=warn>ALIASED</span> = the light wobbled
frame-to-frame (LED/fluorescent flicker, or a changing source) —
stacking CANNOT remove that; use a constant/DC light for a clean read.
mean and sigma-clip track &radic;N; median trades a little for rejecting
transient occluders.</p>""".format(secs=secs)
    open(out, "w").write(html)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", action="append", default=[],
                    help="LABEL=run_dir (repeatable)")
    ap.add_argument("--run", help="single-board run dir (back-compat)")
    ap.add_argument("--label", default="cam")
    ap.add_argument("--stage", default="stack_rgb565_vga")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    specs = []
    for bstr in args.board:
        lbl, _, d = bstr.partition("=")
        specs.append((lbl, d))
    if args.run:
        specs.append((args.label, args.run))
    if not specs:
        raise SystemExit("FAIL: give --board LABEL=dir (repeatable) or "
                         "--run dir")

    boards = []
    for lbl, d in specs:
        try:
            boards.append(analyze_board(lbl, d, args.stage))
        except Exception as e:
            print("WARN: %s (%s) skipped: %s" % (lbl, d, e))
    if not boards:
        raise SystemExit("FAIL: no board bursts analysed")

    render(boards, os.path.expanduser(args.out))
    json.dump({b["label"]: {"pixformat": b["pixformat"], "n": b["n"],
                            "temporal_sigma": b["temporal_sigma"],
                            "flicker": b["flicker"], "ladders": b["ladders"]}
               for b in boards},
              open(os.path.splitext(os.path.expanduser(args.out))[0]
                   + ".json", "w"), indent=1)

    print("== S28 stacking compare ==")
    for b in boards:
        base = b["ladders"]["mean"][1]
        print("  %-4s %-7s %d frames  single-noise %.2f  flicker %s"
              % (b["label"], b["pixformat"], b["n"], b["temporal_sigma"],
                 b["flicker"]))
        print("       mean redux: %s" % "  ".join(
            "k%d %s" % (k, _redux(base, b["ladders"]["mean"][k]))
            for k in b["ks"]))
    print("wrote %s" % os.path.expanduser(args.out))


if __name__ == "__main__":
    main()
