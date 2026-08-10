# DESIGN.md — Architecture & Decisions (as-built)

*What it did / how it's shaped. Agents append; never silently rewrite history.*
*Last updated: 2026-08-09*

## System topology

### Bench target (this project)

```
┌─────────────┐  8-wire harness   ┌──────────────┐   twisted pair    ┌─────────────┐
│ OpenMV AE3  │ SPI @5→25 MHz     │ ADIN1110     │  10BASE-T1L       │ Pi + ADIN   │
│ camera      ├───────────────────┤ (SG shield,  ├───────────────────┤ hat (AOS)   │
│ MJPEG src   │ P0-P5, CS manual  │ later AOS)   │  ≤8 Mbps video    │ shim → HTTP │
└─────────────┘                   └──────────────┘                   └─────────────┘
                                                                        browser ⟵ multipart MJPEG
```

Wiring: `docs/diagrams/wiring_ae3_to_sg_shield.svg` (harness),
`docs/diagrams/wiring_two_node_bench.svg` (two-node link + stream path).

### Production shape this feeds (context, not in scope)

Camera node (AE3 + ADIN on custom PCBA, potted) ↔ pair ↔ telemetry node
(Pi + ADIN + 3–10 Mbps cellular uplink). Optional later: BM-compliant node on
a Spotter bus (requires PoDL front-end + bm_core).

## Driver architecture (the portability contract)

Two layers, hard boundary:

```
┌───────────────────────────────────────────┐
│ Protocol core (portable, no MCU knowledge)│  register map · frame FIFO ·
│                                           │  IRQ cause decode · link mgmt
├────────────── adin_hal.h ─────────────────┤  ~6 calls: xfer, cs, irq_attach,
│ Per-board HAL (thin, rewritten per port)  │  reset, delay
└───────────────────────────────────────────┘
   AE3 impl: machine.SPI(0) + Pin P3/P4/P5     (N6 impl later: same core)
```

Rule: nothing in the core may import `machine` or any board API. This is what
makes AE3→N6 (or MicroPython→C) a HAL swap, not a rewrite.

## Stream pipeline design

- AE3 sends MJPEG in raw Ethernet frames: tiny header (magic, frame seq, chunk
  idx/count) + JPEG chunk. No IP stack on the AE3 in this project.
- Pi shim daemon reassembles chunks → complete JPEGs → pipes into the S3
  stream server. The S3 server is frozen interface; shim adapts to it.
- Rationale: decouples driver bring-up from any TCP/IP porting; receive side
  provably works (S3) before the AE3 driver exists.

## Decision log

| # | Date | Decision | Rationale |
|---|---|---|---|
| D1 | 2026-08-09 | Generic SPI (no CRC) through S6; OPEN Alliance deferred to S7 decision | Matches SG shield as-shipped straps + mainline Linux driver + SG overlay. OA kept open because Sofar's BM Linux driver reportedly uses OA; Microchip oa-tc6-lib is a portable reference if we switch. Per-board choice — nodes don't need to match. |
| D2 | 2026-08-09 | CS driven as manual GPIO (P3), not peripheral SS | AE3's SPI0 SS is not peripheral-driven (OpenMV docs); manual CS also suits ADIN variable-length transactions. |
| D3 | 2026-08-09 | P4 = ADIN RESET, P5 = ADIN IRQ | Keeps everything on the 3.3 V P0–P5 bank; P6–P9 are 1.8 V/B2B. |
| D4 | 2026-08-09 | Pi shim before any lwIP/netif work on AE3 | Days vs weeks; frozen S3 receiver becomes the compatibility target. lwIP netif is the product path, iceboxed. |
| D5 | 2026-08-09 | Pi 5 as first Linux node | Onboard eth0 stays free for SSH/debug while T1L is under test. |
| D6 | 2026-08-09 | Video budget ≤ 8 Mbps | T1L usable ≈ 9.3 Mbps; measured 0.875 bpp anchor puts 1280×800@10 at 9.0 — over. Headroom for retransmit/overhead. S0/S3 replace estimates with measurements. |
| D7 | 2026-08-09 | SG shield for first light; AOS hats for the node pair | SG pinout is vendor-documented (lowest-risk S4); AOS pinout unverified until S2 buzz-out. |
| D8 | 2026-08-09 | S0 gate FAILED (4.89 Mbps < 12). Nick: option A (spike) then B — sprints continue with AE3 video budget ~4 Mbps | Spike confirmed ceiling is software (polled per-byte `machine_spi_transfer`, no DMA/FIFO burst; OpenMV fork = upstream). Fixing it means custom firmware = option C, priced (~50 LoC FIFO burst / DMA) and deferred. S1–S3 unaffected; still ~40× the v1 UART path. |
| D9 | 2026-08-09 | AE3 is the platform; dual targets set (Nick): T1 live stream = QVGA color q35–50 @ 24–30 fps; T2 edge CV = HD @ 3–5 fps on-device inference | Requirement space is a 2×2 (detail × smoothness, SPEC.md); AE3 covers three cells; public-720p cell needs H.264 → N6 follow-on (iceboxed). QVGA is the only mode whose measured encode+tx reaches 24–30 fps; VGA color caps at ~13. T2 chosen at HD because sergeant majors ≈ 32–48 px there (detector floor ~24–32 px); camera distance is the free variable. C driver stays a hard NO for now. |
| D10 | 2026-08-09 | 12 Mbps S0 gate retired → transport gate = SPI effective ≥ 2× T1 stream bitrate (≥3.5 Mbps) | Original gate was derived from the 8 Mbps T1L budget, a workload the AE3 encoder cannot generate; new gate derives from the committed product mode. Measured 4.89 Mbps passes. |
| D11 | 2026-08-09 | Edge inference (T2/S8) sequenced strictly after T1 streaming is met (Nick) | One bottleneck at a time; the NPU bench is S8's first bite, not this sprint's. |

