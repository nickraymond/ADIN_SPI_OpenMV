#!/usr/bin/env python3
"""Align every rectified card to one reference by intensity registration.

Two anchor attempts failed before this one and both failures are informative:
the raw card boundary leaves the printed content at slightly different scales
(the AE3 NEW ramp then sampled its darkest step as its brightest), and the
corner tags -- even upscaled -- were repeatedly out-voted by printed text when
picked by quadrant proximity.

ECC registration sidesteps both by using ALL the content: it solves for the
homography that maximises correlation against the reference. It needs no
feature to be individually detectable, which is the right property for a
target this small and this soft.
"""
import sys, cv2, numpy as np

W, H = 900, 560
REF = None

def prep(im):
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    g -= g.mean()
    s = g.std()
    return g / s if s else g

def align_to(ref_g, im):
    g = prep(im)
    warp = np.eye(3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 400, 1e-7)
    try:
        _, warp = cv2.findTransformECC(ref_g, g, warp, cv2.MOTION_HOMOGRAPHY,
                                       crit, None, 5)
    except cv2.error as exc:
        return None, str(exc).strip().splitlines()[-1][:60]
    out = cv2.warpPerspective(im, warp, (W, H),
                              flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP)
    return out, None

if __name__ == "__main__":
    ref_path = sys.argv[1]
    ref = cv2.imread(ref_path)
    ref_g = prep(ref)
    cv2.imwrite(ref_path.replace("rect_", "algn_"), ref)
    print("%-22s reference" % ref_path.rsplit("/", 1)[-1])
    for p in sys.argv[2:]:
        im = cv2.imread(p)
        out, err = align_to(ref_g, im)
        n = p.rsplit("/", 1)[-1]
        if out is None:
            print("%-22s ECC FAILED: %s" % (n, err)); continue
        cv2.imwrite(p.replace("rect_", "algn_"), out)
        # residual: correlation after alignment, as a sanity number
        c = float(np.mean(prep(out) * ref_g))
        print("%-22s aligned, correlation %.3f" % (n, c))
