#!/usr/bin/env python3
"""Compare two cameras on the SAME scene. Scene-robust metrics only.

Every number here is chosen because it survives the fact that the two rigs
frame the scene slightly differently. Anything that depends on WHAT is in
frame (mean brightness, colour balance against a reference patch) is scene
measurement wearing a camera's clothes, and is reported as context only.
"""
import sys, json
import numpy as np
from PIL import Image

def load(path, rotate180=False):
    im = Image.open(path).convert("RGB")
    if rotate180:
        im = im.rotate(180)
    return im

def luma(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]

def noise_floor(y, patch=16):
    """Median local std over flat tiles -- the noise in smooth regions.

    Taking the MEDIAN over the flattest quartile of tiles, not the mean over
    all of them, is what makes this a camera measurement: edges and texture
    are scene content and would otherwise dominate.
    """
    h, w = y.shape
    ts = []
    for r in range(0, h - patch, patch):
        for c in range(0, w - patch, patch):
            ts.append(y[r:r + patch, c:c + patch].std())
    ts = np.array(sorted(ts))
    return float(np.median(ts[:max(1, len(ts) // 4)]))

def acutance(y):
    """Mean gradient magnitude, normalised by local contrast.

    Higher = crisper edges. Normalising by the image's own std keeps a
    brighter or more contrasty frame from scoring as 'sharper'.
    """
    gx = np.diff(y, axis=1)[:-1, :]
    gy = np.diff(y, axis=0)[:, :-1]
    g = np.sqrt(gx ** 2 + gy ** 2)
    s = y.std()
    return float(g.mean() / s) if s else 0.0

def hf_energy(y):
    """Fraction of spectral energy above 1/4 Nyquist. Resolution proxy."""
    f = np.fft.fftshift(np.abs(np.fft.fft2(y - y.mean())))
    h, w = f.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    r = np.sqrt(((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2)
    tot = f.sum()
    return float(f[r > 0.25].sum() / tot) if tot else 0.0

def vignette(y):
    """Centre luma / mean-corner luma. 1.0 = flat field."""
    h, w = y.shape
    ch, cw = h // 6, w // 6
    centre = y[h // 2 - ch:h // 2 + ch, w // 2 - cw:w // 2 + cw].mean()
    corners = np.mean([y[:ch * 2, :cw * 2].mean(), y[:ch * 2, -cw * 2:].mean(),
                       y[-ch * 2:, :cw * 2].mean(), y[-ch * 2:, -cw * 2:].mean()])
    return float(centre / corners) if corners else 0.0

def chroma_noise(a):
    """Noise in the colour difference channels only.

    Separates SENSOR/ISP colour noise from luma noise: chroma noise is the
    blotchy colour mottling that survives downscaling and looks worst
    underwater, where the blue channel is already starved.
    """
    y = luma(a)
    cb, cr = a[..., 2] - y, a[..., 0] - y
    return float(noise_floor(cb) + noise_floor(cr)) / 2.0

def clipping(y):
    return float((y >= 254).mean() * 100), float((y <= 1).mean() * 100)

def report(path, rotate180=False):
    im = load(path, rotate180)
    a = np.asarray(im).astype(np.float32)
    y = luma(a)
    hi, lo = clipping(y)
    return {
        "path": path, "w": im.width, "h": im.height, "rotated": rotate180,
        "mean": float(y.mean()), "std": float(y.std()),
        "p1": float(np.percentile(y, 1)), "p99": float(np.percentile(y, 99)),
        "noise_floor": noise_floor(y),
        "chroma_noise": chroma_noise(a),
        "acutance": acutance(y),
        "hf_energy": hf_energy(y),
        "vignette": vignette(y),
        "clip_hi_pct": hi, "clip_lo_pct": lo,
        "snr_db": float(20 * np.log10(y.std() / noise_floor(y))) if noise_floor(y) else 0.0,
    }

if __name__ == "__main__":
    out = []
    for spec in sys.argv[1:]:
        path, _, rot = spec.partition("@")
        out.append(report(path, rot == "180"))
    print(json.dumps(out, indent=1))
