#!/usr/bin/env python3
"""S28 bite 2 — the stacking compare tool (the sprint's deliverable).

Turns a locked BAYER burst into a self-contained HTML report: single vs
mean vs median vs sigma-clip, side by side with zoom crops, and a NOISE
LADDER that proves the ~sqrt(N) win honestly.

Noise is measured the group-means way: split the burst into disjoint
groups of k, merge each, and take the per-pixel std ACROSS the merges.
Fixed scene structure (print texture, lighting, lens) is identical in
every group and cancels, so what is left is PURE temporal noise — the
thing stacking removes. (A spatial std on a "uniform" patch hides the
win behind that fixed structure; this does not.) Scene-independent — no
reference card needed.

    python3 pi/s28/s28_compare.py --run ~/s28_runs/stack_card1 \
        --stage stack_bayer_vga

Expected: mean/sigma-clip noise falls ~sqrt(N) (k=8 -> ~2.8x); median a
touch less; the stacked JPEG is smaller at equal quality.
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

from s28_stack import (_green_plane, demosaic, jpeg_size,          # noqa
                       load_burst, merge_mean, merge_median,
                       merge_sigma_clip, noise_ladder, temporal_sigma)
from s28_session import flicker_verdict                            # noqa


def burst_flicker(stack):
    """Is the burst clean sensor noise (stackable) or flicker-contaminated
    (a light source wobbling frame-to-frame — stacking can't remove it)?
    Green plane; frame-mean sigma vs the independent-pixel expectation."""
    gp = np.stack([_green_plane(f) for f in stack])
    means = [float(f.mean()) for f in gp]
    sig_t = temporal_sigma(stack)
    return flicker_verdict(means, sig_t, gp[0].size)

MERGE_FNS = {"mean": merge_mean, "median": merge_median,
             "sigma_clip": merge_sigma_clip}


def gray_world(rgb):
    """Cheap gray-world white balance for VIEWING (raw BAYER demosaic has
    2x green sites and no WB, so it comes out green). View-only — the
    noise metric is on the green plane and WB-independent."""
    f = rgb.astype(np.float64)
    m = f.reshape(-1, 3).mean(0)
    return np.clip(f * (m.mean() / np.maximum(m, 1.0)), 0, 255).astype(
        np.uint8)


def png_data_uri(rgb, scale=1.0):
    im = Image.fromarray(rgb)
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)))
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", default="stack_bayer_vga")
    ap.add_argument("--out", default=None)
    ap.add_argument("--jpeg-q", type=int, default=50)
    args = ap.parse_args()

    run = os.path.expanduser(args.run)
    stack, rows = load_burst(run, args.stage)
    n = len(stack)
    rj = os.path.join(run, "run.json")
    scene = (json.load(open(rj)).get("args", {}).get("scene", "?")
             if os.path.isfile(rj) else "?")

    # ----- visual panels: single vs the full-N merges -----
    panels_merges = {"single": stack[0], "mean_%d" % n: merge_mean(stack),
                     "median_%d" % n: merge_median(stack),
                     "sigma_clip_%d" % n: merge_sigma_clip(stack)}
    order = list(panels_merges)

    # ----- noise ladder (the sqrt(N) proof) -----
    ks = [k for k in (1, 2, 4, 8, 16) if n // k >= 2]
    ladders = {m: noise_ladder(stack, fn, ks) for m, fn in MERGE_FNS.items()}
    base = ladders["mean"][1]                # k=1 = single-frame noise

    # ----- jpeg size at equal quality -----
    sizes = {mk: jpeg_size(demosaic(v), args.jpeg_q)
             for mk, v in panels_merges.items()}

    # zoom crop over a detailed area (upper-left grayscale strip / text)
    h, w = stack.shape[1:]
    cx0, cy0 = int(w * 0.24), int(h * 0.28)
    crop = (cx0, cy0, cx0 + 130, cy0 + 95)

    def panel_html(mk):
        rgb = gray_world(demosaic(panels_merges[mk]))
        cr = rgb[crop[1]:crop[3], crop[0]:crop[2]]
        return ("<div class=p><h4>%s</h4><img src='%s'>"
                "<div class=z><img src='%s'></div>"
                "<div class=cap>%.1f KB @ q%d</div></div>"
                % (mk, png_data_uri(rgb, 0.6), png_data_uri(cr, 3.0),
                   sizes[mk] / 1024, args.jpeg_q))

    pan_html = "".join(panel_html(mk) for mk in order)

    # ladder table: rows = merge mode, cols = k, + sqrt(N) ideal
    head = "".join("<th>k=%d</th>" % k for k in ks)
    lad_rows = ""
    for m in ("mean", "sigma_clip", "median"):
        cells = "".join("<td>%.3f<br><span class=r>%.2fx</span></td>"
                        % (ladders[m][k], base / ladders[m][k]) for k in ks)
        lad_rows += "<tr><td>%s</td>%s</tr>" % (m, cells)
    ideal = "".join("<td>%.2fx</td>" % (k ** 0.5) for k in ks)
    sizerow = "".join("<td>%.1f KB</td>" % (sizes[mk] / 1024)
                      for mk in order)
    sizehead = "".join("<th>%s</th>" % mk for mk in order)

    fv, fdet = burst_flicker(stack)
    flick_str = fv + (" (%.0fx over noise floor)" % fdet["ratio"]
                      if "ratio" in fdet else "")

    html = """<!doctype html><meta charset=utf-8><title>S28 stacking compare</title>
<style>body{{font:13px system-ui;margin:16px;background:#111;color:#eee}}
h2{{margin:0 0 4px}} .sub{{color:#999;margin-bottom:12px;max-width:900px}}
.row{{display:flex;flex-wrap:wrap;gap:10px}}
.p{{background:#1c1c1c;padding:6px;border-radius:6px}}
.p img{{display:block;border-radius:3px}} .p h4{{margin:2px 0;font-size:12px}}
.z{{margin-top:4px;image-rendering:pixelated}} .z img{{width:390px}}
.cap{{color:#999;font-size:11px;margin-top:2px}}
table{{border-collapse:collapse;margin:14px 0}}
td,th{{border:1px solid #333;padding:4px 9px;text-align:right}}
td:first-child,th:first-child{{text-align:left}}
.r{{color:#5c8;font-size:11px}} .ideal td{{color:#89f}}</style>
<h2>S28 frame-stacking compare</h2>
<div class=sub>{scene} &middot; {n} locked BAYER frames, VGA &middot;
single-frame temporal noise (per-pixel &sigma;): <b>{tsig:.2f} counts</b>
&middot; flicker check: <b>{flick}</b>. Zoom crops are 3&times;
nearest-neighbour so the grain is visible.</div>
<div class=row>{panels}</div>

<h3>Noise ladder &mdash; temporal &sigma; (green plane) of a k-frame merge</h3>
<table><tr><th>merge</th>{head}</tr>{lad}
<tr class=ideal><td>&radic;N ideal</td>{ideal}</tr></table>

<h3>File size at equal quality</h3>
<table><tr>{sizehead}</tr><tr>{sizerow}</tr></table>

<p class=sub>Noise is measured by disjoint group-means so FIXED structure
(print/lighting/lens) cancels &mdash; the number is pure temporal noise.
mean and sigma-clip track the &radic;N ideal; median trades a little SNR
for rejecting transient occluders (drifting particulate, a fish). Denoised
frames also encode smaller at equal quality. NOTE: absolute color here is
uncorrected (the AE3 needs a CCM &mdash; a separate finding); this tool
measures NOISE, which is exactly what stacking buys.</p>
""".format(scene=scene, n=n, tsig=temporal_sigma(stack), panels=pan_html,
           head=head, lad=lad_rows, ideal=ideal, sizehead=sizehead,
           sizerow=sizerow, flick=flick_str)

    out = args.out or os.path.join(run, "compare.html")
    open(out, "w").write(html)
    json.dump({"scene": scene, "n": n,
               "temporal_sigma": temporal_sigma(stack),
               "flicker": {"verdict": fv, **fdet},
               "ladders": ladders, "jpeg_bytes": sizes},
              open(os.path.splitext(out)[0] + ".json", "w"), indent=1)

    print("== S28 stacking compare: %s (%d frames) ==" % (args.stage, n))
    print("  single-frame temporal noise: %.2f counts"
          % temporal_sigma(stack))
    print("  flicker check: %s  %s" % (fv, fdet))
    if fv == "ALIASED":
        print("    ^ the light is wobbling frame-to-frame (flicker or a"
              " changing source) — stacking CANNOT remove this; use a"
              " constant/DC light or a longer exposure to average it out.")
    print("  noise ladder (green-plane temporal sigma, x = vs k=1):")
    print("    %-11s %s" % ("k=", "  ".join("%6d" % k for k in ks)))
    for m in ("mean", "sigma_clip", "median"):
        print("    %-11s %s" % (m, "  ".join(
            "%6.3f" % ladders[m][k] for k in ks)))
    print("    %-11s %s" % ("sqrt(N)", "  ".join(
        "%5.2fx" % (k ** 0.5) for k in ks)))
    print("    %-11s %s" % ("mean redux", "  ".join(
        "%5.2fx" % (base / ladders["mean"][k]) for k in ks)))
    print("  jpeg @ q%d: %s" % (args.jpeg_q, ", ".join(
        "%s %.1fKB" % (mk, sizes[mk] / 1024) for mk in order)))
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
