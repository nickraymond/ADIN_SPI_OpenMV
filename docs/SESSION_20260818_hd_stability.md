# Session record 2026-08-18 — S18 HD-stability bite, nibble 1 (measurement)

> **Why this file exists:** mid-session, macOS revoked the agent's file
> access to pre-existing repo files (TCC denial — a permission dialog
> fired on the Mac with nobody there to click Allow), so DEV_LOG.md /
> TRACKER.md / SPEC.md could not be edited in place. This file carries
> the finished doc deltas verbatim. **To apply:** paste §1 at the top of
> `docs/DEV_LOG.md` (below the template header), fold §2 into the
> TRACKER bite entry, fold §3 into SPEC §Open questions, then delete
> this file and `docs/tcc_test.tmp`.

---

## §1 — DEV_LOG entry (paste at top)

## 2026-08-18 — Sprint S18 — HD-stability nibble 1: five probes kill every named suspect and corner the real one — transitions degrade only when the HE is resident

**Branch:** `sprint/18-hd-stability` from `main` @ `0081b65`. All
board work off-chain per `ae3-board-access` (neutral `main.py`,
serialized single ops); chain restored + verified end-to-end at
session end. Two board crashes, two Pi reboots — both budgeted.

**Done — the evidence chain (probes G→G5, `bench/probes/s18_hd_gate_probe*.py`):**
- **Trace re-read first (no board contact):** both matrix HD deaths
  (ref run 5, sensor discriminator) died with NO trace line after the
  gate would have opened — `_apply()` traces only after all steps, so
  both died mid-re-init. The discriminator's "survived and replied
  res=hd ok" was measured ~15–20 s BEFORE the gated re-init ran (the HE
  acks at command time) — findings 2 and 3 were one fault wearing two
  hats.
- **Probe G** (the on-chain death sequence, off-chain): row 0
  calibration QVGA→VGA PASS; **row 1 QVGA→HD mono at 20 s quiet WITH
  NO BARRIER: MemoryError at first HD capture, recovery R1
  `Sensor control failed.`, recovery R2's `set_framebuffers` took the
  board off the bus.** Reproduced in 4 minutes; barrier exonerated.
- **Probe G2** (shape isolation, no publishes): grow-at-color PASS,
  flip-at-HD PASS, **grow-at-GRAYSCALE PASS** (killed that hypothesis),
  VGA-grow-at-mono PASS — then **row E died at a routine QVGA shrink's
  `set_framebuffers`, board off the bus, zero publishes ever sent.**
  Publish-proximity exonerated as CAUSE (it accelerates onset only).
- **Probe G3** (soak, HE loaded, zero traffic): 21 transitions clean,
  **#22 (a QVGA mono flip!) failed politely at `set_framebuffers`
  (289 µs — pool already gone), recovery bootstrap ALSO threw.**
  In-session heal is now 0-for-6 lifetime.
- **Probe G4** (CONTROL — identical soak, NO HE): **40/40 clean.**
  Lifetime no-HE tally incl. B2 rung A: 52/52.
- **Probe G5** (interim path): fresh boot → HE → ONE transition to HD
  mono → 3 captures with real 27-msg chunk-burst publishes: **PASS,
  2/2 boots, byte-identical (12,328 B ×3).**

**The finding:** sensor mode transitions degrade some firmware-level
resource ONLY while the HE core is resident, tripping after N
transitions (on-chain with traffic: N≈2 at HD; off-chain quiet: N≈10
and 22) — politely (`MemoryError`/`Sensor control failed`, then the
B2 wedge) or fatally (board off the bus at `set_framebuffers`, the
D15 class). Publish/barrier/HD/mono/grow are each individually
exonerated; traffic accelerates onset. The MP heap shows NOTHING
(identical ~48 KB/cycle probe-side drift in G3 and G4) — the
degradation lives below MicroPython.

