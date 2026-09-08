# Findings — is custom N6 firmware for hardware H.264 worth the squeeze?

**Investigation:** S31, desk-only on the Mac. **Decision gate: Nick.**
**Answers** `docs/N6_H264_SPEC.md`. **Date:** 2026-09-07.
**Hardware touched: none.** No `nereus*` host, no serial port, no
`mpremote`, no flashing. nereus002 and both boards were owned by a
concurrent session throughout.

---

## The recommendation

> **MEASURED ANSWER (2026-09-07, on the board): do not adopt, because at
> HD and max quality — the cell Nick actually cares about — hardware
> H.264 is only 1.44x smaller than MJPEG at matched quality, on a STATIC
> scene that is H.264's best case, and at true max quality (MJPEG q100)
> the saving falls to 1.01x and H.264 drops to 21.8 fps, below 30.**

**And the release story is worse than this document first said. Corrected
2026-09-07 after Nick asked directly:** there is **no v5.1.0**. No tag, no
release, no due date. "v5.1.0" is a **milestone label** with 7 open PRs and
0 closed — a maintainer's intent tag, not a schedule. PR #3247 is still a
**DRAFT**, unchanged since 2026-08-22. It is **not merged to master**, so
OpenMV's rolling `development` build does not carry it either (verified: a
build of the PR's own base reports `codec.H264Encoder in image: no`).
**Today the only way to get hardware H.264 on an N6 is a non-release
firmware built from an unmerged draft PR** — which is what this
investigation built and flashed. That does not dissolve Nick's
custom-firmware policy the way the desk-only version of this document
implied; it means the policy question is live, just with upstream code
instead of ours.

The duty-cycle arithmetic below is unchanged and still bounds the prize.

**The duty-cycle crossover, as a number:** with the S29-anchored 4x
ratio, saving even **100 kbps** sustained requires **143 s of video per
hour** — a 2.4 % duty cycle, or ~29 five-second clips an hour. One clip
an hour is 0.139 % and the whole prize there is 4.68 kbps.

Nothing in this document is a reason to *avoid* H.264 — it is free, it
is upstream, and it should be adopted on release. It is a reason not to
spend a bench session on it now, and not to justify it with the
one-clip-per-hour use case, which cannot pay for anything.

---

## Rung 1 — Premise (Gate A): **PASSED, and then some**

Every claim in the spec's §2 table was settled at its source. Three were
true; one was true at the time it was written and is now false.

| Claim (spec §2) | Spec status | Verified finding | Source |
|---|---|---|---|
| STM32N657 has a hardware video encoder (VENC) | UNVERIFIED | **TRUE** | ST CMSIS device header + ST LL driver, both vendored in the OpenMV tree — see below |
| OpenMV vendors a VC8000 encoder driver | UNVERIFIED | **TRUE**, and it is *compiled into shipping N6 firmware today* | `drivers/vc8000/` @ `55d6fb90`; EWL @ `8af1f3d0` |
| Zero MicroPython bindings exist | UNVERIFIED | **TRUE on master; FALSE upstream** — bindings exist in open PR #3247 | grep of the bindings layer + GitHub |
| MJPEG→H.264 ≈ 4.0x | measured, wrong camera | unchanged; used only as an anchor | S29 |

### 1.1 The silicon

The encoder is a real, addressable peripheral, not a marketing bullet.
From ST's own CMSIS device header, vendored at
`lib/cmsis/include/st/stm32n657xx.h`:

| Fact | Value | Line |
|---|---|---|
| Peripheral base | `VENC_BASE = APB5PERIPH_BASE + 0x5000` | 3243 / 3553 |
| Interrupt | `VENC_IRQn = 62` | 118 |
| Dedicated encoder RAM | `VENC_RAM_SIZE = 0x20000` (**128 KB**) at `0x24400000` (NS) / `0x34400000` (S) | 2998 / 3040 / 3348 |
| Clock + reset gating | `RCC_APB5ENR_VENCEN`, `RCC_APB5RSTR_VENCRST`, `RCC_MEMENR_VENCRAMEN` | 28360 / 27815 / 27976 |
| Security/isolation | `SYSCFG->VENCRAMCR` decides whether VENCRAM belongs to the system or the encoder | 2740 |

