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

## 2026-08-10 — Sprint S6 — video over the pair, live in the browser; gate run pending light

**Branch:** sprint/6-ae3-video

**Done:**
- Bite 1 (plan approved): BMV6 chunk protocol + bounded Reassembler
  (`s6_video.py`), TX loop with cap/enc/tx telemetry (`s6_video_tx.py`),
  Pi reassembly verifier (`bench/s6_video_counter.py`), shared bring-up
  extracted (`adin_bringup.py`). Verified live (Claude ran the ladder,
  Nick's ask): 60 s @ 20 MHz → 2422/2423 complete, 0 lost, 0 bad JPEGs,
  40.4 fps; artifact JPEGs pulled and eyeballed.
- Bite 2 (plan approved): `chunk_shim.py` + `t1l-chunk-shim.service`
  (CAP_NET_RAW as pi) feeding the FROZEN ingest; browser stream live at
  `http://nereus001-1:8080/stream` (tailnet-wide URL). 2622/2622 frames
  sender→server exact, 0 gaps. Quality made a runtime knob (Nick);
  q90@30 over SPI ruled out by arithmetic + measurement (D20).
- Bite 3 (plan approved, partial): TX loop rides out link outages;
  remote eth1-bounce test → stream freezes + auto-resumes. t1l-sender
  boot service disabled on nereus000 (S6 replaces it). Docs: D20, D21,
  TRACKER states, DESIGN §S6 detail.
- Dark-scene q ladder (NOT gate numbers): q35 45 fps → q90 31 fps, all
  0 loss; tx cost ~2.0 ms/KB at every q.

**Broke/surprised us:**
- SPEC §T1's pipelining requirement is moot on this path (D21): capture
  already DMA-hidden (3.1 ms, not 33), and encode/tx can't overlap
  (polled SPI, one core). Only lever = bytes/frame.
- ADIN1110 MAC drains TX into a dead wire without filling the FIFO —
  sender never stalls on link loss; loss is invisible until the
  receiver counts it. Trust the receiver's ledger, not the sender's.
- `pkill -f chunk_shim.py` over ssh killed its own ssh session (pattern
  matched the remote command line). Use `pkill -f '[c]hunk_shim'`.
- New firmware deprecation warning: `sensor` module → `csi` (watch item).

**Next:** ~~lit-scene gate run~~ → DONE 2026-08-11: **T1 GATE PASSED —
q50 = 32.2 fps / q60 = 25.9 / q70 = 24.2, all 0 loss; standing setting
q50 (D20 final)**. One REPL-wedge between rungs, cleared remotely by
the uhubctl ladder. ~~Remaining: Nick's live demo~~ → **DEMO PASS
(Nick, 2026-08-11): live browser video over the pair, unplug→freeze /
replug→resume, USB REPL-only. S6 `[x]` — THE POINT reached.** PR #9
un-drafted; next sprint = S7 (headless-flash spike already prepping in
a parallel session; flashing unblocked now that the demo is done —
coordinate board access with that session).

---

## 2026-08-10 — Sprint S5 — frame TX + loss demo: 0% loss at 4.21 Mbps

**Branch:** sprint/5-frame-tx

**Done:**
- Bite 1 (plan approved): TX FIFO burst + clause-22 MDIO / MMD-indirect +
  PHY power-up + link mgmt in the portable core; `s5_frame_tx.py` demo;
  21 new host tests. Facts cited from vendored adin1110.c/adin1100.c.
  Verified live: 200/200 × 500 B seq frames in a tcpdump pcap on
  nereus001, in order, zero loss (5 MHz).
- Bite 2 (plan approved): `bench/frame_counter.py` (raw-socket loss
  counter, window-relative accounting, PASS/FAIL verdict),
  `s5_tx_load.py` (65 s @ 20 MHz, 1000 B frames, template+patch_seq),
  core telemetry (sw tx counters, wait_link, status_summary), shared
  `s5_frames.py`. 56 host tests total.
- **Demo PASS (Nick, same day): 31,592/31,592 frames, 0% loss, 526 fps /
  4.21 Mbps sustained 60 s @ 20 MHz — S5 → [x].** 4.21 ≥ the ~4 Mbps D8
  budget; MicroPython driver is not the S6 blocker.
- Claude ran the full manual-test ladder remotely (Nick's ask), incl.
  installing tcpdump on nereus001 and pcap verification by parsing.

**Broke/surprised us:**
- Half the session lost to a **bad pair connector**: both PHYs
  register-perfect (AN on, advertisement correct, forced-mode off) and
  both sides deaf. Register work can only *rule out* software — the
  split came from bench checks: 3V3 under AN load (3.276 V, fine), then
  the connector. Full ladder + lessons in DESIGN §S5 detail.
- Continuity across J1 on POWERED hats reads OL — first readings were
  artifacts; the checklist's "unpowered" rule is load-bearing.
- t1l-sender auto-starts on nereus000 boot and owns the AE3 USB port —
  bit us again after the power cycle; stop it before mpremote work.

**Next:** S6 bite 1 — AE3 capture → MJPEG → chunked seq frames over the
TX path; Pi shim reassembles and feeds the FROZEN S3 stream server
(ingest :8081). Demo = live browser video, USB data pipe unused.

---

## 2026-08-10 — Sprint S4 — AE3 first light: PHY ID 0x0283BC91 over SPI

**Branch:** sprint/4-ae3-first-light

**Done:**
- Nibble 1 plan approved (protocol facts sourced from vendored
  adin1110.c, not the datasheet — proven code beats transcription).
  Power rig revised by Nick before wiring: hat fed from nereus000's 3V3
  header, AE3 USB-powered from the same Pi (D19 — the S2/S3 setup
  already proved this exact load combo); AE3 3V3 question sidestepped.
- Built `firmware/adin_drv/`: portable protocol core + AE3 HAL (two-layer
  contract), first-light demo with built-in no-LA fallback diagnostics,
  16 host unit tests. ~380 LoC, one bite.
- **Demo PASS (Nick, same day): `PHY ID: 0x0283BC91 — OK` at 5 MHz,**
  first attempt on a correctly wired harness, repeatable. S4 → [x].
- Debug tools kept for S5+: `s4_bus_probe.py` (DC checks, incl. rail
  detect via the hat's own RESET_N pull-up), `s4_bitbang_probe.py`
  (pure-GPIO PHY ID read, splits harness faults from machine.SPI faults).
- Remote dev loop notes: ssh as **pi@nereus000**, mpremote at
  `~/.local/bin/mpremote`, AE3 = `/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*`
  (ttyACM0 is the N6 — never hardcode ACM numbers). Claude can run the
  AE3 via mpremote directly; sudo over ssh needs Nick.

**Broke/surprised us:**
- The hat header got counted mirrored TWICE while off the Pi — cost most
  of the session. All-0xFF + stray-bit and TX-echo signatures were
  floating-MISO crosstalk. Ender: meter hat 17 ↔ 6 with power jumpers
  only (~3.3 V iff orientation right) BEFORE landing data wires.
- Debug probes "passed" convincingly on miswired harnesses (coincidental
  nets mimic expected responses) → led to two wrong theories
  (machine.SPI SS-steal, strap mode corruption) before Nick spotted the
  flip. Lesson recorded in DESIGN §S4: verify wiring before trusting
  probe interpretations.
- t1l-sender was still active at session start — it owns the AE3 USB
  port; stop it before any mpremote work (fixture restore: remount hat
  #2 + `systemctl start t1l-sender`).

**Next:** S5 — frame TX path (generic SPI FIFO), seq-numbered payloads;
RX side minimum = link status + counters; Pi raw-socket counter script.
Pi end can be nereus001 as-is (live reference node stays intact).

---

## 2026-08-10 — Sprint S3 — bites 2+3: video across the pair, 30 fps, zero loss

**Branch:** sprint/3-t1l-video

**Done:**
- Bite 2 plan approved; built `pi/stream/stream_server.py` (nereus001:
  TCP ingest :8081 — the FROZEN S6 interface, same frame framing as the
  USB protocol, StreamParser reused — + HTTP :8080 `/stream` `/frame.jpg`
  `/stats.json`, stdlib only) and `pi/stream/t1l_sender.py` (nereus000:
  self-healing leg — board reboot per D15 → USB session → Pacer →
  re-sequenced relay). 9 new unit tests; 24 total pass.
- Live end-to-end same day: browser video that crossed the pair.
  Real-scene q90 frames ≈ 21 KB (2.4× bench scene, as S0 predicted).
- Nick pushed the target: 15 → 30 fps. Measured live: q90@30 = 30.8 fps /
  4.8 Mbps / 0 gaps (~2 fps encoder surplus); q80@30 = 30.4 / 3.0 (~4 fps
  surplus, documented fallback). **D17: standing setting QVGA q90 @ 30 fps**
  (supersedes D16; S6 caveat: exceeds the ~4 Mbps SPI budget — USB-path
  only).
- systemd units + installer (`pi/services/`,
  `pi/install_stream_service.sh receiver|sender`); both nodes converted.
- **Sustained measurement (TODO 3): 10 min 15 s, 18,032 frames, 29.3 fps
  avg, 4.60 Mbps, 0 gaps, 0 resets — zero frame loss.** Sender self-heal
  verified live (receiver restart mid-stream → reconnect + board reboot +
  resume).
- nereus001 ssh via new tailnet name `nereus001-1` (old entry stale).

**Broke/surprised us:**
- pkill -f patterns that also appear in the launching command line kill
  the launcher's own ssh session — twice. Bracket trick alone isn't
  enough; separate the kill and start invocations.
- Nothing else — the pipeline came up on the first end-to-end attempt.

**Next:** S4 — AE3 first light, PHY ID over SPI. Rig revised (Nick, D18):
AE3 drives **AOS hat #2** (freed from nereus000), not the SG shield —
proven silicon/straps, crimped pair, 3.3V-only. Open: AE3 3V3 sourcing
the hat. *(S3 demo passed by Nick same day → S3 [x]; VGA live ceiling
also measured post-demo: q35 13.5 fps / q50 11.7 over the pair.)*

---

## 2026-08-10 — Sprint S3 — bite 1: USB frame source measured; AE3 crash found + worked around

**Branch:** sprint/3-t1l-video

**Done:**
- Nibble 1 plan approved. Vendored the legacy nereus-camera-test-rig USB
  capture service (@ f11befe) into `firmware/ae3_usb/` with provenance
  README (D12 pattern); host-side `pi/stream/usb_frame_source.py` (pure
  incremental StreamParser + UsbFrameSource, 15 unit tests) and
  `bench/usb_stream_bench.py` (fps/Mbps/gaps/JPEG-integrity table +
  sample-frame artifacts).
- **Found an AE3 firmware crash:** second `start_stream` session per boot
  hard-faults the board (USB dies; deep flavor needs physical replug —
  Nick did 4 today). Isolated by elimination: first-session-any-mode OK,
  command loop OK, soft reset insufficient, `machine.reset()` clears it.
  Same on stable v5.0.0 and dev `11852aa3d0` — the dev build's "PAG7936
  halt for safe shutdown" does NOT fix it. Workaround shipped (D15):
  local-patch `reboot` action; hosts reboot the board between sessions.
  Recovery ladder documented (uhubctl → safe-mode REPL → machine.reset).
- Firmware version confusion resolved: IDE "5.0.0 [latest]" ≠ stable —
  dev builds self-report 5.0.0; discriminator is the uname build date.
  Board now runs dev `11852aa3d0 on 2026-08-10`.
- Bench matrix + QVGA q-sweep measured (DESIGN §S3 detail). Manual test
  run by Nick: **PASS** (4/4 modes, 0 gaps, 0 bad JPEGs, samples verified
  as real images). **Setting chosen (Nick, D16): QVGA q90 paced 15 fps.**
- Hard fact: VGA ≥ 15 fps unreachable on AE3 (software JPEG encoder,
  ~70–85 ms/frame); `set_framebuffers(2)` in-stream makes it worse and
  breaks HD (tested, reverted).

**Broke/surprised us:**
- The crash pre-dates Nick's firmware update — same build string as S0.
- Pi 5 USB port power switching (uhubctl) doesn't truly cut VBUS: board
  shows "connect" while port is "off"; deep-crash flavor unrecoverable
  remotely.
- nereus001 re-registered on the tailnet as `nereus001-1` (old entry
  stale); T1L link itself pings fine from nereus000.

**Next:** bite 2 — sender service on nereus000 (frames over T1L) +
receiver/stream server on nereus001 (`:8080/stream`, the frozen S6
interface), QVGA q90 @ 15 fps.

---

## 2026-08-10 — Sprint S2 — AOS hat #1 validated: probes on Pi 5, PHY ID match

**Branch:** sprint/2-aos-node-link

**Done:**
- Nibble 1 plan approved; scope shifted twice as Nick supplied better
  sources: web (no public docs) → schematic PDF → full KiCad layout.
  Parsed the layout netlist pad→net (authoritative for the fabbed board);
  pad numbers match ADIN1110 datasheet p.9 exactly.
- Facts recorded (DESIGN.md §S2 detail): AOS pinout = SG shield
  (CE0/GPIO22 INT/GPIO17 RESET); 3.3V-only; J1 ckt1 = DA−; straps default
  OA → hat #1 pre-bridged CFG0+CFG1 = generic SPI no CRC (D13).
- **Board bug found via netlist + datasheet:** INT_N (open-drain) has no
  pull-up on the board; R10 1.5k is on TEST1 (required there, so not a
  misplacement). Workaround: GPIO22 internal pull-up in overlay (D14).
  Draft note to AOS in docs/aos_hat_checklist.md §D.
- `pi/overlays/aos-adin1110.dts` (SG overlay + pull-up + MAC ...:02) +
  `docs/aos_hat_checklist.md` (meter checklist, now hat-#2/debug only).
- Nick mounted hat #1 on nereus000 directly (skipping the meter pass, his
  call). Probed first try under the stale SG overlay (floating INT rested
  high — luck), then cleanly under the AOS overlay after install+reboot:
  **eth1 MAC 02:ad:11:10:00:02, PHY ID 0x0283bc91, IRQ quiet, verify
  5/5.** Straps proven by working register I/O; #2204 silicon concern
  cleared.

**Broke/surprised us:**
- Tailscale SSH on nereus000 now demands per-session browser re-auth —
  ssh commands hang until someone approves the login URL. Fix before the
  Pi 3/4 bite.
- The hat worked under the SG overlay before any AOS software existed —
  identical pinout meant the only real difference is the INT pull-up.

**Same session, continued (hat #2 + nereus001 + tooling):**
- Hat #2 validated identically on nereus000 (PHY ID match, verify 5/5) —
  both hats good; straps proven by working register I/O.
- T1L tooling written + approved: `pi/setup_t1l_ip.sh <1|2>` (iperf3 + NM
  profile `t1l`, static 192.168.7.x/24, never-default; node 2 clones MAC
  ...:03) and `bench/t1l_link_test.sh server|client` (ping 0% / TCP ≥8
  Mbps both ways / UDP @8M <1% loss, iperf3 JSON parsed). No-carrier
  failure path verified on hardware. `build_adin1110.sh` now takes sg|aos.
- **nereus001 brought up** (second Pi 5 — Nick's call, replaces the Pi 3/4
  plan; SPEC inventory not yet amended): tailnet via pi-tailscale-setup
  skill (vendored from bm_cam_legacy), repo cloned, driver built, AOS
  overlay, hat #1 mounted. Node roles: nereus000 = hat #2 = .7.1,
  nereus001 = hat #1 = .7.2/MAC ...:03.
- **Kernel-orphan incident, resolved:** first-boot unattended upgrades
  bumped nereus001 from 6.18.34 → 6.18.39 between driver build and the
  hat-install power cycle → modules orphaned, probe silently absent
  (pi-kernel-upgrade skill scenario, seen live). Rebuild against running
  kernel + modprobe fixed it without reboot; cold-boot verify 5/5.
  nereus000 still runs 6.18.34 with the same upgrade pending — expect a
  rebuild there on its next apt upgrade.

**Pair test (same day, Nick wired the pair):** link test **4/4 PASS —
TCP 9.32/9.33 Mbps fwd/rev (full T1L line rate), UDP 8 Mbps 0% loss,
ping 0% loss RTT avg 0.84 ms.** Numbers in DESIGN.md §S2 detail. NM
profiles auto-activated on carrier; node-2 MAC clone confirmed.

**Sprint closed (2026-08-10):** Nick blessed the demo run (delegated to
Claude, watched live) and DESCOPED the logic-analyzer captures — no LA on
the bench. S4 consequence noted in TRACKER (no golden trace to diff;
fallback = register readback + live Linux node as reference). **S2 → [x].**

**Next:** merge PR #3, then S3 — video across T1L, Pi to Pi: AE3 → Pi 5
over USB (existing setup) constrained per budget, sender service on
nereus000 → frames over the pair → receiver on nereus001 serves
multipart-MJPEG HTTP. New branch `sprint/3-<slug>`.

---

## 2026-08-09 — Sprint S1 — Pi 5 ADIN1110 driver up: eth1 probes, PHY ID confirmed

**Branch:** sprint/1-pi5-adin1110-driver

**Done:**
- Nibble 1 plan approved by Nick: out-of-tree module build instead of SG's
  full kernel rebuild (D12). Stock trixie kernel has ADIN1110/ADIN1100_PHY
  unset but NET_SWITCHDEV=y + CRC8=m + headers installed → viable.
- Vendored unmodified `adin1110.c` + `adin1100.c` (rpi-6.18.y @ 222a4b41)
  into `pi/drivers/adin1110/` with out-of-tree Makefile + provenance README.
- `pi/overlays/sg-adin1110.dts` written from SG facts + kernel binding:
  SPI0 CE0 @ 23 MHz, IRQ GPIO22 level-low, reset GPIO17 active-low, INT
  bias-none, spidev0 off, no adi,spi-crc. Fixed MAC 02:ad:11:10:00:01.
- `pi/build_adin1110.sh` (idempotent build+install) + `pi/verify_adin1110.sh`
  (artifact checks). Repo cloned on nereus000 at `~/ADIN_SPI_OpenMV`.
- Built, installed, rebooted, verified: **eth1 up on driver ADIN1110,
  internal PHY reads 0x0283BC91** (SPEC match), bound to ADIN1100 phylib
  driver. verify = 5/5 PASS. eth0/SSH untouched.

**Broke/surprised us:**
- `ethtool -i` reports the driver name UPPERCASE ("ADIN1110") — verify
  script initially failed its driver-name check; now case-insensitive.
- SG's published DTS uses edge-trigger for INT but binding + driver source
  say level-low (driver hardcodes IRQF_TRIGGER_LOW) — went with level.
- Non-login ssh shells on the Pi lack /usr/sbin in PATH (modinfo/ethtool
  "not found" red herring); scripts export PATH explicitly.

**Next:** Nick runs the S1 demo (commands in PR + TRACKER); on PASS, close
S1 and open S2 (AOS hats buzz-out, second node).

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

**Decision + spike (same session):** Nick chose A→B. Spike read
`ports/alif/machine_spi.c` (upstream + OpenMV fork: identical): transfer is
polled lock-step per-byte, no DMA/FIFO burst → ceiling is software, firmware
build required to fix (= option C, priced ~50 LoC FIFO burst in one function).
Proceeding per B: ~4 Mbps AE3 video budget through S6 (DESIGN.md D8). New open
question in SPEC.md: true SCLK at 20/25 MHz requests (LA check in S2).

**Video table (Nick re-prioritized, same session):** ran
`bench/ae3_video_bench.py` on the AE3. Needed two fixes: fw 1.28 renamed
`compressed()`→`to_jpeg()`; VGA+ overflowed default framebuffers inside
`skip_frames` and the script skipped points SILENTLY (now prints skip
reasons; `set_framebuffers(1)` fixes capture). Sensor 0x7936 letterboxes;
QQVGA/SVGA/WXGA unsupported. **Headline: encoder is the bottleneck, not
SPI** — all supported modes produce < ~2 Mbps (bench scene bpp 0.10–0.24;
even at 0.875 deployment bpp: VGA ~8 fps @ 2.2 Mbps). SPI ceiling has ≥2×
headroom. Full table in DESIGN.md. Oddity flagged: mono bytes/frame inert
across quality settings.

**Reef-scene bench (Nick re-prioritized again, same session):** Nick supplied
`images/` (UNCOMMITTED in repo root — his call pending on git/LFS). Built
`make_ref_scene.py` (16:10 ROI-preserving crop + downsample to sensor
geometries) + `ae3_ref_scene_bench.py` (encode via mpremote mount). P7071008
baseline: reef bpp brackets the 0.875 anchor; color = encoder-bound, mono =
SPI-bound; **VGA color ~8 fps / VGA mono ~14 fps / HD mono ~4 fps delivered
at 1.7–2.9 Mbps** — MicroPython path viable for real scenes. Dark-room
"mono ignores quality" oddity resolved (scene artifact). Multi-image sweep
captured as new TODO.

**Requirements set (Nick, closing the loop):** AE3 confirmed as platform.
2×2 requirement matrix in SPEC.md; dual targets: **T1 streaming = QVGA color
q35–50 @ 24–30 fps** (feasible per measurements ONLY with capture/encode/tx
pipelining — S6 constraint), **T2 edge CV = HD @ 3–5 fps on-device**
(fish ≥24–32 px; sergeant majors ≈32–48 px at HD from P7071008). 12 Mbps
gate retired → transport gate = 2× T1 bitrate = ≥3.5 Mbps; measured 4.89
PASSES. S8 stub added (T2, strictly after T1). N6 owns the public-720p cell
(icebox). Probes: no hardware JPEG on sensor 0x7936; VGA capture 33 ms
single-buffered / 16.7 ms double. ROI visualization delivered (single 16:10
crop shared by all modes; density-only difference). Decisions D9–D11.

**Sprint closed:** Nick ran the S0 demo in OpenMV IDE — PASS against the
revised ≥3.5 Mbps gate. PR #1 open
(https://github.com/nickraymond/ADIN_SPI_OpenMV/pull/1), Nick approving.
S0 marked `[x]` in TRACKER.

**Next:** merge PR #1, then S1 — Pi 5 + SG shield: build the adin1110
kernel module, install SG's overlay (SPI0 CE0, 23 MHz, IRQ GPIO22, no
adi,spi-crc), verify probe + interface up. New branch `sprint/1-<slug>`.

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
