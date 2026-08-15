# SPEC.md — Bristlemouth Camera Node: Native ADIN1110 Video Path

*What I want. Stable reference — agents skim this; changes require Nick's approval.*
*Last updated: 2026-08-10*

## Goal

Stream video from an OpenMV AE3 camera over an ADIN1110 10BASE-T1L link into an
existing Pi-hosted web stream — replacing the current AE3→Pi USB transport with
the single-pair Ethernet path that the production camera node will use.

**The end-state demo:** open a browser, see live video from the AE3, and the only
data path between camera and Pi is one twisted pair.

## Background

- v1 product pipeline (working today): AE3 → mote UART → Spotter `spotter_tx` →
  Sofar cellular. Stills only; 115200 baud is the bottleneck.
- This project builds the v2 transport: AE3 talks SPI to an ADIN1110 (T1L
  MAC-PHY), which puts real Ethernet on a wet-rated single pair. Target system
  is camera node ↔ Pi telemetry node over our own pair (3–10 Mbps telemetry
  uplink exists separately).
- AE3 → Pi over USB with web streaming already works; that receive/serve side
  is reused as-is so each sprint changes one thing.

## Hardware inventory

| Item | Qty | Role |
|---|---|---|
| OpenMV AE3 (Alif E3) | 1 | Camera + SPI host under test |
| OpenMV N6 (STM32N657) | 1 | Alternate camera board (H.264-capable), later |
| SG-Electronics SPE V1.0.0 shield (ADIN1110) | 1 | Known-good ADIN board, documented pinout |
| AOS BOREALIS Pi-Zero hat (ADIN1110) | 2 | Node hardware; pinout to be buzzed out |
| Raspberry Pi 5 | 1 | Primary Linux node (eth0 stays free for debug) |
| Raspberry Pi 3/4 | 1 | Second Linux node |
| Test/dev Pi on Tailscale | — | Remote access for Claude Code sessions |

## Confirmed technical facts (verified, with sources)

### ADIN1110 SPI protocol straps (datasheet Table 22, ADIN family)

| SPI_CFG1 | SPI_CFG0 | Mode |
|---|---|---|
| 0 | 0 | OPEN Alliance with protection (SparkFun ship default) |
| 0 | 1 | OPEN Alliance without protection |
| 1 | 0 | Generic SPI with 8-bit CRC |
| **1** | **1** | **Generic SPI without CRC — SG shield as-shipped (both pads bridged)** |

SG's own Linux page confirms: "SPI_CFG0 and SPI_CFG1 must be enable[d] to put
ADIN1110 in generic SPI protocol without CRC." Do NOT set `adi,spi-crc` in the
device tree. Other SG straps as-shipped: SWPD/TX2P4/MS_SEL/SHLD/EWP all open
(normal operation, autoneg, default amplitude).

### SG shield ↔ Raspberry Pi pinout (SG documentation)

SPI0 CE0 · reset = GPIO17 · interrupt = GPIO22 (level) · I2C = PCF85063 RTC ·
SG's published overlay uses 23 MHz and compat `ethernet-phy-id0283.bc91`
(→ expected PHY ID readback: **0x0283BC91**).

### OpenMV AE3 user pins (OpenMV docs)

| AE3 pin | Function in this project |
|---|---|
| P0 / P1 / P2 | SPI0 MOSI / MISO / SCLK |
| P3 | Chip select — manual GPIO (SS is not peripheral-driven on AE3) |
| P4 / P5 | ADIN RESET (out) / ADIN IRQ (in) |
| — | All 3.3 V. NOT 5 V tolerant. P6–P9 are 1.8 V — do not use. No 5 V rail on board. |

MicroPython: `machine.SPI(0)`, CS/RST/IRQ as `Pin("P3"/"P4"/"P5")`.

### Link + stream budget

- 10BASE-T1L: 10 Mbps line rate, full duplex, point-to-point; ~9.3 Mbps usable.
- ADIN1110 max SPI clock: 25 MHz. Start bring-up at 5 MHz.
- **Video budget: ≤ 8 Mbps sustained.** Measured anchor: 1000×562 @ 60 KB JPEG
  = 0.875 bpp → 1280×800 @ 10 fps ≈ 9.0 Mbps (just over). Use mono, ~8 fps, or
  lower quality to fit. Sprint S0 replaces estimates with measurements.

### Wiring

See `docs/diagrams/wiring_ae3_to_sg_shield.svg` (8-wire harness, Diagram 1) and
`docs/diagrams/wiring_two_node_bench.svg` (two-node link, Diagram 2).

