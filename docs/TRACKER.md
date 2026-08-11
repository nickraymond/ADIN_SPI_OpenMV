# TRACKER.md — Sprint Ladder & Rules

*The agent entry point. Newest state lives here.*
*Last updated: 2026-08-11 (S8 bite 1 [NPU bench] done via early-ride
exception; S7 decision entry + BM arc next) · Owner/gate: **Nick***

---

## Rules for Agents (READ FIRST, EVERY SESSION)

1. **Read this whole document cover-to-cover first, every session.** Then skim
   `docs/SPEC.md` and `docs/DESIGN.md`. Read the top ~3 entries of
   `docs/DEV_LOG.md`.
2. **Take small code bites.** One TODO item at a time, target ~300 LoC. Before
   every bite, check that SPEC.md is detailed enough to inform it; if not, stop
   and ask Nick — don't invent requirements.
3. **Four nibbles per bite:**
   1. **Plan** — figure it out; throwaway code allowed; change no files.
      *Explicit gate: Nick approves before nibble 2.*
   2. **Code + unit tests** — flag Nick if the plan needs substantial change.
   3. **Manual tests** — Nick runs these. Provide copy-pastable CLI.
   4. **Open PR.**
4. **Feature branch for all new work** — `sprint/<n>-<slug>`. Never commit to
   `main` directly.
5. **Every sprint ends with a live demo Nick can run.** Exact commands go in
   the sprint's Demo section below and in the PR description.
6. **End of every session:** add a DEV_LOG.md entry (newest on top). Update
   DESIGN.md whenever architecture or a decision changes.
7. **Facts carry sources; unknowns get flagged, not guessed.** This project has
   burned people with assumed pinouts and strap polarities.
8. **Hardware safety:** rules in SPEC.md §Safety are absolute — no powered
   BM/Spotter bus, 3.3 V only on AE3 pins.

### Project layout

```
docs/          SPEC.md TRACKER.md DESIGN.md DEV_LOG.md diagrams/
firmware/      AE3 MicroPython (later: C driver work)
pi/            overlays, systemd services, shim daemon, stream server
bench/         benchmark + test scripts (S0 SPI bench, frame counters)
```

---

## Sprint ladder

State key: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

### S0 — AE3 SPI ceiling benchmark  `[x]`  *(demo run by Nick 2026-08-09 — PASS)*
**Goal:** measure what `machine.SPI(0)` actually delivers; go/no-go for the
MicroPython-level driver.
- [x] Loopback P0→P1; sustained throughput at 5/10/20/25 MHz, chunk sizes 64 B–4 KB
      → **4.89 Mbps max effective, 0 errors — below the 12 Mbps gate**
- [x] GPIO edge → handler latency on P5 (IRQ path) → soft 6 µs / hard 5 µs median
- [x] Record results in DESIGN.md; decision note if effective rate < 12 Mbps
      → recorded; decision RESOLVED (Nick): spike confirmed polled per-byte port
      driver (software ceiling, not silicon) → proceed at **~4 Mbps AE3 video
      budget** through S6; C-level FIFO/DMA driver priced + deferred (D8)
- [x] Video encode table (added by Nick 2026-08-09): run `bench/ae3_video_bench.py`
      on the AE3 → measured bytes/frame · bpp · encode ms · max fps per
      resolution × quality × color/mono; table recorded in DESIGN.md §Bench
      results. Verifiable: does the measured table show a usable video mode
      (target resolution/fps) fitting under the 4.89 Mbps SPI ceiling?
      → **ANSWER: yes, all of them — the JPEG encoder caps produced video
      < ~2 Mbps in every supported mode (even scaled to 0.875 deployment bpp),
      so the SPI ceiling has ≥ 2× headroom and is NOT the binding constraint.**
      Best modes: VGA color ~13 fps, HD mono ~8.6 fps, HD color ~3.3 fps.
      Caveat: bench-scene bpp (0.10–0.24) is 4–5× better than the deployment
      anchor; re-measure on the real scene in S3.