**Source corroboration (openmv @ installed dev `7d4dbf7`, verified
byte-identical to master in these files):** `framebuffer_resize`
(lib/imlib/framebuffer.c:158) **frees and re-mallocs the whole
framebuffer block on every transition** (`uma_free` →
`uma_malign(UMA_MAYBE)`) — the main fb is hard-coded dynamic
(framebuffer_init0). The AE3's default UMA pool is **SRAM1, 2512K**
(board_config.h:107) — an HD RGB565 fb (~2 MB) nearly fills it, so the
"eager ceiling claim" was never a reservation. Also on record:
**OMV_GPU_MEMORY = SRAM9_B** (board_config.h:101) — the region whose
upper half our HE ELF occupies at 0x60080000. Bite A's "fb allocator
grows into the HE region" mechanism story was wrong (fb lives in
SRAM1); its pin-the-count mitigation worked by reducing churn.

**Also answered from source (Nick's question):** the sensor is a
**PixArt PAG7936** (ID 0x7936, omv_csi.h:97), native 1280×800, and its
driver supports EXACTLY three framesizes — QVGA 320×200 / VGA 640×400 /
HD 1280×800, redefined 16:10 in pag7936.c:933–940 — everything else is
an explicit `return -1` (pag7936.c:691) unless `OMV_CSI_HW_SCALE_ENABLE`
(defined only for OPENMV_N6). **There is nothing between VGA and HD on
stock AE3 firmware; the S0 sweep's untested keys would all have
failed identically.**

**Broke / found en route:**
- **The shipped HD-ref guard is order-broken:** `command()` runs
  `_ensure_sensor` (the deadly re-init) BEFORE the ref/HD refusal
  (bm_bridge.py:1027 vs :1039) — it blocks only the ref-image load.
  With today's finding the guard is probably unnecessary once the fix
  lands (run 5's death was the transition, not the ref load — no ref
  line ever traced).
- **bench-web holds a dead control socket across a bm-telemetry
  restart** (OSError 107 per request, page answers HTTP 000 until the
  unit is bounced) — filed as a spawn-task chip; Pi-side only.
- **The agent's Mac file access died mid-session** (TCC dialog denied
  unattended) — this file is the workaround; local commits blocked at
  session end.

**Bench state:** chain UP under units and verified end-to-end after
demo_up (capture delivered: `frames_ok=1 gaps=0`, `/frame.jpg` 200,
SOF0 320×200; bench-web `/api/status` 200 after its restart). Board
runs launcher `170e637c…`, bridge `84f34aba…`, `scene: sensor`. New
probes staged on nereus000:`~/bm_bench/`; probe logs also on
`/flash/hd_gate_probe*.txt`. Traces preserved
`~/bridge_traces/20260818T042042_*`.