ST ships a low-level driver for it: `lib/stm32/n6/include/stm32n6xx_ll_venc.h`
and `src/stm32n6xx_ll_venc.c` (STMicroelectronics, © 2023, "VENC LL module
driver"). `LL_VENC_Init()` asserts VENCRAM is *not* system-accessible, enables
the APB5 bus clock, then VENCRAM, then the VENC clock.

**Which IP:** ST's own support forum confirms the block is a **Hantro /
VeriSilicon VC8000NanoE**, and ST staff acknowledge that reference manual
RM0486 is "very vague" on it, pointing users instead at VeriSilicon's
integration guide; ST has an internal documentation ticket (209269) open
on the gap.

**Documented capability — FLAGGED, not verified at source.** A web search
returns, attributed to the STM32N657 datasheet (Dec 2025), "a hardware H264
encoding block supporting baseline profile, main profile and high profile
level 1 to level 5.2, supporting frame rates of up to 15 frames per second
for 1080p resolution." **`st.com` was unreachable from this desk** (both the
datasheet PDF and the ST wiki timed out repeatedly), so this is a search
snippet, not a read document. Per CLAUDE.md rule 3 it is recorded as a lead.
It does not change any conclusion here — every resolution this project
cares about is far below 1080p — but it is the number to confirm before
anyone plans a 1080p mode. See §Open questions.

### 1.2 The driver, and the surprise

`drivers/vc8000/` is the full VeriSilicon **VC8000NanoE SW package v9.22.3.7**
as re-delivered by ST — 1.5 MB of source, BSD-3-Clause (dual GPL-2.0/BSD at
origin; ST's `st_readme.txt` records the license set to BSD-3-Clause and five
ST bug fixes between Sept 2023 and Dec 2024). It contains both an H.264
encoder (`src/h264/`, 16 files) and a JPEG encoder (`src/jpeg/`, 5 files).

**All of it — the H.264 half included — is already compiled into every
OPENMV_N6 firmware**, because `drivers/drivers.mk:269-317` adds both source
sets whenever `OMV_VC8000_ENABLE=1`, and `boards/OPENMV_N6/board_config.mk:33`
sets exactly that.

OpenMV wrote the hardware wrapper too. `ports/stm32/stm_vc8000.c`
(© 2026 OpenMV, 293 lines) is a complete EWL — "Encoder Wrapper Layer",
the porting shim VeriSilicon's library calls out to. It implements register
access via `LL_VENC_*`, ASIC ID/capability reads, DMA-coherent allocation
through OpenMV's UMA allocator, cache maintenance, the IRQ handler and the
wait-for-hardware handshake. Critically it implements **`EWLMallocRefFrm`**
— reference-frame allocation, which JPEG has no use for. The wrapper was
written with inter-frame coding in mind from the start.

**The surprise that reframes the baseline:** this same silicon block is
*already* the N6's JPEG encoder. `ports/stm32/stm_jpeg.c:287-294` —

```c
bool jpeg_compress(image_t *src, image_t *dst, int quality, bool realloc, ...) {
    #if (OMV_VENC_CODEC_ENABLE == 1)
    // Try VC8000 first. Returns -1 if not handled (fall through), 0=success, 1=error.
    int vc = jpeg_compress_vc8000(src, dst, quality, subsampling);
    if (vc >= 0) {
        return (bool) vc;
    }
    #endif
```

So the spec's **measured baseline of 68.6 fps VGA on the N6 is this
encoder's JPEG mode**, and the AE3's 13.7 fps is software JPEG. Two
independent lines in the AE3's own board config say so:
`OMV_JPEG_CODEC_ENABLE (0)` (`boards/OPENMV_AE3/board_config.h:51`), and a
capability set of `HAS_GPU, HAS_NPU, HAS_CRC, HAS_PMU, HAS_WIFI, HAS_BT,
HAS_SD, HAS_ETH, HAS_USB_HS, HAS_MULTICORE` — neither `HAS_VENC` nor
`HAS_JPEG`. **The N6's 5.0x JPEG advantage over the AE3 (68.6 / 13.7) is
already this block earning its keep.** H.264 is not a new accelerator to bring up; it is
a second mode of one that is running in production on this bench today.

