#!/usr/bin/env python3
"""s8_report.py -- S8 bite C: render the accuracy/cost report from recorded rows.

    python3 bench/s8_report.py ~/s8_rows.jsonl \
        [--power "AE3=0.21,N6=1.02"] [--infer-only "AE3=27.5,N6=42.0"] \
        [--out pi/workbench/recipes/guides/s8-metrics-report.html]

Reads ONLY the JSONL rows the --record flag wrote (the campaign's artifact
of record) and emits one self-contained HTML report -- five views, each a
table + one chart, per Nick's Edge-Impulse ethos: "engineers need to see the
data both as a table and a picture". The default --out drops it into the
workbench guides dir, so the report IS a cookbook chapter.

Analysis axes (Nick 2026-08-21): distance is DROPPED (the balls only ever
sat at ~1.5 m); pixels-on-target -- min box side, measured per detection --
is the continuous axis that transfers to urchins and the T2 >=24-32 px
floor. Frame px comes from the BLOB boxes (pixel-accurate) as a
method-independent scene measurement; model-box px is grid-quantized
(~27 px steps at VGA) and used only for the confidence view.

Colors are palette-validated (dataviz skill, 2026-08-21, both modes):
class pair #e87ba4/#4a3aa7, method/board pair #2a78d6/#eb6834, stage bars
end blue-orange-aqua with encode in recessive gray (streaming overhead is
visually separated from the application path). The light-mode magenta and
yellow marks sit below 3:1 -- the relief rule is satisfied because every
chart ships with its table.
"""
import argparse
import html
import json
import os
import sys

PX_BINS = [(0, 16), (16, 24), (24, 32), (32, 48), (48, 64), (64, 10 ** 9)]
PX_LABELS = ["<16", "16-24", "24-32", "32-48", "48-64", "64+"]
T2_FLOOR = (24, 32)          # the SPEC T2 detection floor, shaded on view 2

# Palette roles (light, dark) -- validated pairs, see module docstring.
C = {
    "s1": ("#2a78d6", "#3987e5"),      # blue: board AE3 / method blob / capture
    "s2": ("#eb6834", "#d95926"),      # orange: board N6 / method model / infer
    "s3": ("#1baf7a", "#199e70"),      # aqua: decode stage
    "s4": ("#eda100", "#c98500"),      # yellow: blob stage
    "pink": ("#e87ba4", "#d55181"),    # class pink
    "purple": ("#4a3aa7", "#9085e9"),  # class purple
    "muted": ("#898781", "#898781"),
    "grid": ("#e1e0d9", "#2c2c2a"),
    "ink": ("#0b0b0b", "#ffffff"),
    "ink2": ("#52514e", "#c3c2b7"),
    "surface": ("#fcfcfb", "#1a1a19"),
    "overhead": ("#c3c2b7", "#4a4a47"),  # encode: recessive, not a series
}


# ---------------------------------------------------------------------------
# Row loading + aggregation (pure; unit-tested)
# ---------------------------------------------------------------------------

def load_rows(paths):
    rows = []
    for p in paths:
        with open(p) as fh:
            for ln in fh:
                if ln.strip():
                    rows.append(json.loads(ln))
    return rows


def class_order(truth):
    """Truth dict order IS the class-index order (python dicts preserve
    insertion order; parse_truth preserves the typed order, which must match
    the recipe's --blob-thresh order -- documented in the campaign guide)."""
    return list(truth.keys())


def _min_side(box):
    return min(box[3], box[4])


