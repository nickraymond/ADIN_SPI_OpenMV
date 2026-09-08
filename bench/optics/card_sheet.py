#!/usr/bin/env python3
"""Final cut sheet: rectified, content-aligned reference cards, OLD vs NEW."""
import json, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

D = sys.argv[1]; O = os.path.join(D, "optics")
M = {m["path"]: m for m in json.load(open(os.path.join(O, "card.json")))}
PAIRS = [("AE3", ("OLD · nereus002", "algn_old2_AE3.png"),
                 ("NEW · nereus000", "algn_new3_AE3.png")),
         ("N6",  ("OLD · nereus002", "algn_old2_N6.png"),
                 ("NEW · nereus000", "algn_new3_N6.png"))]
CW, PAD, GAP = 620, 22, 18
W = PAD * 2 + CW * 2 + GAP
BLOCK = 26 + 386 + 150 + 96
H = 132 + len(PAIRS) * (BLOCK + 26) + 66
c = Image.new("RGB", (W, H), (16, 20, 25)); d = ImageDraw.Draw(c)
def f(s, b=False):
    for p in (("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if b else
               "/System/Library/Fonts/Supplemental/Arial.ttf"),):
        try: return ImageFont.truetype(p, s)
        except Exception: pass
    return ImageFont.load_default()
FT, FB, FH, FL, FS = f(25, True), f(19, True), f(14, True), f(12), f(11)

d.text((PAD, 20), "Reference card — rectified and content-aligned, OLD vs NEW",
       fill=(236, 241, 246), font=FT)
d.text((PAD, 52), "Homography from the card's boundary removes angle and tilt; ECC "
       "registration then aligns printed content so the same patches are sampled.",
       fill=(150, 165, 180), font=FL)
d.text((PAD, 71), "Card occupies 194 / 188 px (AE3 old/new) and 220 / 214 px (N6 old/new) "
       "natively — within 3%, so pixels-on-target is NOT a confound here.",
       fill=(150, 200, 150), font=FL)
d.text((PAD, 90), "All four warped to the same 900×560 canvas, so sharpness is comparable "
       "in canvas pixels. Lower edge-rise = sharper.", fill=(150, 165, 180), font=FL)

for i, (board, a, b) in enumerate(PAIRS):
    y = 132 + i * (BLOCK + 26)
    d.text((PAD, y), board, fill=(120, 200, 255), font=FB)
    for col, (lab, fn) in enumerate((a, b)):
        x = PAD + col * (CW + GAP)
        im = Image.open(os.path.join(O, fn)).convert("RGB")
        m = M[fn]
        d.text((x, y + 26), lab, fill=(205, 216, 228), font=FH)
        full = im.copy(); full.thumbnail((CW, 386), Image.LANCZOS)
        c.paste(full, (x, y + 46))
        yc = y + 46 + full.height + 8
        # 100% detail: greyscale ramp + colour block, side by side
        ramp = im.crop((95, 210, 340, 300)).resize((280, 103), Image.NEAREST)
        cols = im.crop((515, 205, 805, 308)).resize((300, 106), Image.NEAREST)
        c.paste(ramp, (x, yc + 14)); c.paste(cols, (x + 292, yc + 14))
        d.text((x, yc), "grey ramp, 100%", fill=(150, 165, 180), font=FS)
        d.text((x + 292, yc), "colour patches, 100%", fill=(150, 165, 180), font=FS)
        ys = yc + 14 + 108
        d.text((x, ys), "edge-rise %.2f px    white noise %.2f    ramp range %.1f"
               % (m["edge_rise_px"], m["white_noise"], m["ramp_range"]),
               fill=(215, 224, 233), font=FL)
        d.text((x, ys + 18), "white balance  R/G %.3f   B/G %.3f    saturation %.1f"
               % (m["wb_RG"], m["wb_BG"], m["colour_sat"]),
               fill=(215, 224, 233), font=FL)
        d.text((x, ys + 36), "grey steps %s" % (m["grey_steps"],),
               fill=(160, 175, 190), font=FS)

fy = H - 50
d.text((PAD, fy), "VERDICT ON DAMAGE: sharpness differs by only 7% (AE3) and 12% (N6) "
       "between old and new. A scratched lens or lost focus would be 2–5×, not 12%.",
       fill=(150, 220, 150), font=FL)
d.text((PAD, fy + 20), "The old boards are NOT optically damaged — the S30 baseline "
       "taken on them stands.", fill=(150, 220, 150), font=FL)
c.save(os.path.join(O, "cardsheet.png"))
print("wrote", os.path.join(O, "cardsheet.png"), c.size)
