#!/usr/bin/env python3
"""S28 bite 3 — red-channel HDR bracket merge + report.

The underwater-specific trick from the kickoff notes: the red channel is
photon-starved, so a LONG (+2/+3 EV) shutter-only exposure collects far
more red light. Merge channel-wise — **green/blue from the NORMAL frame,
RED from the LONG frame divided by the exposure ratio** — and the red
SNR improves by up to the exposure ratio (read-noise-limited, the
underwater regime), a bigger win than stacking's √N. Green/blue clip in
the long frame; that is expected and irrelevant (we don't use them).

Works on a BAYER bracket (bite 3 capture: `--plan bracket` = NORMAL +
+2/+3 EV, shutter only, N frames each). BAYER because the red÷ratio math
must be LINEAR — RGB565 is gamma-encoded and the AE3 crushes it.

    python3 pi/s28/s28_bracket.py --run ~/s28_runs/bracket_x --out report.html

On the bench (red not starved) the win is small and the long red may
CLIP — the tool flags that. The real win is validated underwater; this
proves the mechanism and measures whatever the scene gives.
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
from s28_stack import load_burst, merge_mean            # noqa: E402


def bayer_planes(frame):
    """BGGR (H,W) -> R, G, B float planes (H/2, W/2)."""
    b = frame.astype(np.float64)
    B = b[0::2, 0::2]
    R = b[1::2, 1::2]
    G = (b[0::2, 1::2] + b[1::2, 0::2]) / 2.0
    return R, G, B


def red_stack(stack):
    """N red planes from a BAYER burst."""
    return np.stack([bayer_planes(f)[0] for f in stack])


def temporal_noise(planes):
    """Per-pixel temporal std across frames, mean over pixels."""
    return (float(planes.std(axis=0, ddof=1).mean())
            if len(planes) > 1 else 0.0)


def clip_frac(planes, thresh=250):
    return float((planes >= thresh).mean())


def merge_red_from_long(normal_mean, long_mean, ratio):
    """Channel-wise merge: R = long_red / ratio, G/B = normal. -> (R,G,B)
    half-res planes, brightness-matched to the normal frame."""
    Rl, _, _ = bayer_planes(long_mean)
    _, Gn, Bn = bayer_planes(normal_mean)
    Rm = np.clip(Rl / ratio, 0, 255)
    return Rm, Gn, Bn


def planes_to_rgb(R, G, B):
    return np.clip(np.stack([R, G, B], axis=-1), 0, 255).astype(np.uint8)


def gray_world(rgb):
    f = rgb.astype(np.float64)
    m = f.reshape(-1, 3).mean(0)
    return np.clip(f * (m.mean() / np.maximum(m, 1.0)), 0, 255).astype(
        np.uint8)


def png_uri(rgb, scale=1.0):
    im = Image.fromarray(rgb)
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)))
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def analyze(run_dir):
    """-> dict: rungs (ev -> {exp, ...}) + per-long-rung merge metrics."""
    rungs = {}
    for ev in (0, 2, 3):
        stage = "bracket_ev%d" % ev
        try:
            stack, rows, pf = load_burst(run_dir, stage)
        except Exception:
            continue
        reds = red_stack(stack)
        rungs[ev] = {"stage": stage, "n": len(stack),
                     "exp_us": rows[0]["exp_us"], "stack": stack,
                     "mean": merge_mean(stack),
                     "red_noise": temporal_noise(reds),
                     "red_mean": float(reds.mean()),
                     "red_clip": clip_frac(reds)}
    if 0 not in rungs:
        raise SystemExit("FAIL: no bracket_ev0 (NORMAL) burst in %s"
                         % run_dir)
    norm = rungs[0]
    merges = {}
    for ev in (2, 3):
        if ev not in rungs:
            continue
        lng = rungs[ev]
        ratio = lng["exp_us"] / norm["exp_us"]
        R, G, B = merge_red_from_long(norm["mean"], lng["mean"], ratio)
        # red SNR: signal is the normal red level (merge is brightness-
        # matched); merged red noise = long red noise / ratio.
        sig = norm["red_mean"]
        n_norm = norm["red_noise"] or 1e-9
        n_merged = (lng["red_noise"] / ratio) or 1e-9
        merged_red_mean = float(R.mean())
        merges[ev] = {
            "ratio": ratio,
            "red_clip_long": lng["red_clip"],
            "red_noise_normal": n_norm,
            "red_noise_merged": n_merged,
            "snr_normal": sig / n_norm,
            "snr_merged": sig / n_merged,
            "improvement": (n_norm / n_merged),
            "sqrtN": ratio ** 0.5,
            # red signal FRACTION (notes' metric): median red / full scale.
            # Shows red RECOVERY when the normal frame is crushed to black.
            "red_frac_normal": sig / 255.0,
            "red_frac_merged": merged_red_mean / 255.0,
            "normal_is_black": sig < 2.0,
            "rgb": planes_to_rgb(R, G, B),
        }
    return {"rungs": rungs, "norm": norm, "merges": merges}


def render(res, out, run_dir):
    norm = res["norm"]
    rungs = res["rungs"]
    merges = res["merges"]
    # exposure table
    exp_rows = "".join(
        "<tr><td>EV+%d</td><td>%d µs</td><td>%.1f×</td>"
        "<td>%.0f%% red clipped</td></tr>"
        % (ev, rungs[ev]["exp_us"], rungs[ev]["exp_us"] / norm["exp_us"],
           100 * rungs[ev]["red_clip"])
        for ev in sorted(rungs))
    # red-SNR table
    snr_rows = ""
    for ev in sorted(merges):
        m = merges[ev]
        warn = " ⚠ long red CLIPPED" if m["red_clip_long"] > 0.02 else ""
        if m["red_clip_long"] > 0.02:
            imp = "invalid"            # clipped long red -> fake low noise
        elif m["normal_is_black"]:
            warn = (" ⚠ normal red is black — bracket RECOVERS red "
                    "(fraction %.1f%% → %.1f%%)"
                    % (100 * m["red_frac_normal"],
                       100 * m["red_frac_merged"])) + warn
            imp = "—"
        else:
            imp = "%.2f×" % m["improvement"]
        snr_rows += (
            "<tr><td>EV+%d (%.0f×)</td><td>%.3f</td><td>%.3f</td>"
            "<td class=g>%s</td><td>%.2f× … %.2f×</td><td>%s</td></tr>"
            % (ev, m["ratio"], m["red_noise_normal"], m["red_noise_merged"],
               imp, m["sqrtN"], m["ratio"], warn))
    # visuals: normal vs red-merged (best available long rung)
    best = max(merges) if merges else None
    norm_rgb = gray_world(planes_to_rgb(*bayer_planes(norm["mean"])))
    panels = ("<div class=p><h4>NORMAL (single exposure)</h4><img src='%s'>"
              "</div>" % png_uri(norm_rgb, 1.1))
    if best is not None:
        merged_rgb = gray_world(merges[best]["rgb"])
        panels += ("<div class=p><h4>RED-merged (red from EV+%d ÷%.0f)"
                   "</h4><img src='%s'></div>"
                   % (best, merges[best]["ratio"],
                      png_uri(merged_rgb, 1.1)))

    html = """<!doctype html><meta charset=utf-8><title>S28 red-channel bracket</title>
