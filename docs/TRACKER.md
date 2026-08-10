# TRACKER.md — Sprint Ladder & Rules

*The agent entry point. Newest state lives here.*
*Last updated: 2026-08-09 · Owner/gate: **Nick***

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

### S0 — AE3 SPI ceiling benchmark  `[~]`
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
**Demo (Nick):** run `bench/ae3_spi_bench.py` in OpenMV IDE → printed table of
MHz / chunk / effective Mbps / IRQ µs. **Pass: ≥ 12 Mbps effective.**
**Needs:** AE3, one jumper wire. No ADIN hardware.

### S1 — Pi 5 + SG shield: Linux driver up  `[ ]`
**Goal:** known-good ADIN node on the Pi 5.
- [ ] Build `adin1110` kernel module (menuconfig per SG's Linux page: ADIN1110
      MAC-PHY, industrial T1L PHYs, CRC8)
- [ ] Install SG's device-tree overlay (SPI0 CE0, 23 MHz, IRQ GPIO22); verify
      no `adi,spi-crc` (shield is strapped generic/no-CRC)
- [ ] Driver probes; interface up
**Demo (Nick):** `dmesg | grep adin` shows probe · `ip link` shows the new
interface · `ethtool -i <if>` reports driver `adin1110`.
**Needs:** Pi 5, SG shield on header. eth0 stays free for SSH/debug.

### S2 — AOS hats: node-to-node Linux link  `[ ]`
**Goal:** two Pis linked over T1L using the AOS boards; AOS hardware validated.
- [ ] Buzz out AOS hat: CS/IRQ/RESET GPIOs, strap state, pair-connector
      polarity (or obtain schematic from AOS) → record in DESIGN.md
- [ ] Overlay variant for AOS pinout; hats on Pi 5 + Pi 3/4
- [ ] Wire pair, static IPs (192.168.7.1/2), link up both ends
- [ ] Capture golden logic-analyzer traces: init, link-up, TX/RX (S4's reference)
**Demo (Nick):** `ping 192.168.7.2` · `iperf3 -c 192.168.7.2` shows ~9 Mbps.
**Needs:** both AOS hats, both Pis, crimped pair, logic analyzer.

### S3 — Video across T1L, Pi to Pi  `[ ]`
**Goal:** full streaming pipeline working before any AE3 driver exists.
- [ ] AE3 → Pi 5 over USB (existing setup), constrained to ≤ 8 Mbps
      (settings per SPEC budget; record actual choice)
- [ ] Sender service on Pi 5 → frames over T1L → receiver on Pi 3/4 serves
      multipart-MJPEG HTTP (no transcode)
- [ ] Measure sustained Mbps + dropped frames at target settings
**Demo (Nick):** open `http://<pi3>:8080/stream` in a browser → live video that
crossed the pair.
**Needs:** S2 done. This receive side is FROZEN after S3 — S6 must plug into it
unchanged.

### S4 — AE3 first light: PHY ID over SPI  `[ ]`
**Goal:** AE3 (Diagram 1 rig, generic SPI mode) proves wiring + HAL.
- [ ] Meter check: SG shield power source (pin 1 vs pin 2 regulator) before power-on
- [ ] Minimal generic-SPI register read in MicroPython
- [ ] Read PHY ID; compare LA trace against S2 golden capture on mismatch
**Demo (Nick):** REPL prints `PHY ID: 0x0283BC91 — OK`.
**Needs:** S0 pass, SG shield freed from Pi 5 (S1 knowledge retained), 8-jumper harness.

### S5 — AE3 raw-frame TX + loss measurement  `[ ]`
**Goal:** AE3 transmits real Ethernet frames; link quality quantified.
- [ ] Frame TX path in the driver (generic SPI FIFO), seq-numbered payloads
- [ ] RX path (at minimum: link status + counters)
- [ ] Pi counter script (raw socket) → received/lost/fps
**Demo (Nick):** `python3 bench/frame_counter.py` on Pi shows rate + 0% loss at
target load for 60 s.
**Needs:** S4. Pi end = S1 node or S2 node.

### S6 — Video from AE3 over T1L into the existing stream  `[ ]`  ← THE POINT
**Goal:** replace USB with the pair; the S3 web page doesn't know anything changed.
- [ ] AE3: capture → MJPEG → chunk into frames w/ tiny header + seq
- [ ] Pi shim daemon: raw frames → reassemble → feed the S3 stream server
- [ ] Sustained run at ≤ 8 Mbps; measure fps/loss/latency
**Demo (Nick):** same browser URL as S3 shows live video; USB data pipe unused
(REPL only). Side-by-side: unplug pair → stream stops; replug → resumes.
**Needs:** S3 + S5.

### S7 — Decision gate: OPEN Alliance / bm_core alignment  `[ ]`
**Goal:** a decision, not a build.
- [ ] Assess Sofar's OA-mode Linux/BM driver status (ask them directly)
- [ ] Estimate: port oa-tc6-lib to AE3 vs stay generic; what re-straps
- [ ] Optional spike: re-strap one AOS hat to OA, PHY ID read in OA framing
- [ ] Write DESIGN.md decision entry with recommendation
**Demo (Nick):** written recommendation reviewed together; tracker updated with
the follow-on project's first sprint.

---

## Icebox (captured, not scheduled)

- lwIP netif integration in OpenMV firmware (C) — MicroPython sockets over T1L
- N6 evaluation for H.264 path (needs OpenMV answer on VENC MicroPython API)
- SG JP1/JP4 breakout confirmation (would clean up the S4 harness)
- Power-gating architecture (AE3 supervisor + load switch) from board-selection analysis
- bm_core port (post-S7 decision)