**Next (Nick's gate — the fix menu, nibble 2):**
- **(A) RECOMMENDED root fix: static framebuffer firmware patch** —
  reserve ~2.1 MB in SRAM1/UMA BLOCK0 for the main fb,
  `dynamic=false`, so transitions stop re-allocating entirely. Mac
  docker build (D23) + S7 headless flash, acceptance = G3 soak 40/40
  with HE loaded. Upstream PR candidate (repro = G3 vs G4).
- **(B) Interim, bridge-only, measured 2/2 (G5): boot-per-transition**
  — a command needing a re-init persists the target mode and
  machine.reset()s; the launcher bootstraps straight into it (~20–40 s
  leg outage per mode change). Unblocks the 3 HD matrix rows + B2
  cert rung on CURRENT firmware.
- **(C) Ops accelerator:** test Nick's TP-Link UH720 between Pi and
  AE3 (uhubctl data-line-only caveat, UH700 issue #237); known-good
  buy = Rosonway RSH-A37S. Cuts crash recovery from ~2 min to ~10 s.
- Fold the guard-order fix into whichever bridge bite runs first.
- PublishGate STAYS (quiet windows measurably reduce on-chain onset);
  its 20 s constant's HD caveat should be reworded: HD's instability
  was never about the constant.

---

## §2 — TRACKER delta

Under the S18 bite list (after bite B3), add/replace the HD-stability
candidate bite with:

- [~] **Bite B4 — HD stability (branch `sprint/18-hd-stability`).
      NIBBLE 1 DONE 2026-08-18: measured off-chain in five probes.**
      Matrix findings 2+3 are ONE fault: sensor transitions degrade a
      below-MicroPython resource ONLY while the HE is resident
      (no-HE control 52/52; HE-loaded fails at transition ~2 on-chain
      under traffic, ~10/~22 quiet off-chain; polite wedge or board
      off bus at `set_framebuffers`; in-session heal 0/6). Barrier,
      publish, HD, mono, grow each exonerated (G/G2/G3/G4);
      publish/traffic only accelerates onset. Source-corroborated:
      `framebuffer_resize` = free+malloc per transition into the
      2512K SRAM1 UMA pool (fb hard-coded dynamic); GPU heap
      configured over the HE's SRAM9_B region. **G5: fresh boot +
      single transition + publishes = PASS 2/2 → boot-per-row interim
      is viable on current firmware.** PAG7936 ladder is exactly
      QVGA/VGA/HD (source-cited; nothing between VGA and HD exists).
      HD-ref guard found order-broken (re-init runs before refusal).
      **Nibble 2 awaits Nick: fix menu = (A) static-fb firmware patch
      [recommended root fix] · (B) boot-per-transition bridge interim
      [unblocks HD rows + cert rung now] · upstream report.**
      Session record: `docs/SESSION_20260818_hd_stability.md`.

Header line update: S18 HD-stability bite nibble 1 done (see bite B4);
chain healthy under units; bench-web restart-after-telemetry-restart
gotcha filed.

## §3 — SPEC §Open questions delta

Replace the two 2026-08-18 HD findings ("HD in ref-scene mode
hard-faults…" and the HD half of the reef-matrix notes) with:

- **Sensor mode transitions degrade the board while the HE core is
  resident — measured 2026-08-18 (S18 HD-stability nibble 1), and it
  subsumes both 2026-08-18 HD findings.** With no HE: 52/52 clean
  transitions lifetime. With HE resident: failure after N transitions
  (N≈2 on-chain under traffic — why HD rows died "first"; N=10, 22
  measured quiet off-chain), independent of shape (a QVGA mono flip
  failed), of publishes (a zero-traffic session died), and of the
  barrier. Presentation: polite (`MemoryError` / `Sensor control
  failed.` + the standing wedge; in-session recovery 0/6) or fatal
  (board off USB at `set_framebuffers`, D15 class). Mechanism
  corroborated in openmv source (installed `7d4dbf7` ==master in these
  files): every transition frees + re-mallocs the whole framebuffer
  from the 2512K SRAM1 UMA pool (framebuffer.c:158; fb hard-coded
  dynamic); OMV_GPU_MEMORY=SRAM9_B overlaps the HE ELF at 0x60080000
  (collision on record, not yet proven to be the byte-level killer).
  Exact byte-level mechanism below MicroPython: OPEN (upstream report
  candidate with probes G3/G4 as repro). **Measured safe harbor: a
  fresh boot's FIRST transition + captures + publishes (G5, 2/2).**
  Ref-mode HD was never a separate fault: run 5 died in the
  transition before any ref bytes loaded (and the shipped guard
  refuses only after `_ensure_sensor` — order bug).

- **The AE3 resolution ladder is closed by source, not sweep coverage
  (2026-08-18):** PAG7936 (ID 0x7936), native 1280×800; driver
  supports exactly QVGA 320×200 / VGA 640×400 / HD 1280×800 (16:10
  redefined in pag7936.c:933–940); every other framesize key is
  `return -1` (pag7936.c:691) without `OMV_CSI_HW_SCALE_ENABLE`,
  which only OPENMV_N6 defines. No mode exists between VGA and HD on
  stock AE3 firmware.