<style>body{{font:13px system-ui;margin:16px;background:#111;color:#eee}}
h2{{margin:0 0 4px}} .sub{{color:#999;margin-bottom:10px;max-width:900px}}
.row{{display:flex;flex-wrap:wrap;gap:10px}}
.p{{background:#1c1c1c;padding:6px;border-radius:6px}}
.p img{{display:block;border-radius:3px}} .p h4{{margin:2px 0;font-size:12px}}
table{{border-collapse:collapse;margin:10px 0}}
td,th{{border:1px solid #333;padding:4px 9px;text-align:right}}
td:first-child,th:first-child{{text-align:left}} .g{{color:#5c8}}</style>
<h2>S28 red-channel HDR bracket</h2>
<div class=sub>Green/blue from the NORMAL frame, RED from a LONG
(+2/+3 EV, shutter-only) frame ÷ the exposure ratio. Red SNR improves by
up to the exposure ratio (read-noise-limited — the underwater regime),
between √ratio and ratio otherwise. Measured on BAYER (linear). Color is
uncorrected; this measures the RED channel.</div>
<h3>Bracket exposures</h3>
<table><tr><th>rung</th><th>exposure</th><th>ratio</th><th>red clip</th></tr>
{exp}</table>
<h3>Red SNR: merged vs single</h3>
<table><tr><th>long rung</th><th>red σ (normal)</th><th>red σ (merged)</th>
<th>improvement</th><th>√ratio … ratio</th><th></th></tr>{snr}</table>
<div class=row>{panels}</div>
<p class=sub>The merged red should be cleaner (lower σ) than the single
red by up to the exposure ratio. A CLIPPED long red invalidates the
merge (raise nothing — lower the base exposure or the scene light) — the
bench isn't red-starved, so expect small gains here; the real win is
underwater, where red is near the noise floor and the ratio applies in
full.</p>""".format(exp=exp_rows, snr=snr_rows, panels=panels)
    open(out, "w").write(html)
    # json (drop the arrays)
    j = {"norm_exp_us": norm["exp_us"], "merges": {
        ev: {k: v for k, v in m.items() if k != "rgb"}
        for ev, m in merges.items()}}
    json.dump(j, open(os.path.splitext(out)[0] + ".json", "w"), indent=1)
    return j


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    run = os.path.expanduser(args.run)
    out = args.out or os.path.join(run, "bracket.html")
    res = analyze(run)
    j = render(res, out, run)
    print("== S28 red-channel bracket: %s ==" % run)
    print("  NORMAL exposure %d µs, red mean %.1f, red σ %.3f"
          % (res["norm"]["exp_us"], res["norm"]["red_mean"],
             res["norm"]["red_noise"]))
    for ev in sorted(res["merges"]):
        m = res["merges"][ev]
        if m["red_clip_long"] > 0.02:
            print("  EV+%d (%.0f×): long red CLIPPED (%.0f%%) — merge "
                  "INVALID here (red not starved). σ %.3f->%.3f is a "
                  "clip artifact, not a real gain."
                  % (ev, m["ratio"], 100 * m["red_clip_long"],
                     m["red_noise_normal"], m["red_noise_merged"]))
        elif m["normal_is_black"]:
            print("  EV+%d (%.0f×): normal red is BLACK — bracket recovers"
                  " red fraction %.1f%% -> %.1f%%%s"
                  % (ev, m["ratio"], 100 * m["red_frac_normal"],
                     100 * m["red_frac_merged"],
                     "  (long red CLIPPED)" if m["red_clip_long"] > 0.02
                     else ""))
        else:
            print("  EV+%d (%.0f×): red σ %.3f -> %.3f  improvement %.2f× "
                  "(√ratio %.2f, ratio %.2f)%s"
                  % (ev, m["ratio"], m["red_noise_normal"],
                     m["red_noise_merged"], m["improvement"], m["sqrtN"],
                     m["ratio"],
                     "  CLIPPED" if m["red_clip_long"] > 0.02 else ""))
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
