# S32 bite 0 — can this rig record HD q90 at 30 fps?

**Answer: not at q90. HD at 30 fps needs q70; q90 at 30 fps needs VGA.**
Measured on **nereus000 (Pi 5)** 2026-09-08. Every number here is measured on
this rig; nothing is carried over from nereus002.

---

## The rig changed under the sprint plan, and it moved two of the three answers

S32 was scoped for **nereus002, a Pi Zero 2 W**. Nick redirected the work to
**nereus000, a Pi 5**, which is a different host in ways that matter:

| | nereus002 (Pi Zero 2 W) | nereus000 (Pi 5) |
|---|---|---|
| CSI camera | IMX708 present | **none** — the IMX708 leg cannot be demoed here |
| Hardware H.264 encoder | yes (BCM2710A1) | **no** (BCM2712 dropped it) |
| Software x264 at HD | far too slow | **68.7 fps, faster than real time** |
| SD card | unmeasured | 68.7 MB/s direct |
| Boards share a USB controller | yes, one OTG port via a hub | **no**, N6 on xhci-hcd.0, AE3 on xhci-hcd.1 |

So the kickoff's third unknown ("is the Zero 2 W's hardware H.264 usable?")
does not apply here, and **the SD and transcode answers below do NOT transfer
to the field rig.** nereus002 still owes both measurements.

---

## The finding that unblocked the sprint: firmware, not hardware

nereus000's N6 shipped on **OpenMV v4.8.1** (Dec 2025), not the v5.0.1 that
S31 measured on nereus002. On the identical probe plan the two boards were
wildly different, and the shape of the difference named the cause:

| HD q90 | v4.8.1 | v5.0.1 (after flashing) | nereus002 ref |
|---|---|---|---|
| Encode | 165.9 ms → 6.0 fps | **33.3 ms → 29.3 fps** | 32.0 ms → 30.5 fps |
| USB link N6→Pi | 9.13 MB/s | **17.64 MB/s** | 19.5 MB/s |

v4.8.1 was *faster* at QVGA (7.9 ms vs 12.5) and 5.2x slower at HD, i.e. its
cost scaled with pixel count (21x time for 16x pixels) while v5.0.1 barely
scaled (2.6x). That is the signature of a **software** JPEG encoder versus the
VC8000 hardware block, and it matches D49's documented dispatch. Both boards
were flashed to stock v5.0.1 with byte-verified read-back.

**A flash changes the by-id path.** The N6 went from
`…HS_Mode_0065345D3643-if01` to `…FS_Mode_10003500025043364d343000-if00`,
because v5.0.1 reports the full 96-bit chip UID. Every `by_id`-pinned recipe on
this rig broke; role lookup did not notice. Find boards by ROLE.

---

## 1. SD write throughput — not the limit, but its LATENCY is a design constraint

`python3 sd_write_bench.py --gb 6 --frame-bytes 484030 --paced-fps 30`

| | Unpaced (drives the card to its ceiling) | **Paced at the recorder's real 14.5 MB/s** |
|---|---|---|
| Sustained | 86.2 MB/s writes-only | 14.52 MB/s — held exactly |
| Median second | 77.5 MB/s | 14.52 MB/s |
| write() p50 / p99 | 0.15 / 23.7 ms | **0.21 / 0.52 ms** |
| **Worst write() stall** | **3.67 s** | **3.86 s** |
| Stalls > 1 s | 10 | **3** |

Throughput has ~5x headroom and is a non-issue. **Latency is not.** Even at
21% of the card's ceiling it stalled for up to 3.86 s, which at 30 fps is ~115
frames. The recorder therefore never writes on the reader thread: frames go
into a RAM ring sized from this measurement and a writer thread drains it.
Sizing is derived from `MemAvailable` at run time, because a Pi Zero 2 W has
512 MB and cannot lend what a Pi 5 can.

*Caveat:* the paced run followed a 6 GB unpaced run, so the card may have been
doing internal garbage collection. A rig that records repeatedly is in that
state anyway, and the mitigation is identical.

## 2. Encode rungs — and the quality knob is quantized