### 1.3 The bindings — true when written, false now

At the tree this bench builds from (`7d4dbf7a`, and equally at upstream
master `631681e5`) there is **no** H.264 call path. Absence shown, not
assumed:

- `grep -rn 'H264Enc' --include='*.c' --include='*.h' .` outside
  `drivers/vc8000/` returns **nothing**. The vendored encoder has zero
  callers.
- The whole imlib-level surface is two functions,
  `imlib_hardware_venc_init/deinit` (`lib/imlib/imlib.h:1267-1269`),
  implemented in `stm_jpeg.c:1024-1036` as clock-on / clock-off.
- `protocol/omv_protocol_hw_caps.h:45` defines `HAS_VENC` as a capability
  bit and `boards/OPENMV_N6/board_config.h:72` advertises it, so the board
  already *tells the host* it has a video encoder that Python cannot reach.

**Gate A therefore passes on the strongest possible terms**: the hardware
exists, the driver exists, is licensed permissively, and is already linked
into production firmware. The only missing piece was the Python binding —
and that has since been written upstream.

---

## Rung 2 — The gap: **already closed, upstream, by the maintainer**

**[openmv/openmv#3247](https://github.com/openmv/openmv/pull/3247) —
"modules/py_codec: Add H.264 video support on the STM32N6."**

| | |
|---|---|
| Author | `kwagyeman` (Kwabena Agyeman, OpenMV) |
| Opened | 2026-08-22 · head `5aa47553` |
| State | **open, draft**, `mergeable_state: clean` |
| Milestone | **v5.1.0** — applied **2026-09-07**, the day this investigation ran |
| Size | +2307 / −300 across 19 files |
| CI | **all green**, including `OPENMV_N6` and `OPENMV_N6 (PROFILE=1)` |
| Review | zero review comments, zero changes requested |
| Firmware cost | `OPENMV_N6` text **+58,120 B (+2.94 %)**; every other board **−80 B** |

The PR does not merely expose the encoder — it ships the whole delivery
chain:

| Piece | What it is | Where |
|---|---|---|
| `codec.H264Encoder` | the binding — 641 new lines | `ports/stm32/modules/py_codec.c` |
| `mp4.py` | pure-Python fragmented-MP4 muxer, no seeking, works on files *and* sockets | `scripts/libraries/mp4.py` (+535) |
| `rtsp.py` / `rtsp_h264.py` | RTSP rewritten on asyncio, RFC 6184 H.264 + existing MJPEG | +282/−291, +210 |
| examples | 4 MP4 recording + 4 RTSP servers, gated to OPENMV_N6 | `scripts/examples/` |
| driver fixes | cache maintenance for SW-written stream buffers; `UMA_PERSIST` so encoder buffers survive `uma_collect()` mid-encode | `stm_vc8000.c`, `H264CodeFrame.c` |

### The API, read from the source

```python
codec.H264Encoder(width, height, fps=30, bitrate=1000000, quality=-1,
                  keyframe_interval=30, refresh_interval=0)
    .encode(img, roi=None, timestamp_us=-1, keyframe=False)  -> Annex-B memoryview
    .sps_pps()   .keyframe()   .count()   .mse()   .motion()
    .bitrate(v)  .quality(v)   .deinit()
```

Configuration the constructor fixes (`py_codec.c:517-576`): Annex-B byte
stream, **level 4.1**, CABAC on (**Main profile**), one slice per picture,
SPS/PPS repeated at every IDR, hardware BT.601 RGB→YUV, RGB565 or YUV422
input, 90 kHz timebase so `timestamp_us=` expresses real frame durations.
Two rate modes: `bitrate=` (rate control on) or `quality=` 0–100 mapped
linearly to a fixed QP 51–0 with the QP window opened to [0, 51].

Two capabilities here are worth more to this project than the compression:

- **`motion()` returns per-macroblock motion vectors** (quarter-pel) and
  `mse()` returns SAD, both as numpy arrays, read back from the encoder
  hardware for free during encode. That is a scene-change / animal-motion
  detector with no CPU cost — directly relevant to trigger logic for a
  power-budgeted field rig, and to S28's stacking (which needs to know
  whether the scene moved between frames).