- [x] Synthetic reef-scene bpp (proposed by Nick 2026-08-09): load a stored
      "coral reef" reference image on the AE3 (not the camera feed — bench is
      a dark room, unrepresentatively compressible) and re-run the encode
      table against it → representative bytes/frame + bpp per mode recorded
      next to the dark-room table in DESIGN.md.
      → DONE with P7071008: reef bpp brackets the 0.875 anchor; color modes
      encoder-bound, mono modes SPI-bound; delivered stream 1.7–2.9 Mbps at
      q50 → **VGA color ~8 fps / VGA mono ~14 fps / HD mono ~4 fps** on the
      MicroPython path. Pipeline: `bench/make_ref_scene.py` +
      `bench/ae3_ref_scene_bench.py`.
- [ ] Multi-image trend sweep (Nick): run the ref-scene pipeline over the
      other `images/` files (both Setup scenes + P707xxxx series) → bpp
      spread per mode recorded in DESIGN.md; flags if any scene busts the
      working-mode fps estimates. NOTE: images/ not yet committed to git —
      Nick to decide (LFS / untracked + regenerate).
**Demo (Nick):** run `bench/ae3_spi_bench.py` in OpenMV IDE → printed table of
MHz / chunk / effective Mbps / IRQ µs. **Pass (revised 2026-08-09, Nick):
SPI effective ≥ 2× T1 stream bitrate = ≥ 3.5 Mbps (QVGA q35 @ 30 fps).**
Measured 4.89 Mbps → passes; the script's printed "≥12 Mbps FAIL" verdict
line is against the RETIRED gate — table values are what count.
**Needs:** AE3, one jumper wire. No ADIN hardware.

### S1 — Pi 5 + SG shield: Linux driver up  `[x]`  *(demo run by Nick 2026-08-09 — PASS)*
**Goal:** known-good ADIN node on the Pi 5.
- [x] Build `adin1110` kernel module → done as **out-of-tree module build**
      (vendored mainline `adin1110.c` + `adin1100.c`, stock kernel untouched —
      DESIGN.md D12), not SG's full kernel rebuild. `pi/drivers/adin1110/` +
      `pi/build_adin1110.sh`.
- [x] Install device-tree overlay → written from SG's published facts + the
      kernel binding (`pi/overlays/sg-adin1110.dts`): SPI0 CE0, 23 MHz, IRQ
      GPIO22 level-low, reset GPIO17 active-low, NO `adi,spi-crc`.
- [x] Driver probes; interface up → **`eth1` on nereus000**, driver `ADIN1110`,
      internal PHY ID reads **0x0283BC91** (matches SPEC), bound to `ADIN1100`
      PHY driver. `pi/verify_adin1110.sh` = 5/5 PASS.
**Demo (Nick):** `dmesg | grep adin` shows probe · `ip link` shows the new
interface · `ethtool -i <if>` reports driver `adin1110`.
**Needs:** Pi 5, SG shield on header. eth0 stays free for SSH/debug.

### S2 — AOS hats: node-to-node Linux link  `[x]`  *(demo run by Claude 2026-08-10, blessed by Nick — PASS: TCP 9.32/9.33 Mbps, 0% loss)*
**Goal:** two Pis linked over T1L using the AOS boards; AOS hardware validated.
- [x] Buzz out AOS hat: CS/IRQ/RESET GPIOs, strap state, pair-connector
      polarity (or obtain schematic from AOS) → record in DESIGN.md
      → DONE via Nick's KiCad layout + schematic + photos + live
      validation (DESIGN.md §S2 detail): pinout = SG shield (CE0/22/17);
      straps default OA, hat #1 re-strapped generic-SPI-no-CRC (D13,
      proven by working register I/O); INT_N pull-up missing on board →
      overlay workaround (D14). Hat #1 probed on nereus000: PHY ID
      0x0283bc91, verify 5/5; hat #2 validated identically same day. Remaining: hat #2
      date code; J1 wire + copper-pad
      questions (checklist §C).
