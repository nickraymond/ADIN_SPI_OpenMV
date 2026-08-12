# DESIGN.md — Architecture & Decisions (as-built)

*What it did / how it's shaped. Agents append; never silently rewrite history.*
*Last updated: 2026-08-10*

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
| D12 | 2026-08-09 | S1 driver via out-of-tree module build (vendored mainline sources), not SG's full kernel rebuild (Nick approved) | Same two driver files either way; builds in ~1 min against installed headers, stock rpt kernel untouched, reversible. Trade-off: apt kernel upgrades orphan the modules → re-run `pi/build_adin1110.sh` (script detects and fails loudly). Provenance pinned in `pi/drivers/adin1110/README.md` (rpi-6.18.y @ 222a4b41). |
| D13 | 2026-08-10 | AOS hats run generic SPI without CRC via bridged SPI_CFG0+SPI_CFG1 solder jumpers (hat #1 arrived pre-bridged; verify per checklist) | Board default is all-straps-low = OPEN Alliance w/ protection (internal pull-downs, ADIN1110 datasheet), which mainline `adin1110.c` cannot speak. Bridging both jumpers (each → 4.7k → 3.3V) matches the SG shield config and D1. Jumpers are reversible for S7's optional OA spike. |
| D14 | 2026-08-10 | AOS overlay enables Pi internal pull-up on GPIO22 (INT) — the one functional difference from the SG overlay | AOS board has no pull-up on INT_N; datasheet (p.9, pin 25) specifies open-drain, active-low, 1.5 kΩ pull-up to VDDIO required (board's R10 1.5k is on TEST1 — itself required, so not a misplacement; INT pull-up simply absent). Pi internal ~50 kΩ is out of datasheet spec but adequate for a level IRQ at bring-up; fallback = 1.5–4.7 kΩ bodge INT→3.3V if IRQs misbehave. Reported to AOS (draft in `docs/aos_hat_checklist.md`). |
| D15 | 2026-08-10 | S3 USB source = vendored nereus-camera-test-rig capture service (`firmware/ae3_usb/`, @ f11befe) + one local patch: `reboot` action, and hosts reboot the board between stream sessions | Reuse before rewriting — the legacy `start_stream` framed-JPEG path is proven. The patch works around a hard AE3 firmware crash: the SECOND `start_stream` session per boot hard-faults the board (USB dies, physical replug sometimes needed). Reproduced 3×, isolated by elimination; NOT fixed by OpenMV dev build `11852aa3d0` (2026-08-10) despite its "PAG7936 halt for safe shutdown" note; MicroPython soft reset insufficient, `machine.reset()` clears it. Repro + details: `firmware/ae3_usb/README.md` §Known firmware crash. Candidate OpenMV upstream report. |
| D16 | 2026-08-10 | S3 stream setting (Nick): **QVGA color q90, sender-paced to 15 fps** (~2.5 Mbps free-run ceiling measured) | Nick wanted "as high a quality as we can safely do" at 15 fps. Measured q-sweep (bench scene): q90 free-runs 35.7 fps — 2.4× the target, margin enough even for real scenes encoding 3–5× slower (S0 reef data). VGA rejected: software-encoder-bound at 9.8 fps (q70) / 12.1 (q50); **VGA ≥ 15 fps is unreachable on the AE3 on any transport** (no hardware JPEG on this sensor). For a future 30 fps mode, drop to q80 (real-scene margin at q90 is thin). *(Superseded by D17 same day.)* |
| D17 | 2026-08-10 | D16 revised (Nick, after live end-to-end tests): standing S3 setting = **QVGA q90, 30 fps** — sender/service defaults updated | Measured live over the pair on the real bench scene: q90@30 delivers 30.8 fps rx / 4.6–4.8 Mbps / 0 gaps with ~2 fps encoder surplus (thin, scene-dependent — pacer degrades gracefully by riding the source rate); q80@30 = 30.4 fps / 3.0 Mbps / ~4 fps surplus, the documented fallback if a scene can't hold 30 at q90. S6 caveat recorded: ~4.7 Mbps exceeds the ~4 Mbps AE3 SPI budget (D8), so this exact mode is USB-path only; the SPI-era target remains T1 (QVGA q35–50). |
| D18 | 2026-08-10 | S4 rig uses an **AOS hat** (hat #2, freed from nereus000), not the SG shield — supersedes D7's S4 half (Nick) | Both AOS hats are now S2-proven silicon with proven straps (generic SPI no CRC, verified by working register I/O) and crimped pair connectors, and the board is 3.3V-only — removing the SG shield's 5V-regulator meter question entirely. Header pinout is identical to the SG shield (DESIGN §S2), so the Diagram 1 harness maps pin-for-pin. Consequences: AE3-side must supply the missing INT_N pull-up (P5 internal pull-up, per D14) and the S3 stream fixture pauses while hat #2 is off nereus000. SG shield remains shelved as a known-good backup. Open question flagged in TRACKER: AE3 3V3 pin's ability to source the hat's draw. |
| D19 | 2026-08-10 | S4 rig power (Nick): hat fed from **nereus000's 3V3 header** (Pi pin 1 → hat 1, GND pin 9 → hat 9); AE3 stays USB-powered from the same Pi; direct AE3→hat ground jumper for signal return; AE3's 3V3 pin unused | No bench supply on hand, and this exact load combination is already proven — during S2/S3 nereus000 simultaneously powered hat #2 on its header and the AE3 over USB. Grounds are common through the Pi; the extra AE3→hat GND wire keeps the SPI return path out of the USB cable. Sidesteps (does not answer) the D18 open question about the AE3 3V3 pin's sourcing ability — re-flag if a standalone rig ever needs it. |
| D20 | 2026-08-10 | S6 stream quality is a **runtime knob** (`s6_video_tx.main(quality=…)`), default q50; T1 target stays q35–50 (Nick, after seeing the numbers). q90 @ 30 fps confirmed impossible on the SPI path; final standing quality picked from a lit-scene ladder | Nick initially asked for q90@30 (the D17 USB-path setting). Arithmetic + measurement: real-scene q90 ≈ 19–21 KB/frame → 4.6–4.8 Mbps at 30 fps > the 4.21 Mbps S5-measured SPI payload ceiling; tx alone ≈ 41 ms/frame (measured ~2.0 ms/KB). Dark-scene ladder (q35→q90, 30 s rungs, all 0 loss) confirms the cost curve. D17's "USB-path only" caveat stands. **FINALIZED 2026-08-11 (lit-scene gate ladder): standing S6 setting = q50 — 32.2 fps with ~8 fps margin; q60 = 25.9, q70 = 24.2 (gate-edge, zero margin). Caveat recorded: on a busier-than-bench scene (reef anchor ≈ 9.2 KB @ q50) even q50 projects to ~24 fps — scene-dependent, q35 is the fallback.** |
| D21 | 2026-08-10 | SPEC §T1's "MUST pipeline capture/encode/tx (≥2 framebuffers)" is **moot on the MicroPython path** — measured, not assumed. Only throughput lever on this path is bytes/frame (quality) | Bite-1 timing split: capture = 3.1 ms (sensor DMA already overlaps at QVGA default buffering — not the feared 33 ms), encode 17.4 ms, tx 4.2 ms @ ~2.1 KB dark-scene frames. Encode and tx CANNOT overlap: the SPI driver is per-byte polled (D8), so both are CPU-bound on the single MicroPython core. Related hardware finding from the S6 link-bounce test: with the far end down, the ADIN1110 MAC drains TX frames into the dead wire without filling the FIFO — the sender never stalls, loss is silent at the sender, and loss accounting therefore lives at the receiver (which is the project's counting philosophy anyway). The C/DMA driver (option C, D8) would reopen the pipelining lever. |
| D22 | 2026-08-11 | *(D20/D21 were authored in parallel on `sprint/6-ae3-video`.)* Headless AE3 flashing goes through **OpenMV's own DFU bootloader over USB** — not SWD (no debugger on the bench), not Alif SE-UART ISP (on the AE3 the SE UART reaches USB only in recovery mode: physical front switch or B2B RECOVERY pin low → hands or a board mod). SE-UART stays the documented deep-recovery path; the B2B recovery wire is NOT being added. | Verified from source (openmv.git): the bootloader runs first on **every** boot as USB DFU `37C5:96E3` with a ~1 s + 1.5 s window before jumping to the app (`boot/src/common/main.c`); `machine.bootloader()` writes magic `0xB00710AD` → `0x200FFFFC` + reset and the bootloader then stays in DFU (`micropython ports/alif/boards/OPENMV_AE3/board.c:107`); partitions are named DFU alts `BOOT HP HE ROMFS1 TOC RWFS ROMFS0 RECOVERY` (`boot_config.h:112`), flashable with stock `dfu-util`. The tooling never writes `BOOT`, so a bad app flash is always recoverable by power cycle (uhubctl) — residual brick risk requires corrupting the bootloader partition itself, which nothing in the loop touches. Firmware self-identifies: `os.uname().version` embeds `OpenMV <sha10>; MicroPython <sha10>` (verified in release binaries) → flash verification = hash match, per the S7 spike's verifiable. Tooling: `pi/ae3_flash/`. |
| D23 | 2026-08-11 | Firmware **build host = Nick's Mac** (Apple Silicon), docker `linux/amd64` container under Rosetta with the `linux-x86_64` OpenMV SDK; artifacts scp to the Pi and flash via D22's path. Not docker-on-Pi. Mac is also the one-machine home for OpenMV dev now, bm_core dev next (own container later); VS Code for edits; OpenMV IDE (dmg install) kept as the hands-on flashing option. (Nick's call, 2026-08-11.) | The OpenMV SDK toolchain bundle is published **only** for `linux-x86_64` and `darwin-arm64` (`download.openmv.io` probed 2026-08-11; `linux-aarch64` 404s), so docker on the Pi 5 would mean qemu amd64 emulation on the live fixture host — slow and risky for zero benefit; the Mac runs the same container under Rosetta at near-native speed and the flash step still runs entirely from nereus000, so the loop stays fully remote. Reuses openmv.git's own `docker/Makefile` build (reuse before rewriting); wrapper adds rev pinning, platform/SDK plumbing, artifact verification. Tooling: `firmware/openmv_build/`. |
| D24 | 2026-08-11 | AE3 firmware builds go through openmv's `docker/Makefile` **`build-firmware-dev`** target, never the stock `build-firmware` — the stock target cannot link the M55_HE image. `build_ae3.sh` updated (clean by default, `--incremental` for the dev loop); this unblocks the S9/S10 arc (bm_core runs on HE). | Root-caused 2026-08-11: stock `docker/build.sh` passes `BUILD=<dir>` on the make **command line**; command-line vars ride MAKEFLAGS into every sub-make and override `ports/alif/alif.mk:34`'s `BUILD := $(BUILD)/$(MCU_CORE)` per-core nesting. HP and HE then share one object dir — HP builds first and links; HE "reuses" HP-configured objects (USB device stack on) → FLASH_TEXT 154% (2.21 MB ≈ the HP image), undefined `dcd_*` TinyUSB DCD refs, "dangerous relocation". Reproduced byte-identical with the S9 usermod fully compiled out — environmental, as suspected. OpenMV CI never hits it: `.github/workflows/firmware.yml` + `tools/ci.sh::ci_build_target` build AE3 with plain `make TARGET=OPENMV_AE3`, no docker, no `BUILD=` override (their docker matrix entry builds only single-core NICLA_VISION). Upstream commit `6adf40fd` (2026-04) added `build-firmware-dev`, whose `build-dev.sh` comment states the nesting requirement verbatim — stock target remains broken for multi-core Alif targets (candidate upstream report). Fix verified from clean at `7d4dbf7ab2`: HE links 1,193,520 B / FLASH_TEXT 83.25% (official artifact 1,185,744 B), HP 2,200,176 B (official 2,200,784 B), `build/OPENMV_AE3/M55_{HP,HE}/` nesting present. Consequence found during verify: our tagged clone embeds describe-form ids (`v5.0.0-52.g7d4dbf7ab2`, dots per micropython `makeversionhdr.py`), not the bare sha10 of tagless CI builds — `flash_ae3.py`'s exact-match label check would have false-failed every local build; MANIFEST now carries `openmv_label` (exact embedded string) and the label check accepts a sha10 inside a describe id (byte-level readback verify unchanged as the real gate). |

## Verified-facts ledger

See SPEC.md §Confirmed technical facts. Anything not there or here is
unverified — treat as unknown.

## Bench results

*(appended by sprints)*

- S0 SPI benchmark: **FAIL — 4.89 Mbps max effective vs ≥ 12 Mbps gate.**
  Run 2026-08-09, AE3 fw v1.28.0-49 (2026-07-02), `bench/ae3_spi_bench.py`
  driven remotely via nereus000, P0→P1 loopback, 0 integrity errors at every
  point. IRQ path is excellent. Details below.
- S1 Pi 5 driver bring-up: **PASS — demo run by Nick 2026-08-09.** Details
  below.
- S2 iperf3 over T1L: **PASS — TCP 9.32/9.33 Mbps fwd/rev (line rate),
  UDP 8.0 Mbps @ 0% loss, ping 0% loss RTT avg 0.84 ms.** Run 2026-08-10,
  nereus000 (hat #2, .7.1) ↔ nereus001 (hat #1, .7.2) over the bench
  pair, `bench/t1l_link_test.sh` 4/4. Details below.
- S3 sustained video Mbps / fps: **PASS — 10-min sustained run 2026-08-10 at
  the D17 setting (QVGA q90 @ 30 fps) under systemd: 18,032 frames / 615 s =
  29.3 fps avg (29.6 rolling), 4.60 Mbps avg on the pair, 0 gaps, 0 resets —
  every sent frame delivered.** Detail below.
- S4 PHY ID first light: **PASS — demo run by Nick 2026-08-10.**
  `PHY ID: 0x0283BC91` (SPEC match) over generic SPI no CRC at 5 MHz,
  AE3 → hat #2, first attempt on a correctly wired harness; repeatable.
  Detail below.
- S5 loss rate: **PASS — demo run by Nick 2026-08-10. 60 s sustained at
  20 MHz SPI: 31,592/31,592 frames received (0% loss, 0 dupes, 0
  out-of-order), 526 fps / 4.21 Mbps payload; sender 0 FIFO stalls,
  SPI_ERR clear. 4.21 Mbps ≥ the ~4 Mbps D8 AE3 video budget.** Detail
  below.
- S6 end-to-end fps / loss: **T1 GATE PASSED — lit-scene ladder run
  2026-08-11 (Claude, remote), 60 s counter windows, 20 MHz SPI, zero
  loss and zero bad JPEGs at every rung: q50 = 32.2 fps (standing
  setting, D20) · q60 = 25.9 · q70 = 24.2.** Longest sustained run:
  15 min @ q50 dark scene, 36,299 frames, 0 stalls, SPI_ERR clear.
  Glass-to-glass latency unmeasured (flagged; sender-side ~31 ms/frame
  at q50). **Sprint demo PASS — run by Nick 2026-08-11: live browser
  video over the pair, unplug→freeze / replug→resume, USB REPL-only.
  S6 complete = the project's end-state demo (SPEC §Goal) achieved on
  the MicroPython driver.** Detail below.
- S8 NPU inference bench (bite 1, early-ride exception): **CORRECTED
  2026-08-11 (same day): the first run was unknowingly measured on the
  OpenMV N6 — mpremote auto-connect grabbed /dev/ttyACM0, which is the
  N6, not the AE3 (both boards live on nereus000's USB; the "OpenMV
  v5.0.0" version string and 25.6 MB heap were the tells). Re-run on
  the real AE3 (ttyACM1, explicit connect): conclusions UNCHANGED,
  numbers re-attributed — yolov8n_192 = 26.3 ms/tile (~38 fps); HD
  (1280×800) tiled coverage at 192-px input = 40 tiles → 0.95 fps,
  BELOW the T2 ≥3 fps gate; only yolo_lc_192 (4.9 ms/tile) meets it
  tiled (5.1 fps). Single-pass HD downscale puts 100–150 px fish at
  15–23 px — below the 24 px floor. All ROM detectors person-class-only
  → T2 needs a custom Vela-compiled fish detector regardless; larger
  input size is the lever. Bonus: the mistaken run is a free N6
  comparison point (caveat: ROMFS model binaries differ per board —
  not an apples-to-apples silicon comparison).** Both tables in the
  detail below. Standing rule from the incident: **never bare
  `mpremote` on nereus000 — always `connect` with an explicit
  /dev/serial/by-id path** (two OpenMV boards on this host).

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

### S1 detail (2026-08-09) — Pi 5 + SG shield bring-up (as-built)

Pi 5 (nereus000), Debian 13 trixie, kernel `6.18.34+rpt-rpi-2712`. Stock
kernel ships `CONFIG_ADIN1110` unset → out-of-tree build (D12) of the two
unmodified mainline files against installed headers. Kernel-side deps all
present in stock config: `NET_SWITCHDEV=y`, `PHYLIB=y`, `CRC8=m`, module
signing off.

As-built facts (verified on hardware, not assumed):

- Overlay `sg-adin1110.dtbo`: SPI0 CE0 @ 23 MHz, `interrupts = <22 8>`
  (level-low — the driver hardcodes `IRQF_TRIGGER_LOW`; SG's published DTS
  says edge but the binding + driver source say level), `reset-gpios =
  <&gpio 17 GPIO_ACTIVE_LOW>` (driver strobes it in probe), INT pin
  bias-none per SG + ADI-eval precedent, spidev0 disabled, no
  `adi,spi-crc`.
- Probe: `adin1110 spi0.0 eth1: Link is Down` at ~7 s boot; interface
  **eth1**, fixed locally-administered MAC `02:ad:11:10:00:01` (set in
  overlay for boot-stable naming/addressing).
- `ethtool -i eth1` → driver **ADIN1110** (note: uppercase), bus-info
  `spi0.0`, version = kernel release.
- Internal PHY: MDIO addr 1 (`spi0.0:01`), **`phy_id 0x0283bc91`** —
  matches the SPEC-predicted readback — bound to `ADIN1100` phylib driver.
  (MDIO addr 0 shows `0x0000ffff` unbound; expected — the driver scans
  the ADIN2111 address range and the second port doesn't exist here.)
- eth0 untouched (SSH stays on wlan0/tailscale); "loading out-of-tree
  module taints kernel" in dmesg is expected and benign.
- Debug note for future PHY trouble: RPi-forum thread on this driver
  reports early ADIN1110 silicon revs fail probe with "PHY ID read: 0"
  (-EIO). Ours reads clean.

### S2 detail (2026-08-10) — AOS BOREALIS hat: facts from design files

Sources, in order of authority: **AOS KiCad layout netlist**
(`aos-rpi-zero-spe.kicad_pcb`, provided by Nick, parsed pad→net;
authoritative for the fabbed board), AOS schematic PDF (`aos-rpi-zero-spe`,
title block: "Based on a MM Ethernet board by N. Seidle", CC BY-SA 4.0, rev
field unpopulated), **ADIN1110 datasheet p.9 pin table** (netlist pad
numbers match datasheet pin numbers exactly, validating the Eagle import),
and photos of hat #1. Meter verification per `docs/aos_hat_checklist.md`
pending — treat rows below as design-file facts until then.

| Fact | Value | Notes |
|---|---|---|
| SPI | SPI0 **CE0** (header 24 → R24 22Ω → CS_N pin 29); MOSI/MISO/SCLK header 19/21/23, 22Ω series each | identical mapping to SG shield |
| INT | header 15 = **GPIO22** → R8 22Ω → INT_N pin 25 | **no pull-up on board**; open-drain per datasheet → overlay adds Pi pull-up (D14) |
| RESET | header 11 = **GPIO17** → R29 22Ω → {R28 100k↑3.3V, S1 button↓GND} → R27 10k + C23 0.1µF → RESET_N pin 5 | active-low; manual reset button on board |
| Straps | SPI_CFG0/CFG1/MS_SEL/SWPD/TX2P4 all default LOW (chip internal pull-downs) = OA w/ protection; NO solder jumpers each → 4.7k (R12–R16) → 3.3V | CFG0+CFG1 bridged = generic SPI no CRC (D13); hat #1 photo shows both bridged |
| Pair | J1 Molex Micro-Fit 430450201: **ckt 1 = DA−, ckt 2 = DA+** (T1 WE-STST secondary pins 6/7) | ckt 1 = silk triangle; DC-shorted through winding — identify by silk, wire bench pair straight |
| Power | **3.3V rail only** (header 1/17); 5V net dead-ends at header pads 2/4 | ADIN + DS3231 RTC both on 3.3V |
| Extras | DS3231 RTC on I2C + SQW→header 12; ADIN LED_0→header 13 (GPIO27), LED_1→header 7 (GPIO4); LED trace-jumpers NC (cut to disable); TEST1 pulled up 1.5k (R10, required per datasheet), TEST2 open | header nets PI_GPIO_25 (pin 22), CE1 (26), TXD (8), RXD (10) are dead ends — no conflicts |
| Silicon | hat #1 chip date code **#2204** | watch item: early revs reported failing probe with "PHY ID read: 0" (see S1 debug note) |

Overlay: `pi/overlays/aos-adin1110.dts` — SG overlay + GPIO22 internal
pull-up + MAC `02:ad:11:10:00:02` (second hat overrides to `...:03` at
runtime until the per-node config bite). 23 MHz kept (known-good, one
variable at a time).

**Validated on hardware (2026-08-10, hat #1 on nereus000):** Nick skipped
the meter pass and mounted the hat directly (his call — a successful probe
is stronger evidence). It probed first try, twice:

- Under the leftover **SG overlay** (software steps not yet run): probe at
  7.4 s, eth1, verify 5/5 — pinout identity with the SG shield confirmed
  live. Caveat: INT was floating (no pull anywhere) and happened to rest
  high; worked by luck, not design.
- Under the **AOS overlay** (installed + reboot): probe at 7.1 s, eth1 MAC
  `02:ad:11:10:00:02` (proves overlay active), driver ADIN1110, **PHY ID
  0x0283bc91** (SPEC match), IRQ 22 level count = 1 and stable (no storm,
  pull-up active), verify 5/5.
- Working register I/O also proves the straps conclusively: hat #1 IS in
  generic SPI no CRC mode (bridged CFG0+CFG1) — stronger than the meter
  check it replaced. Silicon watch item (#2204 date code) cleared: PHY ID
  reads clean.

### S2 detail (2026-08-10) — first T1L link: line rate, zero loss

nereus000 (Pi 5, hat #2, 192.168.7.1, MAC ...:02) ↔ nereus001 (Pi 5, hat
#1, 192.168.7.2, MAC ...:03 NM-cloned) over Nick's crimped pair, straight
ckt1↔ckt1. `bench/t1l_link_test.sh client` from nereus000:

| Check | Result | Gate |
|---|---|---|
| ping ×20 | 0% loss, RTT 0.788/0.843/1.651 ms min/avg/max | 0% loss |
| iperf3 TCP forward 10 s | **9.32 Mbps** | ≥ 8.0 |
| iperf3 TCP reverse 10 s | **9.33 Mbps** | ≥ 8.0 |
| iperf3 UDP 8 Mbps 10 s | 8.0 Mbps, **0% loss** | < 1% |

TCP at 9.3 Mbps = the full 10BASE-T1L usable rate (SPEC: ~9.3) — the link
adds no penalty on top of wire physics. UDP at the 8 Mbps video budget is
lossless. NM `t1l` profiles auto-activated on carrier as designed; MAC
clone on node 2 confirmed (`permaddr` still ...:02). Kernel skew between
nodes (6.18.34 vs 6.18.39) — no effect on interop, as expected for a
wire protocol.

**Hat #2 validated the same way (2026-08-10, swapped onto nereus000):**
probe at 8.6 s, eth1, PHY ID 0x0283bc91, IRQ quiet, verify 5/5 — straps
proven bridged by working register I/O. Both hats are good hardware.

Still open (flagged, not guessed): unexplained soldered wire/pin at J1
edge on hat #1; two bare copper rectangles top of back side; hat #2 date
code not yet recorded. nereus000 now runs an AOS hat; SG shield is on the
shelf (S1 restore = swap back + flip the two config.txt lines).

### S4 detail (2026-08-10) — AE3 first light: PHY ID over SPI (as-built)

Rig per D18 + D19: AE3 → hat #2, 7 data wires + AE3→hat ground; hat powered
from nereus000's 3V3 header; AE3 on nereus000 USB (dev loop runs fully
remote via `mpremote mount` from the Pi — same pattern as S0).

Driver start, `firmware/adin_drv/`, per the two-layer portability contract:

- `adin_spi.py` — portable protocol core (no `machine` imports): generic
  SPI no-CRC framing taken from vendored `adin1110.c:195-264` (read =
  7-byte full-duplex xfer, 3-byte header + turnaround, value BE32; write =
  2-byte header + BE32). `adin_regs.py` — constants with line citations.
- `adin_hal_ae3.py` — AE3 HAL: `machine.SPI(0)` @ 5 MHz mode 0, CS = P3
  manual GPIO (D2), RESET strobe per driver timing (10 ms low, 90 ms
  settle, bus quiet — adin1110.c:1101-1108), P5 input with internal
  pull-up (the D14 INT_N fix, AE3-side per D18). SPI is constructed
  before the CS Pin as a defensive pad-claim order (suspected SS-steal
  during debug; turned out to be miswiring — order kept as precaution).
- `s4_first_light.py` — demo + built-in no-LA fallback ladder (raw RX
  dump, failure-signature wiring hints, 5x stability check, STATUS0/
  CONFIG1 raw dumps for diffing against the live Linux node, clock retry
  sweep). 16 host unit tests (`test_adin_spi.py`).

**Result: `PHY ID: 0x0283BC91 — OK` at 5 MHz, first attempt once the
harness was actually wired right, repeatable.** The chip + straps +
protocol layer worked immediately; every failure on the way was wiring.

Bring-up war story, kept for the next rig: the hat header was counted
mirrored twice (off-Pi female header, easy to flip). Signatures seen:
all-0xFF with stray low bits = floating MISO reading crosstalk (the
stray bits vary with grounding — a solidly driven line doesn't);
MISO "echoing" fragments of TX = same. Debug tools built and kept:

- `s4_bus_probe.py` — no-SPI DC checks: hat rail detect via the board's
  own R28 100k RESET_N pull-up (drive P4 low, release to input, watch
  bounce-back), MISO pull-up-vs-pull-down float/drive test.
- `s4_bitbang_probe.py` — full PHY ID read in pure-GPIO mode-0 SPI;
  separates harness/chip faults from `machine.SPI` faults.
- Orientation validator that ended the mirror cycle: power jumpers only,
  meter hat pin 17 ↔ pin 6 — ~3.3 V only if the count is right (17 is
  the second 3V3 pin, same net as 1; a mirrored count lands on dead nets).

Caution recorded during debug (unproven on correct wiring, kept as a
watch item): probe interpretations on a miswired harness can look
plausible — the DC probe "passed" convincingly on two different wrong
harnesses because floating/coincidental nets mimicked the expected
responses. Trust a probe only after the wiring it assumes is verified.

### S3 detail (2026-08-10) — bite 1: AE3→Pi USB frame source, measured

Path: vendored legacy capture service on the AE3 (D15) → framed JPEG over
USB CDC → `pi/stream/usb_frame_source.py` → `bench/usb_stream_bench.py`.
Bench scene (indoor, compressible — real scenes 3–5× more bytes per S0 reef
data), 10 s/mode, board rebooted before each session (D15 workaround).
Identical numbers on stable v5.0.0 (`v1.28.0-49 / 2026-07-02`) and dev
`11852aa3d0` (2026-08-10); 677+ frames per full run, 0 seq gaps, 0 bad JPEGs.

| Mode | fps (free-run) | KB/frame | Mbps | notes |
|---|---|---|---|---|
| QVGA q50 | 47.0 | 2.6 | 1.00 | |
| QVGA q70 | 38.2 | 3.8 | 1.20 | |
| QVGA q80 | 37.5 | 5.1 | 1.57 | 30 fps mode candidate |
| **QVGA q90** | **35.7** | 8.6 | 2.51 | **chosen (D16), paced to 15 fps** |
| VGA q50 | 12.1 | 7.1 | 0.70 | encoder-bound |
| VGA q70 | 9.8 | 11.0 | 0.88 | encoder-bound |
| HD q50 | 2.9 | 24.0 | 0.56 | encoder-bound |

Hard facts established:

- **VGA ≥ 15 fps is unreachable on the AE3** — software JPEG encode
  (~70–85 ms/frame at VGA) is the bound, not transport; true on stable and
  dev firmware. Only levers: smaller frames (QVGA) or the N6's hardware
  encoder (iceboxed).
- `sensor.set_framebuffers(2)` in the stream loop makes everything WORSE
  (VGA q70 9.8→8.6 fps, QVGA q50 47→30) and hard-crashes HD — tested and
  reverted; the legacy repo's "VGA+ needs set_framebuffers(1)" note holds
  in-stream too.
- The one-session-per-boot firmware crash + reboot workaround (D15).
- USB CDC recovery ladder for a crashed AE3: `uhubctl -l 1 -p 2 -a cycle`
  on nereus000 → board may come back in safe-mode REPL (main.py skipped)
  → `machine.reset()` restores the service. The deeper crash flavor
  (enumeration error -71) needs a physical replug — Pi 5 port power
  switching doesn't truly cut VBUS.

### S3 detail (2026-08-10) — bites 2+3: pipeline across the pair, sustained

`t1l_sender.py` (nereus000) → TCP 192.168.7.2:8081 over the T1L pair →
`stream_server.py` (nereus001) → browser multipart MJPEG. One wire framing
project-wide (frame JSON header + JPEG; StreamParser both hops). Sender
re-sequences forwarded frames so receiver `gaps` = true transit loss.
Both ends systemd services (`pi/services/`, auto-restart; sender leg
self-heals: TCP reconnect → board reboot → new USB session, verified live
when the receiver was restarted under it).

Live end-to-end measurements (real bench scene, ~19–21 KB/frame at q90 —
2.4× the static-bench frames, as S0's reef data predicted):

| Setting | delivered fps | Mbps on pair | encoder surplus |
|---|---|---|---|
| q90 @ 15 fps paced | 14.9 | 2.4 | ~16 fps |
| **q90 @ 30 fps (D17)** | **30.8 rolling / 29.3 sustained-avg** | **4.6–4.8** | ~2 fps (thin; pacer rides source rate if a scene dips) |
| q80 @ 30 fps (fallback) | 30.4 | 3.0 | ~4 fps |

Sustained: **10 min 15 s, 18,032 frames, 0 gaps, 0 resets** — zero frame
loss across the pair at the demo setting. TCP over the T1L link adds no
measurable penalty at this load (4.8 of 9.3 Mbps line rate).

VGA demo modes, measured live over the pair (post-demo, same scene —
first VGA-across-the-pair numbers; requested by Nick for a future demo):

| Setting | delivered fps | Mbps | note |
|---|---|---|---|
| VGA q35, unpaced | 13.5 | 1.3 | **the VGA ceiling** — encoder-bound |
| VGA q50, unpaced | 11.7 | 1.45 | visibly better JPEG, ~2 fps cost |

VGA @ 30 fps stays impossible on the AE3 (software encoder, D16); the
pair itself is loafing in every VGA mode — VGA's constraint is compute,
QVGA q90@30 (4.6+ Mbps) remains the highest-load mode for exercising the
link and stays the standing test/demo setting per Nick (better hardware
stressor).

### S5 detail (2026-08-10) — AE3 raw-frame TX + loss measurement (as-built)

Rig: S4 harness unchanged + the crimped pair hat #2 ↔ hat #1 (nereus001 =
untouched live reference node). All new register/sequence facts cited from
the vendored drivers, none from datasheet transcription:

- **TX path** (`adin_spi.py`): TX_FSIZE + burst-to-TX-reg with 2-byte
  port header, pad-to-64-with-FCS, 4-byte rounding (adin1110.c:369-424,
  281-292); space accounting = TX_SPACE×2 bytes, need = len+4
  (adin1110.c:915, :995). Polled, no IRQs (bite-sized; IRQ TX is an S6
  option, not a need at these rates).
- **MDIO**: clause-22 via MDIOACC + TRDONE poll (adin1110.c:440-502);
  clause-45 MMD regs reached by C22 MMD-indirect (regs 13/14) — the same
  mechanism phylib uses over this C22-only bus, so hardware-proven.
- **PHY bring-up**: unconditional software-power-down exit + CRSM_STAT
  poll (adin1100.c:195-206), then PMA_STAT1 link poll (latched-low, read
  twice). Measured on silicon: autoneg is enabled by hardware default
  (7.512=0x1000) with a correct T1L advertisement (7.514/5/6 =
  0x0001/0x4000/0x3000) — no AN configuration needed at all.
- **Test frame format** (`s5_frames.py`, mirrored by
  `bench/frame_counter.py`): unicast to nereus001's eth1 MAC (passes its
  hardware MAC filter), EtherType 0x88B5 (IEEE local experimental),
  magic `BMS5` + BE32 seq at offset 18.
- **Clocking**: bring-up stays at 5 MHz; the load path runs 20 MHz
  (S0-proven, 0 errors) after a PHY-ID gate with loud 5 MHz fallback.

Results: bite 1 — 200/200 × 500 B frames in a tcpdump pcap on nereus001,
in order, correct headers, zero loss (5 MHz, 380 fps, 1.5 Mbps). Bite 2 /
sprint demo — 60 s @ 20 MHz: **31,592/31,592, 0% loss, 0 dupes/ooo,
526 fps, 4.21 Mbps payload**, 0 FIFO stalls, SPI_ERR clear. 4.21 Mbps
delivered ≥ the ~4 Mbps D8 budget with the T1 stream target (~2 Mbps)
under 2× headroom — the MicroPython driver is not the S6 blocker.

Bring-up war story (half the session): first link attempts failed with
both PHYs register-perfect and both sides stone deaf — root cause was a
**bad pair connector** (found by Nick at the bench). Lessons recorded:

- Two healthy, advertising, AN-enabled PHYs that never see each other's
  energy = analog path fault; no amount of register work fixes it. The
  registers that cleared software suspects: 7.512 (AN on), 7.514-6
  (advertisement), 7.0x8000 (forced-mode OFF), 1.0/1.2294 (no low-power),
  bilateral watch (nereus001 carrier + dmesg silent during 3-min AN run).
- **Continuity checks on powered boards read OL / garbage** — the
  aos_hat_checklist "unpowered" precondition is load-bearing. First OL
  readings on both hats' J1 were artifacts of measuring live boards.
- Meter checks that split power vs pair: 3V3 at hat pins 17↔6 *during*
  an AN transmit session (read 3.276 V = rail fine under line-driver
  load), then cable-only continuity with the pair unplugged at both ends.
- Blue LEDs on the hats = link/activity: dark during the hunt, back on
  at first link. Red = power.

Fixture notes: t1l-sender is an *enabled* boot service on nereus000 — it
reclaims the AE3 USB port on every reboot; stop it before mpremote work.
tcpdump is now installed on nereus001. `/tmp/s5_bite1.pcap` on nereus001
holds the bite-1 capture artifact. *(S6 update: t1l-sender is now
`disabled` — the S6 path replaces it; re-enable to restore the USB-era
S3 fixture.)*

### S6 detail (2026-08-10) — video over the pair into the frozen server (as-built)

Rig: S4/S5 harness + pair unchanged. Path: AE3 `s6_video_tx.py` →
20 MHz SPI → hat #2 → T1L pair → nereus001 hat #1 → `chunk_shim.py`
(systemd, CAP_NET_RAW as pi) → TCP localhost:8081 (the FROZEN S3 ingest,
byte-untouched) → browser at `http://nereus001-1:8080/stream` (that
hostname is the tailnet MagicDNS name — same URL from any tailnet
subnet; server binds 0.0.0.0, verified from off-subnet).

- **Wire protocol** (`s6_video.py`, portable): Ethernet 0x88B5 + magic
  `BMV6` + BE32 frame_seq + BE16 chunk_idx/chunk_count/payload_len,
  1400 B payload/chunk. payload_len is in-header because runt chunks
  arrive zero-padded to the 60 B Ethernet minimum. Reassembler bounded
  at 4 partial frames (oldest evicted = counted incomplete).
- **Frozen-interface fact:** the server reads only `seq` + `size_bytes`
  from the ingest header (stream_server.py ingest_loop); width/height
  are protocol filler, sent 0. Shim re-sequences output and carries
  out_seq across TCP reconnects, so server `resets` = producer restarts.
- **Bite-1 counter proof** (60 s, q50, dark scene): 2422/2423 complete
  (1 = window-edge partial), 0 lost, 0 dupes, 0 bad JPEGs, 40.4 fps.
  End-to-end (shim → server): 2622/2622 exact, 0 gaps.
- **Timing split** (QVGA, dark scene ~2.1 KB frames): capture 3.1 /
  encode 17.4 / tx 4.2 ms per frame, 0 FIFO stalls. tx scales at
  ~2.0 ms/KB across q35–q90 (pure byte cost). Basis of D21.
- **Quality ladder** (30 s rungs, 20 MHz, ALL 0 loss — dark night
  scene, 3–5× under real-scene bytes, NOT gate numbers):
  q35 45.2 fps/1.85 KB · q50 40.4/2.08 · q70 32.6/2.76 · q80 32.1/2.91 ·
  q90 31.3/3.33. Real-scene projections (S0 reef + S3 live bytes):
  q50 ≈ 24 fps serialized (at the T1 gate edge), q90 ≈ 14 fps (tx-bound,
  D20). Lit-scene rerun decides the standing quality.
- **Link-outage behavior** (remote `ip link set eth1 down` 10 s, then
  up): stream freezes and auto-resumes; sender drains into the dead
  wire stall-free (D21); ~484 frames wire-dropped silently, whole —
  shim saw 0 partials, server gaps stayed 0 (re-sequenced), receiver
  counters are the loss ledger. `AdinError` catch + `link_up()` wait
  kept in the TX loop as belt-and-suspenders for a true FIFO-fill.
- **Lit-scene gate ladder (2026-08-11)** — the T1 verdict runs, 60 s
  counter windows, all PASS (0 lost, 0 bad JPEGs):

  | q | fps | KB/frame | enc ms | tx ms | verdict vs ≥24 fps |
  |---|-----|----------|--------|-------|--------------------|
  | 50 | 32.2 | 4.5–5.0 | 18.3 | 9.4 | PASS, ~8 fps margin → **standing (D20)** |
  | 60 | 25.9 | 6.0–6.4 | 23.3 | 12.1 | pass, thin margin |
  | 70 | 24.2 | 7.1–7.6 | 23.6 | 14.4 | gate-edge, zero margin |

  Scene caveat: this bench scene ≈ half the reef-anchor bytes (9.2 KB
  @ q50), so q50 on a deployment scene projects to ~24 fps — the gate
  holds, without margin. q35 is the fallback lever.
- **D15 watch item:** one REPL-wedge occurred after a hard reset between
  ladder rungs (board enumerated, raw REPL dead — the S3-documented
  crash class). The documented recovery worked without hands:
  `sudo uhubctl -l 1 -p 2 -a cycle` on nereus000 → REPL back → resume.
  Otherwise no recurrence across ~15 sensor sessions; board reset
  between runs kept as ritual.
- Board firmware deprecation warning appeared: `sensor` module →
  `csi` module "in a future release". Watch item for the next firmware
  bump; all project scripts use `sensor`.
holds the bite-1 capture artifact.

### S7 detail (2026-08-11) — headless AE3 flash path: facts + tooling (pre-hardware)

Research + tooling bite for the S7 first spike, done with **zero board
contact** (S6 fixture live). Every fact below is from reading source or
probing URLs, not from touching hardware; items needing a live board are
listed as flash-day checks in `pi/ae3_flash/README.md`.

**Boot/flash protocol (openmv.git @ master 2026-08-11, micropython.git):**

- Boot order on every power-up: OpenMV bootloader (`boot/`) runs before the
  app, enumerates as USB DFU **VID:PID 37C5:96E3**, waits ~1 s for USB mount
  then 1.5 s (`OMV_BOOT_DFU_TIMEOUT`, AE3 `boot_config.h:44`) for a DFU
  attach, else jumps to the app (`boot/src/common/main.c:55-113`).
- Software bootloader entry: `machine.bootloader()` → AE3 board hook writes
  `0xB00710AD` to `0x200FFFFC` and calls `NVIC_SystemReset()`
  (micropython `ports/alif/boards/OPENMV_AE3/board.c:107-115`); bootloader
  reads+clears the magic and then ignores the DFU timeout (stays until
  reset). Backup entry: OpenMV IDE CDC protocol opcode `SYS_BOOT` 0x11
  (`protocol/omv_protocol.h:150`) — not implemented in our tool (YAGNI).
- Partitions = named DFU alt settings: `BOOT HP HE ROMFS1 TOC RWFS ROMFS0
  RECOVERY` (`boards/OPENMV_AE3/boot_config.h:101-112`). App firmware =
  `HP` (+ `HE` for the second core; staff-confirmed HP-only suffices for
  single-core use, we flash both to avoid version skew). Plain `dfu-util`
  (Debian arm64) speaks this. **`BOOT` is never written by our tooling** —
  un-brickable at the app level; power cycle always re-opens the window.
- Firmware self-identifies: **`sys.version`** reads
  `"3.4.0; OpenMV <id>; MicroPython <id>"` — corrected on flash day from
  the pre-hardware guess of `os.uname().version`, which carries only the
  MicroPython id. `<id>` is a sha10 on dev builds (`7d4dbf7ab2`) but a
  version tag on tagged releases (`v5.0.0`) — both verified live.
  Flash verification = compare against the build manifest.
- Deep recovery (bootloader itself corrupted — outside our loop): Alif
  SE-UART ISP via `tools/alif` (micropython/alif-security-toolkit,
  `app-write-mram.py` + ATOC `firmware.toc`); on the AE3 the SE UART
  reaches USB only in recovery mode = front switch (hands) or B2B RECOVERY
  pin low (board mod, declined — D22).

**Artifacts:** every openmv release ships `firmware_OPENMV_AE3.zip`
(`firmware_M55_HP/HE.bin`, `bootloader.bin`, `romfs0/1.img`,
`firmware.toc`); stable `v5.0.0` = the board's current firmware,
`development` = rolling. Contents verified by download 2026-08-11.

**Build (D23):** openmv.git `docker/Makefile build-firmware TARGET=
OPENMV_AE3` in an ubuntu:24.04 container; SDK pinned by `SDK_VERSION`
(1.6.0), published linux-x86_64 + darwin-arm64 only → Mac builds the amd64
container under Rosetta. `make deploy` in `ports/alif/port_config.mk` is
the SE-UART flash path (unused here but exists).

**Tooling shipped this bite:** `firmware/openmv_build/` (Mac: `setup_mac.sh`,
`build_ae3.sh` → sha256'd artifacts + `MANIFEST.txt` with `openmv_sha`) and
`pi/ae3_flash/` (`flash_ae3.py` ladder with preflight/t1l-sender refusal/
PASS-FAIL verdict + `--dry-run` + `--recover`, `fetch_firmware.sh`, udev
rule, 16 host unit tests). Docker/VS Code/IDE setup facts probed on the
Mac 2026-08-11: arm64, brew present, VS Code installed (no `code` CLI),
no docker yet (cask `docker-desktop`), no OpenMV IDE cask (dmg only).

**Flash-day results (2026-08-11, after S6 demo pass — Nick's go):** the
round-trip demo PASSED entirely from the nereus000 CLI. Board went dev
`7d4dbf7ab2` → `v5.0.0` → back to `7d4dbf7ab2`, sys.version verified after
each leg; leg 2 ran the shipped ladder end-to-end green including its own
PASS verdict. HP download 2,200,784 B / HE 1,185,744 B, clean
`dfuMANIFEST → dfuIDLE` both legs. Live findings folded into the tooling:

- dfu-util `-R` exits non-zero (251) even on success — the device drops
  off the bus during the USB reset. The reset invocation is now
  `check=False`; CDC re-enumeration + sys.version match are the success
  signals. (dfu-util's "Invalid DFU suffix" warning is expected — OpenMV
  bins carry no DFU suffix.)
- mpremote exits with an I/O-error traceback when `machine.bootloader()`
  drops the connection — expected success signature, output now captured.
- Tagged releases ship ONE combined `firmware_<tag>.zip` (all boards);
  per-board `firmware_OPENMV_AE3.zip` exists only on the `development`
  tag. `fetch_firmware.sh` handles both.
- The board had been running dev `7d4dbf7ab2`/`11852aa3d0` since the D15
  crash-hunt — S6 passed on the dev build, and the round trip restored
  exactly that state.
- ROMFS partitions were NOT reflashed; both builds booted fine on the
  installed images. Re-check on bigger version jumps (release zips carry
  `romfs0/1.img` if needed).
- Not exercised: `--recover` (uhubctl) — installed, untested, hub
  location/port for the AE3 still unverified on the Pi 5.
- udev rule `99-openmv-dfu.rules` (VID 37c5 → plugdev) makes the whole
  ladder sudo-free; dfu-util 0.11 from Debian arm64 works as-is.

### S8 detail (2026-08-11) — AE3 NPU inference bench, bite 1 (early ride)

Run under the TRACKER's S8 exception (NPU bench as board-selection input);
the rest of S8 stays gated behind S13. Setup: `bench/ae3_npu_bench.py` via
`mpremote mount` from nereus000, reef ref scene (P7071008 derivative,
`bench/assets/ref_scene`), **no sensor** (D15 class avoided), nothing
flashed. Models discovered live from `/rom`; 10 timed reps after 2 warmups;
ms/inference includes image→tensor preprocessing (the real `predict()` path).
Firmware self-reports `OpenMV v5.0.0` — per Nick this is a stale label on
the in-development build, not a reflash; treat as the dev-build fixture.

Per-model `predict()` latency (reef scene; 320×200 input ≈ tile cost,
1280×800 shows preprocessing growth):

| model (input) | arena B | ms @320×200 | ms @1280×800 | 1-pass fps |
|---|---|---|---|---|
| blazeface_front_128 (128²) | 88,704 | 11.1 | 14.5 | 69.2 |
| face_landmarks_192 (192²) | 146,304 | 14.1 | 21.5 | 46.5 |
| fomo_face_detection (96²) | 29,440 | 2.7 | 4.8 | 209.2 |
| hand_landmarks_full_224 (224²) | 1,176,896 | 57.6 | 65.1 | 15.4 |
| movenet_singlepose_192 (192²) | 143,360 | 21.5 | 28.8 | 34.7 |
| palm_detection_full_192 (192²) | 323,200 | 30.0 | 37.4 | 26.8 |
| person_detect (96²) | 48,384 | 2.8 | 4.9 | 206.0 |
| yolo_lc_192 (192²) | 34,560 | 4.0 | 8.9 | 113.0 |
| yolov8n_192 (192², YoloV8 pp) | 195,648 | 21.1 | 28.4 | 35.2 |

(`force_int_quant.tflite` correctly SKIPped — non-image input `(1, 36)`.)

HD (1280×800) full-frame detection, tiles at 32 px overlap, tile cost =
the 320×200 latency: yolov8n_192 = 40 tiles → 843 ms/frame → **1.19 fps
(BELOW the T2 ≥3 fps gate)**; yolo_lc_192 = 40 tiles → 159 ms → **6.27 fps
(MEETS)**; everything else 0.6–1.8 fps (below). Single-pass downscale is
30–200+ fps everywhere but shrinks 100–150 px fish to 8–26 px, at or
below the 24–32 px detection floor for every ≤192-px-input model.

**Findings that carry:**

- The NPU is not the T2 bottleneck — per-tile latency is excellent. The
  bottleneck is coverage arithmetic: tiles × ms/tile. Levers: larger
  model input (fewer tiles), lighter architecture (yolo_lc-class cost),
  or reduced coverage (ROI scheduling).
- Label files read live: `yolov8n_192` and `yolo_lc_192` detect ONE class
  (`person`); `person_detect` is a person/no-person classifier. "0 det"
  on the reef scene is therefore the correct artifact, and latency is
  class-independent — but **any real T2 detector is a custom-trained,
  Vela-compiled model** (Mac docker env, D23 territory). Nick: needed
  either way.
- NPU-vs-CPU dispatch is not queryable from MicroPython — these are
  wall-clock numbers; attribution unverified (flagged in the script
  header).

### S8 detail CORRECTION (2026-08-11, same day) — first table was the N6

The table above was measured on the **OpenMV N6** (`/dev/ttyACM0` on
nereus000), not the AE3: bare `mpremote` auto-connects to the first CDC
device, and nereus000 carries BOTH boards. Diagnosed during S9 bring-up
(device-identity check before flashing); the "OpenMV v5.0.0" string and
25.6 MB heap were the missed tells. Re-run on the AE3
(`/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00`,
fw 7d4dbf7ab2, free heap 3.9 MB), same script, same ref scene:

| model (input) | AE3 ms @320×200 | AE3 tiled@HD fps | (N6 ms / tiled fps) |
|---|---|---|---|
| blazeface_front_128 (128²) | 17.0 | 0.6 | (11.1 / 0.9) |
| face_landmarks_192 (192²) | 29.3 | 0.9 | (14.1 / 1.8) |
| fomo_face_detection (96²) | 1.7 | 2.4 | (2.7 / 1.5) |
| hand_landmarks_full_224 (224²) | 21.5 | 1.7 | (57.6 / 0.6) |
| movenet_singlepose_192 (192²) | 28.1 | 0.9 | (21.5 / 1.2) |
| palm_detection_full_192 (192²) | 43.7 | 0.6 | (30.0 / 0.8) |
| person_detect (96²) | 4.9 | 0.9 | (2.8 / 1.5) |
| yolo_lc_192 (192²) | 4.9 | **5.1 MEETS** | (4.0 / 6.3) |
| yolov8n_192 (192², YoloV8 pp) | 26.3 | 0.95 BELOW | (21.1 / 1.2) |

All T2 conclusions survive re-attribution: the gate is met only by the
lc-class detector; yolov8n-class needs a larger input to cut tiles.
Caveats: the two boards' ROMFS carry **different model binaries** (e.g.
yolov8n_192: 1,994,976 B on AE3 vs 3,233,408 B on N6; AE3 additionally
ships audio models) — cross-board numbers are model-variant-confounded,
useful as platform points, not a silicon shoot-out. AE3's
hand_landmarks being 2.7× faster than N6's is that confound in action.

Standing bench rule adopted: on nereus000, always
`mpremote connect /dev/serial/by-id/...` — never rely on auto-connect.

### S9 detail (2026-08-11) — bite 1: OA first light, spike PASSED

**Result: bm_core's adin2111 OA driver (vendored @ d4ecc38, byte-identical)
reads our ADIN1110's PHY ID through its own framing.** Final on-target run
(hat #2 straps opened, `--no-prot` build): verdict 1 `read=SUCCESS
PHYID=0x0283BC91`; verdict 2 `adin2111_Init = COMM_TIMEOUT` at
waitDeviceReady's PHYID==0x0283BCA1 poll — the identity gate,
source-predicted (adi_mac.c:568/1128) and demonstrated compiled in the
host harness before any hardware.

**The complete 1110-vs-2111 delta list (bite-1 scope):**

1. **OA control protection unavailable on our 1110.** Chip comes up
   PROTE=0 and the bit rejects writes (plain + with CONFIG0.SYNC; IMASK0
   and other CONFIG0 bits write fine — measured, reproducible). bm_core
   ships `CONFIG_SPI_PROT_EN` defined → protected framing → reads decode
   garbage against this chip (and the driver *swallows*
   PROTECTION_ERROR on control reads — host test [4] pins that quirk).
   Fix for 1110 bench work: build with the define REMOVED (driver tests
   defined-ness, not value — a `=0` build is byte-identical, sha-proven);
   `build_spike.sh --no-prot`. Production 2111 presumably keeps
   protection (Sofar's shipped default) — Sofar question queued.
2. **`RSTVAL_MAC_PHYID` (0x0283BCA1)** — blocks every init path
   (MAC-layer AND full init; the gate lives in MAC_Init → MAC_Reset →
   waitDeviceReady, NOT just adin2111-level init).

**Debug ladder that got there (kept for reuse, `~/ae3_flash/` on
nereus000):** `s9_raw_probe.py` (mirror-vs-echo discriminator: replay the
driver's exact control frame with distinctive padding; a miswire mirrors
padding, a real OA chip echoes only the header) → `s9_matrix.py`
(padding × length behavior matrix; surfaced the chip parsing follow-on
MOSI words as new control headers — bad-parity words draw the 0x40000000
HDRB reply) → `s9_regs.py` (CONFIG0/PROTE authoritative readback; also
IDVER=0x10, CAPABILITY=0x6C3, and a clean 2-word auto-increment read) →
`s9_wrtest.py` (write-path proof + PROTE dead-bit isolation). Plus one
mid-hunt hardware fix by Nick: CFG0 strap pad wasn't fully cleared on the
first rework (chip answered in OA-no-protection framing; razor + meter
fixed it — though PROTE stayed 0 regardless, see SPEC open question; one
never-reproduced protected-mode complement sighting recorded as anomaly).

**Build-env facts (D23 leg, first real exercise):** docker-on-Mac works
end-to-end (image build under Rosetta, SDK 1.6.0 plumbing, MANIFEST).
Two traps found and fixed in `build_spike.sh`: staged-header changes can
reuse stale objects (stage/unstage defeats make deps — `--clean` flag,
sha-compare to verify) and the `modules/` wildcard compiles usermods into
BOTH cores (HE can't fit the spike → `#if !defined(CORE_M55_HE)` guards,
vendored files staged as `.inc` behind generated wrappers). Open
environmental issue: the **M55_HE image does not link in our env at any
rev** (FLASH_TEXT 154% with our code fully excluded — reproduced on
master and 7d4dbf7ab2, clean trees). Workaround in use: flash HP only at
the exact rev of the installed HE image (7d4dbf7ab2) → no core skew.
Debug deferred; needed before S10 (bm_core runs ON the HE core).
→ **RESOLVED 2026-08-11 (D24):** stock `build-firmware` docker target
passes `BUILD=` on the make command line, flattening the per-core build
dirs — HE was linking HP's objects. `build_ae3.sh` now uses
`build-firmware-dev`; HE links (1,193,520 B, matches official ~1.19 MB).
HP-only workaround retired; both-core flashing restored.

**Fixture note:** the AE3 currently runs the spike HP build
(`v5.0.0-52.g7d4dbf7ab2`, sha256 921cdd03…). Restore to stock dev =
reflash `7d4dbf7ab2` HP via the S7 ladder. Hat #2 is strapped OA
(default) — re-bridge CFG0+CFG1 to return to the S6 generic-SPI baseline.

### S9 detail (2026-08-11) — bite 2: Alif-native ADI-HAL (as-built + measured)

**Result: adi_hal.h implemented against the Alif silicon directly —
`firmware/bm_spike/src/bm_spike_hal_alif.c` — and the full bite-2 demo
(`s9_hal_native.py`) PASSES repeatably: PHYID over the driver's OA framing
with zero MicroPython objects in the transfer path, INT_N → NVIC → driver
callback proven live, 2× the mp-HAL control-read rate at 5 MHz and
scaling with clock.** DMA deferred to S10 (Nick's call at plan approval;
`useDma` accepted and ignored; SPI_DMACR hooks documented in spi.h).

**Bench (2000 PHYID round trips through the unmodified driver, init
excluded, run-to-run repeatable):**

| HAL | SPI clock | reads/s | µs total | fails |
|---|---|---|---|---|
| mp (bite 1) | 5 MHz | 22,886 | 87,389 | 0 |
| alif | 5 MHz | 45,895 | 43,577 | 0 |
| alif | 10 MHz | 83,794 | 23,868 | 0 |
| alif | 20 MHz | (127,852) | 15,643 | **2000 — garbage** |

The mp HAL is call-overhead-bound (2× clock ≈ no gain); the native
FIFO-burst engine scales with clock. Engine: full-duplex, ≤16 frames in
flight (both machine_spi.c AND Alif's own `spi_transfer_blocking` are
per-word lock-step — the D8 ceiling lives at both layers; ours is the
only FIFO-depth user). Init recipe mirrors machine_spi 1:1 incl.
`spi_control_ss` (SER — DW won't clock without it) and SSTE off. Facts:
P0/P1/P2 = P5_1/P5_0/P5_3 = SPI0 MOSI(AF4)/MISO(AF4)/**SCLK(AF3)**;
CS = P5_2 GPIO (D2); INT = P0_4 → GPIO0_IRQ4_IRQn(183); bases per
global_map.h; SPI0 clock = GetSystemAHBClock(), always on.

**20 MHz OA finding:** reads decode garbage (phyid=0) at 20 MHz while
the same electrical path ran generic-SPI at 20 MHz in S5 → OA-mode
MISO timing, not wiring; RX_SAMPLE_DELAY (currently 0, as machine_spi)
is the first knob — bite-3/S10 tuning item. WORSE: misclocked MOSI can
decode as VALID control writes — one 20 MHz rung flipped CONFIG0.PROTE
to 1 (chip then drops unprotected writes + latches CDPE while reads
stay clean; recovered via protected-framed soft reset). SPEC §Open
questions amended; the runner now sanitizes (both-framing soft reset +
CONFIG0==0x06 verify) before gating checks and re-sanitizes at exit,
and the 20 MHz rung runs LAST, gating nothing.

**INT_N semantics (measured, was flagged-unverified in the plan):**
asserted from power-up (RESETC pending; post-reset IMASK0 = 0x1FBF =
RESETC unmasked); stays asserted until STATUS0 is W1C'd; STATUS0.LOFE
relatches continuously on this bench (live far side on the pair) and
must be masked for INT_N to rise; the deterministic falling edge =
W1C + chip soft reset (RESETC relatches). IRQ delivery rides
machine_pin.c's GPIO0_IRQ4Handler dispatch (vector table is const in
MRAM; the handler symbol is machine_pin's) into a hard-mode C trampoline
→ `HAL_RegisterCallback` target. Two scaffolding realities, documented
in the runner: the driver's FAILED-init exits (expected — identity gate)
leave the NVIC line disabled via HAL_DisableIrq (re-arm after driver
calls; real inits re-enable it themselves, adi_mac.c:986/1076), and
machine_pin exposes edge triggers only (falling-edge suffices for the
proof; native level-low conversion is a bite-3 option).

**Two rig/firmware lessons (both bit us live):**
1. **P4 reset line is a no-op** — register scratch survives a 50 ms
   pulse. Never previously verified; SPEC §Open questions + bench check
   for Nick. Chip soft reset (reg 0x003) is the only working reset, and
   chip state persists across every board flash/reboot (always-on 3V3).
2. **C statics survive MicroPython soft resets** — a bench MAC handle
   carried across `mpremote` sessions benched all-fails at every speed;
   `bm_spike.fresh()` (drops + zeroes the handle) now leads every
   runner.

**Build/regression:** `build_spike.sh --hal mp|alif` stages exactly one
HAL (same symbols); mp remains default and the bite-1 runner passes
unchanged on a final-source mp build. Host tests 10 → 16 (bench
plumbing). Both HP images build post-D24; flashing stays HP-only at the
pinned rev (installed HE untouched, no skew).

**Fixture note:** AE3 runs the bite-2 alif HP build (MANIFEST sha
recorded in `~/fw/spike-alif-7d4dbf7ab2/` on nereus000); hat #2 still
strapped OA; chip exit-sanitized (CONFIG0=0x06). Restore ladder
unchanged from bite 1. *(Superseded by bite 3 — see below.)*

### S9 detail (2026-08-11) — bite 3: OA data-path smoke (nibble 2 + partial rehearsal)

**Code (driver still byte-identical):** `firmware/bm_spike/src/
bm_spike_datapath.c/h` — the **init bridge** that supplies exactly what
the driver's failed init path skips on a 1110: (1) `macDriverEntry.Init`
tolerated at COMM_TIMEOUT (identity gate, bite-1 fact); (2)
waitDeviceReady replica polling OUR PHYID + RESETC W1C (mirror of
adi_mac.c:1107–1157); (3) macInit replica — IMASK0/1, STATUS reads,
CONFIG0.TXFCSVE clear, CONFIG2.CRC_APPEND set, shadow irqMask fields
kept consistent (adi_mac.c:581–703); (4) the one-line state nudge
INITIALIZED → READY (the field lives in spike-owned `pDevMem`); (5) the
driver's own `PHY_Init` (MDIO addr 1) via wrappers over
`macDriverEntry.PhyRead/Write`; (6) `SyncConfig` then
`ExitSoftwarePowerdown` (bm_adin2111.c:327's enable order —
`adin2111_EnablePort` is verbatim ExitSoftwarePowerdown). TX =
`macDriverEntry.SubmitTxBuffer` with a single static BufDesc; the
synchronous HAL completes the whole OA data transaction inline
(spiCallback recursion, proven since bite 1), so TX needs no IRQ path.
Python: `dp_init/dp_link/dp_send/dp_stats` (both HAL tables);
`fresh()` also drops the dp handles. Runner `s9_oa_datapath.py` (S5
frame format inline → both S5 receivers work unchanged).

**1110-vs-2111 delta item 3 (for S13):** even past the identity gate,
`adin2111_Init` waits on a port-2 PHY at MDIO addr 2
(adin2111.c:169–180) which a 1110 lacks — a 1110 port of bm_core must
drive the MAC/PHY-layer driver entries directly (what the bridge does).
Host test [8] proves the bridge degrades to the plain driver sequence on
a 2111 identity (no nudge).

**Host tests 16 → 41** (`host_test/`): mock grew writable MAC regs, a
MDIOACC engine over a clause-45 PHY model (DEVIDs, powerdown handshake,
AN), and OA data-chunk parsing (per-chunk footers SYNC=1/TXC=31/odd
parity; byte-exact TX frame capture). New: [6] bridge rungs on a 1110,
[7] TX chunk math byte-identical at 500 B/61 B + sub-minimum refusal,
[8] 2111 degradation, [9] loud PHY-identity refusal.

**Rehearsal (Claude, 2026-08-11, fixture live):** builds green (alif HP
2,219,008 B carries dp code; HE byte-count unchanged vs D24 reference —
guards hold; mp regression build compiles). Flash via S7 ladder PASS.
**On-target: the entire init bridge PASSES first try — rungs 1–6 all
SUCCESS, PHYID 0x0283BC91 via replica, DEVID 0x0283/0xBC91 through the
driver's PHY layer = MDIO-over-OA proven (the bite's flagged new
surface), SyncConfig + powerdown-exit clean.** Then: **link never
comes up (60 s+, PMA_STAT1 stuck 0x0002, AN_STATUS stuck 0x0008 = AN
able, no pages).** Isolation ladder, one variable at a time: (a)
S5-minimal sequence (exit SWPD only, raw C45 MDIO over the bench-handle
MDIOACC passthrough) — also no link → NOT the driver's phyInit extras;
(b) far side bounced + inspected (`ethtool`: advertising 10baseT1L,
AN on, master-slave unknown = sees no partner either); (c) **LOFE
relatch probe: STATUS0 fully quiet after W1C** vs bite-2's measured
continuous relatch from far-side energy, same chip/straps/probe →
**no energy on the pair. Physical medium fault (pair unplugged since
the bite-2/S6-demo bench work?) — bench check flagged for Nick.**
Chip register state measured en route (post-reset defaults): AN_CONTROL
0x1000 (AN_EN set by default — validates S5's assumption), AN_ADV
0x0001/0x4000/0x3000, CRSM_STAT 0x0007 → 0x0015 after SWPD exit.

**Fixture note:** AE3 now runs the **bite-3 alif HP build**
(`~/fw/spike-dp-alif-7d4dbf7ab2/` on nereus000, MANIFEST alongside;
byte-verified flash). Hat #2 strapped OA; chip exit-sanitized. Debug
helpers added to `~/ae3_flash/`: `s9b3_debug.py` (bridge + 30 s link
hold), `s9b3_mdio_diag.py` (raw C45 MDIO dump + S5-minimal replay).
Restore ladder unchanged.

### S10 detail (2026-08-12) — bite 1: FreeRTOS on M55_HE + OpenAMP pipe (INTERIM 1, USB-only)

**Result: all three verdicts PASS, twice, identically — the BM-native
arc's platform assumption holds.** FreeRTOS (V11.3.0, GCC ARM_CM55_NTZ
port) runs on the HE core serving an rpmsg endpoint; the HP↔HE pipe
clears the ≥5 Mbps gate with huge margin; the HE core demonstrably owns
SPI0 + its NVIC line. NOTHING WAS FLASHED — the app is runtime-loaded
(`openamp.RemoteProc` ELF load into SRAM9_B) and recovery is a stop or
power cycle. The "bm_core on HP alongside MicroPython" fallback is MOOT.

**Throughput (receiver-counted, seq+CRC on every frame):**

| Path | Rate | Notes |
|---|---|---|
| rung 0: stock py↔py, HE pump (C under `ept.send`) | **219 Mbps** | 57k × 480 B msgs/s, flat 10 s; zero custom firmware |
| our stack: HP→HE (python sender) | 13.2 Mbps | 17.2k msgs/5 s, 0 crc errs, 0 gaps — HP-python-bound |
| our stack: HE→HP (python rx callback) | 5.6 Mbps | 20,000/20,000, 0 bad — HP-python-bound |

The gate rides the fabric (rung 0), not the Python ends; S12's real
producer/consumer on the gated hop is C on both sides.

**Verdict C (HE owns SPI0):** pinmux writes from HE land and read back
(af=4 + padctrl verified via pinconf_get), machine_spi-recipe init runs
against SPI0 registers, and SPI0's IRQ (137) fires on the HE NVIC
(RXFIM, counted). RX-with-real-data is deferred to hardware day: see
facts (3)/(4) below.

**Hardware facts measured this bite (all live, none guessed):**

1. **vring roles are the reverse of modopenamp.c's comment**: rsc
   vring0 (0x60001400) is the ring the host pre-fills with 64 empty
   buffers = the REMOTE'S TX; rsc vring1 (0x60000400) carries host
   sends (avail flags = NO_INTERRUPT) = the REMOTE'S RX. Matches
   open-amp's host-role vq mapping; the "VRING0 host to remote" comment
   refers to notify IDs.
2. **Descriptor .addr fields are offsets** relative to SHM base + 1 KB
   (first pool buffer = 0x2000 → 0x60002400), not absolute addresses.
3. **used.len is a capacity contract, not a message length**: the
   open-amp host recycles rx buffers with used.len as their NEW
   capacity (message length travels in the rpmsg header; stock remotes
   report `virtqueue_get_buffer_length()` = capacity,
   rpmsg_virtio.c:433). Reporting message size shrinks buffers
   permanently — found live as a pump stall once all 64 buffers had
   carried one small message; the host-test harness now reproduces the
   recycle semantics so this class is caught off-target.
4. **SPI0's DW SRL loopback (CTRLR0 bit 13) is tied off** on this
   instance — reads 0 after writing 1 with the controller disabled → no
   internal loopback on this silicon config.
5. **Kick suppression loses the wakeup race**: honoring
   VRING_AVAIL_F_NO_INTERRUPT on the remote's TX ring throttled the
   pump to ~1 msg/s (host toggles the flag while draining); the remote
   now kicks unconditionally on TX (spurious MHU word ≈ µs).
6. **PADCTRL_DRIVER_DISABLED pulls do not steer an AF-mode input on
   this pad**: AE3 P1/MISO reads 0xFF under BOTH pulls with the pin
   verifiably unconnected (bench check answered by Nick 2026-08-12:
   nothing wired to the board) and the pinconf writes verifiably
   landing (readback ok). The pad floats/reads high regardless →
   the pull-based RX self-test cannot work here; it stays a non-gating
   diagnostic. First ADIN PHY-ID read from HE is the real RX proof.
7. MHU doorbell = one 32-bit word on the RTSS MHU pair
   (HP→HE RX 0x40080000/IRQ 41, HE→HP TX 0x40090000; value ignored by
   both receivers); ~37k doorbells exchanged per bench run, no losses.

**Design shape (why no open-amp on the HE side):** the host's SHM
layout is pinned by the flashed firmware (rsc @ 0x60000000, 2×64×512 B),
so the remote is a ~250-line explicit vring/rpmsg implementation
(`rpmsg_remote.c`, host-testable with the target's 32-bit address
arithmetic) instead of a libmetal+open-amp port whose glue would exceed
the protocol. App lives at 0x60080000 (SRAM9_B upper half — provably
untouched: HP's .gpu_memory ends exactly there in the D24 maps; SE
boot_cpu passes non-TCM addresses through unchanged). Status page at
0x600BFF00 peekable from HP via machine.mem32 — the first debugging
stop, and how both live bugs were found. Host tests: 29 checks, clang
+ASan, fake-SHM host driver with measured-true recycle semantics, ring
wrap ×3, restart-resume.

**Fixture note:** unchanged from S9 bite 3 (HP runs the bite-3 alif
build; nothing flashed this bite). `~/he_spike/` on nereus000 holds the
ELF + runner; `/flash/he_spike.elf` on the board VFS. HE core left
STOPPED after each run.
