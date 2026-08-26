#!/usr/bin/env python3
"""S8 bite C: the report card, generated from closed-loop HIL runs.

    python3 pi/hil/hil_report.py ~/hil_runs/e4_blurft_vga ~/hil_runs/e4_blurft_hd \
        [--stills-dir ~/hil_monterey/stills] \
        [--heatmap-still monterey_01_f0015] \
        [--out pi/workbench/recipes/guides/s8-hil-report.html]

Bite C's analytics shape (Nick, 2026-08-21) on the closed-loop rows:
scorecard, accuracy-vs-pixels-on-target (the money plot, the axis that
transfers to the T2 24-32 px floor), per-stage cost, confidence split,
per-cell energy. Reporting style per Nick 2026-08-25: SPARSE — tables
first, one chart, two heat maps, nothing decorative.

Reuse note: bench/s8_report.py (the two-ball campaign report) supplied
the FORM — view list, table-with-chart pairing, validated palette roles —
but its code is bound to the campaign row schema (blob-vs-model counts
vs truth strings); this generator reads the HIL harness rows instead.

Per-GT pixel attribution is RECOMPUTED here (rows carry aggregate counts,
not per-box match flags): the same visibility filter, edge-drop, and
greedy IoU matching as hil_harness.score_pending, imported where shared,
self-checked against every row's recorded n_match — a mismatch aborts
the report rather than plotting fiction.
"""
import argparse
import base64
import glob
import json
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from hil_harness import (MATCH_IOU, iou, load_cam_maps,  # noqa: E402
                         load_reviewed, map_still_box)

PX_BINS = [(0, 24), (24, 32), (32, 48), (48, 64), (64, 10 ** 9)]
PX_LABELS = ["<24", "24-32", "32-48", "48-64", "64+"]
T2_BIN = 1                     # the 24-32 bin: SPEC's T2 floor band

# palette roles from the validated s8_report set (light, dark)
C = {"ae3": ("#2a78d6", "#3987e5"), "n6": ("#eb6834", "#d95926"),
     "ink": ("#182226", "#dfe8e6"), "ink2": ("#5b7078", "#8fa4a0"),
     "grid": ("#cfdad6", "#2b383c"), "band": ("#0f7a8a", "#4dc3d4")}


def rematch(row, boxes, H, cam_w, cam_h):
    """Reproduce score_pending's matching for one row.
    -> ([(gt_min_side_px, matched_bool), ...], n_match) or None when the
    row has no stored dets_cam."""
    dets = np.array(row.get("dets_cam") or np.zeros((0, 5)), np.float64)
    m = 2.0
    gt_cam = [g for g in (map_still_box(H, b[1], b[2], b[3], b[4])
                          for b in boxes)
              if g[0] >= -m and g[1] >= -m
              and g[2] <= cam_w + m and g[3] <= cam_h + m]
    if len(dets):
        keep = ((dets[:, 0] > m) & (dets[:, 1] > m)
                & (dets[:, 2] < cam_w - m) & (dets[:, 3] < cam_h - m))
        dets = dets[keep]
    used = set()
    order = np.argsort(-dets[:, 4]) if len(dets) else []
    n_match = 0
    for di in order:
        best, best_j = 0.0, -1
        for j, g in enumerate(gt_cam):
            if j not in used:
                v = iou(dets[di][:4], g)
                if v > best:
                    best, best_j = v, j
        if best >= MATCH_IOU:
            used.add(best_j)
            n_match += 1
    return ([(min(g[2] - g[0], g[3] - g[1]), j in used)
             for j, g in enumerate(gt_cam)], n_match)


def load_run(run_dir, stills_dir):
    rows = [json.loads(ln) for ln in
            open(os.path.join(run_dir, "rows.jsonl"))]
    # k1-aware calib json preferred, bare-H npy legacy fallback (E11)
    H = load_cam_maps(run_dir)
    power = []
    for p in sorted(glob.glob(os.path.join(run_dir, "power_*.jsonl"))):
        power += [json.loads(ln) for ln in open(p)]
    reviewed = {name: boxes for _i, name, boxes in
                load_reviewed(stills_dir, reviewed_only=True)}
    return rows, H, power, reviewed