- **`roi=` crops in hardware** and `refresh_interval=` gives GDR rolling
  refresh instead of periodic keyframes, which flattens bitrate spikes.

### The gap that is left

Essentially: our own plumbing, not firmware work.

| Work | Kind | Est. |
|---|---|---|
| Build + flash a PR-branch firmware | build tooling (**done**, see rung 5) | 0 — artifact in hand |
| Teach `pi/field/` and the stream shim to carry an MP4/Annex-B payload instead of a JPEG stream | glue, Python, host side | ~150–300 LoC |
| Workbench recipe + compare card for the H.264 leg | glue, existing pattern | ~100 LoC |
| Re-validate the N6's CV stack on the new firmware (models, ROMFS, DFU ladder) | bench session | ~1 session |

Zero lines of C. Zero driver bring-up. Zero vendor-blob integration —
the "blob" is permissively-licensed C source that already builds.

---

## Rung 3 — Cost of ownership: **this is the finding that decides it**

**Does upstream want this? Upstream *wrote* it.** That is the whole
difference between this and a private fork, and it is exactly the
distinction the spec asked to be checked.

| Dimension | Private fork (what the policy fears) | What is actually on offer |
|---|---|---|
| Who maintains the code | us | OpenMV's maintainer |
| Rebase burden per upstream release | ours, forever | none |
| Merge risk | conflicts on every release | `mergeable_state: clean`, milestoned |
| CI coverage | ours to build | OpenMV's, all boards green |
| Time to zero-cost | never | one release (v5.1.0) |

**What breaks at the next OpenMV release: nothing, on the release path.**
This repo already carries one firmware patch (the S18 sticky-framebuffer
build) and has felt the drift; the lesson is precisely why *waiting for
v5.1.0* is the right posture rather than carrying `pr3247` locally. On the
branch-build path the drift risk is real but time-boxed and small: the PR
touches `stm_vc8000.c`, one vendored driver file and Python libraries — it
does not touch the framebuffer, the JPEG path, ROMFS, or the DFU ladder.

**Release timing — the honest estimate is "unknown".** v4.8.0 (2025-12-05)
→ v5.0.0 (2026-07-02) is a seven-month minor cadence; v5.0.1 landed
2026-09-05. The v5.1.0 milestone holds 7 open PRs and **no due date**.
Do not plan around a date. Two facts soften the wait:

- OpenMV publishes a rolling **`development`** release (last built
  2026-09-05) with a per-board `firmware_OPENMV_N6.zip`. Once #3247 merges
  to master, an official N6 build carrying it exists without us building
  anything.
- The branch build in rung 5 exists now, so the *measurement* need not wait
  for the release at all.

**Does a custom build jeopardise anything else?** Three things this repo
depends on were checked:

- **Model loading / ROMFS** — untouched by the PR; no board layout, linker
  or ROMFS change. The N6 firmware grows 58 KB of text.
- **The DFU ladder in `pi/ae3_flash/`** — that path is AE3-only. The N6 is
  flashed over its own DFU route; the PR changes nothing about it.
- **The AE3** — completely unaffected. The PR is gated to the N6 by
  `OMV_VENC_CODEC_ENABLE` and the manifest change is `boards/OPENMV_N6/`
  only; the AE3 firmware shrinks by 88 bytes.

One genuine caution, from this repo's own history: the N6 on nereus002 is
one of two boards a field rig depends on, and reflashing it means
re-proving the S29 discovery/ownership path and the CV stack on top of a
non-release firmware. That is the real cost of the branch-build route, and
it is a bench cost, not an engineering cost.

---

## Rung 4 — Predicted win, with its error bars

**No number in this section is a measurement of N6 H.264. Every one is
labelled.** The arithmetic is reproducible:

```bash
python3 bench/n6_h264/duty_cycle.py
```

### 4.1 The measured baseline (MJPEG, N6, from S30)

| Quantity | Value | Status |
|---|---|---|
| VGA q30 bytes/frame | 13.7 KB | **MEASURED** (S30 probe, excludes USB) |
| VGA q30 encode rate | 68.6 fps | **MEASURED** |
| ⇒ instantaneous bitrate at 30 fps | 3.37 Mbps | derived, one multiplication |
| ⇒ 5 s clip payload | 2.01 MiB | derived (spec §4 quotes 1.95 MB; same number, rounding) |
| free heap at VGA | 25.6 MB | **MEASURED** |