- [x] Overlay variant for AOS pinout; hats on Pi 5 + second node
      → `pi/overlays/aos-adin1110.dts` on BOTH nodes; second node is a
      second **Pi 5** (nereus001), not the Pi 3/4 — Nick's call 2026-08-10
      (identical kernel/recipe; SPEC inventory table not yet amended).
      nereus000 = hat #2, .7.1, MAC ...:02 · nereus001 = hat #1, .7.2,
      MAC ...:03 (NM clone). Both verify 5/5 from cold boot. Tooling:
      `pi/setup_t1l_ip.sh`, `bench/t1l_link_test.sh`, build script takes
      sg|aos arg. nereus001 on tailnet via vendored pi-tailscale-setup
      skill. Live rehearsal of pi-kernel-upgrade: first-boot unattended
      upgrade bumped nereus001 to 6.18.39 and orphaned the freshly built
      modules; rebuild fixed it (nereus000 still on 6.18.34 — will hit
      the same on next apt upgrade).
- [x] Wire pair, static IPs (192.168.7.1/2), link up both ends
      → DONE 2026-08-10: link test 4/4 — TCP 9.32/9.33 Mbps fwd/rev (line
      rate), UDP 8M 0% loss, ping 0% loss RTT 0.84 ms (DESIGN.md §S2)
- [ ] ~~Capture golden logic-analyzer traces: init, link-up, TX/RX (S4's
      reference)~~ — DESCOPED by Nick 2026-08-10: no logic analyzer on the
      bench. CONSEQUENCE for S4: on a PHY-ID mismatch there is no golden
      trace to diff against — fallback is register readback + the working
      Linux node as a live reference. Also leaves the SPEC "true SCLK at
      20/25 MHz" open question unresolvable for now. Revisit if an LA
      turns up.
**Demo (Nick):** `ping 192.168.7.2` · `iperf3 -c 192.168.7.2` shows ~9 Mbps.
**Needs:** both AOS hats, both Pis, crimped pair, logic analyzer.

### S3 — Video across T1L, Pi to Pi  `[x]`  *(demo run by Nick 2026-08-10 — PASS: live browser video across the pair, QVGA q90 @ 30 fps, 0 loss)*
**Goal:** full streaming pipeline working before any AE3 driver exists.
- [x] AE3 → Pi 5 over USB (existing setup), constrained to ≤ 8 Mbps
      (settings per SPEC budget; record actual choice)
      → DONE 2026-08-10 (manual test passed by Nick): vendored legacy
      capture service (`firmware/ae3_usb/`, D15) + host frame source
      (`pi/stream/usb_frame_source.py`) + `bench/usb_stream_bench.py`.
      **Chosen setting (Nick): QVGA color q90, sender-paced 15 fps** —
      free-runs 35.7 fps / 2.5 Mbps, all modes 0 loss (D16). Found + worked
      around an AE3 firmware crash: 2nd stream session per boot hard-faults
      the board → hosts reboot it between sessions (README §Known firmware
      crash; not fixed by OpenMV dev build; candidate upstream report).
      Hard fact: VGA ≥ 15 fps impossible on AE3 (software encoder).
- [x] Sender service on Pi 5 → frames over T1L → receiver on Pi 3/4 serves
      multipart-MJPEG HTTP (no transcode)
      → DONE 2026-08-10 (receiver = nereus001, the second Pi 5):
      `pi/stream/t1l_sender.py` (self-healing leg: board reboot → USB
      session → pace → relay, re-sequenced) + `pi/stream/stream_server.py`
      (ingest :8081 speaking the project framing — **the frozen S6
      interface** — HTTP :8080 `/stream` `/frame.jpg` `/stats.json`).
      Both run as systemd services (`pi/services/`,
      `pi/install_stream_service.sh`). Standing setting revised to
      **QVGA q90 @ 30 fps** (D17; q80 = margin fallback).