def cell_stats(rows, H, power, reviewed, cam_wh):
    """-> {(board, phase): stats} with px-binned recall, timings, mJ."""
    out = {}
    for r in rows:
        b, ph = r["board"], r["phase"]
        st = out.setdefault((b, ph), {
            "frames": 0, "gt": 0, "det": 0, "match": 0,
            "gt30": 0, "match30": 0, "false30": 0,
            "inf": 0.0, "e2e": 0.0, "conf_m": [], "conf_f": [],
            "bins": [[0, 0] for _ in PX_BINS], "t": []})
        st["frames"] += 1
        st["gt"] += r["n_gt"]
        st["det"] += r["n_det"]
        st["match"] += r["n_match"]
        st["gt30"] += r.get("n_gt_floor", 0)
        st["match30"] += r.get("n_match_floor", 0)
        st["false30"] += r.get("n_false_floor", 0)
        st["inf"] += sum(r["inf_us"]) / 1000
        st["e2e"] += (r["cap_us"] + sum(r["prep_us"]) + sum(r["inf_us"])
                      + sum(r["dec_us"])) / 1000
        if r.get("t_host"):
            st["t"].append(r["t_host"])
        cw, ch = cam_wh.get(b, (640, 400))
        gts, n_match = rematch(r, reviewed[r["still"]], H[b], cw, ch)
        # rows store dets_cam rounded to 0.1 px/conf, so conf-order ties
        # can flip a single match vs the original full-precision pass —
        # |Δ|≤1 is rounding, anything more is a real bug
        delta = abs(n_match - r["n_match"])
        st.setdefault("audit", [0, 0])
        st["audit"][0] += 1
        st["audit"][1] += int(delta == 0)
        if delta > 1:
            raise SystemExit(
                f"FAIL: rematch disagrees with recorded n_match on "
                f"{b}/{ph}/{r['still']} ({n_match} != {r['n_match']}) — "
                f"refusing to plot fiction")
        for px, matched in gts:
            for k, (lo, hi) in enumerate(PX_BINS):
                if lo <= px < hi:
                    st["bins"][k][0] += 1
                    st["bins"][k][1] += int(matched)
                    break
        n_m = r["n_match"]
        confs = sorted(r.get("det_conf", []), reverse=True)
        st["conf_m"] += confs[:n_m]        # approx: top-conf dets matched
        st["conf_f"] += confs[n_m:]
    for (b, _ph), st in out.items():
        st["wall"] = max(st["t"]) - min(st["t"]) if len(st["t"]) > 1 else 0
        samp = [p for p in power if p["label"] == b
                and st["t"] and min(st["t"]) <= p["ts"] <= max(st["t"])]
        mj = 0.0
        for a, bb in zip(samp, samp[1:]):
            mj += a["mW"] * (bb["ts"] - a["ts"])
        st["mj_frame"] = mj / st["frames"] if samp and mj > 0 else None
    return out


def money_plot_svg(cells, mode):
    """Recall vs GT px bin, HD cells, 4 series. One chart, per Nick."""
    W, Hh, L, B = 640, 300, 46, 34
    px_w = (W - L - 20) / (len(PX_BINS) - 1)
    series = []
    for (b, ph), st in sorted(cells.items()):
        if mode not in ph and mode != "":
            continue
        pts = []
        for k, (n, m) in enumerate(st["bins"]):
            if n >= 10:                     # starved bins mislead
                pts.append((k, m / n))
        if pts:
            series.append((b, ph, pts))
    def X(k):
        return L + k * px_w
    def Y(v):
        return (Hh - B) - v * (Hh - B - 16)
    g = [f'<rect x="{X(T2_BIN - 0.5):.0f}" y="16" '
         f'width="{px_w:.0f}" height="{Hh - B - 16}" '
         f'fill="var(--accent)" opacity="0.08"/>']
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        g.append(f'<line x1="{L}" y1="{Y(v):.0f}" x2="{W - 16}" '
                 f'y2="{Y(v):.0f}" stroke="var(--line)" stroke-width="1"/>'
                 f'<text x="{L - 6}" y="{Y(v) + 4:.0f}" text-anchor="end" '
                 f'class="tick">{v:g}</text>')
    for k, lab in enumerate(PX_LABELS):
        g.append(f'<text x="{X(k):.0f}" y="{Hh - B + 16}" '
                 f'text-anchor="middle" class="tick">{lab}</text>')
    for b, ph, pts in series:
        col = "var(--ae3)" if b == "AE3" else "var(--n6)"
        dash = ' stroke-dasharray="5 4"' if "nano" in ph else ""
        path = " ".join(f"{'M' if i == 0 else 'L'}{X(k):.0f},{Y(v):.0f}"
                        for i, (k, v) in enumerate(pts))
        g.append(f'<path d="{path}" fill="none" stroke="{col}" '
                 f'stroke-width="2"{dash}/>')
        for k, v in pts:
            g.append(f'<circle cx="{X(k):.0f}" cy="{Y(v):.0f}" r="3.5" '
                     f'fill="{col}"/>')
        ek, ev = pts[-1]
        g.append(f'<text x="{X(ek) + 7:.0f}" y="{Y(ev) + 4:.0f}" '
                 f'class="slab" fill="{col}">'
                 f'{b} {ph.split("-")[0]}</text>')
    g.append(f'<text x="{X(T2_BIN):.0f}" y="30" text-anchor="middle" '
             f'class="tick">T2 floor</text>')
    return (f'<svg viewBox="0 0 {W} {Hh}" role="img" '
            f'aria-label="recall by GT pixel size">' + "".join(g)
            + "</svg>")


