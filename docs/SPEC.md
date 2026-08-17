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

- **A sensor re-init too soon after a capture throws
  `RuntimeError('Sensor control failed.')` and WEDGES the sensor for the
  rest of the bridge's life (measured 2026-08-16, S18 bite B trial
  matrix).** `_ensure_sensor` catches it, sets `cur_res/cur_pf = None`,
  and every later command fails identically — measured across 7 further
  commands over 60 s, including `qvga color` that had worked a minute
  earlier. Recovery requires restarting the bridge.
  **The visible symptom lies:** the HE camera service replies `ok=1` with
  advancing `cmds` because it never learns the HP bridge refused, so the
  operator sees healthy acks and zero images while `pub_ok` stays frozen.
  Measured, one variable at a time (chain up, systemd units, S19
  artifacts):
  - sub-second gap between a capture and the next re-init → **fails**,
    2/2 on freshly staged bridges, deterministic under the trial driver;
  - ≥6 s gap → **succeeds**, 3/3;
  - at a **2 s** gap it survived three re-inits and failed on the fourth —
    the one that followed a **VGA** frame (8 chunks) rather than a QVGA
    one (2–3 chunks).
  So the required quiet time **scales with the previous frame's size**,
  which is why a fixed delay is the wrong shape of fix.
  **It is NOT greyscale.** Greyscale works: `capture 50 qvga mono` with
  time around it delivered 320×200, 1 component, 1,090 B, `gaps_delta=0`
  — the first mono frame this project has carried over the chain.
  Greyscale was simply the first command in the sweep that required a
  re-init at all; QVGA→QVGA colour repeats need none.
  **Mechanism NOT established — do not guess it.** Two candidates, and
  they imply different fixes: (a) the sensor's own frame pipeline/DMA is
  still busy (the size scaling fits, and 8 chunks would drain from the HE
  in ~20 ms, not 2 s — which argues against the HE); (b) the HE core is
  still publishing (`wire_status_t.stream_sent` is already parsed by the
  bridge and would gate it precisely). **The decisive experiment is an
  off-chain probe with the HE core NOT loaded**
  (`bench/probes/s18_reinit_probe.py`, written but never executed).
  **Prerequisite learned the hard way: the board cannot be probed while
  `/flash/main.py` is the bridge launcher** — `mpremote run` enters the
  raw REPL via a soft reset, which runs main.py, which starts a bridge
  that then holds the VCP. Stage a neutral `main.py` first.
  **ANSWERED 2026-08-16 (S18 bite B2 nibble 1, measured off-chain — no
  Pi, no chain — in one board window; full record in DEV_LOG):** three
  rungs, one ingredient added at a time.
  - **Rung A, no HE core loaded: 12/12 PASS** (QVGA/VGA/HD ×
    0/250/1000/4000 ms). Candidate (a) — the sensor's own frame pipeline
    — is **REFUTED**, and with it the "required quiet time scales with
    the previous frame's size" reading: a 0 ms re-init after a 35.7 KB HD
    frame is fine when nothing else is running.
  - **Rung B, HE core loaded but idle: 9/9 PASS**, core ticking, rpmsg
    queue empty. A loaded core is not sufficient either.
  - **Rung C, HE core loaded AND publishing: the board went off the USB
    bus on the first measured re-init.** 4,051 B QVGA capture → its real
    3-chunk WCMD_PUB burst (0 send timeouts) → `set_pixformat(GRAYSCALE)`
    → `device not accepting address, error -71`, `unable to enumerate`.
    **So the mechanism is the overlap of a sensor re-init with a publish
    in flight** — candidate (b), narrowed.
  **SEVERITY CORRECTION, and it changes the shape of the fix:** the fault
  is not only the catchable `RuntimeError` above. With a publish in
  flight the same trigger can kill the board outright, with **no Python
  exception to catch** (the D15 class; recovery = `sudo reboot` on
  nereus000 per `ae3-usb-unstick`). **A fix that catches and recovers is
  therefore not sufficient — the overlap must be prevented.** The bridge
  already parses `wire_status_t.stream_sent` and can gate on it.
  Rate unknown: the board died on the first rung, so N=1 for the fatal
  variant. **Until the fix lands, the bench page's 8 s settle guard is
  safety equipment** — it is the only thing between a fast double-click
  and a bench that needs a reboot.
  Reproducer, off-chain and ~4 minutes, no Pi chain required:
  `bench/probes/s18_reinit_probe{,_b,_c}.py`.
  **FIX ATTEMPT #1 FALSIFIED (2026-08-16, S18 bite B2 nibble 3, rung D):**
  gating the re-init on "publish drained" — an in-order WCMD_QUERY
  barrier + heap_free recovery + stable stream counters — is NOT
  sufficient. The gate opened with every condition satisfied (GO after
  4 ms, status_seq=2, heap_high=20,576) and the board still went off the
  bus, at `set_framebuffers(1)`, one call further than the ungated run.
  **Refined hypothesis, not yet proven: the killer is an HE→HP rpmsg
  ARRIVAL (MHU doorbell + MicroPython endpoint callback) landing during
  the framebuffer calls**, not the publish being in flight per se. Fits
  all four rungs: rung B exchanged zero rpmsg after the announce (status
  via `machine.mem32`) and was safe; the barrier reply can overtake the
  published frames' drain tail (`wire_pump_tx` is incremental, the reply
  is direct), so traffic was still arriving when rung D re-inited. Also
  fits the size scaling and the ≥6 s heal. **Decisive next experiment:
  rung E — after the barrier, pump until the HE→HP side is silent for
  N ms, then re-init.** If that dies too, the fix is not bridge-side.
  **RUNG E RUN (2026-08-16) — BOTH HYPOTHESES NOW FALSIFIED, AND THE
  ORIGINAL WEDGE REPRODUCED OFF-CHAIN FOR THE FIRST TIME.** With the
  gate open AND **zero** late messages AND 250 ms of measured rpmsg
  silence, `set_framebuffers(1)` at ~270 ms after the publish **still
  failed — but politely**: `RuntimeError('Sensor control failed.')`
  after a **100,818 µs** attempt (I2C-timeout scale), board alive, probe
  exited cleanly. Every later `set_framebuffers` this session failed in
  **13 µs** (instant refusal, not an attempt) across 9 tries spanning
  ~80 s of quiet — bite B's "wedged for the bridge's life," measured.
  **The severity is a function of TIME SINCE THE PUBLISH, not of
  traffic:** ~0–10 ms → board off the bus (rungs C, D); ~270 ms →
  catchable RuntimeError + persistent wedge (rung E); ≥6 s on-chain →
  success 3/3 (bite B). Some HE-publish-coupled state decays over
  seconds and breaks sensor control from the HP side; it is **not
  observable from the bridge** — both observable proxies (publish
  drained; rpmsg quiet) are now measured insufficient. Root cause is
  below MicroPython and remains open (candidate upstream report).
  **Never tested, and now the two questions that decide the fix:**
  (1) does `sensor.reset()` + re-bootstrap CLEAR the wedge (rung E
  never attempted recovery — if yes, the bridge can catch and
  self-heal); (2) where is the safe-delay boundary per frame size
  (bite B's 2 s/6 s points are on-chain; no off-chain boundary has
  been measured — it would replace the 8 s guess with a number).
  **RUNG F RUN (2026-08-16): the ~250 ms failure is STOCHASTIC, and
  ≥500 ms passed 10/10 off-chain.** The deliberate wedge provocation at
  rung E's exact 250 ms point PASSED this run — so Q1 (recovery) stays
  OPEN: there was no wedge to clear, and R1/R2/R3 remain unexercised.
  The sweep then passed every row: QVGA and HD × 500/1000/2000/4000/
  6000 ms, including HD frames of 45 rpmsg messages, board alive, HE
  stopped cleanly. Consolidated picture, all sources:
  - **~0–10 ms after a publish: board off the bus** (rungs C, D — 2/2,
    with and without the barrier exchange);
  - **~250–270 ms: stochastic** — 1 fatal-polite (rung E: RuntimeError
    + wedge), 1 pass (rung F), n=1 each;
  - **≥500 ms off-chain: 10/10 pass**;
  - **on-chain (bite B): sub-second fails 2/2, 2 s failed ONCE (after a
    VGA frame), ≥6 s passes 3/3** — the on-chain environment (VCP relay
    pumping, service traffic) fails at delays the off-chain bench
    survives, so **off-chain bounds are optimistic and the binding
    boundary must be measured on-chain** (folds into B2's matrix).
  Fix shape this supports (bridge-only): a minimum wall-clock quiet
  window after the last publish before any re-init — 6 s until the
  on-chain matrix tightens it (6 s = the only measured-safe on-chain
  point; 2 s is measured-unsafe) — plus catch-and-self-heal
  (reset + re-bootstrap) as an instrumented backstop, unproven but
  strictly no worse than today's permanent wedge.
  **CORRECTION (2026-08-17, after Nick's live demo): THE HAZARD IS
  PROBABILISTIC, NOT A THRESHOLD.** With the 20 s bridge build verified
  running, a QVGA colour→grey re-init (the gentlest transition, pixformat
  only) took the board off the USB bus during Nick's demo. Add the
  rung E/F disagreement at ~250 ms and the n=1 ladder rows, and the
  honest model is a **per-re-init failure probability that decreases
  with delay but never reaches zero**: ~100% in the fast-click zone
  (~0–1 s, 4/4 fatal-or-wedge), low-but-nonzero at ≥15–20 s (dozens of
  passes tonight, one death). Consequences: (1) the gate stays — it
  closes the deterministic zone; (2) **no bridge-side constant fully
  closes the hazard** — recovery (Pi reboot + demo_up ≈ 3 min, scripted,
  agent-runnable) is part of the bench's operating model, not an
  incident; (3) the true fix is the upstream root cause (OpenMV/Alif —
  below MicroPython; report owed) and/or SWD/JTAG on the AE3 to read
  the fault directly. Re-init sparingly; treat every mode change as a
  small gamble with a 3-minute worst case.

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
  **ROOT CAUSE FOUND 2026-08-15 by two breadcrumb probes (each step
  flushed to flash before the call it names, so the fault leaves a
  record): GROWING the framebuffer while the HE core is loaded is
  fatal. Shrinking is safe. VGA capture alongside a running HE stack is
  completely fine.**
  Probe 1 (`s18_vga_probe.py`, HE loaded → QVGA → grow to VGA): last
  breadcrumb `set_framesize(VGA) ->`, board gone. Heap was **4,067,616
  B free** against a 512,000 B VGA framebuffer and there was **no VCP
  traffic at all** — so neither exhaustion nor bridge activity.
  Probe 2 (`s18_order_probe.py`, VGA allocated FIRST, then HE loaded):
  VGA capture pre-HE 10,957 B OK · HE load OK · **VGA capture WITH HE
  up 10,935 B OK** · shrink to QVGA OK · QVGA capture 4,007 B OK ·
  **grow back to VGA → dead**.
  Mechanism (consistent with both runs, not yet confirmed against the
  allocator source): the HE ELF loads at **0x60080000, the SRAM9_B
  upper half** (bm_he MANIFEST `load_base`), and OpenMV's framebuffer
  allocator grows into that region, destroying the running core. QVGA
  (320×200×2 = 128,000 B) stays below the collision; VGA (512,000 B)
  does not. Explains why S17 never hit it — QVGA only, framebuffer
  never grew — and why S18's switching code hit it immediately.
  **WORKAROUND FOUND + PROVEN 2026-08-15 (`s18_fb_probe.py`): pin
  `sensor.set_framebuffers(1)` immediately BEFORE every
  `set_framesize()`, and allocate the session's maximum resolution
  BEFORE loading the HE ELF.** With that recipe the grow that killed
  the board twice now succeeds repeatably: VGA pre-HE 11,331 B → HE
  loaded → VGA-with-HE 11,423 B → shrink QVGA 3,965 B → **grow back to
  VGA OK** 10,968 B → second shrink/grow cycle 3,950 / 10,978 B →
  clean HE stop, board alive. Reading: OpenMV sizes the framebuffer
  COUNT to fit the pool, so an unpinned shrink silently re-allocates
  several buffers and the later grow has to expand the pool into
  SRAM9_B; pinning the count to 1 stops the pool reflowing.
  **HD PROVEN 2026-08-15 (`s18_hd_probe.py`) — the full ladder is
  switchable in-session with the HE core live**, including pixel-format
  swaps: HD-preHE 36,845 B → HE loaded → HD-with-HE 36,694 → VGA 11,233
  → QVGA 4,080 → VGA 11,277 → **HD regrown 36,489** → HD-mono 25,131 →
  HD-colour 36,544 → clean HE stop, board alive.
  **THE RECIPE (bridge must follow exactly):**
  1. `sensor.reset()`
  2. `set_pixformat(RGB565)`
  3. `set_framesize(QVGA)` — small, always safe
  4. `set_framebuffers(1)` — pins the count; **cannot be called earlier**,
     it raises "Pixel format is not supported or is not set" and then
     "Frame size is not supported or is not set" until both exist (both
     found live). QVGA-then-pin sidesteps the chicken-and-egg: an
     unpinned `set_framesize(HD)` is the over-allocation to avoid.
  5. `set_framesize(HD)` — claim the session CEILING before the HE loads
  6. load the HE ELF
  7. thereafter, per change: `set_pixformat` (if changing) →
     `set_framebuffers(1)` → `set_framesize(<= ceiling)` → settle
  **Still untested:** growing ABOVE the pre-HE ceiling. Every passing
  run allocated the maximum before loading the HE, so the ceiling rule
  stands as stated — do not assume a bridge that booted at QVGA can
  reach HD.
  **Watch item:** MicroPython heap drifted 3,893,968 → 3,755,904 B
  across seven switches (~20 KB each) in one run. Not fatal here, and
  the framebuffer itself is NOT on this heap (it stayed ~3.8 MB with HD
  allocated), but a long web-tool session doing hundreds of switches
  should be checked for a plateau.
  Underlying allocator behaviour remains a candidate upstream OpenMV
  report — it should refuse to grow into a loaded remoteproc image
  rather than corrupting it. Pairs with D15.

- **SECOND, INDEPENDENT HD LIMIT: the HE core's FreeRTOS heap cannot
  carry an HD frame through pub/sub (measured 2026-08-15, S18 bite A
  rehearsal).** With the framebuffer recipe above in place, HD capture
  on the HP core works perfectly over the live chain — bridge ledger
  `cap_frames=4 cap_bytes=54,232 cap_chunks=40`, i.e. the HD frame was
  captured at ~36 KB and chunked into 26 payloads — but the HE debug
  ring ends: `camera: cmd mode 1 -> bridge` ×4 then **`freertos: malloc
  failed`**, after publishing only 8 of those 26 chunks (`pub_ok` 6→14).
  The board stayed on the USB bus and the bridge quiet-exited cleanly,
  so this is an ordinary resource exhaustion, not the allocator fault.
  Note the S18 probes did NOT cover this: probe 4 exercised capture and
  encode on the HP core and never published a frame over BM.
  **Measured ladder as it now stands: QVGA and VGA work end to end
  (640×400 / 11,030 B delivered to the browser, gaps=0); HD captures
  but cannot be delivered.**
  Untested candidate fixes, in rough order of cost: pace/backpressure
  the WCMD_PUB chunk burst so bm_pub can drain (the bridge currently
  emits a frame's chunks back-to-back with no flow control — 3 chunks
  at QVGA and 8 at VGA drain fine, 26 does not); raise
  configTOTAL_HEAP_SIZE on the HE (RAM, not the 16 KB flash headroom);
  or accept HD stills at low q only. HD greyscale (~25 KB, ~18 chunks)
  is likely to hit the same wall and was not reached.
  **ANSWERED 2026-08-16 (S19 bite 1, measured off-chain with no Pi and
  no camera — full table in DESIGN §S19 detail):**
  - The wall is **bytes in flight, not chunk count**. 26 chunks of 350 B
    publish fine; 13 × 1400 B is the exact limit; the 14th 1400 B chunk
    dies. Reproduced at 13 on three independent rows.
  - **Free FreeRTOS heap at RUNNING = 20,712 B** (of 64 KB) and one
    1,400 B chunk costs **exactly 1,488 B** (the `bm_malloc` L2 frame
    copy in `wire_send` + heap_4 overhead). 20,712 / 1,488 = 13.9.
  - Nothing is drained *during* a burst: the netwire txq depth climbs in
    lockstep with the heap falling, and the heap fully recovers after
    every surviving burst (no leak).
  - **Mechanism:** the HE wire task both receives WCMD_PUB and drains the
    TX queue. `rr_poll()` loops until the inbound vring is empty,
    publishing inline; `wire_pump_tx()` only runs after it returns. A
    back-to-back burst keeps the poll loop fed, so the pump never runs.
  - Consequently **HP-side pacing is not the fix**: draining on the HP
    alone changed nothing, 2 ms pacing died identically (the HE spends
    ~2.5 ms/chunk), and ≥5 ms only works by starving the poll loop —
    at a cost of 130–260 ms per HD frame.
  - Independent robustness gap: `NETWIRE_TXQ_LEN` (16) × 1,488 B =
    23.8 KB **exceeds** the free heap, so at the production chunk size
    the fatal malloc beats the survivable queue-full drop. At 700 B the
    queue fills first and the node survives (lossy, counted). Bounding
    the queue by BYTES rather than frames converts a board-killing
    allocation failure into a counted drop.
  **CLOSED 2026-08-16 (S19 bite 2 — HD delivers end to end).** Fix:
  bound the poll (`rr_poll_n`, budget 4) so `wire_pump_tx` gets a turn;
  make that pump **non-blocking** (it used to retry `rr_send` 100 × 1 ms
  and park the wire task, which merely converted the heap death into a
  deadlock — measured: exactly one chunk published); drain the HE→HP
  direction on the HP while pushing a frame's chunks; bound the netwire
  TX queue by bytes as a net. Measured on the live chain:
  **`capture 50 hd color` → 1280×800, 42,574 B, valid SOI→EOI at
  `nereus001:8080/frame.jpg`, 31 chunks, `pub_ok=34 pub_errs=0
  gaps=0`.** Off-chain the probe now carries 60 × 1400 B = 84,000 B
  (2.3× an HD frame) with zero drops; heap floor 17,704 of 20,680.
  Sustained regression `stream 2.0 15 600` held 15.0 fps with zero
  gaps/drops. Detail: DESIGN §S19 bite 2.
  **Caveat on the bite-1 record:** the "HP-side draining alone changes
  nothing" row was invalid — the probe's drain popped its own list
  without yielding to MicroPython, so it recycled no vring buffer. The
  heap arithmetic and the bytes-not-count result are unaffected; the
  mechanism attribution was confounded and is resolved properly in
  bite 2 (the HE-side fix delivers 26/26 even with the HP not draining).

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