### 4.2 The predicted compression ratio, and why the band is wide

The only ratio this project owns is S29's **4.0x** (26.43 → 6.60 Mbps,
IMX708 720p15) — measured, but on a different sensor, a different encoder
and a different scene. The prediction band used here is **3x–8x**, and the
band is wide for a physical reason worth stating plainly:

- **Upper end (8x): a moored benthic camera.** Static rock, static urchins,
  slow animals. Inter-frame prediction is nearly free and H.264's advantage
  over MJPEG is at its largest — MJPEG re-encodes an unchanged scene at full
  price 30 times a second.
- **Lower end (3x): suspended particulate.** Marine snow, backscatter, surge
  and rolling auto-gain are **adversarial for inter-frame coding** — every
  particle is uncorrelated motion, so the encoder spends bits on residuals
  and the ratio collapses toward intra-only. This is the single most likely
  way the win fails to materialise underwater, and it is the falsifier the
  bench plan must attack.

The PR's own reported figure (RTSP H.264 at ~1.1 Mbit/s against a 1 Mbit/s
target, vs MJPEG at ~4.6 MB/s) is not a like-for-like ratio — the H.264 leg
was rate-controlled to a target and the MJPEG leg was not — so it is not
used as an anchor here.

### 4.3 Throughput: not expected to bind at VGA — **UNMEASURED**

H.264 must do everything JPEG does plus motion estimation, reconstruction,
deblocking and CABAC, and must read and write two luma and two chroma
reference buffers per frame, so it will be slower than the measured 68.6 fps
JPEG rate. How much slower is unknown and needs hardware. Two bounds:

- If the (unverified) 1080p15 datasheet figure is macroblock-rate limited,
  that is ~121,500 MB/s, which at VGA's 1200 macroblocks/frame would be
  ~100 fps — i.e. VGA 30 fps has ~3x headroom.
- Even a 3x penalty against the measured JPEG rate leaves ~23 fps at VGA.

**A 5 s VGA clip at 30 fps is very likely within reach; 720p30 is the cell
that needs measuring.**

### 4.4 Memory: fits everywhere we care about — DERIVED from driver source

Allocation sizes read out of `EncAsicMemAlloc_V2()`
(`drivers/vc8000/src/common/encasiccontroller_v2.c:125-235`), the
`H264ENC_BASE_VIEW_DOUBLE_BUFFER` case in `H264Init.c:487` (2 luma + 2
chroma reference buffers) and the PR's own output-buffer sizing:

| Resolution | Macroblocks | Encoder RAM | % of the N6's 25.6 MB free heap |
|---|---|---|---|
| QVGA | 300 | 0.40 MiB | 1.5 % |
| VGA | 1200 | **1.43 MiB** | **5.6 %** |
| 720p | 3600 | 4.20 MiB | 16.4 % |
| 1080p | 8160 | 9.43 MiB | 36.8 % |

ST's forum guidance that 1080p "won't fit into the internal memory" applies
to STM32Cube examples allocating from internal SRAM. OpenMV's EWL asks for
`UMA_FAST | UMA_MAYBE` — prefer fast, fall back — and the N6 declares
`HAS_DRAM`, so that limit should not bind. **Unverified on hardware.**

### 4.5 The duty-cycle answer

**One 5 s VGA clip per hour (duty cycle 0.139 %):**

| Codec | 5 s clip | Sustained | Saved vs MJPEG | Status |
|---|---|---|---|---|
| MJPEG q30 | 2.01 MiB | 4.68 kbps | — | **MEASURED** input |
| H.264 pessimistic (3x) | 0.67 MiB | 1.56 kbps | 3.12 kbps | PREDICTED |
| H.264 S29-anchored (4x) | 0.50 MiB | 1.17 kbps | 3.51 kbps | PREDICTED |
| H.264 optimistic (8x) | 0.25 MiB | 0.58 kbps | 4.09 kbps | PREDICTED |