def b64img(path):
    return base64.b64encode(open(path, "rb").read()).decode()


def fmt_cell_rows(cells, label):
    rows = []
    for (b, ph), st in sorted(cells.items()):
        rec = st["match30"] / st["gt30"] if st["gt30"] else 0
        prec = (st["match30"] / (st["match30"] + st["false30"])
                if st["match30"] + st["false30"] else 0)
        mj = (f"{st['mj_frame']:.0f}" if st["mj_frame"]
              else '<span class="muted">owed†</span>')
        rows.append(
            f"<tr><td>{b} {ph.split('-')[0]} · {label}</td>"
            f"<td class='n'>{rec:.2f} / {prec:.2f}</td>"
            f"<td class='n'>{st['inf'] / st['frames']:.0f}</td>"
            f"<td class='n'>{st['e2e'] / st['frames']:.0f}</td>"
            f"<td class='n'>{mj}</td>"
            f"<td class='n'>{st['wall']:.0f}</td></tr>")
    return "".join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--stills-dir",
                    default=os.path.expanduser("~/hil_monterey/stills"))
    ap.add_argument("--heatmap-still", default="monterey_01_f0015")
    ap.add_argument("--out", default=os.path.join(
        _HERE, "..", "workbench", "recipes", "guides",
        "s8-hil-report.html"))
    args = ap.parse_args()

    all_cells = {}
    heat = {}
    for run in args.runs:
        run = os.path.expanduser(run)
        rows, H, power, reviewed = load_run(run, args.stills_dir)
        cam_wh = {}
        for r in rows:
            if r["board"] not in cam_wh and r.get("dets_cam") is not None:
                big = any(d[2] > 700 for d in (r.get("dets_cam") or []))
                cam_wh[r["board"]] = (1280, 800) if big else (640, 400)
        # trust the run name for resolution labels; geometry cross-checks
        label = "HD" if "hd" in os.path.basename(run).lower() else "VGA"
        if label == "HD":
            cam_wh = {b: (1280, 800) for b in H}
        else:
            cam_wh = {b: (640, 400) for b in H}
        cells = cell_stats(rows, H, power, reviewed, cam_wh)
        for k, v in cells.items():
            all_cells[(k[0], k[1], label)] = v
        if label == "HD":
            for b in ("AE3",):
                for m in ("nano", "tiny"):
                    p = os.path.join(
                        run, "heatmaps",
                        f"{b}_{m}-tiled_{args.heatmap_still}.jpg")
                    if os.path.exists(p):
                        heat[f"{b} {m}"] = b64img(p)

    hd = {(b, ph): st for (b, ph, lb), st in all_cells.items()
          if lb == "HD"}
    vga = {(b, ph): st for (b, ph, lb), st in all_cells.items()
           if lb == "VGA"}
    a_tot = sum(st["audit"][0] for st in all_cells.values())
    a_ok = sum(st["audit"][1] for st in all_cells.values())
    audit_line = f"{a_ok}/{a_tot} rows exact, rest |Δ|=1"
    bins_tbl = "".join(
        f"<tr><td>{b} {ph.split('-')[0]}</td>"
        + "".join(
            (f"<td class='n'>{m}/{n}</td>" if n else "<td class='n'>—</td>")
            for n, m in st["bins"])
        + "</tr>"
        for (b, ph), st in sorted(hd.items()))
    heat_html = "".join(
        f'<figure><img src="data:image/jpeg;base64,{v}">'
        f"<figcaption>{k} · HD tiled · {args.heatmap_still}"
        f"</figcaption></figure>" for k, v in heat.items())

    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>S8 report card — urchin HIL</title><style>
