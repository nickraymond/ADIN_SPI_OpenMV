# Capture-Side Frame Stacking & Red-Channel HDR — Engineering Notes & Sprint Kickoff

**For:** AE3 camera project (adapting findings from the Nereus BM camera / bmcam fleet)
**Author context:** Nereus Vision underwater camera work, Sep 2026
**Status:** direction + engineering notes for sprint planning. No code exists yet on any platform; the BM camera project has this captured as its own future sprint (Sprint 20 there).

---

## 1. Why this exists — the problem, measured

Underwater imagery at ~4–5 m depth in coastal water arrives with the **red channel nearly dead**. Measured on real deployed reef images (Nereus BM camera, IMX708, 8-bit JPEG, color reference card in frame):

- White reference patch red channel: **3.5%–14% of full scale** (9–37 counts of 255), varying with hour of day.
- Empirical recoverability floor: below ~5% of full scale, red is quantization/sensor noise — **no post-processing method can recover it**. This was verified against a broad method sweep (chart-based colorimetric fits, physics-based backscatter/attenuation inversion, sea-thru, OceanLens): every method became red-noise-bound on the same frames.
- Corollary: any correction that forces red to its true level applies ~20× red gain, which amplifies noise into a red glow scene-wide.

**Conclusion:** the highest-leverage fix is at capture time — deliver more red photons and less noise into the file *before* compression. Post-processing then works instead of fighting noise.

Two additional free wins:
- **Bandwidth:** unchanged (still transmit one image) — and denoised images compress *smaller* at equal quality, because noise is incompressible.
- **Power:** cost is a few extra seconds awake per capture cycle, small against transmit budgets.

---

## 2. The two experiments (run separately, same hardware)

Do NOT conflate these; they answer different questions and should be A/B'd independently against a single-frame control on the same scene.

### Experiment A — same-exposure frame stacking (the MVP)

1. Let auto-exposure/AWB converge once, then **lock exposure time, analog gain, and white-balance gains**.
2. Capture N identical frames back-to-back (start N=8; also test N=16).
3. Average (or median-combine) the frames **before** lossy encode.
4. Encode once; ship as normal.

Expected physics:
- Temporal (shot + read) noise falls as √N: N=8 → ~2.8× SNR, N=16 → 4×.
- Averaging before 8-bit quantization recovers **sub-LSB detail** (dither effect) — real red values between quantization steps become resolvable. This is lost if you stack already-encoded 8-bit files.
- **Median** vs mean: median rejects transient occluders (fish, drifting particulate) instead of ghosting them — valuable underwater; slightly less SNR gain than mean. A sigma-clipped mean is the best of both.

### Experiment B — red-channel HDR bracket (the underwater-specific trick)

Standard HDR (merge brackets for dynamic range) is only mildly useful here. The high-value variant is **channel-aware**:

1. Capture one NORMAL frame (locked settings, as above).
2. Capture LONG frames at **+2 EV and +3 EV, via shutter time only** (never gain — gain adds back the noise you're buying photons to remove).
3. Merge channel-wise: **green/blue from the normal frame; red from the long frame**, divided by the exposure ratio. Green/blue clipping in the long frame is expected and irrelevant — you're not using those channels.

Expected physics: 4–8× more red photons → red SNR multiplied by the exposure ratio directly. Bigger red win than stacking; the two techniques compose (stack the brackets).

Failure mode to test: motion blur in the long frame (surge, swaying growth). At underwater light levels the +3 EV shutter is still shortish, but verify on bench; if blur appears, red from the long frame can be low-pass anyway (red carries little fine detail) — evaluate whether that's acceptable.

---

## 3. Implementation guidance (platform-agnostic, learned the hard way)

- **AE/AWB lock is load-bearing.** If exposure/gain/WB drift across the burst, the stack blurs *color* instead of averaging *noise*. Converge once → freeze all three → burst. Verify frames are statistically identical before trusting any result.
- **Stack in the rawest domain you can afford.** Best: RAW/Bayer or YUV before the ISP's 8-bit output. Acceptable: decoded 8-bit frames pre-JPEG. Worst: stacking decoded JPEGs — block artifacts are correlated frame-to-frame and don't average away.
- **Memory strategy on constrained SoCs:** never hold N full-resolution frames. Use a running accumulator (uint16 sum handles N≤8 of 8-bit; uint32 beyond) plus the current frame only. Alternatively stack at the transmit resolution if that's all you ship. (On the Pi Zero 2W this was the difference between feasible and OOM; check your platform's contiguous-memory allocator behavior — on Pi, CMA — during the burst, and note that different capture APIs on the same platform can have wildly different memory footprints.)
- **Bracketing order:** capture the normal frame first (it's the one you can't compromise), long frames after.
- **Watch the ISP:** some pipelines apply frame-dependent denoise/tonemapping that breaks stack math. Prefer capture modes that bypass or fix those stages.

---

## 4. Acceptance metrics — make it measurable before you build

Put a known reference in the scene (the Nereus work uses a printed reference card with AprilTags and gray/color patches; a gray card or X-Rite-style target works). Then, single-frame vs stacked vs bracketed, same scene, same minute:

1. **Red signal fraction:** median red of the white/light-gray patch ÷ full scale. Success for the BM case = lifting 3.5% above the 5% floor; define your own floor from your water.
2. **Red SNR:** patch red median ÷ patch red std. Expect ~√N (A) or ~exposure-ratio (B) improvement; if you don't get it, the AE lock or ISP is leaking.
3. **File size at equal encoder quality** — expect the stacked image smaller.
4. **Downstream check:** run whatever color-correction you use on both versions; the corrected stacked image should show visibly less red blotch/noise and better patch ΔE.

Bench first, on a dev unit, with the target at a fixed distance; only then field trials.

---

## 5. Suggested sprint shape

- **Chunk 1 (bench):** locked-AE burst capture proof — N frames, verify pixel-identical settings, measure per-frame noise. No stacking yet. *Exit: burst of identical frames on disk + memory headroom measured.*
- **Chunk 2:** accumulator stacking + single encode; A/B vs single frame on the metrics above. *Exit: √N SNR demonstrated (or explained).*
- **Chunk 3:** shutter bracket capture + channel-wise merge; A/B. *Exit: red fraction/SNR vs exposure ratio table.*
- **Chunk 4 (decision):** pick A, B, or A+B for the production path based on metrics + cycle-time/power cost; write the config knobs (N, EV steps, merge mode) into the capture config rather than hard-coding.

Known open questions to resolve early: does the platform expose true shutter-priority manual exposure? Can it deliver pre-JPEG frames fast enough back-to-back? What is the real memory ceiling during a burst?
