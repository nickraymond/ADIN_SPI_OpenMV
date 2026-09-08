#!/usr/bin/env python3
"""Colour and sharpness on the RECTIFIED card.

Every measurement is on the 900x560 canonical canvas, so angle and tilt are
already removed and all four cameras are compared on identical geometry. The
native card size is within 3% across all four frames, so this is not hiding a
large pixels-on-target difference either.
"""
import json, sys, cv2, numpy as np

# ROIs on the 900x560 canvas, read off a coordinate-grid overlay.
WHITE   = (360, 430, 560, 540)      # clean unprinted card
GREY    = (100, 220, 330, 292)      # 5-step greyscale ramp
COLOUR  = (520, 212, 800, 302)      # colour patch block
BAR     = (445, 330, 615, 380)      # solid dark bar: a strong horizontal edge

def roi(im, r):
    x0, y0, x1, y1 = r
    return im[y0:y1, x0:x1]

def edge_rise(im):
    """10-90% rise distance in canvas px on the dark bar's top edge.

    Sub-pixel by linear interpolation between samples: the canvas upsamples the
    native card ~4.5x, so an integer-index measurement quantises to whole
    canvas pixels and every camera scores the same 12. Larger = softer.
    """
    g = cv2.cvtColor(roi(im, BAR), cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = cv2.GaussianBlur(g, (3, 1), 0)          # along-edge only, keeps the profile
    rises = []
    for c in range(g.shape[1]):
        col = g[:, c]
        hi, lo = float(col.max()), float(col.min())
        if hi - lo < 25:
            continue
        i_hi, i_lo = int(np.argmax(col)), int(np.argmin(col))
        if i_lo <= i_hi:
            continue                              # want bright above dark
        seg = col[i_hi:i_lo + 1]
        if len(seg) < 3:
            continue
        t10, t90 = lo + 0.1 * (hi - lo), lo + 0.9 * (hi - lo)
        x = np.arange(len(seg), dtype=np.float32)
        d = seg[::-1]                             # make it increasing for interp
        xs = x[::-1]
        p90 = np.interp(t90, d, xs)
        p10 = np.interp(t10, d, xs)
        r = float(p10 - p90)
        if 0 < r < len(seg):
            rises.append(r)
    return float(np.median(rises)) if rises else float("nan")


def grey_steps(im):
    """Mean luma of each of the 5 ramp steps, left to right."""
    g = cv2.cvtColor(roi(im, GREY), cv2.COLOR_BGR2GRAY).astype(np.float32)
    w = g.shape[1] // 5
    return [float(g[:, i * w + w // 4:i * w + 3 * w // 4].mean()) for i in range(5)]

def colour_patches(im, rows=2, cols=6):
    """Mean BGR of each patch in the colour block."""
    p = roi(im, COLOUR).astype(np.float32)
    h, w = p.shape[:2]
    out = []
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * h // rows, (r + 1) * h // rows
            x0, x1 = c * w // cols, (c + 1) * w // cols
            cell = p[y0 + (y1-y0)//4:y1 - (y1-y0)//4, x0 + (x1-x0)//4:x1 - (x1-x0)//4]
            b, g_, r_ = cell[..., 0].mean(), cell[..., 1].mean(), cell[..., 2].mean()
            out.append((float(r_), float(g_), float(b)))
    return out

def report(path):
    im = cv2.imread(path)
    wht = roi(im, WHITE).astype(np.float32)
    wb, wg, wr = wht[..., 0].mean(), wht[..., 1].mean(), wht[..., 2].mean()
    steps = grey_steps(im)
    pats = colour_patches(im)
    # saturation of the colour block, in HSV, as a colour-vividness proxy
    hsv = cv2.cvtColor(roi(im, COLOUR), cv2.COLOR_BGR2HSV)
    sat = float(hsv[..., 1].mean())
    # chroma spread of the WHITE area = colour noise where there should be none
    wn = float(np.mean([wht[..., i].std() for i in range(3)]))
    return {
        "path": path.rsplit("/", 1)[-1],
        "edge_rise_px": edge_rise(im),
        "white_R": float(wr), "white_G": float(wg), "white_B": float(wb),
        "wb_RG": float(wr / wg), "wb_BG": float(wb / wg),
        "white_noise": wn,
        "grey_steps": [round(s, 1) for s in steps],
        "ramp_range": round(steps[0] - steps[-1], 1),
        "colour_sat": sat,
        "patches": [[round(v, 1) for v in p] for p in pats],
    }

if __name__ == "__main__":
    out = [report(p) for p in sys.argv[1:]]
    print(json.dumps(out, indent=1))
