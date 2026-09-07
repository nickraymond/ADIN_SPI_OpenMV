#!/usr/bin/env python3
"""S28 patch-card media generator + the ONE patch definition.

Generates a playback-server media dir (``stills/frames/*.jpg``, no
manifest — playback_server.load_media falls back to sorted frames/) with
two 1920x1080 stills:

  s28_000_card.jpg   the patch card: 6 uniform patches inside aim box D
  s28_001_gray.jpg   full-content uniform mid-gray (whole-frame flicker
                     stimulus for the LCD-PWM check)

PATCHES is imported by s28_burst_stats.py — one definition, zero drift
(the playback MARKERS precedent). Patch positions are CONTENT-BOX
fractions; the still fills the content box (16:9 into 16:9, object-fit
fill), so still fractions == content fractions == what the E11 CamMap
maps to camera px. Everything sits inside aim box D (x 0.185..0.815,
y 0.15..0.85 — the region both cameras actually frame; see
playback_server AIM_BOXES/MARKERS), with margin so an off-center camera
still sees every patch whole after the homography.
"""
import argparse
import os

# (name, cx, cy, w, h, (r, g, b)) — content-box fractions.
PATCHES = [
    ("white",  0.32, 0.35, 0.11, 0.17, (235, 235, 235)),
    ("gray50", 0.50, 0.35, 0.11, 0.17, (128, 128, 128)),
    ("gray20", 0.68, 0.35, 0.11, 0.17, (52, 52, 52)),
    ("red",    0.32, 0.63, 0.11, 0.17, (200, 40, 40)),
    ("green",  0.50, 0.63, 0.11, 0.17, (40, 200, 40)),
    ("blue",   0.68, 0.63, 0.11, 0.17, (40, 40, 200)),
]
BACKGROUND = (70, 70, 70)
GRAY_STILL = (110, 110, 110)
CARD_W, CARD_H = 1920, 1080

# Still indices in the sorted frames/ dir — the collector steps to these.
STILL_CARD = 0
STILL_GRAY = 1


def render_card(w=CARD_W, h=CARD_H):
    """-> PIL Image of the patch card."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), BACKGROUND)
    dr = ImageDraw.Draw(im)
    for name, cx, cy, pw, ph, rgb in PATCHES:
        x0 = int((cx - pw / 2) * w)
        y0 = int((cy - ph / 2) * h)
        x1 = int((cx + pw / 2) * w)
        y1 = int((cy + ph / 2) * h)
        dr.rectangle([x0, y0, x1, y1], fill=rgb)
    return im


def render_gray(w=CARD_W, h=CARD_H):
    from PIL import Image
    return Image.new("RGB", (w, h), GRAY_STILL)


def write_media(media_dir):
    """Write the playback media dir; -> list of still relpaths (sorted
    order == playback still indices)."""
    frames = os.path.join(media_dir, "stills", "frames")
    os.makedirs(frames, exist_ok=True)
    # JPEG quality 95: the LCD shows 8-bit content either way; what the
    # experiment measures is CAMERA noise, not the stimulus encoding.
    render_card().save(os.path.join(frames, "s28_000_card.jpg"),
                       quality=95)
    render_gray().save(os.path.join(frames, "s28_001_gray.jpg"),
                       quality=95)
    return ["stills/frames/s28_000_card.jpg",
            "stills/frames/s28_001_gray.jpg"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.expanduser("~/s28_media"),
                    help="media dir for playback_server --media")
    args = ap.parse_args()
    stills = write_media(args.out)
    print("wrote %d stills under %s" % (len(stills), args.out))
    for s in stills:
        print("  " + s)
    print("serve with: python3 pi/hil/playback_server.py --media "
          + args.out)


if __name__ == "__main__":
    main()