- [x] Measure sustained Mbps + dropped frames at target settings
      → DONE 2026-08-10: 10-min sustained run under systemd at D17
      settings — **18,032 frames / 615 s = 29.3 fps avg, 4.60 Mbps,
      0 gaps, 0 resets (zero frame loss)**. DESIGN.md §S3 detail.
**Demo (Nick):** open `http://nereus001-1:8080/stream` in a browser → live
video that crossed the pair (page with stats at `/`).
**Needs:** S2 done. This receive side is FROZEN after S3 — S6 must plug into it
unchanged.

### S4 — AE3 first light: PHY ID over SPI  `[x]`  *(demo run by Nick 2026-08-10 — PASS: `PHY ID: 0x0283BC91 — OK` at 5 MHz)*
**Goal:** AE3 (generic SPI mode) proves wiring + HAL.
**Rig revised 2026-08-10 (Nick, D18): AE3 drives an AOS hat, not the SG
shield** — known-good silicon + straps (S2-validated on both hats), pair
connector already crimped, 3.3V-only board. Header pinout = SG shield
(DESIGN §S2 table), so Diagram 1's harness applies at the same header
positions; hat #2 comes off nereus000 (pauses the S3 stream fixture —
restore = remount hat + `systemctl start t1l-sender`), nereus001 + hat #1
stays intact as the live Linux reference node on the pair.
- [x] Harness AE3 → hat header → DONE 2026-08-10, revised power scheme
      (D19, Nick): hat powered from nereus000's 3V3 header (Pi pin 1 →
      hat 1, Pi pin 9 → hat 9) — combination already proven in S2/S3;
      AE3 stays USB-powered from the same Pi; 7 data wires AE3→hat
      (P0→19 P1→21 P2→23 P3→24 P4→11 P5→15 GND→6), AE3 3V3 unused.
      The "can AE3 3V3 source the hat" open question is SIDESTEPPED
      (still unmeasured — re-flag if a standalone rig ever needs it).
      Hard-won lesson: off-Pi header counting got mirrored twice;
      validator that ended it = meter hat pin 17 ↔ pin 6 with only the
      power jumpers on (~3.3 V only if orientation is right).
- [x] AE3 P5 (IRQ in) configured with internal pull-up — in
      `firmware/adin_drv/adin_hal_ae3.py` (D14/D18)
- [x] Minimal generic-SPI register read in MicroPython →
      `firmware/adin_drv/` two-layer driver start (portable core + AE3
      HAL), framing from vendored adin1110.c; 16 host unit tests
- [x] Read PHY ID → **0x0283BC91 — OK at 5 MHz**, repeatable, first
      attempt on a correctly wired harness. Fallback ladder shipped as
      code and battle-tested during the miswire hunt:
      `s4_bus_probe.py` (DC rail/CS/MISO checks, no SPI) and
      `s4_bitbang_probe.py` (GPIO-only PHY ID read) — keep for S5+.
**Demo (Nick):** REPL prints `PHY ID: 0x0283BC91 — OK`.
**Needs:** S0 pass, hat #2 freed from nereus000, 8-jumper harness. SG
shield stays shelved as backup (S1 knowledge retained).

### S5 — AE3 raw-frame TX + loss measurement  `[x]`  *(demo run by Nick 2026-08-10 — PASS: 31,592/31,592 frames, 0% loss, 526 fps / 4.21 Mbps @ 20 MHz)*
**Goal:** AE3 transmits real Ethernet frames; link quality quantified.
- [x] Frame TX path in the driver (generic SPI FIFO), seq-numbered payloads
      → `adin_spi.py` grows TX burst + clause-22 MDIO/MMD-indirect + PHY
      power-up + link mgmt, all framing cited from the vendored
      adin1110.c/adin1100.c. Bite-1 proof: 200/200 500 B frames into a
      tcpdump pcap on nereus001, in order, zero loss, at 5 MHz.