## Verified-facts ledger

See SPEC.md §Confirmed technical facts. Anything not there or here is
unverified — treat as unknown.

## Bench results

*(appended by sprints)*

- S0 SPI benchmark: **FAIL — 4.89 Mbps max effective vs ≥ 12 Mbps gate.**
  Run 2026-08-09, AE3 fw v1.28.0-49 (2026-07-02), `bench/ae3_spi_bench.py`
  driven remotely via nereus000, P0→P1 loopback, 0 integrity errors at every
  point. IRQ path is excellent. Details below.
- S2 iperf3 over T1L: —
- S3 sustained video Mbps / fps: —
- S5 loss rate: —
- S6 end-to-end fps / latency: —

### S0 detail (2026-08-09) — AE3 `machine.SPI(0)` ceiling

Loopback P0→P1, 512 KB moved per point, verify pass separate from timing:

| SPI clock | 64 B | 256 B | 1 KB | 4 KB | errors |
|---|---|---|---|---|---|
| 5 MHz  | 2.47 | 2.51 | 2.51 | 2.51 | 0 |
| 10 MHz | 3.26 | 3.31 | 3.33 | 3.33 | 0 |
| 20 MHz | 4.74 | 4.85 | 4.88 | 4.89 | 0 |
| 25 MHz | 4.74 | 4.85 | 4.88 | **4.89** | 0 |

(effective Mbps, payload bits / wall time)

- **20 vs 25 MHz identical to the microsecond** → the real SCLK is clamped at
  or below 20 MHz; requesting 25 changes nothing.
- **Bottleneck is per-byte, not per-call**: 64 B → 4 KB chunks barely helps,
  and a single 4 KB `spi.write()` call takes 6.6 ms where wire time at
  20 MHz is 1.6 ms. Follow-up probe: TX-only `write()` 4.97 Mbps, RX-only
  `readinto()` 4.90 Mbps at 20/25 MHz — direction-independent. Hypothesis
  (unverified, flag not fact): polled non-DMA FIFO in the port's SPI driver,
  ~600 ns/byte CPU cost. Confirm by reading the OpenMV/MicroPython Alif port
  source before acting on it.
- **IRQ latency (P4→P5 edge → Python handler, 100 edges, 0 missed):**
  soft ISR min 5 / median 6 / p99 15 / max 15 µs; hard ISR min 4 / median 5 /
  p99 9 / max 9 µs. The IRQ path is a non-issue at these numbers.

### S0 video encode table (2026-08-09) — encoder, not SPI, is the bottleneck

`bench/ae3_video_bench.py` on the AE3 (fw 1.28, sensor id 0x7936, indoor
bench scene). Sensor facts: letterboxes (QVGA→320×200, VGA→640×400,
HD→1280×800); QQVGA/SVGA/WXGA unsupported ("Sensor control failed");
needs `set_framebuffers(1)` for VGA+. Supported modes at q50:

| Mode | bytes/fr | bpp | enc ms | enc-limited fps | + SPI tx ms* | est fps | est Mbps |
|---|---|---|---|---|---|---|---|
| QVGA color | 1 884 | 0.24 | 17.2 | 58 | 3.1 | ~49 | 0.74 |
| QVGA mono | 1 106 | 0.14 | 6.0 | 120 (cap) | 1.8 | ~100 | 0.9 |
| VGA color | 5 611 | 0.18 | 68.5 | 14.6 | 9.2 | ~12.9 | 0.58 |
| VGA mono | 3 328 | 0.10 | 24.1 | 41.6 | 5.4 | ~34 | 0.9 |
| HD color | 20 611 | 0.16 | 273.6 | 3.7 | 33.7 | ~3.3 | 0.54 |
| HD mono | 12 328 | 0.10 | 96.0 | 10.4 | 20.2 | ~8.6 | 0.85 |

*SPI tx at measured 4.89 Mbps (611 KB/s), polled driver = encode and tx
serialize. Capture time and ADIN protocol overhead not yet included.

**Key finding:** the software JPEG encoder caps every supported mode below
~2 Mbps of produced video — the ~4.9 Mbps SPI ceiling has ≥ 2× headroom
even in the worst case. Scaled to the deployment-scene anchor (0.875 bpp,
~4–5× worse than this bench scene): VGA color ≈ 8 fps @ 2.2 Mbps, HD color
≈ 2.3 fps @ 1.9 Mbps — still under the SPI ceiling. The 12 Mbps S0 gate was
sized for a workload the encoder can't generate.

Oddities recorded, not yet explained: mono bytes/frame identical across
q15–q90 (quality knob appears inert for grayscale); VGA color q75 = q90
byte-identical. Re-measure bpp with the deployment scene/underwater footage
before freezing stream settings (S3).

### S0 reference-scene encode table (2026-08-09) — coral reef P7071008

Dark-room caveat resolved: `bench/make_ref_scene.py` center-crops the
reference photo to the sensor's 16:10 letterbox (full ROI kept, 4000×3000 →
4000×2500) and downsamples to the three mode geometries;
`bench/ae3_ref_scene_bench.py` encodes them on the AE3 via mpremote mount.
Reef bpp is 3–5× the dark room and brackets the 0.875 SPEC anchor
(q35 ≈ 0.53–0.77, q50 ≈ 0.59–1.15, q75 ≈ 0.91–2.0 across modes).

Composite at q50 — encode + SPI tx at measured 4.89 Mbps, serialized
(polled driver), capture excluded:

| Mode | bytes/fr | bpp | enc ms | tx ms | est fps | est Mbps | bound by |
|---|---|---|---|---|---|---|---|
| QVGA color | 9 198 | 1.15 | 19.7 | 15.0 | ~29 | 2.1 | encoder |
| QVGA mono | 7 536 | 0.94 | 8.3 | 12.3 | ~49 | 2.9 | SPI |
| VGA color | 29 148 | 0.91 | 76.7 | 47.7 | ~8.0 | 1.9 | encoder |
| VGA mono | 23 831 | 0.75 | 31.1 | 39.0 | ~14.3 | 2.7 | SPI |
| HD color | 93 253 | 0.73 | 299.2 | 152.6 | ~2.2 | 1.7 | encoder |
| HD mono | 75 324 | 0.59 | 117.6 | 123.3 | ~4.2 | 2.5 | balanced |

Refined conclusion (supersedes the dark-room "encoder is the bottleneck"
headline for real scenes): **color modes stay encoder-bound; mono modes are
SPI-bound or balanced.** Delivered stream lands at 1.7–2.9 Mbps in every
mode — under the 4.89 Mbps SPI ceiling (which serialization can never
saturate: delivered = ceiling × tx/(tx+enc)) and far under the 8 Mbps T1L
budget. Working modes for the product: VGA color ~8 fps, VGA mono ~14 fps,
HD mono ~4 fps. A C-level driver (DMA + overlap) would roughly buy back the
tx column: VGA color → ~13 fps, QVGA mono → ~120 fps.