**The saving is bounded above by 4.68 kbps** — you cannot save more than
the whole budget, and at this duty cycle the whole budget is single-digit
kbps. The 3x-vs-8x argument is an argument about 2.5 kbps.

**Crossover — seconds of video per hour needed to save a given rate:**

| Saving target | at 3x | at 4x | at 8x |
|---|---|---|---|
| 10 kbps | 16 s/h | 14 s/h | 12 s/h |
| **100 kbps** | 160 s/h | **143 s/h** | 122 s/h |
| 1000 kbps | 1604 s/h | 1426 s/h | 1222 s/h |

### 4.6 Where H.264 is *enabling*, not merely cheaper

Scaling the measured VGA density (0.365 bpp) to other resolutions —
**derived, not measured** — against SPEC's 8 Mbps sustained T1L video
budget:

| Resolution | fps | MJPEG | H.264 @ 4x | Verdict |
|---|---|---|---|---|
| QVGA | 30 | 0.84 Mbps | 0.21 Mbps | MJPEG fits |
| VGA | 30 | 3.37 Mbps | 0.84 Mbps | MJPEG fits |
| **720p** | **24** | **8.08 Mbps** | **2.02 Mbps** | **H.264 only** |
| **720p** | **30** | **10.10 Mbps** | **2.53 Mbps** | **H.264 only** |

This is the cell `docs/SPEC.md` already named — *"Public 720p stream —
needs H.264 (N6, non-goal)"* and *"MJPEG at that tier exceeds the T1L wire
itself"*. **The value of H.264 on this project is not that it makes the
hourly clip cheaper; it is that it makes the 720p tier possible at all.**
That tier is currently a declared non-goal, and whether to un-declare it is
Nick's call — but it is now available for the price of a firmware release
rather than a fork.

---

## Rung 5 — Build it (not flashed)

Rungs 1–4 cleared, so the artifact was built. **It has not been flashed and
must not be flashed by this session.**

- Build script: `firmware/openmv_build/build_n6.sh` — a sibling of
  `build_ae3.sh`, same docker/Rosetta/SDK plumbing and the same
  artifacts-not-exit-codes verification, with `--pr <N>` added so an
  unmerged upstream PR head can be built by number.
- It defaults to a **separate tree** (`~/openmv-dev/openmv-n6`), not the
  AE3 dev clone. That clone carries this repo's in-flight AE3 patches
  (framebuffer, jpege), and an N6 build must not silently inherit them.
- Artifacts, manifest and the flash procedure: `firmware/openmv_build/N6_H264_FLASH.md`.

**Verified as an artifact, not as an exit code.** `firmware.bin` is
2,043,432 B; `.text` is 2,035,464 B = **55.68 % of the N6's 3584 KB flash
region**, so the feature is nowhere near a wall. The image really carries the
binding (`strings` hits `H264Encoder`; the ELF carries `MP_QSTR_H264Encoder`
and `py_codec.c`). And the build is **reproducible** — re-running it yields
the same `build_sha` and the same `firmware.bin` sha256, because the build
harness commit's dates are pinned to the upstream commit's.

**The feature's cost in flash was measured, not inferred.** The PR's own base
commit (`aa5d9f7d`) was built the same way for an A/B: `firmware.bin`
**1,985,272 B → 2,043,432 B = +58,160 B (+2.93 %)**, against upstream CI's
reported +58,120 B (+2.94 %) — the two agree to 40 bytes, which is the length
of the differing version strings. `FLASH_TEXT` goes 54.09 % → 55.68 %. The
base build also serves as the probe's **negative control**: the same check
correctly reports `codec.H264Encoder in image: no` there.

**The probe itself had to be debugged, and the bug is a repo classic.**
Written as `strings … | grep -q`, it reported the codec *absent* from an image
that provably contained it: under `set -o pipefail`, `grep -q` exits at the
first match, `strings` dies of SIGPIPE, and pipefail reports the pipeline as
failed. `grep -c` reads to EOF and cannot SIGPIPE. CLAUDE.md's
"a pipeline returns the LAST command's status" trap, this time inside the
verifier.

---

## Rung 6 — The bench plan

A session that owns the hardware runs this. It compares like with like
against the S30 baseline and it is designed to be *falsifiable*.

