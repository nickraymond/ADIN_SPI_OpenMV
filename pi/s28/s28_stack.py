"""S28 bite 2 — offline frame-stacking core (the testable math).

Merges a locked BAYER burst (8-bit, linear — the domain the notes call
for) into a single denoised frame, three ways, and measures the noise it
removed. Pure numpy + PIL; no board, no OpenCV in the core (demosaic uses
OpenCV only when producing a viewable image).

Merge modes (the S28 kickoff's three):
  mean          average of N frames — best SNR (temporal noise / sqrt N),
                and recovers sub-LSB detail (dither) before the 8-bit
                quantization the single frame already suffered.
  median        per-pixel median — rejects transient occluders (a drifting
                particle, a fish) instead of ghosting them; slightly less
                SNR gain than mean.
  sigma_clip    per-pixel: drop frames > kappa*sigma from the mean, then
                mean the rest — the best-of-both the notes recommend.

Noise is measured as the SPATIAL std on a flat patch (a uniform region is
pure noise), single vs merged — the honest, directly-visible number.
"""
import numpy as np


def load_burst(run_dir, stage):
    """-> (N,H,W) uint8 raw-BAYER stack + the meta rows, in seq order."""
    import json
    import os
    rows = []
    with open(os.path.join(run_dir, "meta.jsonl")) as fh:
        for ln in fh:
            r = json.loads(ln)
            if r["stage"] == stage:
                rows.append(r)
    rows.sort(key=lambda r: r["seq"])
    frames = []
    for r in rows:
        buf = open(os.path.join(run_dir, r["file"]), "rb").read()
        frames.append(np.frombuffer(buf, np.uint8).reshape(r["h"], r["w"]))
    return np.stack(frames), rows


def merge_mean(stack, k=None):
    s = stack[:k] if k else stack
    return np.clip(np.round(s.mean(axis=0)), 0, 255).astype(np.uint8)


def merge_median(stack, k=None):
    s = stack[:k] if k else stack
    return np.median(s, axis=0).astype(np.uint8)


def merge_sigma_clip(stack, k=None, kappa=2.5):
    s = (stack[:k] if k else stack).astype(np.float64)
    mean = s.mean(axis=0)
    std = s.std(axis=0)
    # mask frames beyond kappa*sigma; where std==0 keep everything
    lo, hi = mean - kappa * std, mean + kappa * std
    keep = (s >= lo) & (s <= hi)
    keep |= (std == 0)                      # avoid div-by-zero cols
    ssum = (s * keep).sum(axis=0)
    cnt = keep.sum(axis=0)
    out = ssum / np.maximum(cnt, 1)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def temporal_sigma(stack, k=None):
    """Per-pixel temporal std across the burst, mean over pixels — the
    single-frame noise the merge attacks."""
    s = stack[:k] if k else stack
    return float(s.std(axis=0, ddof=1).mean()) if len(s) > 1 else 0.0


def _green_plane(bayer):
    """BGGR green sites -> (H/2,W/2) float."""
    return (bayer[0::2, 1::2].astype(np.float64)
            + bayer[1::2, 0::2].astype(np.float64)) / 2.0


def noise_ladder(stack, merge_fn, ks=(1, 2, 4, 8)):
    """TEMPORAL noise of a k-frame merge, measured honestly: split the
    burst into disjoint groups of k, merge each, and take the per-pixel
    std ACROSS the group-merges (green plane, whole frame). FIXED scene
    structure is identical in every group and cancels, leaving pure
    temporal noise — so this shows the true ~sqrt(N) fall that a spatial
    std on a 'uniform' patch hides behind print/lighting texture.
    -> {k: noise}. Needs >=2 groups per k."""
    out = {}
    N = len(stack)
    for k in ks:
        ng = N // k
        if ng < 2:
            continue
        groups = np.stack([_green_plane(merge_fn(stack[i * k:(i + 1) * k]))
                           for i in range(ng)])
        out[k] = float(groups.std(axis=0, ddof=1).mean())
    return out


def demosaic(bayer, pattern="BGGR"):
    """Raw BAYER (H,W) uint8 -> RGB (H,W,3) uint8, for VIEWING only
    (consistent method for single and merged, so the comparison is fair;
    absolute color is not the point — noise is). Uses OpenCV bilinear when
    present, else a numpy+PIL half-res-plane fallback so the tool runs on
    a bare Pi."""
    try:
        import cv2
        code = {"BGGR": cv2.COLOR_BayerRG2RGB,   # OpenCV names by 2nd row
                "RGGB": cv2.COLOR_BayerBG2RGB,
                "GRBG": cv2.COLOR_BayerGB2RGB,
                "GBRG": cv2.COLOR_BayerGR2RGB}[pattern]
        return cv2.cvtColor(bayer, code)
    except Exception:
        from PIL import Image
        assert pattern == "BGGR"
        H, W = bayer.shape
        B = bayer[0::2, 0::2]
        R = bayer[1::2, 1::2]
        G = ((bayer[0::2, 1::2].astype(np.uint16)
              + bayer[1::2, 0::2]) // 2).astype(np.uint8)
        up = lambda p: np.asarray(                                 # noqa
            Image.fromarray(p).resize((W, H), Image.BILINEAR))
        return np.stack([up(R), up(G), up(B)], axis=-1)


def jpeg_size(rgb, quality=50):
    """Bytes of `rgb` encoded at a JPEG quality — denoised frames compress
    smaller (noise is incompressible), a free S28 win to show."""
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, "JPEG", quality=quality)
    return b.tell()
