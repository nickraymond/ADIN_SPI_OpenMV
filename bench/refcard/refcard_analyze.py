#!/usr/bin/env python3
"""Reference-card color analyzer (S28 side-quest, 2026-09-02).

Measure a camera's color reproduction against the Nereus Reef Reference
Card V1 — the true ground truth (the N6 is NOT a reference; it has its
own ISP color error). Detects the card's four 36H11 AprilTags, rectifies
via homography, samples every patch, and reports per-patch delta-E vs the
card's known sRGB values. Then FITS a color-correction matrix (CCM) from
the patches and reports how much closer the AE3 (or any camera) can get —
the "how good can the color be" answer, testable in post before deciding
whether to bake a CCM into firmware.

    python3 bench/refcard/refcard_analyze.py --image ref_AE3.jpg --label AE3
    # writes <label>_report.json, <label>_overlay.jpg, <label>_corrected.jpg

Caveats it prints, not hides: the card is lit by ambient light (not a
calibrated illuminant) and the camera white-balances, so ABSOLUTE
delta-E carries illuminant error; the CCM absorbs most of it, and the
AE3-vs-N6 comparison at the same moment is the robust read. Glare on the
laminate corrupts individual patches — flagged per patch (near-clipped).

Pure-math core (homography sampling, sRGB<->Lab, CCM fit) is importable
and host-tested in test_refcard.py; only detect_tags needs OpenCV.
"""
import argparse
import json
import os

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_DEFAULT = os.path.join(_HERE, "refcard_v1.json")

# Tag id -> card corner (verified on both cameras 2026-09-02).
TAGID_CORNER = {0: "TL", 1: "TR", 2: "BL", 3: "BR"}


# --------------------------------------------------------- color science
def srgb_to_linear(c):
    c = np.asarray(c, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, np.float64), 0.0, 1.0)
    s = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
    return np.clip(s * 255.0, 0, 255)


_M_XYZ = np.array([[0.4124, 0.3576, 0.1805],
                   [0.2126, 0.7152, 0.0722],
                   [0.0193, 0.1192, 0.9505]])
_WHITE = np.array([0.95047, 1.0, 1.08883])   # D65


def rgb_to_lab(rgb):
    """sRGB (0-255) -> CIELAB (D65)."""
    lin = srgb_to_linear(rgb)
    xyz = lin @ _M_XYZ.T / _WHITE

    def f(t):
        d = 6 / 29
        return np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4 / 29)
    fx, fy, fz = f(xyz[..., 0]), f(xyz[..., 1]), f(xyz[..., 2])
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)],
                    axis=-1)


def delta_e76(a, b):
    """CIE76 delta-E between two sRGB colors (or arrays)."""
    return np.linalg.norm(rgb_to_lab(a) - rgb_to_lab(b), axis=-1)


def saturation(rgb):
    rgb = np.asarray(rgb, np.float64)
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    return np.where(mx > 0, (mx - mn) / mx, 0.0)


# ------------------------------------------------------------ geometry
def solve_homography(src, dst):
    """4+ correspondences src->dst -> 3x3 H (DLT, least squares)."""
    A = []
    for (sx, sy), (dx, dy) in zip(src, dst):
        A.append([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy, -dx])
        A.append([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy, -dy])
    _, _, vt = np.linalg.svd(np.asarray(A, np.float64))
    return (vt[-1] / vt[-1, -1]).reshape(3, 3)


def apply_h(H, pts):
    pts = np.asarray(pts, np.float64)
    p = np.hstack([pts, np.ones((len(pts), 1))]) @ H.T
    return p[:, :2] / p[:, 2:3]


# --------------------------------------------------------------- CCM
def fit_ccm(measured, true):
    """Least-squares 3x4 CCM in LINEAR light: linear_true ~= [lin_meas|1] @ M.
    Returns M (4x3). Fitting linear + affine offset captures gain, cross-
    talk, and black level in one solve."""
    lm = srgb_to_linear(measured)
    lt = srgb_to_linear(true)
    X = np.hstack([lm, np.ones((len(lm), 1))])
    M, *_ = np.linalg.lstsq(X, lt, rcond=None)
    return M


def apply_ccm(M, rgb):
    """Apply a fitted CCM (4x3) to sRGB values -> corrected sRGB."""
    lin = srgb_to_linear(rgb)
    flat = lin.reshape(-1, 3)
    out = np.hstack([flat, np.ones((len(flat), 1))]) @ M
    return linear_to_srgb(out).reshape(rgb.shape)


# --------------------------------------------------------- detection
def detect_tags(gray, family="36H11"):
    """-> {corner: (x,y) image center} using the card's AprilTags."""
    import cv2
    fam = getattr(cv2.aruco, "DICT_APRILTAG_" + family)
    d = cv2.aruco.getPredefinedDictionary(fam)
    det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None:
        raise SystemExit("FAIL: no AprilTags found — is the whole card "
                         "in frame and lit?")
    out = {}
    for c, i in zip(corners, ids.flatten()):
        if int(i) in TAGID_CORNER:
            out[TAGID_CORNER[int(i)]] = tuple(c[0].mean(0))
    missing = [k for k in ("TL", "TR", "BL", "BR") if k not in out]
    if missing:
        raise SystemExit("FAIL: card tags missing %s (found ids %s) — "
                         "re-aim so all four corners are visible"
                         % (missing, ids.flatten().tolist()))
    return out


