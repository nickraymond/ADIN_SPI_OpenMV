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

- S0 SPI benchmark: —
- S2 iperf3 over T1L: —
- S3 sustained video Mbps / fps: —
- S5 loss rate: —
- S6 end-to-end fps / latency: —
