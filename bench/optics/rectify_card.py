#!/usr/bin/env python3
"""Rectify the reference card via its four corner fiducials.

The card carries four AprilTags, one per corner. At this working distance they
are ~18 px across -- far too small to DECODE (36h11 needs ~10 cells plus a
quiet border) -- but decoding is not what a homography needs. Their centroids
are high-contrast, sub-pixel-locatable points at known positions on a planar
target, which is exactly the input for a 4-point homography. So the tags are
used as fiducials, not as identifiers.

Rectifying to a fixed canvas removes the angle/tilt difference between the two
rigs. It does NOT remove the difference in pixels-on-target: a camera further
away genuinely resolves less, and warping cannot invent that back. The output
therefore reports native card size alongside every sharpness number.
"""
import json, sys, cv2, numpy as np

#: Card location per frame, found by eye from a coordinate grid overlay and
#: recorded here so the run is reproducible. Only a search window -- the
#: corners themselves are measured, not asserted.
ROI = {
    "old2_AE3.jpg": (585, 395, 825, 570),
    "new3_AE3.jpg": (455, 160, 690, 295),
    "old2_N6.jpg":  (580, 390, 840, 570),
    "new3_N6.jpg":  (445, 290, 700, 470),
}

OUT_W, OUT_H = 900, 560          # canonical card canvas

def load(path, rot180):
    im = cv2.imread(path)
    return cv2.rotate(im, cv2.ROTATE_180) if rot180 else im

def find_card(im, roi):
    """The card's own white boundary -- a far stronger feature than its 18 px
    tags, which are too small to localise reliably (a first attempt on them
    produced a degenerate 133x19 quad by picking printed text in a line).

    Swept over threshold; the winner is the largest convex 4-gon of plausible
    landscape aspect. Corners are then refined to sub-pixel with cornerSubPix.
    """
    x0, y0, x1, y1 = roi
    sub = im[y0:y1, x0:x1]
    g = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    gb = cv2.GaussianBlur(g, (5, 5), 0)
    best = None
    for t in range(90, 220, 5):
        _, th = cv2.threshold(gb, t, 255, cv2.THRESH_BINARY)
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            a = cv2.contourArea(c)
            if a < 3000:
                continue
            p_ = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
            if len(p_) != 4 or not cv2.isContourConvex(p_):
                continue
            q = p_.reshape(4, 2).astype(np.float32)
            w = (np.linalg.norm(q[0]-q[1]) + np.linalg.norm(q[2]-q[3])) / 2
            h = (np.linalg.norm(q[1]-q[2]) + np.linalg.norm(q[3]-q[0])) / 2
            if h == 0 or not (1.2 < max(w, h) / min(w, h) < 2.4):
                continue
            if best is None or a > best[0]:
                best = (a, q, t)
    if not best:
        return None
    a, q, t = best
    q = cv2.cornerSubPix(g, q.reshape(-1, 1, 2), (5, 5), (-1, -1),
                         (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                          40, 0.01)).reshape(4, 2)
    q[:, 0] += x0
    q[:, 1] += y0
    return (a, order(q), t, 4)


def order(q):
    s, d = q.sum(1), np.diff(q, axis=1).ravel()
    return np.array([q[np.argmin(s)], q[np.argmin(d)],
                     q[np.argmax(s)], q[np.argmax(d)]], dtype=np.float32)


def rectify(im, quad):
    dst = np.array([[0, 0], [OUT_W - 1, 0], [OUT_W - 1, OUT_H - 1], [0, OUT_H - 1]],
                   dtype=np.float32)
    H = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    return cv2.warpPerspective(im, H, (OUT_W, OUT_H), flags=cv2.INTER_CUBIC)

if __name__ == "__main__":
    out = {}
    for spec in sys.argv[1:]:
        path, _, r = spec.partition("@")
        name = path.rsplit("/", 1)[-1]
        im = load(path, r == "180")
        f = find_card(im, ROI[name])
        if not f:
            print("%-16s NO CARD" % name); continue
        area, quad, t, n = f
        w = (np.linalg.norm(quad[0]-quad[1]) + np.linalg.norm(quad[3]-quad[2])) / 2
        h = (np.linalg.norm(quad[1]-quad[2]) + np.linalg.norm(quad[0]-quad[3])) / 2
        print("%-16s card %.0f x %.0f px (native)  aspect=%.2f  thr=%d"
              % (name, w, h, w/h if h else 0, t))
        rect = rectify(im, quad)
        dst = path.rsplit("/", 1)[0] + "/rect_" + name.replace(".jpg", ".png")
        cv2.imwrite(dst, rect)
        out[name] = {"native_w": float(w), "native_h": float(h),
                     "quad": quad.tolist(), "rect": dst}
    json.dump(out, open(sys.argv[1].rsplit("/", 1)[0] + "/rectify.json", "w"), indent=1)