## Product requirement space (set by Nick, 2026-08-09)

Two axes carry the physics: spatial detail × temporal smoothness. Use cases
land in quadrants; each cell has a different binding constraint.

| | Low fps (≤5) | High fps (≥15–30) |
|---|---|---|
| **High res (HD+)** | Edge CV counting — NPU-bound | Public 720p stream — needs H.264 (N6, non-goal) |
| **Low res (≤VGA)** | Presence/stills — solved | Ambient live stream — SPI/encoder-bound |

**Platform: AE3** (N6 reserved for the public-720p cell, out of scope).
Committed targets, both pursued:

- **T1 — Live stream:** QVGA color, q35–50, **24–30 fps** delivered over T1L
  into the web stream. Resolution rises only if fps holds. Basis: measured
  encode 17–20 ms + SPI tx 12–15 ms/frame → 29–35 fps ceiling; REQUIRES
  capture/encode/tx overlap (≥2 framebuffers) — S6 design constraint.
- **T2 — Edge CV:** HD capture (mono acceptable — target fish are
  high-contrast), **3–5 fps** capture+inference on-device; only alerts +
  evidence JPEGs cross the link. Fish must be ≥ ~24–32 px for detection
  (~100–150 px at P7071008 range → HD suffices; else move camera closer).
  Sequenced AFTER the SPI driver meets T1 (Nick).

**Transport gate (replaces the retired ≥12 Mbps S0 gate):** SPI effective
throughput ≥ 2× the T1 stream bitrate = **≥ 3.5 Mbps** (q35) / 4.4 Mbps
(q50). Measured 4.89 Mbps → passes.

## Safety rules (non-negotiable)

1. **Never connect any of these ADIN boards to a powered Spotter/Bristlemouth
   bus.** They have no PoDL protection; the bus pair carries DC. Bench twisted
   pair only.
2. AE3 I/O is 3.3 V only. Nothing from a 5 V rail touches it.
3. Before first power-up of the SG shield off-Pi: meter check whether pin 1
   (3V3) or pin 2 (5 V + regulator) feeds the ADIN supply.

## Success criteria by sprint

See TRACKER.md — every sprint ends with a live demo Nick can run. Project done
when the S6 demo passes: browser stream sourced from the AE3 across the T1L
pair, USB carrying no video.

## Non-goals (this project)

- Bristlemouth protocol compliance / bm_core port (S7 produces a *decision*, not code)
- Connecting to a live Spotter bus
- N6/H.264 path, potting, enclosure work
- Public-tier streaming (720p ≥24 fps needs H.264 → N6 follow-on; MJPEG at
  that tier exceeds the T1L wire itself, ~16 Mbps)
- v2 PCBA layout (this project produces the facts it needs)

## Open questions (flag, don't guess)