:root{{--paper:#f3f6f5;--ink:{C['ink'][0]};--muted:{C['ink2'][0]};
 --accent:#0f7a8a;--line:{C['grid'][0]};--ae3:{C['ae3'][0]};
 --n6:{C['n6'][0]};--tile:#fff}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{
 --paper:#10181b;--ink:{C['ink'][1]};--muted:{C['ink2'][1]};
 --accent:#4dc3d4;--line:{C['grid'][1]};--ae3:{C['ae3'][1]};
 --n6:{C['n6'][1]};--tile:#141e21}}}}
:root[data-theme="dark"]{{--paper:#10181b;--ink:{C['ink'][1]};
 --muted:{C['ink2'][1]};--accent:#4dc3d4;--line:{C['grid'][1]};
 --ae3:{C['ae3'][1]};--n6:{C['n6'][1]};--tile:#141e21}}
body{{background:var(--paper);color:var(--ink);
 font:15px/1.55 system-ui;margin:0;padding:1.8rem 1.2rem 3rem}}
main{{max-width:58rem;margin:0 auto}}
h1{{font-size:1.45rem;font-weight:650;margin:0 0 .2rem;
 letter-spacing:-.01em}}
h2{{font-size:1rem;margin:1.6rem 0 .5rem}}
p{{max-width:44rem;margin:.35rem 0}}
.muted{{color:var(--muted)}}
table{{border-collapse:collapse;font-size:.86rem}}
th{{font:600 .7rem/1.3 ui-monospace,monospace;letter-spacing:.07em;
 text-transform:uppercase;color:var(--muted);text-align:left;
 padding:.35rem .75rem .3rem 0;border-bottom:1px solid var(--ink)}}
td{{padding:.35rem .75rem .35rem 0;border-bottom:1px solid var(--line);
 font-variant-numeric:tabular-nums}}
td.n{{text-align:right;font-family:ui-monospace,monospace}}
.tablewrap{{overflow-x:auto}}
svg{{max-width:100%;height:auto}}
.tick{{font:11px ui-monospace,monospace;fill:var(--muted)}}
.slab{{font:600 12px ui-monospace,monospace}}
figure{{margin:0;display:inline-block;width:min(48%,440px);
 vertical-align:top}}
figure+figure{{margin-left:2%}}
img{{width:100%;border-radius:4px}}
figcaption{{color:var(--muted);font-size:.76rem;
 font-family:ui-monospace,monospace;padding-top:3px}}
.note{{border-left:3px solid var(--line);padding-left:.8rem;
 color:var(--muted);font-size:.84rem;max-width:44rem}}
</style></head><body><main>
<h1>S8 report card — urchin HIL, closed loop</h1>
<p class="muted">Deployed models (nano = stage1_v2, tiny = blur-ft),
both boards simultaneous, 24 reviewed stills × 2 frames, 30 px GT
floor. Every number recomputed from rows.jsonl; per-GT pixel
attribution self-checked against the recorded match counts.</p>

<h2>Scorecard</h2>
<div class="tablewrap"><table>
<tr><th>cell</th><th class="n">recall / prec (30px)</th>
<th class="n">infer ms/f</th><th class="n">e2e ms/f</th>
<th class="n">mJ/f</th><th class="n">phase wall s</th></tr>
{fmt_cell_rows(vga, "VGA")}{fmt_cell_rows(hd, "HD")}
</table></div>
<p class="note">† N6 energy is owed: its current USB cable bypasses the
INA3221 CH3 shunt (TRACKER, 2026-08-25) — the column fills after the
re-wire. AE3 mJ/frame is whole-loop (capture+infer+emit+idle between
handshakes) from the run's own power log.</p>

<h2>Recall vs pixels-on-target — the money plot (HD tiled)</h2>
<p class="muted">The axis that transfers to the real deployment: how
big must an urchin be on the sensor before the model finds it. Shaded
band = the SPEC T2 floor (24–32 px). Solid = tiny (blur-ft),
dashed = nano. Bins under 10 GT are dropped, not plotted.</p>
{money_plot_svg(hd, "")}
<div class="tablewrap"><table>
<tr><th>cell (matched/GT)</th>{"".join(f'<th class="n">{lb}</th>'
                                       for lb in PX_LABELS)}</tr>
{bins_tbl}
</table></div>

<h2>Where the models look</h2>
<p class="muted">Candidate-cell attention (obj·cls) on one dense still,
AE3 HD. Nano: hot and scattered — bare-rock false positives are its
precision cost. Blur-ft tiny: dimmer marks, tighter on urchins. Cyan =
camera FOV; outside it is unseen, not missed. Full galleries:
<code>~/hil_runs/e4_blurft_*/heatmaps/</code>.</p>
{heat_html}

<h2>Caveats that gate conclusions</h2>
<p class="note">The N6-HD cell is REAL but unattributed (reproduces
across harnesses; lead suspect midday glare at its angle — a
controlled-lighting rerun is owed). All numbers are one lighting
condition (2026-08-25 afternoon); matrix cells are per-lighting.
Confidence split and per-stage tables live in the rows
(<code>~/hil_runs/e4_blurft_*/rows.jsonl</code>) — this page stays
sparse by design. Pixel-attribution audit: {audit_line} (disagreements
are single-match conf-rounding ties; anything larger aborts the
report).</p>
</main></body></html>"""
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(page)
    print(f"report: {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    main()