Oddity resolved: dark-room "mono ignores quality" was a scene artifact —
on reef content mono bytes scale q15→q90 (3 562 → 19 102 B at QVGA). The
dark room simply had too little detail for the knob to matter.

Follow-up probes (2026-08-09, answering "is something bogging us down?"):
- **Hardware JPEG: NOT available.** Firmware exposes `sensor.JPEG` but this
  sensor (0x7936) rejects it at every size ("Sensor control failed") — no
  free encoder on the AE3; software encode is the only path. (N6/H.264 is
  the hardware-encode lever, iceboxed.)
- **Capture cost measured:** VGA RGB565 snapshot = 33.3 ms single-buffered
  (30 fps sensor cadence), 16.7 ms with `set_framebuffers(2)`; capture DMA
  overlaps CPU work when double-buffered, so it mostly hides behind encode
  in a real pipeline. HD fits only 1 buffer → capture serializes there.
- Encoder timings are trustworthy: tight `ticks_us` around `to_jpeg` only;
  mount/USB not in the timed path; reef encode ≈ dark-room encode (77 vs
  69 ms VGA q50) confirms per-pixel cost dominates.

Multi-image trend sweep (other `images/` files) pending — pipeline is
parameterized by label; not yet run. Downsampled P7071008 set committed at
`bench/assets/ref_scene/` (raws stay untracked per Nick).

### S0 decision note (gate hit: < 12 Mbps) — RESOLVED: A then B (Nick, 2026-08-09)

**Spike result (option A, done):** hypothesis confirmed from source.
`ports/alif/machine_spi.c` — `machine_spi_transfer()` is a fully polled,
lock-step, per-byte loop: per byte it (1) spins on `SPI_SR.TFNF` with a
`ticks_ms` timeout check + `mp_event_handle_nowait()` per iteration,
(2) writes one byte to the FIFO, (3) spins on `SPI_SR.RFNE`, (4) reads one
byte. No DMA, no FIFO bursting — the 16-deep hardware FIFO is used one entry
at a time. OpenMV's fork (`openmv/micropython` master) is byte-identical to
upstream here, so a newer firmware won't change the number. Measured
software cost ≈ 1.2–1.6 µs/byte, which caps ~5 Mbps regardless of SCLK —
consistent with 20 and 25 MHz benching identically.

Fix pricing (for the future C decision): a FIFO-burst rewrite of
`machine_spi_transfer` (keep TX FIFO fed while draining RX, no lock-step)
is ~50 LoC of C in one function and should approach wire rate; DMA is the
full-fat version. Either requires building custom AE3 firmware — i.e. it IS
option C territory. Candidate upstream contribution later.

Unresolved (flagged, not guessed): the true SCLK at requested 20 vs 25 MHz.
Timings are identical but both reprs echo the requested rate; DW-SSI even-
divider rules suggest one of them is not literal. Needs a logic analyzer
(S2 has one on the bench) — irrelevant to the ceiling, which is software.

**Original options considered:**

The MicroPython-level driver cannot carry the ≤ 8 Mbps video budget:
~4.9 Mbps raw SPI ceiling before any protocol overhead (ADIN frame headers,
control reads, turnaround) — realistic payload well under 4.5 Mbps. Options:

- **A. Timeboxed spike:** read the Alif port SPI driver source; if the slow
  path is a known/fixable issue (DMA exists but unused, divider bug), a
  firmware fix or OpenMV upstream issue may recover most of the gap cheaply.
- **B. Proceed at reduced budget:** S4–S6 work unchanged at ~4 Mbps video
  (e.g. VGA mono @ ~8 fps fits). Ship the ladder, treat C-level DMA driver
  as the follow-on. Still ~40× the v1 UART path.
- **C. Go to C now:** native OpenMV firmware driver with DMA (the iceboxed
  lwIP/C path pulled forward). Highest cost, removes the ceiling.

Decision (Nick): **A then B.** Spike ran same day (result above); project
proceeds per B — MicroPython driver sprints S4–S6 continue against a
**~4 Mbps realistic video budget** (raw ceiling 4.89 Mbps minus protocol
overhead), e.g. VGA mono @ ~8 fps. The 8 Mbps SPEC budget is the T1L-side
ceiling and stays correct for the Pi↔Pi sprints; the AE3-sourced budget is
now the binding one. C-level FIFO/DMA driver remains the priced follow-on
if v2 needs more than ~4 Mbps.