- **VGA capture hard-faults the AE3 when the HE stack + rpmsg + VCP
  bridge are live (measured 2026-08-15, S18 bite A nibble 3).** A
  `capture 50 vga color` over the BM chain was accepted (`ok=1
  res=vga pf=color`) and the board then died: `uart_l2: decode error`
  on the Light node (garbage mid-transmission), neighbor offline 46 s
  later, and the AE3 off the USB bus entirely (`device not accepting
  address, error -71`, `unable to enumerate`). Recovered only by the
  `ae3-usb-unstick` ladder (Pi reboot).
  **Isolated by bisection on a clean REPL, same firmware, same session:**
  (a) QVGA capture under the bridge — **works** (3 chunks published);
  (b) VGA capture standalone, no HE stack — **works** (640×400,
  10,833 B); (c) the runtime switch QVGA→VGA→QVGA standalone —
  **works** (3,898 / 10,779 / 3,889 B, 4 MB heap free throughout).
  Only VGA *with the HE stack loaded and the bridge pumping* fails, so
  it is neither VGA itself nor the re-init/switch path.
  **The fault is below MicroPython:** no Python traceback was raised
  (the bridge's non-fatal sensor handler never ran) and
  `bridge_crash.txt` has a `boot:` line with no matching exit record,
  where every clean shutdown writes one. Same family as D15.
  Candidate causes, none verified — do not guess in code: framebuffer
  allocation (VGA RGB565 = 512,000 B) colliding with the OpenAMP/rpmsg
  shared-memory mapping; sensor DMA vs MHU/rpmsg contention; heap
  fragmentation under the loaded ELF. Next probe: a `bridge_cfg.json`
  VGA one-shot with the HE loaded but no Pi chain, now that the bridge
  preserves `bridge_trace.prev.txt` across a restart (the first crash's
  trace was wiped by the relaunching bridge).
  **Consequence: S18's VGA+HD scope is BLOCKED on this** — the bench
  tool's whole point is comparing resolutions. QVGA is unaffected.

- ADIN1110 OA control-data protection (CONFIG0.PROTE, bit 5): measured
  2026-08-11 on hat #2 (straps opened to default) — chip comes up in OA
  mode with PROTE=0 and the bit does NOT accept a write (tried plain and
  with CONFIG0.SYNC; other registers/bits write fine). Contradicts this
  file's Table-22 note that all-straps-low = "OA WITH protection", and
  breaks bm_core's driver as-shipped (compiled CONFIG_SPI_PROT_EN=1).
  Needs datasheet cross-check (PDF fetch timed out): is PROTE
  unimplemented on the 1110 (2111-only?), silicon-rev-dependent, or
  gated some other way — and what do the strap combos actually select
  on the 1110?
  **AMENDED 2026-08-11 (S9 bite 2, measured): PROTE=1 is REACHABLE on
  this chip** — after a 20 MHz OA bench rung (2000 misclocked garbage
  frames) CONFIG0 read 0x26 with PROTE set; in that state the chip
  silently DROPS unprotected control writes (latching STATUS0.CDPE)
  while unprotected reads still return correct data (first-data-word
  alignment), and a PROTECTED-framed write (data + ones-complement)
  works — that's how the chip was recovered (protected soft reset →
  CONFIG0 back to 0x06, PROTE=0). Working theory: garbage traffic can
  decode as a valid CONFIG0 write; this also plausibly explains bite 1's
  one-shot protected-mode complement sighting (fd7c436e — the ANOMALY
  note above). Bite-1's "PROTE rejects writes" claim should be re-tested
  deliberately (protected/unprotected, with/without SYNC) before S13's
  2111 notes; if PROTE is settable on purpose, bm_core's SHIPPED
  protected default may work on the 1110 unmodified. Runner mitigation
  in place: both-framing soft-reset sanitize + CONFIG0 verify before and
  after risky rungs (`s9_hal_native.py`).

