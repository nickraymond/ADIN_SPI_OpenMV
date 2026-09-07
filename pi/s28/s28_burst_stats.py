#!/usr/bin/env python3
"""S28 bite 1 — stats pass over a burst-capture run dir.

Turns frames + meta.jsonl into the bite's verdicts, printed as tables
and persisted as stats.json (trust artifacts, not exit codes — the
verdict lines name what failed):

  LOCK      per burst: were exposure/gain/WB readbacks identical?
  NOISE     per card burst, per patch, per channel: mean, temporal sigma
            (the number stacking attacks), spatial sigma, SNR — patches
            located through the run's own E11 CamMap (fresh calibration
            each run: the camera is NOT assumed centered).
  ORIENT    RGB565 byte order + channel identity proven against the red/
            blue patches, never assumed.
  EXPO      commanded vs readback exposure table (bite-3 feasibility).
  BRACKET   +2/+3 EV rungs: linear brightening ratio per channel (Bayer).
  FLICKER   LCD-PWM verdict per exposure: frame-mean sigma vs the
            independent-pixel expectation (SAFE/ALIASED).

No board contact — pure files. Runs on the Pi or the Mac.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "pi", "hil"))
sys.path.insert(0, os.path.join(_ROOT, "bench"))

import numpy as np                                    # noqa: E402

from s28_session import (bayer_planes, bracket_check, flicker_verdict,  # noqa: E402
                         gray_plane, lock_verdict, noise_stats,
                         orient_check, patch_region, rgb565_to_rgb,
                         scale_cam_map)
from s28_patch_card import PATCHES                    # noqa: E402


def load_run(run_dir):
    rows = []
    with open(os.path.join(run_dir, "meta.jsonl")) as fh:
        for ln in fh:
            rows.append(json.loads(ln))
    stages = defaultdict(list)
    for r in rows:
        stages[r["stage"]].append(r)
    return stages


def load_cam_map(run_dir, label):
    p = os.path.join(run_dir, "calib_%s.json" % label)
    if not os.path.isfile(p):
        return None, None
    from hil_harness import CamMap
    d = json.load(open(p))
    return CamMap.from_dict(d), (d["cam_w"], d["cam_h"])


def frame_bytes(run_dir, row):
    with open(os.path.join(run_dir, row["file"]), "rb") as fh:
        return fh.read()


def decode_stack(run_dir, rows, byteswap=False):
    """-> dict of channel -> N x h x w float32 stacks. BAYER gives
    r/g/b half-res planes; RGB565 gives r/g/b full-res; GRAYSCALE
    gives one 'y' plane."""
    per_ch = defaultdict(list)
    for r in rows:
        buf = frame_bytes(run_dir, r)
        if r["pixformat"] == "BAYER":
            for ch, plane in bayer_planes(buf, r["w"], r["h"]).items():
                per_ch[ch].append(plane)
        elif r["pixformat"] == "RGB565":
            rgb = rgb565_to_rgb(buf, r["w"], r["h"], byteswap=byteswap)
            for i, ch in enumerate("rgb"):
                per_ch[ch].append(rgb[:, :, i].astype(np.float32))
        else:
            per_ch["y"].append(gray_plane(buf, r["w"], r["h"]))
    return {ch: np.stack(v) for ch, v in per_ch.items()}


def region_for(cam_map, cam_wh, patch, row):
    """Patch region in THIS frame's pixel grid (calib may be another
    framesize; the sensor letterboxes proportionally, HD->VGA = 0.5)."""
    s = row["w"] / cam_wh[0]
    m = cam_map if s == 1.0 else scale_cam_map(cam_map, s)
    return patch_region(m, patch, row["w"], row["h"])


def patch_stats(run_dir, rows, cam_map, cam_wh, byteswap=False):
    """-> {patch: {channel: noise_stats}} for one burst."""
    stacks = decode_stack(run_dir, rows, byteswap=byteswap)
    bayer = rows[0]["pixformat"] == "BAYER"
    out = {}
    for patch in PATCHES:
        reg = region_for(cam_map, cam_wh, patch, rows[0])
        if reg is None:
            out[patch[0]] = {"err": "patch off-frame — re-aim/re-calib"}
            continue
        x0, y0, x1, y1 = reg
        if bayer:      # planes are half-res
            x0, y0, x1, y1 = x0 // 2, y0 // 2, x1 // 2, y1 // 2
        ch_stats = {}
        for ch, stack in stacks.items():
            ch_stats[ch] = noise_stats(stack[:, y0:y1, x0:x1])
        out[patch[0]] = ch_stats
    return out


def find_orientation(run_dir, stages, cam_map, cam_wh):
    """Prove the RGB565 byte order on the card burst. -> (byteswap,
    verdict-str)."""
    for stage, rows in stages.items():
        if not stage.startswith("card_rgb565") or not rows:
            continue
        buf = frame_bytes(run_dir, rows[0])
        s = rows[0]["w"] / cam_wh[0]
        m = cam_map if s == 1.0 else scale_cam_map(cam_map, s)
        for swap in (False, True):
            rgb = rgb565_to_rgb(buf, rows[0]["w"], rows[0]["h"],
                                byteswap=swap)
            if orient_check(rgb, m, PATCHES):
                return swap, "verified (byteswap=%s)" % swap
        return False, "FAILED — neither byte order puts red/blue where " \
                      "the card says; check aim/calib before trusting " \
                      "channel identity"
    return False, "no RGB565 card burst in run"


def fmt_patch_table(stats):
    lines = ["  %-7s %-3s %9s %8s %8s %8s" %
             ("patch", "ch", "mean", "sig_t", "sig_s", "SNR_t")]
    for pname, chs in stats.items():
        if "err" in chs:
            lines.append("  %-7s %s" % (pname, chs["err"]))
            continue
        for ch, st in sorted(chs.items()):
            snr = st["mean"] / st["sigma_t"] if st["sigma_t"] else \
                float("inf")
            lines.append("  %-7s %-3s %9.2f %8.3f %8.3f %8.1f"
                         % (pname, ch, st["mean"], st["sigma_t"],
                            st["sigma_s"], snr))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True)
    ap.add_argument("--label", default="AE3")
    args = ap.parse_args()
    run_dir = os.path.expanduser(args.run)
    stages = load_run(run_dir)
    cam_map, cam_wh = load_cam_map(run_dir, args.label)
    report = {"run": run_dir, "stages": sorted(stages)}

    print("== S28 burst stats: %s ==" % run_dir)

    # LOCK — every burst stage
    report["lock"] = {}
    print("\n-- LOCK (settings frozen across each burst?) --")
    for stage in sorted(stages):
        ok, detail = lock_verdict(stages[stage])
        report["lock"][stage] = {"ok": ok, **detail}
        gaps = [g for g in detail.get("gaps_ms", []) if g is not None][1:]
        print("  %-28s %s exp=%s gain=%s gap_ms med=%s"
              % (stage, "HELD  " if ok else "LEAKED", detail["exp_us"],
                 detail["gain_db"],
                 int(np.median(gaps)) if gaps else "-"))

    # ORIENT + NOISE — need calibration
    if cam_map is None:
        print("\n-- no calib_%s.json: patch stats skipped (control run "
              "or --skip-calib) --" % args.label)
    else:
        byteswap, orient = find_orientation(run_dir, stages, cam_map,
                                            cam_wh)
        report["orient"] = orient
        print("\n-- ORIENT: %s --" % orient)
        report["noise"] = {}
        for stage in sorted(stages):
            if not stage.startswith("card_"):
                continue
            st = patch_stats(run_dir, stages[stage], cam_map, cam_wh,
                             byteswap=byteswap)
            report["noise"][stage] = st
            print("\n-- NOISE %s (n=%d) --" % (stage, len(stages[stage])))
            print(fmt_patch_table(st))

    # EXPO
    expo_p = os.path.join(run_dir, "expo_rows.jsonl")
    if os.path.isfile(expo_p):
        rows = [json.loads(l) for l in open(expo_p)]
        report["expo"] = rows
        print("\n-- EXPO (commanded vs readback, us) --")
        by_fps = defaultdict(list)
        for r in rows:
            by_fps[r["fps"]].append(r)
        for fps in sorted(by_fps, reverse=True):
            got = ["%d->%d" % (r["cmd"], r["got"]) for r in by_fps[fps]]
            print("  fps=%-3d max=%d  %s"
                  % (fps, max(r["got"] for r in by_fps[fps]),
                     " ".join(got)))

    # BRACKET — red + gray50 patch means per rung, Bayer linear
    br_stages = sorted(s for s in stages if s.startswith("bracket_ev"))
    if br_stages and cam_map is not None:
        report["bracket"] = {}
        print("\n-- BRACKET (linear brightening vs exposure ratio) --")
        for pname in ("gray50", "red"):
            patch = next(p for p in PATCHES if p[0] == pname)
            rungs = []
            for stage in br_stages:
                rows = stages[stage]
                exp = rows[0]["exp_us"]
                stacks = decode_stack(run_dir, rows)
                reg = region_for(cam_map, cam_wh, patch, rows[0])
                if reg is None:
                    break
                x0, y0, x1, y1 = [v // 2 for v in reg]
                rungs.append((exp,
                              float(stacks["r" if pname == "red" else
                                           "g"][:, y0:y1,
                                               x0:x1].mean())))
            if len(rungs) >= 2:
                ok, rws = bracket_check(rungs)
                report["bracket"][pname] = {"ok": ok, "rows": rws,
                                            "rungs": rungs}
                print("  %-7s %s %s" % (pname,
                                        "LINEAR" if ok else "NONLINEAR",
                                        json.dumps(rws)))

    # FLICKER — pwm stages: frame means vs independent-pixel expectation
    pwm_stages = sorted(s for s in stages if s.startswith("pwm_"))
    if pwm_stages:
        report["flicker"] = {}
        print("\n-- FLICKER (LCD-PWM check; SAFE = stackable) --")
        for stage in pwm_stages:
            rows = stages[stage]
            stacks = decode_stack(run_dir, rows)
            stack = stacks.get("g", stacks.get("y"))
            means = [float(f.mean()) for f in stack]
            sig_t = noise_stats(stack)["sigma_t"]
            verdict, detail = flicker_verdict(means, sig_t,
                                              stack[0].size)
            report["flicker"][stage] = {"verdict": verdict, **detail}
            print("  %-22s %-8s %s" % (stage, verdict,
                                       json.dumps(detail)))

    out = os.path.join(run_dir, "stats.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1)
    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