- [x] RX path (at minimum: link status + counters) → `link_up()`/
      `wait_link()` (PMA STAT1, latched-low), software TX counters
      (mainline driver's own pattern — it reads no hw count regs),
      `status_summary()` incl. SPI_ERR flag. AE3 frame *reception*
      deliberately deferred: the video path is one-way; revisit only if
      the S6 shim needs it.
- [x] Pi counter script (raw socket) → `bench/frame_counter.py`:
      received/lost/fps/Mbps, window-relative loss accounting, explicit
      PASS/FAIL verdict + exit code.
**Demo (Nick):** `python3 bench/frame_counter.py` on Pi shows rate + 0% loss at
target load for 60 s. → **PASS 2026-08-10: 31,592/31,592 frames, 0% loss,
0 dupes, 0 out-of-order, 526 fps / 4.21 Mbps sustained; sender side 0 FIFO
stalls, SPI_ERR clear, 20 MHz SPI.** Delivered payload ≥ the ~4 Mbps D8
video budget → the MicroPython driver is not the S6 blocker.
**Needs:** S4. Pi end = S1 node or S2 node. → used nereus001 + hat #1
(live reference node), untouched.

### S6 — Video from AE3 over T1L into the existing stream  `[x]`  *(demo run by Nick 2026-08-11 — PASS: live browser video over the pair at q50/32 fps, unplug→freeze, replug→resume; USB carried REPL only)*
**Goal:** replace USB with the pair; the S3 web page doesn't know anything changed.
- [x] AE3: capture → MJPEG → chunk into frames w/ tiny header + seq —
      ~~MUST pipeline capture/encode/tx (≥2 framebuffers; SPEC §T1)~~
      → DONE 2026-08-10: BMV6 chunk protocol (`firmware/adin_drv/s6_video.py`)
      + TX loop (`s6_video_tx.py`, duration/quality runtime knobs).
      Pipelining requirement found MOOT by measurement (D21): capture is
      already DMA-hidden (3.1 ms), and encode/tx cannot overlap in
      MicroPython (polled SPI = CPU-bound, one core, D8). Lever = bytes/frame.
      Bite-1 proof: 60 s @ 20 MHz → 2422/2423 frames reassembled on
      nereus001, 0 lost, 0 bad JPEGs (counter `bench/s6_video_counter.py`).
- [x] Pi shim daemon: raw frames → reassemble → feed the S3 stream server
      → DONE 2026-08-10: `pi/stream/chunk_shim.py` + `t1l-chunk-shim.service`
      (CAP_NET_RAW, no root) on nereus001; frozen ingest untouched. Live:
      2622/2622 frames sender→server exact match, 0 gaps; browser stream up.
      Dark-scene q ladder run (all rungs ≥31 fps, 0 loss — NOT gate numbers).
- [x] Sustained run; measure fps/loss/latency vs **T1 target: QVGA color
      q35–50 @ 24–30 fps** (raise resolution only if fps holds)
      → GATE PASSED 2026-08-11, lit scene, 60 s counter windows, all
      0 lost / 0 bad JPEGs: **q50 = 32.2 fps** (4.5–5.0 KB/f, 8 fps
      margin) · q60 = 25.9 · q70 = 24.2 (zero margin). Standing setting
      = **q50** (D20 finalized). Latency: glass-to-glass unmeasured
      (flagged); sender-side pipeline is ~31 ms/frame at q50. Link-outage
      behavior verified via remote eth1 bounce: stream freezes +
      auto-resumes; AE3 TX drains stall-free into a dead wire (D21 note).
      Also: one REPL-wedge after a hard reset (D15 crash class), cleared
      by the documented uhubctl recovery ladder — no physical replug.
**Demo (Nick):** same browser URL as S3 shows live video; USB data pipe unused
(REPL only). Side-by-side: unplug pair → stream stops; replug → resumes.
**Pass: ≥ 24 fps sustained at QVGA color for 60 s.**
**Needs:** S3 + S5.

### S7 — Decision gate: OPEN Alliance / bm_core alignment  `[ ]`
**Goal:** a decision, not a build.
- [x] **First spike (Nick 2026-08-10): headless AE3 firmware flashing from
      nereus000** — no OpenMV IDE, no hands. Verifiable: build (or reuse) a
      known `firmware.bin`, flash it entirely from the nereus000 CLI, board
      re-enumerates running that exact build (uname git-hash matches).
      Unblocks a fully remote firmware dev loop (edit → docker build on Pi →
      flash → test → uhubctl recover) for ALL option-C work: SPI FIFO/DMA
      rewrite (D8), bm_core port, upstream crash-fix testing. Investigate:
      Alif SE/bootloader protocol over USB CDC vs SWD; OpenMV's IDE-less
      loader tooling.
      → IN PROGRESS 2026-08-11 (research + tooling done, ZERO board contact;
      branch `sprint/7-headless-flash`): answer = OpenMV's own DFU bootloader
      (37C5:96E3, runs every boot, `machine.bootloader()` entry, dfu-util
      alts HP/HE, BOOT never written → power-cycle recoverable) — not SWD,
      not SE-UART (recovery-only; needs hands or B2B mod, declined). D22.
      Build host revised by Nick: **docker on the Mac**, not the Pi — the
      OpenMV SDK has no linux-aarch64 build (D23). Shipped, untested on
      hardware: `firmware/openmv_build/` (Mac setup + build → MANIFEST) and
      `pi/ae3_flash/` (flash ladder + verify + `--recover`; 16 host tests
      pass). GATED: first live flash only after the S6 demo passes (Nick),
      then the round-trip demo in `pi/ae3_flash/README.md`.
      → **DONE 2026-08-11 (Nick's go after S6 demo pass): round-trip flash
      demo PASSED from the nereus000 CLI, no hands, no IDE** — board went
      dev `7d4dbf7ab2` → `v5.0.0` (verified) → back to `7d4dbf7ab2`
      (verified), sys.version id match both legs, full ladder green incl.
      the script's own PASS verdict; fixture firmware restored to exactly
      what S6 ran on. Flash-day facts folded into the tooling: verify via
      `sys.version` not `os.uname()` (uname carries only the MicroPython
      id); release builds embed tags not sha10s; v5.0.0 ships a combined
      all-boards zip; dfu-util `-R` exits non-zero on success (device
      drops off the bus mid-reset) — script tolerates it, rungs 4+5 are
      the success signals. ROMFS was NOT reflashed (both builds ran fine
      on the installed images) — check on bigger version jumps. uhubctl
      `--recover` path installed but untested (not needed).
- [~] Assess Sofar's OA-mode Linux/BM driver status (ask them directly)
      → RESEARCHED from source 2026-08-11 (bm_core + bm_sbc cloned & read):
      **bm_core** = portable C17 BM stack (BCMP, pub/sub middleware, raw-UDP
      bm_ip API, MTU 1500, FreeRTOS + POSIX bm_os backends, lwIP + Linux IP
      backends, NetworkDevice trait) with an **ADIN2111-only OA driver**
      (adi_spi_oa.c, ~8.9k LoC, no 1110 conditionals). **bm_sbc** = BM on
      Linux (POSIX), stock transport = UART gateway to a mote, **max 230400
      baud ≈ 0.18 Mbps — control-plane only, ~25× short of our video**. BUT
      branch `feature/adin_linux_implementation` (WIP commits, active) adds
      a **raw_eth AF_PACKET transport bound to a named interface** — i.e.
      full-rate Linux BM attachment over any netdev, incl. our proven eth1
      kernel driver, no Pi re-strap. Nick contacting Sofar CTO for
      status/early access; forum questions drafted.
- [x] Estimate: port oa-tc6-lib to AE3 vs stay generic; what re-straps
      → ANSWERED by the research: route is **bm_core's own OA driver**
      (native NetworkDevice integration), NOT oa-tc6-lib; test-first on our
      ADIN1110 (OA-TC6 is an OPEN Alliance standard; chip-specifics are
      mostly switch/port regs) with a hard fallback = buy ADIN2111 bench
      hw rather than port (Nick moves to 2111 for production anyway — a
      1110 port would be throwaway). Re-strap: **AE3-side hat only**; Pi
      hat stays generic SPI (strap mode is board-local, D1; bm_sbc raw_eth
      rides the mainline kernel driver).
- [ ] ~~Optional spike: re-strap one AOS hat to OA, PHY ID read in OA
      framing~~ — folded into S9 bite 1 (same spike, done in C via
      bm_core's driver, which is what actually needs proving)
- [ ] Write DESIGN.md decision entry with recommendation — after Sofar
      responds on raw_eth/1110; ladder below is the working plan (Nick
      approved shape 2026-08-11)
**Demo (Nick):** written recommendation reviewed together; tracker updated with
the follow-on project's first sprint.

---

## BM-native arc (added 2026-08-11, Nick-approved shape) — S9–S13

*Goal of the arc: the S6 demo, but natively Bristlemouth — AE3 speaks OA
SPI to the ADIN in C, runs bm_core, talks to a Pi running bm_sbc; video
rides BM's IPv6/UDP. Power (PoDL) explicitly deferred (Nick). The S6
MicroPython path stays intact as the regression baseline. Facts base:
S7 research notes above + DESIGN §S7 detail.*

### S9 — OA first light in C (custom firmware + driver spike)  `[ ]`
**Goal:** prove the C dev loop end-to-end and OA mode on our silicon.
- [ ] Bite 1 — **1110-vs-2111 verify spike**: re-strap hat #2 to OA
      (default straps; D13 jumpers reversible), minimal C module in a
      custom OpenMV firmware calling bm_core's adin2111 driver
      **unmodified** for an OA register/PHY-ID read. Mac docker build
      (S7 env) → S7 headless flash → REPL/log verdict.
      **Decision point on fail: buy ADIN2111 bench hardware; do NOT
      port the driver to 1110 (throwaway — production goes 2111).**
- [ ] ADI-HAL implementation for Alif (SPI + IRQ on P0–P5; DMA hooks
      exist in silicon — SPI_DMACR + DMA0/DMALOCAL engines, vendor
      headers in openmv tree — wire up if bite budget allows, else S10)
- [ ] OA data-path smoke: one frame TX via OA chunks → tcpdump on
      nereus001 (Pi side untouched, generic SPI + kernel driver)
**Demo (Nick):** custom-firmware AE3 prints `PHY ID — OK (OA mode)`;
a seq-numbered frame lands in tcpdump across the pair.
**Needs:** S7 flash loop, Mac build env, hat #2 re-strap.

### S10 — bm_core boots on the AE3 (HE core)  `[ ]`
**Goal:** BM stack alive on the camera board; camera side untouched.
- [ ] Spike first, one bite: FreeRTOS on M55_HE + OpenAMP HP↔HE pipe —
      measure pipe throughput (**gate: ≥5 Mbps**) and confirm HE can own
      SPI0 + its IRQ (pinmux/EWIC). Fallback if HE loses: bm_core on HP
      alongside MicroPython (invasive — price it before choosing).
- [ ] bm_os(FreeRTOS) + lwIP + NetworkDevice glue on HE; BCMP up
      (heartbeat, neighbors, ping)
- [ ] Validate against reference hardware: dev-kit mote (on hand) sees
      the AE3 as a BM neighbor
**Demo (Nick):** BCMP ping to the AE3 answered (from mote or Pi);
heartbeats visible in tcpdump.
**Needs:** S9.

### S11 — Pi becomes a BM node (bm_sbc)  `[ ]`
**Goal:** nereus001 running bm_sbc, attached at full rate.
- [ ] bm_sbc mainline on the Pi + stock UART-gateway cross-check vs the
      dev-kit mote (reference bite — needs only the dev kit, not S10)
- [ ] raw_eth transport on eth1 (Sofar's
      `feature/adin_linux_implementation` branch / CTO early access;
      finish it ourselves only if theirs stalls) — kernel driver and
      straps unchanged
- [ ] Two-node BM network: AE3 ↔ Pi neighbors + ping + topology
**Demo (Nick):** bm_sbc lists the AE3 node id as a neighbor; BCMP ping
both ways over the pair.
**Needs:** S10 (final bite); dev kit only (first bite).

### S12 — video over Bristlemouth  `[ ]`  ← THE ARC'S POINT
**Goal:** S6's demo verbatim, but the transport is native BM.
- [ ] AE3: HP capture/encode (MicroPython, as in S6) → OpenAMP → HE
      bm_udp chunked TX (≤ ~1400 B chunks, seq header — S6 framing
      adapted to UDP)
- [ ] Pi shim v2: consume via bm_sbc IPC (python client) → **frozen S3
      stream server** (ingest :8081 unchanged)
- [ ] Sustained run: gate ≥15 fps first, then push toward T1
      (QVGA 24–30 fps)
**Demo (Nick):** same browser URL as S3/S6, live video, USB data pipe
unused; unplug pair → stops; replug → resumes.
**Needs:** S10 + S11.

### S13 — soak, numbers, production notes  `[ ]`
**Goal:** the decision package for the production camera node.
- [ ] 10-min+ soak at T1 settings: fps/loss/latency, CPU headroom on
      both cores, SPI utilization (did DMA land? measured effect)
- [ ] ADIN2111 switchover notes: every 1110/2111 delta hit during the
      arc; what the production PCBA needs (feeds Nick's 2111 move)
- [ ] PoDL/power-path scoping (deferred work, scoped not started)
- [ ] DESIGN decision entry: production architecture recommendation
**Demo (Nick):** soak stats live + written report reviewed together.
**Needs:** S12.

### S8 — Edge CV bring-up (T2)  `[ ]`  *(stub — resequenced 2026-08-11,
Nick: runs AFTER the BM-native arc S9–S13. Board risk gates CV
investment: if the AE3 can't go BM-native, the board changes and CV
work would be redone. Exception: the NPU inference bench (first bite)
doubles as board-selection input — may ride early during the arc as one
cheap bite if a board decision needs it.)*
**Goal:** HD capture + on-device detection at 3–5 fps; alerts over BM.
- [x] NPU inference bench (S0-style): detector fps vs input size on AE3
      → DONE 2026-08-11 (early ride per the exception above; branch
      `sprint/8-npu-bench`, run by Claude, blessed by Nick). Per-tile
      inference fast (yolov8n_192 = 21 ms ≈ 47 fps) but HD tiled
      coverage at 192-px input = 40 tiles → **1.2 fps, BELOW the T2
      ≥3 fps gate**; only yolo_lc_192 meets it tiled (6.3 fps).
      Single-pass downscale drops fish below the 24 px floor. All ROM
      detectors person-class-only → **T2 requires a custom Vela-compiled
      fish detector either way (Nick); larger input size is the lever.**
      Tables in DESIGN.md §S8 detail. `bench/ae3_npu_bench.py` +
      18 host tests; no sensor, no flash, fixture untouched.
- [ ] Detect/track/count pipeline vs T2 spec (fish ≥ 24–32 px)
- [ ] Alert + evidence-JPEG path over the existing link
**Demo (Nick):** camera watches reef footage → "N unique fish in 30 min"
summary arrives; evidence stills viewable. *(Flesh out when reached.)*
**Needs:** S13 (was: S6). Do not start before — Nick's sequencing decision.

---

## Icebox (captured, not scheduled)

- lwIP netif integration in OpenMV firmware (C) — MicroPython sockets over T1L
- N6 evaluation for H.264 path (needs OpenMV answer on VENC MicroPython API)
  — now formally owns the public-stream cell (720p ≥24 fps) of the SPEC
  requirement matrix; AE3 confirmed as this project's platform (Nick)
- SG JP1/JP4 breakout confirmation (would clean up the S4 harness)
- Power-gating architecture (AE3 supervisor + load switch) from board-selection analysis
- ~~bm_core port (post-S7 decision)~~ → scheduled 2026-08-11 as sprint S10
