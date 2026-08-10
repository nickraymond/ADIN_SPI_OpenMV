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

### S0 decision note (gate hit: < 12 Mbps) — decision PENDING Nick

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

Recommendation: A then B — spike is cheap and informs whether B's ceiling is
temporary; S1–S3 don't touch AE3 SPI and can proceed regardless.