def aggregate(rows):
    """rows -> {(run, board): stats dict}. Pure arithmetic, no rendering."""
    out = {}
    for r in rows:
        hdr = r.get("hdr", {})
        if "seq" not in hdr:        # info rows / junk: not frame rows
            continue
        key = (r["run"], r["board"])
        s = out.setdefault(key, {
            "truth": r["truth"], "classes": class_order(r["truth"]),
            "n": 0, "ts": [], "bc": [], "mc": [],
            "stage_us": {k: 0 for k in ("cap_us", "inf_us", "blob_us",
                                        "enc_us", "mdec_us")},
            "blob_px": [], "model_pts": [],   # (px, conf) pairs
            "frame_px": [], "b_exact": 0, "m_exact": 0,
            "b_err": 0, "m_err": 0,
        })
        truth_counts = [s["truth"][c] for c in s["classes"]]
        s["n"] += 1
        s["ts"].append(r["ts"])
        bc = list(hdr.get("bc", []))
        mc = list(hdr.get("mc", []))
        s["bc"].append(bc)
        s["mc"].append(mc)
        for k in s["stage_us"]:
            s["stage_us"][k] += hdr.get(k, 0)
        sides = [_min_side(b) for b in hdr.get("bb", [])]
        s["blob_px"].extend(sides)
        if sides:
            srt = sorted(sides)
            s["frame_px"].append(srt[len(srt) // 2])
        for b in hdr.get("mb", []):
            if len(b) > 5:
                s["model_pts"].append((_min_side(b), b[5] / 100.0))
        pad = lambda v: v + [0] * (len(truth_counts) - len(v))
        b_err = sum(abs(a - t) for a, t in zip(pad(bc), truth_counts))
        m_err = sum(abs(a - t) for a, t in zip(pad(mc), truth_counts))
        s["b_err"] += b_err
        s["m_err"] += m_err
        s["b_exact"] += (b_err == 0)
        s["m_exact"] += (m_err == 0)
    for s in out.values():
        n = max(1, s["n"])
        span = (max(s["ts"]) - min(s["ts"])) if len(s["ts"]) > 1 else 0
        s["fps"] = (s["n"] - 1) / span if span > 0 else 0.0
        s["stage_ms"] = {k: v / n / 1000.0 for k, v in s["stage_us"].items()}
        s["mean_bc"] = [sum(v[i] if i < len(v) else 0 for v in s["bc"]) / n
                        for i in range(len(s["classes"]))]
        s["mean_mc"] = [sum(v[i] if i < len(v) else 0 for v in s["mc"]) / n
                        for i in range(len(s["classes"]))]
    return out


def px_bin_accuracy(agg, board):
    """-> [(bin_label, n_frames, blob_exact_frac, model_exact_frac)] over all
    runs of one board, binned by the frame's median BLOB min-side px."""
    bins = [[0, 0, 0] for _ in PX_BINS]      # n, b_exact, m_exact
    for (run, b), s in agg.items():
        if b != board:
            continue
        truth_counts = [s["truth"][c] for c in s["classes"]]
        for i in range(s["n"]):
            if i >= len(s["frame_px"]):
                break
            px = s["frame_px"][i]
            bi = next(j for j, (lo, hi) in enumerate(PX_BINS)
                      if lo <= px < hi)
            pad = lambda v: v + [0] * (len(truth_counts) - len(v))
            bins[bi][0] += 1
            bins[bi][1] += (sum(abs(a - t) for a, t in
                                zip(pad(s["bc"][i]), truth_counts)) == 0)
            bins[bi][2] += (sum(abs(a - t) for a, t in
                                zip(pad(s["mc"][i]), truth_counts)) == 0)
    return [(PX_LABELS[j], n, (be / n if n else None), (me / n if n else None))
            for j, (n, be, me) in enumerate(bins)]


def parse_kv_floats(spec):
    """"AE3=0.21,N6=1.02" -> {"AE3": 0.21} (None -> {})."""
    if not spec:
        return {}
    out = {}
    for part in spec.split(","):
        k, _, v = part.partition("=")
        out[k.strip()] = float(v)
    return out


# ---------------------------------------------------------------------------
# SVG helpers -- thin marks, rounded value-ends, hairline grid, <title> hover
# ---------------------------------------------------------------------------

def _bar(x, y, w, h, fill_var, title, horizontal=False, r=3):
    """A bar with the VALUE end rounded (4px-class), baseline end square."""
    r = min(r, (h if horizontal else w) / 2, (w if horizontal else h))
    if r <= 0 or w <= 0 or h <= 0:
        return ""
    if horizontal:
        d = ("M%.1f %.1f h%.1f a%.1f %.1f 0 0 1 %.1f %.1f v%.1f "
             "a%.1f %.1f 0 0 1 -%.1f %.1f h-%.1f z"
             % (x, y, w - r, r, r, r, r, h - 2 * r, r, r, r, r, w - r))
    else:
        d = ("M%.1f %.1f v-%.1f a%.1f %.1f 0 0 1 %.1f -%.1f h%.1f "
             "a%.1f %.1f 0 0 1 %.1f %.1f v%.1f z"
             % (x, y + h, h - r, r, r, r, r, w - 2 * r, r, r, r, r, h - r))
    return ('<path d="%s" fill="var(--%s)"><title>%s</title></path>'
            % (d, fill_var, html.escape(title)))


def _grid_h(x0, x1, ys):
    return "".join('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                   'stroke="var(--grid)" stroke-width="1"/>'
                   % (x0, y, x1, y) for y in ys)


def _text(x, y, s, cls="lbl", anchor="middle"):
    return ('<text x="%.1f" y="%.1f" class="%s" text-anchor="%s">%s</text>'
            % (x, y, cls, anchor, html.escape(str(s))))


def svg_open(w, h):
    return ('<svg viewBox="0 0 %d %d" role="img" '
            'style="width:100%%;max-width:%dpx">' % (w, h, w))


def chart_counts(agg, board, mode_classes):
    """View 1: per class, per run -- blob vs model mean counts + truth tick."""
    runs = sorted({run for (run, b) in agg if b == board})
    if not runs:
        return "<p class='muted'>no rows for %s</p>" % board
    classes = mode_classes
    W, H = 640, 90 + 120 * len(classes)
    parts = [svg_open(W, H)]
    y0 = 30
    for ci, cname in enumerate(classes):
        top = y0 + ci * 120
        maxv = 1
        for run in runs:
            s = agg[(run, board)]
            maxv = max(maxv, s["truth"].get(cname, 0),
                       s["mean_bc"][ci] if ci < len(s["mean_bc"]) else 0,
                       s["mean_mc"][ci] if ci < len(s["mean_mc"]) else 0)
        scale = 80.0 / maxv
        parts.append(_text(8, top + 10, cname, "lbl2", "start"))
        parts.append(_grid_h(40, W - 10, [top + 90 - v * scale
                                          for v in range(0, int(maxv) + 1,
                                                         max(1, int(maxv // 4) or 1))]))
        gw = (W - 60) / max(1, len(runs))
        for ri, run in enumerate(runs):
            s = agg[(run, board)]
            gx = 50 + ri * gw
            bw = min(28, gw / 3 - 2)
            bv = s["mean_bc"][ci] if ci < len(s["mean_bc"]) else 0
            mv = s["mean_mc"][ci] if ci < len(s["mean_mc"]) else 0
            tv = s["truth"].get(cname, 0)
            parts.append(_bar(gx, top + 90 - bv * scale, bw, bv * scale, "s1",
                              "%s %s blob mean %.1f (truth %d)"
                              % (run, cname, bv, tv)))
            parts.append(_bar(gx + bw + 2, top + 90 - mv * scale, bw,
                              mv * scale, "s2",
                              "%s %s model mean %.1f (truth %d)"
                              % (run, cname, mv, tv)))
            ty = top + 90 - tv * scale
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                         'stroke="var(--ink)" stroke-width="2" '
                         'stroke-dasharray="5 3"><title>%s truth %d</title>'
                         '</line>' % (gx - 4, ty, gx + 2 * bw + 6, ty,
                                      html.escape(run), tv))
            if ci == len(classes) - 1:
                parts.append(_text(gx + bw + 1, top + 104, run, "lbl"))
    parts.append(_text(50, 14, "bars: mean counted -- dashes: ground truth",
                       "lbl", "start"))
    parts.append("</svg>")
    return "".join(parts)


def chart_px(rows_binned, board):
    """View 2: exact-count fraction vs px bin, blob + model, T2 floor shaded."""
    W, H = 640, 200
    parts = [svg_open(W, H)]
    x0, x1, y0, y1 = 46, W - 12, 20, 160
    bw = (x1 - x0) / len(PX_BINS)
    fi = [i for i, lab in enumerate(PX_LABELS) if lab in ("24-32",)]
    if fi:
        parts.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" '
                     'fill="var(--grid)" opacity="0.5"><title>T2 floor '
                     '24-32 px</title></rect>'
                     % (x0 + fi[0] * bw, y0, bw, y1 - y0))
    parts.append(_grid_h(x0, x1, [y1 - f * (y1 - y0) for f in (0.5, 1.0)]))
    parts.append(_text(x0 - 6, y1 - (y1 - y0) + 4, "100%", "lbl", "end"))
    parts.append(_text(x0 - 6, y1 - 0.5 * (y1 - y0) + 4, "50%", "lbl", "end"))
    for si, (name, var, col) in enumerate((("blob", "s1", 2),
                                           ("model", "s2", 3))):
        pts = []
        for j, (lab, n, bf, mf) in enumerate(rows_binned):
            f = (bf, mf)[si]
            if f is None:
                continue
            cx = x0 + (j + 0.5) * bw
            cy = y1 - f * (y1 - y0)
            pts.append((cx, cy))
            parts.append('<circle cx="%.1f" cy="%.1f" r="5" '
                         'fill="var(--%s)"><title>%s %s: %.0f%% exact '
                         '(%d frames)</title></circle>'
                         % (cx, cy, var, name, lab, 100 * f, n))
        if len(pts) > 1:
            parts.append('<polyline points="%s" fill="none" '
                         'stroke="var(--%s)" stroke-width="2"/>'
                         % (" ".join("%.1f,%.1f" % p for p in pts), var))
    for j, lab in enumerate(PX_LABELS):
        parts.append(_text(x0 + (j + 0.5) * bw, y1 + 16, lab, "lbl"))
    parts.append(_text(x0 + (x1 - x0) / 2, y1 + 32,
                       "median detected ball size, px (min box side)", "lbl"))
    parts.append("</svg>")
    return "".join(parts)


STAGES = (("cap_us", "capture", "s1"), ("blob_us", "blob", "s4"),
          ("inf_us", "inference", "s2"), ("mdec_us", "decode", "s3"),
          ("enc_us", "encode (streaming overhead)", "overhead"))


def chart_stages(agg, boards):
    """View 3: horizontal stacked ms bars; encode in recessive gray."""
    rows = []
    for b in boards:
        keys = [k for k in agg if k[1] == b]
        if not keys:
            continue
        ms = {st: sum(agg[k]["stage_ms"][st] * agg[k]["n"] for k in keys)
              / max(1, sum(agg[k]["n"] for k in keys))
              for st, _, _ in STAGES}
        rows.append((b, ms))
    W = 640
    H = 40 + 34 * len(rows) + 20
    maxv = max((sum(ms.values()) for _, ms in rows), default=1)
    scale = (W - 150) / maxv
    parts = [svg_open(W, H)]
    for i, (b, ms) in enumerate(rows):
        y = 34 + i * 34
        parts.append(_text(8, y + 13, b, "lbl2", "start"))
        x = 60.0
        for st, name, var in STAGES:
            w = ms[st] * scale
            if w < 0.5:
                continue
            parts.append(_bar(x, y, w - (2 if w > 2 else 0), 18, var,
                              "%s %s %.1f ms" % (b, name, ms[st]),
                              horizontal=True))
            x += w
        app_ms = sum(v for k, v in ms.items() if k != "enc_us")
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="var(--ink)" stroke-width="2"><title>'
                     'application path ends: %.1f ms</title></line>'
                     % (60 + app_ms * scale, y - 3, 60 + app_ms * scale,
                        y + 21, app_ms))
        parts.append(_text(x + 6, y + 13, "%.0f ms" % sum(ms.values()),
                           "lbl", "start"))
    parts.append(_text(60, 16, "per-frame budget -- tick = end of the "
                       "application path; gray = streaming overhead a "
                       "deployed counter never pays", "lbl", "start"))
    parts.append("</svg>")
    return "".join(parts)


def chart_conf(agg, boards):
    """View 4: model confidence vs px (model-box min side; grid-quantized)."""
    W, H = 640, 210
    x0, x1, y0, y1 = 46, W - 12, 16, 168
    pts_all = [(b, px, cf) for b in boards
               for k in agg if k[1] == b
               for (px, cf) in agg[k]["model_pts"]]
    if not pts_all:
        return "<p class='muted'>no model detections recorded</p>"
    maxpx = max(px for _, px, _ in pts_all) or 1
    parts = [svg_open(W, H)]
    parts.append(_grid_h(x0, x1, [y1 - f * (y1 - y0) for f in (0.5, 1.0)]))
    parts.append(_text(x0 - 6, y1 - (y1 - y0) + 4, "1.0", "lbl", "end"))
    parts.append(_text(x0 - 6, y1 - 0.5 * (y1 - y0) + 4, "0.5", "lbl", "end"))
    for b, var in zip(boards, ("s1", "s2")):
        for k in agg:
            if k[1] != b:
                continue
            for px, cf in agg[k]["model_pts"]:
                parts.append('<circle cx="%.1f" cy="%.1f" r="4" '
                             'fill="var(--%s)" fill-opacity="0.55">'
                             '<title>%s %d px conf %.2f</title></circle>'
                             % (x0 + (px / maxpx) * (x1 - x0 - 10),
                                y1 - cf * (y1 - y0), var, b, px, cf))
    parts.append(_text(x0 + (x1 - x0) / 2, y1 + 30,
                       "detected size, px (model boxes are grid-quantized "
                       "~27 px at VGA)", "lbl"))
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def _fmt(v, spec="%.1f"):
    return "not measured" if v is None else (spec % v)


def table(headers, rows):
    h = "".join("<th>%s</th>" % html.escape(str(x)) for x in headers)
    body = "".join("<tr>%s</tr>" % "".join(
        "<td>%s</td>" % html.escape(str(x)) for x in r) for r in rows)
    return "<table><tr>%s</tr>%s</table>" % (h, body)


def render(agg, power, infer_only, power_note):
    boards = sorted({b for _, b in agg})
    classes = next(iter(agg.values()))["classes"] if agg else []
    runs = sorted({r for r, _ in agg})

    # -- view tables ------------------------------------------------------
    v1_rows = []
    for run in runs:
        for b in boards:
            s = agg.get((run, b))
            if not s:
                continue
            for ci, c in enumerate(s["classes"]):
                v1_rows.append((run, b, c, s["truth"][c],
                                "%.1f" % s["mean_bc"][ci],
                                "%.1f" % s["mean_mc"][ci]))
    v1_tbl = table(("run", "board", "class", "truth", "blob mean",
                    "model mean"), v1_rows)

    v3_rows = []
    for b in boards:
        keys = [k for k in agg if k[1] == b]
        n = max(1, sum(agg[k]["n"] for k in keys))
        ms = {st: sum(agg[k]["stage_ms"][st] * agg[k]["n"] for k in keys) / n
              for st, _, _ in STAGES}
        fps = sum(agg[k]["fps"] * agg[k]["n"] for k in keys) / n
        v3_rows.append((b, "%.1f" % ms["cap_us"], "%.1f" % ms["blob_us"],
                        "%.1f" % ms["inf_us"], "%.1f" % ms["mdec_us"],
                        "%.1f" % ms["enc_us"], "%.1f" % fps))
    v3_tbl = table(("board", "capture ms", "blob ms", "infer ms", "decode ms",
                    "encode ms", "delivered fps"), v3_rows)

    score_rows = []
    for b in boards:
        keys = [k for k in agg if k[1] == b]
        n = max(1, sum(agg[k]["n"] for k in keys))
        infer = sum(agg[k]["stage_ms"]["inf_us"] * agg[k]["n"]
                    for k in keys) / n
        bex = sum(agg[k]["b_exact"] for k in keys) / n
        mex = sum(agg[k]["m_exact"] for k in keys) / n
        fps = sum(agg[k]["fps"] * agg[k]["n"] for k in keys) / n
        watts = power.get(b)
        mj = (watts * infer) if watts is not None else None
        score_rows.append((b, "%.1f" % fps, "%.2f" % infer,
                           "%.0f%%" % (100 * bex), "%.0f%%" % (100 * mex),
                           _fmt(infer_only.get(b), "%.1f /s"),
                           _fmt(watts, "%.2f W"), _fmt(mj, "%.2f mJ")))
    score_tbl = table(("board", "fps", "infer ms", "blob exact", "model exact",
                       "infer-only ceiling", "power", "mJ/inference"),
                      score_rows)

    conf_note = ("Blobs carry NO confidence by construction -- find_blobs is "
                 "a hard LAB threshold (a pixel passes or it does not); the "
                 "nearest analogue is pixel count. Documented asymmetry, not "
                 "an omission.")

    css_vars = lambda i: ";".join("--%s:%s" % (k, v[i]) for k, v in C.items())
    style = """
<style>
 .r { color-scheme: light; %s }
 @media (prefers-color-scheme: dark) {
   :root:not([data-theme="light"]) .r { color-scheme: dark; %s } }
 :root[data-theme="dark"] .r { color-scheme: dark; %s }
 .r { background: var(--surface); color: var(--ink);
      font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
      max-width: 46rem; margin: 0 auto; padding: 1.2rem; }
 .r h1 { font-size: 1.3rem; } .r h2 { font-size: 1.05rem; margin-top: 1.6rem; }
 .r .muted { color: var(--ink2); }
 .r table { border-collapse: collapse; margin: .6rem 0; font-size: .88rem;
            font-variant-numeric: tabular-nums; }
 .r th, .r td { border-bottom: 1px solid var(--grid); padding: .25rem .6rem;
                text-align: right; }
 .r th:first-child, .r td:first-child { text-align: left; }
 .r th { color: var(--ink2); font-weight: 600; }
 .r .lbl { font: 11px system-ui; fill: var(--muted); }
 .r .lbl2 { font: 12px system-ui; fill: var(--ink2); }
 .r .legend span { display:inline-block; margin-right:1rem; }
 .r .legend i { display:inline-block; width:12px; height:12px;
                border-radius:3px; vertical-align:-1px; margin-right:.35rem; }
 .r figure { margin: .6rem 0 0; }
</style>""" % (css_vars(0), css_vars(1), css_vars(1))

    leg = ('<div class="legend"><span><i style="background:var(--s1)"></i>'
           'blob</span><span><i style="background:var(--s2)"></i>model</span>'
           '<span style="color:var(--ink2)">--- ground truth</span></div>')

    parts = ["<!doctype html><html><head><meta charset='utf-8'>",
             "<title>S8 accuracy &amp; cost report</title>", style,
             "</head><body><div class='r'>",
             "<h1>S8 -- two-colour detector: accuracy &amp; cost</h1>",
             "<p class='muted'>Rendered from recorded campaign rows; every "
             "chart is backed by its table. Distance axis dropped (Nick "
             "2026-08-21): px-on-target is the axis that transfers.</p>"]

    parts.append("<h2>1 -- Does it count right?</h2>" + leg)
    for b in boards:
        parts.append("<h3 class='muted'>%s</h3>" % b)
        parts.append("<figure>%s</figure>" % chart_counts(agg, b, classes))
    parts.append(v1_tbl)

    parts.append("<h2>2 -- What transfers: accuracy vs pixels-on-target</h2>"
                 + leg)
    v2_tbl_rows = []
    for b in boards:
        binned = px_bin_accuracy(agg, b)
        parts.append("<h3 class='muted'>%s</h3>" % b)
        parts.append("<figure>%s</figure>" % chart_px(binned, b))
        for lab, n, bf, mf in binned:
            if n:
                v2_tbl_rows.append((b, lab, n, "%.0f%%" % (100 * bf),
                                    "%.0f%%" % (100 * mf)))
    parts.append(table(("board", "px bin", "frames", "blob exact",
                        "model exact"), v2_tbl_rows))
    parts.append("<p class='muted'>Shaded band = the T2 &ge;24-32 px "
                 "detection floor (SPEC). Frame px = median blob-box min "
                 "side (pixel-accurate, method-independent).</p>")

    parts.append("<h2>3 -- Where the frame budget goes</h2>")
    parts.append("<figure>%s</figure>" % chart_stages(agg, boards))
    parts.append(v3_tbl)

    parts.append("<h2>4 -- Confidence vs size (model only)</h2>")
    parts.append("<figure>%s</figure>" % chart_conf(agg, boards))
    parts.append("<p class='muted'>%s</p>" % conf_note)

    parts.append("<h2>5 -- The board scorecard</h2>")
    parts.append(score_tbl)
    parts.append("<p class='muted'>Energy method: %s</p>"
                 % html.escape(power_note))
    parts.append("</div></body></html>")
    return "".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rows", nargs="+", help="rows.jsonl file(s) from --record")
    ap.add_argument("--out",
                    default="pi/workbench/recipes/guides/s8-metrics-report.html")
    ap.add_argument("--power", default=None,
                    help='measured watts per board, e.g. "AE3=0.21,N6=1.02"')
    ap.add_argument("--infer-only", default=None,
                    help='inference-only ceiling /s, e.g. "AE3=27.5,N6=42"')
    ap.add_argument("--power-note",
                    default="one USB meter, swapped between runs; "
                            "constant-load assumption (same model, same "
                            "scene class) -- Nick 2026-08-21")
    args = ap.parse_args(argv)
    rows = load_rows(args.rows)
    agg = aggregate(rows)
    if not agg:
        print("no frame rows found in %s" % ", ".join(args.rows),
              file=sys.stderr)
        return 1
    html_text = render(agg, parse_kv_floats(args.power),
                       parse_kv_floats(args.infer_only), args.power_note)
    with open(args.out, "w") as fh:
        fh.write(html_text)
    n_frames = sum(s["n"] for s in agg.values())
    print("report: %s  (%d frame rows, %d run×board cells, boards: %s)"
          % (args.out, n_frames, len(agg),
             ", ".join(sorted({b for _, b in agg}))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
