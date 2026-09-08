# Optics comparison — is board A the same camera as board B?

Built S31 (2026-09-08) to answer a specific question from Nick: *are the two
AE3s and the two N6s comparable, or did I scratch a lens / bodge the focus on
the old boards and invalidate the S30 baseline?*

**Answer that session: the old boards are not damaged.** Sharpness differed by
7% (AE3) and 12% (N6) between old and new. A scratched lens or a lost focus
ring is a 2–5× effect, not 12%.

## Why the card, and why rectify

Comparing two cameras on a real scene is mostly measuring the scene. Two rigs
at different heights frame different content, and then:

- **vignetting is unmeasurable** — corners containing a bright door read as
  "no falloff" no matter what the lens does;
- **acutance and HF energy track subject distance**, not resolving power;
- only **flat-region noise** survives, and even that is confounded by gain.

A printed reference card fixes this. It is planar, so a homography removes
angle and tilt exactly; it has known patches, so colour is comparable; and it
has hard printed edges, so sharpness has something honest to bite on.

## The pipeline

```bash
# 1. one HD still per board, by ROLE, auto-exposure LEFT ON
python3 bench/optics/still_grab.py --tag old --roles AE3,N6

# 2. rectify the card (needs an ROI hint per frame -- see rectify_card.ROI)
python3 bench/optics/rectify_card.py optics/old_AE3.jpg@180 optics/new_AE3.jpg

# 3. register content to a common reference
python3 bench/optics/align_card.py optics/rect_old_AE3.png optics/rect_new_AE3.png

# 4. measure, and build the sheet
python3 bench/optics/card_metrics.py optics/algn_*.png > optics/card.json
python3 bench/optics/card_sheet.py <scratch-dir>
```

`scene_metrics.py` / `scene_sheet.py` are the whole-frame equivalents. Keep
them for looking; do not draw optics conclusions from them (above).

Needs `numpy`, `pillow`, `opencv-python-headless` — Mac-side, in a venv.
`@180` after a path rotates that frame (nereus002 is mounted upside down
relative to nereus000).

## Four traps, each of which produced a wrong number first

1. **`composite.board_burst` freezes AE/AWB/gain.** That is correct for
   stacking and wrong here: in a dim room the freeze lands before AE has
   converged and returns a **black frame that is still a valid 28 kB JPEG**.
   `still_grab.py` leaves the ISP running.
2. **The card's AprilTags cannot be decoded at working distance.** They are
   ~18 px; 36h11 needs ~10 cells plus a quiet border. Their *centroids* are
   still usable as fiducials in principle, but blob-picking by quadrant
   proximity kept selecting printed text and produced a degenerate 133×19
   quad. Rectify on the card's white boundary instead.
3. **The boundary homography is not enough on its own.** It includes a
   variable sliver of border, so printed content lands at slightly different
   scales and fixed ROIs sample the wrong patches — caught in the data when
   the AE3 NEW greyscale ramp reported its darkest step as its brightest.
   `align_card.py` (ECC) fixes it.
4. **Integer-index edge measurement quantises.** The canvas upsamples the
   native card ~4.5×, so a whole-pixel 10–90% rise gives every camera the
   same score (12.00). Interpolate.

## Reading the output

| metric | meaning | direction |
|---|---|---|
| `edge_rise_px` | 10–90% rise on the dark bar, canvas px | lower = sharper |
| `white_noise` | RGB std on unprinted card | lower = cleaner |
| `ramp_range` | brightest − darkest grey step | higher = more tonal range |
| `wb_RG`, `wb_BG` | white balance on the card white | 1.000 = neutral |
| `colour_sat` | mean HSV S over the patch block | see caveat |

**Saturation caveat:** chroma noise inflates it. The new N6 measured 22% more
saturated than the old one while its patches were visibly mottled — that is
noise being counted as colour, not richer rendering.

## What this rig still cannot answer

Noise is **not gain-normalised**. Reading `exposure_us()`/`gain_db()` off the
boards worked on nereus000 and returned silence with rc=0 on nereus002, three
times. Until both rigs are lit the same and gain is read, "the new boards are
noisier" is an observation about the rigs, not a claim about the sensors.