- AE3 P4 → hat RESET line is INEFFECTIVE (measured 2026-08-11, S9
  bite 2): an IMASK0 register scratch value survives a 50 ms P4 low
  pulse, and STATUS0.RESETC does not re-latch — the ADIN never sees the
  reset. Every earlier "reset pulse" in S4–S9 was followed by an init
  that worked regardless, so nothing had actually verified this line.
  Bench check needed (Nick): P4 jumper seated at hat pin 11? hat's
  RESET_N header routing to the chip? Until resolved, the chip's
  software reset (RESET reg 0x003 = 1) is the only working reset, and
  chip state persists across ALL board flashes/reboots (hat is powered
  from the Pi's always-on 3V3 header — D19).

- **T1L link dead — CONCLUSION 2026-08-12 (S9 bite 3, full-day
  isolation with Nick): AT LEAST TWO of the three line interfaces are
  broken; both AOS hats are the economical suspects.** All three
  pairings among {hat #1, hat #2, SG shield} fail with zero energy in
  either direction, across two different cables, three termination
  styles (crimped Micro-Fit, solder, screw block), both protocols (AN
  and ethtool-forced master/slave, matched), reference kernel drivers
  on both ends, verified straps/overlays/modules (vermagic match, no
  spi-crc), and clean PMIC rail telemetry. If only one endpoint were
  dead, one pairing would have linked. Damage window: after the S6
  demo's successful replug (link resumed = hats alive then), during
  the re-strap/bench-work era — one transient into the shared pair
  reaches both line drivers at once (no line protection on these
  boards). Consequences: bench needs replacement link hardware (2nd
  AOS hat order, or jump to ADIN2111 eval boards per bite-1's
  pre-approved fallback); SG shield is the one probably-good endpoint
  (line side never proven though — no shield↔shield pairing possible);
  add bench rule candidates: no hot-plug of the pair with ends
  powered, ESD strap during hat handling.
- CORRECTION (2026-08-12, measured by Nick on BOTH hats + shield,
  Fluke 15B+, unpowered): the S2 design-file claim "hat J1 DC-shorted
  through the T1 winding" is WRONG — both hats read OPEN (OL) at the
  J1 pads, shield reads ~2 MΩ at its terminals. All three line fronts
  are DC-blocked (series caps); an open/high DC reading is the HEALTHY
  signature. DC continuity therefore CANNOT verify these line paths
  (only the bare cable). DESIGN §S2 table row stands corrected here;
  the netlist parse followed nets through the coupling network.
- AOS hat TX2P4 strap is PULLED HIGH (measured 2026-08-11 on hat #2:
  B10L_PMA_CNTRL powers up 0x1000 = 2.4 Vpp TX enabled; chip reset
  default is 0). Contradicts the SG-shield-derived assumption that all
  amplitude straps float. Harmless with AN (amplitude negotiated) but
  matters for forced-mode configs and for the S13 production notes.
- **AE3 P0–P5 are level-translated, not direct SoC pins** (Nick's
  schematic review, 2026-08-12): `P0_INT…P3_INT` / `QWIIC_*_INT` nets
  confirm translation from the SoC's 1.8 V domain; AE3 BOM carries an
  NXS0104 (4-bit) + NXS0102 (2-bit) — open-drain auto-direction
  translators, internal 10 kΩ pull-ups, 24 Mbps max. UNVERIFIED detail
  (flag, don't act): which part sits on which nets was inferred from bit
  counts, not read off the sheet — needs EE confirmation on the AE3
  schematic before any mitigation. Consequences if confirmed: (a) a
  PHYSICAL SPI ceiling independent of the D8 software ceiling — slow
  open-drain rising edges mean usable SCLK lands well below the
  ADIN1110's 25 MHz; (b) a clean physical hypothesis for the S9 bite-2
  finding that 20 MHz OA READS return garbage while 20 MHz generic-SPI
  TX ran the whole S6 demo at zero loss (MISO-into-AE3 is the
  edge-sensitive direction; RX_SAMPLE_DELAY sweep already banked as an
  S9 bite-3 starter, external pull-up stiffening on the hat side is a
  new mitigation candidate — B-side only, the AE3's internal 10 kΩ is
  fixed); (c) the "true SCLK at 20/25 MHz" open question below gains a
  reason to exist beyond curiosity; (d) production v2 PCBA: measure
  actual clock + rise time at the B2B connector before releasing layout
  (S13 item), and budget the throughput model for a lower SPI clock.
  No impact on the current USB-only interim ladder (no SPI involved).
- SG shield JP1 (5-pin) / JP4 (3-pin): undocumented publicly; hypothesis =
  standalone-MCU breakout. Resolve by continuity or by emailing SG.
- ~~AOS hat: CS/IRQ/RESET GPIO mapping, strap state, pair-connector
  polarity~~ — ANSWERED from AOS design files (2026-08-10, S2): CE0 /
  GPIO22 / GPIO17, same as SG shield; straps default OA (hats re-strapped
  to generic SPI no CRC via CFG0+CFG1 jumpers); J1 ckt 1 = DA−. One board
  gap: INT_N pull-up missing → Pi internal pull-up in overlay. Full table
  in DESIGN.md §S2 detail; hat #1 validated live on nereus000 (PHY ID
  0x0283bc91, verify 5/5); hat #2 still to verify per
  `docs/aos_hat_checklist.md`.
- Sofar's OA-mode Linux/BM driver status — input to S7 decision.
- ~~AE3 `machine.SPI` real throughput~~ — ANSWERED by S0 (2026-08-09):
  4.89 Mbps max effective, software-limited (polled per-byte port driver).
  Video from the AE3 is budgeted ~4 Mbps until a C driver exists (DESIGN.md D8).
- True AE3 SCLK at requested 20 vs 25 MHz (timings identical; even-divider
  rules suggest one repr lies) — was waiting on S2's logic analyzer; Nick
  has none (descoped 2026-08-10). Parked until an LA turns up; irrelevant
  to the ceiling, which is software (D8).
- AE3 NPU inference rate: small detector (YOLO-class) fps vs input size on
  HD frames (tiled/downscaled) — gates T2; bench when T2 work begins
  (sequenced after T1 per Nick).
- QVGA delivered fps with full capture/encode/tx pipelining — model says
  ~29 fps at q50; verify in S6 (T1 pass/fail hangs on it).
- AE3 firmware crash (found S3, 2026-08-10): the second `start_stream`
  session per boot in the USB capture service hard-faults the board (USB
  dies; sometimes needs physical replug). Present on stable v5.0.0 AND dev
  `11852aa3d0`; `machine.reset()` between sessions works around it (D15).
  Root cause in firmware unknown — clean repro exists
  (`firmware/ae3_usb/README.md` §Known firmware crash); file upstream with
  OpenMV. Watch item for S6: does the SPI-driver-era capture loop hit the
  same fault class on sensor re-init?