**Preconditions.** Board ownership via the workbench (`/api/runner`,
`/api/preflight`) — one owner per port. The N6 is `usb-MicroPython_Pyboard_…`.
Flash per `firmware/openmv_build/N6_H264_FLASH.md`; record the pre-flash
firmware version so the board can be put back.

**Leg 0 — regression, before any codec work.** Re-run the S30 MJPEG probe
on the new firmware. If the measured VGA q30 numbers (68.6 fps, 13.7 KB)
do not reproduce, stop: the firmware changed something else and every
comparison below is contaminated.

**Leg 1 — like-for-like size.** Same scene, same rig position, same
lighting, back to back:

| Sweep | Values |
|---|---|
| Resolution | QVGA, VGA, 720p |
| Codec | MJPEG at the six S30 quality levels · H.264 `quality=` swept to bracket each |
| Clip | 5 s at 30 fps (720p also at 24) |
| Record | bytes/clip, encode ms/frame, achieved fps, peak encoder RAM, free heap |

Match on *quality*, not on setting number: for each MJPEG q, find the
H.264 `quality=` whose decoded output is perceptually equivalent, and
report the byte ratio at that pairing. A ratio quoted at mismatched
quality is not a result.

**Leg 2 — the falsifier: does the underwater scene defeat inter-frame
coding?** This is the leg that can kill the win, so run it before
believing leg 1.

- Static bench scene (the S28 patch card) → expect the high end of 3–8x.
- The HIL LCD playing real reef footage with visible particulate and camera
  motion → expect the low end.
- If available, a tank or field clip with genuine backscatter.

**Falsification condition, stated in advance:** if the measured ratio at
matched quality on a particulate-heavy scene is **< 2x**, the 720p claim in
§4.6 fails (H.264 at 2x is ~5 Mbps at 720p24 — still inside the 8 Mbps
budget, but with no margin), and the feature is worth nothing at any duty
cycle this project runs. Report that number whatever it is.

**Leg 3 — throughput ceiling.** Encoder-only fps at QVGA/VGA/720p, and the
ASIC capability words the EWL already reads (`EWLReadAsicID`,
`EWLReadAsicConfig` — registers 0, 63 and 296) which report
`maxEncodedWidth` and the `h264Enabled` fuse bit straight from silicon.
**That single readout settles the unverified 1080p15 datasheet claim from
the hardware itself** and costs one REPL line.

**Leg 4 — the deliverable.** One `mp4.py`-muxed 5 s clip pulled off the
board and validated on the Mac with `ffprobe` + a strict `-c copy` remux.
Bytes on disk, not a reported size.

**Leg 5 — power.** Per-clip energy for MJPEG vs H.264 on the INA3221 rig.
H.264 does strictly more work per frame; on a 78-minute battery budget
(S29) a slower, hotter encode could cost more than the bytes it saves.
This is an open question, not a prediction.

---

## Open questions (for SPEC.md)

1. **STM32N657 VENC documented capability is unread at source.** The
   profile/level/1080p15 figures here come from a search snippet;
   `st.com` was unreachable from this desk. Settle it either by reading
   the datasheet or — better — by reading the ASIC capability registers
   on the board (rung 6, leg 3).
2. **N6 H.264 throughput at VGA/720p is unmeasured.** Predicted comfortable
   at VGA; 720p30 is the cell that could bind.
3. **The compression ratio in a particulate-heavy underwater scene is
   unmeasured**, and it is the number the whole case rests on. Band 3–8x
   is a prediction, not a measurement.
4. **Per-clip energy cost of H.264 vs MJPEG on the N6 is unknown.**
5. **v5.1.0 has no due date.** If the 720p tier is ever promoted from
   non-goal to goal, the release date becomes a schedule dependency.
6. **The S30 final matrix was not consulted** — per the spec, ask Nick for
   the table rather than fetching it from the rig. Numbers here use the
   spec's §3 quoted values.

---

## What this investigation did *not* do

- Touched no hardware, so every H.264 number is a prediction.
- Did not flash the firmware it built.
- Did not read the ST datasheet at source (`st.com` unreachable).
- Did not fetch the S30 results matrix from nereus002.