Measured on the board (capture+encode only, no link cost), HD 1280×800:

| Rung | B/frame | Encoder fps |
|---|---|---|
| q70 | 117,564 | 37.2 |
| **q80 = q85** | **195,368** | **34.9** |
| q90 = q95 | 423,200 | 29.3 |

**q80 and q85 are byte-identical, as are q90 and q95** — the hardware encoder
maps ranges of the quality knob onto the same quantization table. Asking for
q85 over q80, or q95 over q90, buys nothing at all.

## 3. Browser-playable file — software x264, no hardware needed here

The boards emit concatenated MJPEG. Remuxing is instant (448 fps) and useless:
the result is still `mjpeg / yuvj444p`, which **Chrome and Safari refuse**.

| Path | Speed on 150 HD frames | Output |
|---|---|---|
| `-c:v copy` remux | 448 fps | 72.6 MB, **will not play** |
| x264 ultrafast | **68.7 fps** | 13.1 MB, plays |
| x264 superfast | 54.5 fps | 11.4 MB, plays |
| x264 veryfast | 41.4 fps | 7.2 MB, plays |

A 5 s HD clip becomes a playable `h264 / yuvj420p` mp4 in 1–3.6 s, and 3–10x
smaller than the MJPEG source. The Pi 5 has no hardware H.264 encoder at all —
confirmed by device enumeration (only `rpi-hevc-dec`, a *decoder*) as well as
by ffmpeg — so `libx264` is the only real path, and it is fast enough. The
recorder DETECTS a V4L2 M2M encoder and prefers it where one exists, so the
field rig will use its hardware block without a code change.

---

## What the whole pipeline actually delivers

Measured end to end through the recorder — board encode, USB, ring, disk —
5 s clips, **every run with zero dropped frames and zero sequence gaps**:

| Setting | Delivered fps | Frames | Written | Verdict vs 30 fps |
|---|---|---|---|---|
| VGA q90 | **30.16** | 151 | 17.5 MB | **meets it** |
| HD q70 | **30.06** | 151 | 11.5 MB | **meets it** |
| HD q30 | 30.12 | 151 | 7.2 MB | meets it |
| HD q85 | 23.81 | 120 | 23.4 MB | 21% short |
| HD q90 | **16.23** | 82 | 34.7 MB | **46% short** |

**Delivered is well below the encoder ceiling at high bitrates** (HD q85:
34.9 encoding, 23.8 delivered) because the board writes each frame over USB
*inside the same single-threaded loop that encodes it*. A 195 KB frame costs
~11 ms of link on top of 27 ms of encode. This is why the recorder's guard uses
delivered rates, not encoder rates: guarding on 34.9 fps would promise 30 and
hand back 24.

**So the answer to "HD, q90, 30 fps": pick two.**

- HD + 30 fps → **q70** (30.06 fps, 11.5 MB per 5 s)
- q90 + 30 fps → **VGA** (30.16 fps, 17.5 MB per 5 s)
- HD + q90 → 16.2 fps

Since q80≡q85 and q90≡q95, the useful HD rungs are q70, q85 and q90, and
**q70 is the only one that reaches 30 fps at HD**.

---

## Reproducing

```bash
# SD card, paced at the real recorder rate
python3 bench/s32_recorder/sd_write_bench.py --gb 1.8 --frame-bytes 484030 --paced-fps 30

# encoder rungs on a board (stop the workbench demo from the page first)
python3 pi/field/n6_h264_run.py --port "$(python3 pi/field/discover.py --json | \
  python3 -c 'import json,sys;print(json.load(sys.stdin)["found"]["N6"]["port"])')" \
  --plan bench/s32_recorder/results/hd_plan.json --mpremote ~/.local/bin/mpremote

# rebuild the card's limits from the artifacts, including delivered rates
python3 pi/field/make_ceilings.py N6=n6_matrix_v501.json --recordings ~/recordings
```

`results/` holds the raw JSON for every table above. `n6_matrix.json` and
`n6_rungs.json` are the **v4.8.1** measurements, kept as the before-half of the
firmware A/B; `*_v501.json` are the after.
