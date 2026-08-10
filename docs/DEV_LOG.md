# DEV_LOG.md — Session Log

*Newest entries on top. One entry per working session. Keep entries short:
what changed, what broke, what's next. Agents: add yours before ending the session.*

---

## Entry template

```
## YYYY-MM-DD — Sprint Sn — <one-line summary>
**Branch:** sprint/n-slug
**Done:**  <bullets>
**Broke/surprised us:** <bullets or "nothing">
**Next:** <the single next bite>
```

---

## 2026-08-09 — Sprint S0 — SPI bench run: 4.89 Mbps ceiling, gate FAILED

**Branch:** sprint/0-SPI-bench-test

**Done:**
- `bench/ae3_spi_bench.py` (~250 LoC) + 15 host-side unit tests for the pure
  helpers (all pass, CPython 3.13). Two-phase: loopback throughput sweep,
  then auto-detected jumper move to P4→P5 for IRQ latency.
- Ran it on the AE3 (fw v1.28.0-49) remotely: Mac → ssh pi@nereus000 →
  mpremote → `/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*-if00`. Nick moved
  the jumper on cue. mpremote 1.28.0 installed on nereus000.
- Results recorded in DESIGN.md §Bench results + decision note. Headline:
  **max 4.89 Mbps effective (25 MHz/4 KB), 0 integrity errors, FAIL vs
  12 Mbps gate.** IRQ latency superb (soft median 6 µs, hard 5 µs, 0 missed).

**Broke/surprised us:**
- 20 and 25 MHz timings identical → SCLK clamped ≤ 20 MHz.
- Bottleneck is per-byte inside the port driver (TX-only = RX-only = duplex
  ≈ 5 Mbps; chunk size nearly irrelevant). Hypothesis: polled non-DMA FIFO —
  unverified, needs port-source read.
- `/dev/ttyACM0` on nereus000 is the N6, not the AE3 — use the by-id path.

**Next:** Nick decides on the decision note (spike port source / proceed at
reduced budget / go C). Then: Nick's official demo run per TRACKER + PR.

**Branch:** n/a (no code yet)

**Done:**
- Board selection analysis (AE3 vs N6): AE3 for v1; N6 iceboxed pending
  OpenMV answer on H.264 MicroPython API. Full analysis in the decision-matrix
  artifact; key numbers in SPEC.md.
- Identified all ADIN hardware: SG SPE V1.0.0 shield (documented pinout:
  SPI0/CE0, RST GPIO17, IRQ GPIO22) + 2× AOS BOREALIS Pi-Zero hats
  (pinout NOT yet verified).
- Strap state confirmed from vendor docs, not guesswork: SG shield ships
  SPI_CFG0+CFG1 bridged = generic SPI without CRC (SG Linux page + ADIN
  datasheet Table 22). SWPD/TX2P4/MS_SEL/EWP/SHLD open = defaults.
- Found SG's published device-tree overlay (23 MHz, GPIO22, PHY compat
  0283.bc91 → expected PHY ID 0x0283BC91). Kernel module still needs
  menuconfig build — that's S1.
- Wiring diagrams drawn + reviewed: AE3↔SG harness (8 wires) and two-node
  bench link. In docs/diagrams/.
- Sprint ladder S0–S7 defined in TRACKER.md; ≤8 Mbps stream budget set from
  measured 0.875 bpp still.

**Broke/surprised us:**
- SG schematic PDF is image-only (no text layer) — strap meanings had to come
  from SparkFun COM-19038 guide + ADIN datasheet + SG's Linux page instead.
- SparkFun's default strap state (OA with protection) is the OPPOSITE of SG's
  as-shipped state (generic no-CRC) — same jumpers, different factory setting.
- JP1/JP4 on the SG shield are publicly undocumented. Open question in SPEC.md.

**Next:** S0 — run the AE3 SPI loopback benchmark (needs only the AE3 and one
jumper wire). Nick gate: approve S0 plan nibble first.
