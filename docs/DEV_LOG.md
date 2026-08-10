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

**Next:** golden logic-analyzer captures (last S2 item), then Nick
formally re-runs/blesses the demo to close S2.

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