def sample_patch(img, center, half=6):
    """Median RGB of a small window at an image-space center (median is
    glare/edge robust). Returns (rgb, clipped_frac)."""
    x, y = int(round(center[0])), int(round(center[1]))
    h, w = img.shape[:2]
    x0, y0 = max(0, x - half), max(0, y - half)
    x1, y1 = min(w, x + half), min(h, y + half)
    reg = img[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
    if len(reg) == 0:
        return None, 1.0
    clipped = float(((reg >= 250).any(1) | (reg <= 5).all(1)).mean())
    return np.median(reg, axis=0), clipped


# --------------------------------------------------------------- analyze
def analyze(image_bgr_rgb, spec):
    """image_bgr_rgb: HxWx3 RGB uint8. -> dict of results."""
    import cv2
    gray = cv2.cvtColor(image_bgr_rgb, cv2.COLOR_RGB2GRAY)
    tags = detect_tags(gray, spec.get("tag_family", "36H11"))
    src = [spec["tags"][k] for k in ("TL", "TR", "BL", "BR")]
    dst = [tags[k] for k in ("TL", "TR", "BL", "BR")]
    H = solve_homography(src, dst)

    rows = []
    meas, true = [], []
    for p in spec["patches"]:
        ic = apply_h(H, [[p["cx"], p["cy"]]])[0]
        rgb, clip = sample_patch(image_bgr_rgb, ic)
        if rgb is None:
            continue
        de = float(delta_e76(rgb, p["rgb"]))
        rows.append({"name": p["name"], "true": p["rgb"],
                     "measured": [round(v) for v in rgb],
                     "deltaE": round(de, 1), "clipped": round(clip, 2),
                     "img_xy": [round(ic[0]), round(ic[1])]})
        if clip < 0.25:                       # trust only clean patches
            meas.append(rgb)
            true.append(p["rgb"])
    meas, true = np.array(meas), np.array(true)

    # CCM from the clean patches, then re-score
    M = fit_ccm(meas, true)
    corr = apply_ccm(M, meas)
    for r in rows:
        if r["clipped"] < 0.25:
            c = apply_ccm(M, np.array([r["measured"]], np.float64))[0]
            r["corrected"] = [round(v) for v in c]
            r["deltaE_corrected"] = round(float(delta_e76(c, r["true"])), 1)

    clean = [r for r in rows if "deltaE_corrected" in r]
    return {
        "H": H.tolist(), "tags": {k: [round(v[0]), round(v[1])]
                                  for k, v in tags.items()},
        "ccm": M.tolist(),
        "n_patches": len(rows), "n_clean": len(clean),
        "mean_deltaE": round(float(np.mean([r["deltaE"] for r in clean])), 1),
        "mean_deltaE_corrected": round(
            float(np.mean([r["deltaE_corrected"] for r in clean])), 1),
        "rows": rows,
    }


def main():
    import cv2
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True)
    ap.add_argument("--label", default="cam")
    ap.add_argument("--spec", default=SPEC_DEFAULT)
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    spec = json.load(open(args.spec))
    bgr = cv2.imread(args.image)
    if bgr is None:
        raise SystemExit("FAIL: cannot read %s" % args.image)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    res = analyze(rgb, spec)

    os.makedirs(args.out_dir, exist_ok=True)
    json.dump(res, open(os.path.join(args.out_dir,
                                     "%s_report.json" % args.label), "w"),
              indent=1)

    # overlay: sampled centers + per-patch deltaE
    ov = bgr.copy()
    for r in res["rows"]:
        x, y = r["img_xy"]
        cv2.circle(ov, (x, y), 5, (0, 0, 255), 1)
        cv2.putText(ov, str(r["deltaE"]), (x - 10, y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
    cv2.imwrite(os.path.join(args.out_dir, "%s_overlay.jpg" % args.label),
                ov)

    # corrected full frame (apply the CCM everywhere)
    M = np.array(res["ccm"])
    corr = apply_ccm(M, rgb.astype(np.float64)).astype(np.uint8)
    cv2.imwrite(os.path.join(args.out_dir, "%s_corrected.jpg" % args.label),
                cv2.cvtColor(corr, cv2.COLOR_RGB2BGR))

    print("== %s vs Reef Reference Card V1 ==" % args.label)
    print("  tags: all 4 found; %d patches sampled (%d clean of glare)"
          % (res["n_patches"], res["n_clean"]))
    print("  %-11s %-14s %-14s %6s %6s" %
          ("patch", "true", "measured", "dE", "dE_cc"))
    for r in res["rows"]:
        print("  %-11s %-14s %-14s %6s %6s%s" %
              (r["name"], tuple(r["true"]), tuple(r["measured"]),
               r["deltaE"], r.get("deltaE_corrected", "  -"),
               "  GLARE" if r["clipped"] >= 0.25 else ""))
    print("  ----")
    print("  mean deltaE (clean patches):  %5.1f  -> after CCM: %5.1f"
          % (res["mean_deltaE"], res["mean_deltaE_corrected"]))
    print("  (ambient light, not a calibrated illuminant — the CCM number"
          " is the achievable-color read; absolute dE carries illuminant"
          " error.)")
    print("wrote %s_{report.json,overlay.jpg,corrected.jpg}" % args.label)


if __name__ == "__main__":
    main()
