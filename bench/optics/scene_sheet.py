#!/usr/bin/env python3
"""Cut sheet: OLD vs NEW side by side, per board type, with 100% crops."""
import json, os, sys
from PIL import Image, ImageDraw, ImageFont

D = sys.argv[1]
OUT = os.path.join(D, "optics", "cutsheet.png")
M = json.load(open(os.path.join(D, "optics", "metrics.json")))
PAIRS = [("AE3", ("OLD  ·  nereus002", "old2_AE3.jpg", True, M[0]),
                 ("NEW  ·  nereus000", "new3_AE3.jpg", False, M[1])),
         ("N6",  ("OLD  ·  nereus002", "old2_N6.jpg",  True, M[2]),
                 ("NEW  ·  nereus000", "new3_N6.jpg", False, M[3]))]

CW, PAD, GAP = 560, 20, 16          # column width
CROP = 270                          # 100% crop box height
TOP = 104
BLOCK = 350 + 24 + CROP + 78        # full + label + crops + stats
W = PAD * 2 + CW * 2 + GAP
H = TOP + len(PAIRS) * (BLOCK + 34) + 74

c = Image.new("RGB", (W, H), (17, 21, 26))
d = ImageDraw.Draw(c)
def f(sz, b=False):
    for p in (("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if b else
               "/System/Library/Fonts/Supplemental/Arial.ttf"),
              "/System/Library/Fonts/Helvetica.ttc"):
        try: return ImageFont.truetype(p, sz)
        except Exception: pass
    return ImageFont.load_default()
F_T, F_B, F_H, F_L, F_S = f(25, True), f(18, True), f(14, True), f(12), f(11)

d.text((PAD, 20), "OLD boards vs NEW boards — same scene, same capture path",
       fill=(235, 240, 245), font=F_T)
d.text((PAD, 54), "nereus002 (OLD) rotated 180° to match nereus000 (NEW).  "
                  "field-streams card, HD 1280×800, JPEG q90, colour, auto-exposure.",
       fill=(150, 165, 180), font=F_L)
d.text((PAD, 74), "Crops are 100% pixels, no resampling. All four captured within ~10 min. "
                  "Both N6 boards on stock OpenMV v5.0.1. NOTE the rigs sit at different "
                  "distances — see caveats.", fill=(240, 165, 100), font=F_L)

for i, (board, a, b) in enumerate(PAIRS):
    y = TOP + i * (BLOCK + 34)
    d.text((PAD, y), board, fill=(120, 200, 255), font=F_B)
    for col, (lab, fn, rot, m) in enumerate((a, b)):
        x = PAD + col * (CW + GAP)
        im = Image.open(os.path.join(D, "optics", fn)).convert("RGB")
        if rot: im = im.rotate(180)
        d.text((x, y + 26), lab, fill=(200, 212, 224), font=F_H)
        full = im.copy(); full.thumbnail((CW, 350), Image.LANCZOS)
        c.paste(full, (x, y + 46))
        yc = y + 46 + full.height + 10
        cx, cy = im.width // 2, im.height // 2
        half = CW // 2 - 4
        centre = im.crop((cx - half, cy - CROP // 2, cx + half, cy + CROP // 2))
        c.paste(centre, (x, yc + 14))
        d.text((x, yc), "centre, 100% pixels", fill=(150, 165, 180), font=F_S)
        ys = yc + 14 + CROP + 8
        d.text((x, ys), "luma %.0f    noise %.2f    chroma-noise %.2f"
               % (m["mean"], m["noise_floor"], m["chroma_noise"]),
               fill=(210, 220, 230), font=F_L)
        d.text((x, ys + 17), "SNR %.1f dB    acutance %.4f    HF %.3f"
               % (m["snr_db"], m["acutance"], m["hf_energy"]),
               fill=(210, 220, 230), font=F_L)

fy = H - 62
d.text((PAD, fy), "TRUSTWORTHY here: noise floor and chroma-noise (flat-region "
                  "measurements). Lower is better.", fill=(150, 200, 150), font=F_L)
d.text((PAD, fy + 19), "NOT trustworthy here: vignetting (the corners contain a bright "
                       "door/floor — it measures the scene, not the lens) and acutance/HF "
                       "(different subject distance).", fill=(240, 150, 90), font=F_L)
d.text((PAD, fy + 38), "Exposure/gain could not be read from the OLD boards, so the noise "
                       "difference is NOT gain-normalised.", fill=(240, 150, 90), font=F_L)
c.save(OUT)
print("wrote", OUT, c.size)
