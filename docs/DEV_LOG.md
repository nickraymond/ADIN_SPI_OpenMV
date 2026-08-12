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

## 2026-08-12 — Sprint S10 (INTERIM 2a) — bm_core/lwIP/BCMP alive on HE vs trait-level mock; rehearsal PASSES ×2; translator flag captured

**Branch:** `sprint/10-bcmp-he` (worktree `sprint-s10-planning-ec0ae3`)

**Done:**
- Nibble 1 (Nick approved: 2a/2b split, trait-level mock over the
  TRACKER's chip-level parenthetical, fetch-and-pin sys_arch → D25):
  bm_core's NetworkDevice trait is the seam; bm_sbc's own
  virtual-device init ladder followed verbatim.
- Nibble 2: `firmware/bm_he/` — bm_core @ d4ecc38 vendored
  byte-identical (BCMP slice, zero patches needed), lwIP 2.2.1 by
  reference from the D23 openmv clone + pinned contrib sys_arch,
  RAM/tick integrator stubs, trait-level mock with rpmsg fake wire,
  4 KB debug ring peekable from HP, runner + pcap writer. Host tests
  72 checks (clang+ASan). Size checkpoint: 231 K / 262 K (~88%) —
  fits, ITCM lever unneeded.
- Rehearsal (Claude, twice, identical): **A PASS** (ladder RUNNING,
  node id + fe80::/fd00:: addrs correct, link up) · **B PASS**
  (heartbeats at boot+10.02/20.02 s, checksum + src-node-id +
  egress-nibble verified, monotonic) · pcap reads clean in tcpdump
  (heartbeats = ip-proto-188; bonus MLD6 join of ff03::1 visible).
  S6 USB baseline re-verified after (33.7 fps, 0 gaps, 0 bad).
- Captured Nick's schematic-review finding (via capture-task): AE3
  P0–P5 ride NXS0104/NXS0102 level translators (open-drain, 10 kΩ,
  24 Mbps max; part-to-net mapping UNVERIFIED) → SPEC §Open questions
  entry + S13 measurement item. Gives the S9 20 MHz-OA-garbage finding
  a physical hypothesis (MISO edges); no impact on the USB-only interim.

**Broke/surprised us:**
- bm_core compiled for CM55 with ZERO source patches — the whole
  integration fit in headers we own + stubs. Rare and pleasant.
- newlib-nano printf silently mangles %llx (caught live in the debug
  ring); nano's syscall layer needed explicit stubs with a trapping
  _sbrk. LWIP_ETHERNET=1 must be spelled out when ARP is off; the 2021
  contrib sys_arch wants FreeRTOS backward-compat names. VPATH: shared
  he_spike dir almost shadowed our startup.c/main.c.
- bcmp sends NO heartbeat at link-up (timer-only, source TODO) —
  first one lands at +10 s; runner capture window sized accordingly.

**Next:** Nick runs the 2a demo (`bm_he/README.md` ladder, one mpremote
command + pcap pull) → 2b: HP python peer (inject heartbeats →
neighbor table; BCMP ping both ways) = the INTERIM-2 demo proper → PR.
→ **demo PASSED (Nick, same day — A/B identical to both rehearsals);
PR opened.**

---

## 2026-08-12 — INTERIM re-plan + Sprint S10 (bite 1) — USB-only ladder approved; FreeRTOS-on-HE spike rehearsal PASSES (A/B/C, gate 44×)

**Branch:** worktree `claude/interim-arc-replan-68f99c` (→ pushes to
`sprint/10-he-pipe-spike` at PR time)

**Done:**
- Fresh-eyes interim re-plan (Nick approved): TRACKER gains the
  INTERIM USB-only ladder (S10 bites 1–2 → S11 dev-kit reference w/
  HARD SAFETY GATE → D24 + D15 upstream reports), S9 marked `[!]` with
  RESUME-ON-HARDWARE; all ADIN-touching work parked.
- Nibble 1 exploration paid off big: stock AE3 firmware already ships
  OpenAMP host+remoteproc on HP and a remote-execution service on HE —
  **rung-0 probe measured 219 Mbps py↔py through the stock pipe, zero
  custom firmware → the ≥5 Mbps S10 gate was effectively answered
  before writing a line of C.**
- Nibbles 2–3: `firmware/he_spike/` — FreeRTOS V11.3.0 (vendored,
  CM55_NTZ port) on M55_HE, runtime-ELF-loaded into SRAM9_B via stock
  remoteproc (NOTHING flashed, recovery = stop/power-cycle);
  hand-rolled ~250-line device-role rpmsg; MHU doorbells; he-bench
  endpoint; SPI0 probe. Host tests 29 checks (clang+ASan, fake-SHM
  host driver). **Demo rehearsed twice, identical PASS: A (FreeRTOS
  serves rpmsg), B (13.2 Mbps HP→HE / 5.6 HE→HP, 0 loss/crc errs,
  37k msgs — python-end-bound; fabric does 219), C (HE pinmux
  write+readback + SPI0 init + IRQ 137 on HE NVIC).** The bm_core-on-HP
  fallback is MOOT.

**Broke/surprised us:**
- Three wire-format facts came only from live ring dumps (source
  inference was wrong or silent): vring roles reversed vs the
  modopenamp comment; desc .addr = offsets from SHM+1K; **used.len is
  a capacity contract** — reporting message size made the host recycle
  shrunken buffers (pump stalled after exactly 64 messages; small
  replies still flowed — that asymmetry was the tell). Host harness now
  reproduces the recycle semantics.
- Honoring NO_INTERRUPT on our TX ring loses the host's wakeup race
  (~1 msg/s trickle) → kick unconditionally.
- SPI0's DW SRL loopback bit is tied off in this silicon; the pad-pull
  fallback is inconclusive (P1 reads high under both pulls though
  pinconf verifiably lands) → **bench check (Nick): anything still
  wired to AE3 P0–P2?** RX-with-real-data proof = first PHY-ID read
  from HE on replacement hardware.
- One-off `machine.mem32` AttributeError (self-resolved on re-run;
  README troubleshooting note).

**Next:** Nick runs the bite-1 demo (`he_spike/README.md` ladder, one
mpremote command) → nibble 4 PR → INTERIM 2 (bm_os/lwIP/BCMP on HE vs
mock NetworkDevice).
→ **demo PASSED (Nick, same day — identical A/B/C numbers); PR
opened.**

---

## 2026-08-11 — Sprint S9 (bite 3) — OA data-path bridge PASSES on hardware; link blocked by dead pair (bench check for Nick)

**Branch:** work in worktree branch `claude/s9-oa-datapath-smoke-dc2e62`
(→ pushes to `sprint/9-oa-datapath` at PR time; that branch is checked
out in another worktree at the same base commit)

**Done:**
- Nibble 1 (plan approved by Nick): exploration verified against source —
  state-nudge theory confirmed (MAC_Init:542/574, ProcessTxQueue:1479);
  found adin2111-level init ALSO blocks on a port-2 PHY wait
  (adin2111.c:169) → bridge drives macDriverEntry/phyDriverEntry
  directly; PHY identity gate is DEVID1+OUI only → predicted pass.
- Nibble 2: `bm_spike_datapath.c/h` (init bridge, driver byte-identical),
  dp_* API in both HAL tables, `s9_oa_datapath.py` runner, host tests
  16 → 41 (mock: writable regs, MDIOACC/clause-45 PHY emulation, OA
  data-chunk parse + byte-exact TX capture). Both firmware builds green
  post-D24; HE image byte-count unchanged (guards hold).
- Rehearsal (partial): flash PASS (byte-verified). **Init bridge PASSES
  live, first try: rungs 1–6 SUCCESS, MDIO-over-OA proven (DEVID
  0x0283/0xBC91 through the driver's own PHY layer — the flagged new
  surface), SyncConfig + SWPD-exit clean.** VERDICT A achieved.

**Broke/surprised us:**
- **Link never comes up — and it's the BENCH, not the code.** Isolation
  (one variable at a time): S5-minimal sequence over raw C45 MDIO also
  fails → not the driver's phyInit extras; far side advertises fine but
  sees no partner (ethtool, bounced mid-window); **LOFE relatch probe
  silent** vs bite-2's measured continuous relatch from far-side energy
  → no energy on the pair. Suspect the pair got unplugged during the
  bite-2/S6-demo bench work. **Nick: re-seat/check the pair at both J1s**,
  then re-run `s9_oa_datapath.py` (README bite-3 ladder) — everything
  else is in place.
- Chip default AN_CONTROL=0x1000 measured (AN_EN on by default) —
  retroactively validates S5's power-up-only sequence.

**Next:** Nick's bench check → re-run runner (expect link UP ≲1 s, then
VERDICT B + frames in tcpdump on nereus001) → nibble 3 manual test →
nibble 4 PR. Debug helpers staged in `~/ae3_flash/` on nereus000.

**CONTINUED same day (bench debugging with Nick, paused mid-bisect):**
- Pair re-seated + continuity-verified (J1↔J1) by Nick → STILL no link.
  Isolation extended, all software-only: far side hardware-reset via
  module reload (fresh PHY init) → nothing; **forced-mode test (AN
  bypassed entirely: far = ethtool forced-master, ours = registers per
  the kernel driver's own recipe, amplitudes matched) → also dead both
  directions.** Fault is squarely in the analog/MDI domain.
- Correction recorded: the multimeter AC test I suggested was invalid —
  DMM bandwidth ≪ 7.5 MBd PAM-3; "no AC" readings are expected even on
  a healthy line. No line-capable instrument on the bench (LA descoped
  in S2).
- New measured facts: **hat #2 straps 2.4 Vpp TX on**
  (B10L_PMA_CNTRL powers up 0x1000; chip reset default is 0 → AOS
  TX2P4 strap pulled high — SPEC §Open questions); chip default
  AN_CONTROL=0x1000 (AN_EN on). Hat blue LEDs track link (dark = no
  link); red = power.
- Suspicion worth recording: the S6 demo's unplug/replug was a hot-plug
  with both ends powered on an unprotected line interface; plus heavy
  bench handling since. A damaged line driver on either hat explains
  every observation.
- **Bisect in progress (paused):** plan = SG shield (known-good,
  generic SPI) on nereus000 ↔ new shorter pair ↔ hat #1/nereus001,
  rerun S2's `t1l_link_test.sh`. Links → fault follows hat #2 / old
  harness. Nick dismantled the AE3 rig (hat #2 off, set aside, straps
  UNTOUCHED = still OA) and mounted the shield, but **nereus000 stopped
  joining the network entirely (no tailscale, no LAN ping, even with
  the shield removed)** — unresolved, needs local console/router check.
  Note: hat #2 on a Pi is NOT testable while OA-strapped (kernel driver
  is generic-SPI-only, D13) and a powered-but-unmanaged hat's PHY stays
  in software powerdown → dark blue LED proves nothing in that config.
- Fixture state at pause: AE3 rig DISMANTLED (rebuild = D19 wiring for
  bite-3 demo); AE3 still flashed with the bite-3 alif build; hat #2
  aside, OA straps intact; SG shield on/near nereus000; nereus000 OFF
  NETWORK; nereus001 healthy (autoneg on, eth1 up; tailnet name is
  **nereus001-1**, not nereus001 — post-reflash registration).

**CONTINUED 2026-08-12 (resumed with Nick; INVESTIGATION CLOSED):**
- nereus000 WiFi root-caused: hard power-cuts → ext4 orphan cleanup ate
  the NM WiFi profile (dmesg evidence). Nick recreated it. Bench rule:
  `sudo poweroff`, never pull power.
- Bisect completed across ALL three endpoint pairings (two cables,
  three termination styles, AN + matched forced master/slave, straps/
  overlays/modules/rails all formally verified — incl. Nick's process
  checks: module vermagic matches running kernel on BOTH nodes, live
  DT has no adi,spi-crc): **every pairing dead, zero energy either
  direction. Verdict: ≥2 of 3 line interfaces broken; both AOS hats
  prime suspects** (single transient into the shared pair; window =
  post-S6-demo bench-work era). Full logic + the DC-blocked-front-end
  correction in SPEC §Open questions.
- Nick's Fluke measurements KILLED a documented "fact": hat J1 is NOT
  DC-shorted through the winding — both hats OL, shield 2 MΩ = DC-
  blocked fronts everywhere; my DMM-based localization attempts were
  invalid (also: DMM AC range can't see 7.5 MBd PAM-3 — recorded so
  nobody tries again).
- En-route mishap (fixed): bare `build_adin1110.sh` on nereus001
  defaults to sg and ADDED a second overlay line to config.txt —
  removed before it could double-bind SPI CS on next boot. Rule:
  always pass the sg|aos argument.
- **Bite-3 status: code DONE and hardware-proven to the wire (bridge +
  MDIO-over-OA + TX submit all pass live); demo BLOCKED solely on
  replacement link hardware.** Options for Nick: new AOS hat(s), or
  ADIN2111 eval hw (bite-1 decision point pre-approved; production
  direction). SG shield = probable good endpoint; can be re-strapped
  to OA as the AE3-side chip if roles reshuffle (hat #2 stays generic
  as the Linux node).

**Next:** Nick picks replacement hardware → rebuild fixture → re-run
`s9_oa_datapath.py` (one command) → nibble 3 manual test → PR.

**SPUN DOWN 2026-08-12 (Nick's call): boards confirmed dead, bite-3 PR
opened with the demo deferred to hardware arrival. Interim pivot: new
session plans a USB-only dev track for the BM-native arc (S10's
FreeRTOS-on-HE + OpenAMP spike needs zero ADIN hardware; TRACKER
review with fresh eyes). Kickoff prompt handed to Nick.**

---

## 2026-08-11 — Sprint S9 (bite 2) — Alif-native ADI-HAL: demo PASSES repeatably; PROTE self-flip + dead reset line found

**Branch:** sprint/9-adi-hal

**Done:**
- Nibble 1 (plan approved by Nick, DMA deferred to S10): facts gathered
  from openmv.git @ 7d4dbf7ab2 — P0–P3 = SPI0 on Alif port 5 (SCLK is
  AF3, siblings AF4), P5 = P0_4 → GPIO0_IRQ4_IRQn; the D8 per-word
  ceiling exists in BOTH machine_spi.c and Alif's own
  spi_transfer_blocking; GPIO0_IRQ4Handler symbol owned by
  machine_pin.c + const MRAM vector table → ride its dispatch.
- Nibble 2: `bm_spike_hal_alif.c` (FIFO-burst SPI0 engine ≤16 in
  flight, NVIC-gated INT_N, real critical sections, stats),
  `--hal mp|alif` staging switch (mp = default/baseline), per-HAL
  Python API + bench + raw reg passthrough + `fresh()`, host tests
  10 → 16. Both HP images build post-D24; HAL exclusivity verified in
  the objects.
- Nibble 3 rehearsal (Claude ran the demo per Nick's ask, both
  runs PASS identically): **VERDICT A** PHYID=0x0283BC91 via native
  HAL; **VERDICT B** INT_N → hard IRQ → driver callback (1 callback per
  soft reset); bench 45.9k reads/s @5 MHz (mp HAL: 22.9k = 2.0×),
  83.8k @10 MHz, 0 stalls; bite-1 runner still passes on a final-source
  mp build.

**Broke/surprised us:**
- **20 MHz OA rung reads garbage AND is dangerous**: misclocked MOSI
  decoded as a valid CONFIG0 write and flipped PROTE=1 mid-rehearsal —
  chip then dropped every unprotected write (CDPE latching, reads still
  clean) until recovered by a protected-framed soft reset. Explains
  bite-1's one-off complement anomaly. SPEC §Open questions amended;
  runner now sanitizes before/after and runs 20 MHz last, gating
  nothing. RX_SAMPLE_DELAY sweep = bite-3 item.
- **P4 reset line is a no-op on the rig** (register scratch survives a
  50 ms pulse) — never actually verified in S4–S9; soft reset via reg
  0x003 is the only reset. Bench continuity check flagged for Nick.
- INT_N is asserted from power-up and W1C of STATUS0 is the only way
  up; LOFE relatches continuously (live far side) and must be masked
  for the IRQ proof; driver's failed-init exits leave NVIC disabled
  (correct driver behavior — runner re-arms).
- C statics survive MicroPython soft resets: a stale bench MAC handle
  benched all-fails until `fresh()` was added.

**Next:** Nick runs the bite-2 demo (`s9_hal_native.py`, commands in
README) → nibble 4 PR. Then bite 3: OA data-path smoke (frame TX →
tcpdump on nereus001) — the open half of the S9 demo.
→ demo PASSED (Nick, same day); PR #15 opened. Bite 3 is next.

---

## 2026-08-11 — Sprint S9 (build fix) — HE link failure root-caused: stock docker target flattens per-core build dirs

**Branch:** sprint/9-build-fix

**Done:**
- Root-caused the S9 blocker (M55_HE image never links in our env,
  FLASH_TEXT 154% + undefined `dcd_*`): openmv's stock
  `docker/Makefile build-firmware` → `build.sh` passes `BUILD=<dir>` on
  the make **command line**; that rides MAKEFLAGS into every sub-make and
  overrides `ports/alif/alif.mk`'s `BUILD := $(BUILD)/$(MCU_CORE)` — HP
  and HE share one object dir, so the HE link consumed HP-configured
  objects (USB device stack on → 2.21 MB text ≈ the HP image). Explains
  the byte-identical failure with the usermod compiled out. OpenMV CI
  builds AE3 with plain `make TARGET=` (no docker) and never hits it;
  upstream's own `build-firmware-dev` (commit `6adf40fd`, 2026-04)
  documents the nesting requirement in its comments. D24.
- Verified from clean at `7d4dbf7ab2` before touching the repo: HE links
  **1,193,520 B / FLASH_TEXT 83.25%** (official artifact 1,185,744 B),
  HP 2,200,176 B; `build/OPENMV_AE3/M55_{HP,HE}/` nesting present.
- `build_ae3.sh`: switched to `clean-dev` + `build-firmware-dev`; new
  `--incremental` flag (dev-loop fast path); HE size-window check; dirty
  openmv tree now skips rev sync instead of hard-resetting edits away.
- Label fallout fix: our tagged clone embeds describe-form ids
  (`v5.0.0-52.g7d4dbf7ab2` — makeversionhdr turns dashes into dots), not
  the bare sha10 of tagless CI builds. `flash_ae3.py`'s exact-match label
  check would have false-FAILED every local build after a good byte
  verify. MANIFEST now records `openmv_label` (exact embedded string);
  label check accepts a sha10 inside a describe id. Host tests 25 → 33.
- Docs: D24 decision entry; §S9 open issue marked resolved; HP-only
  workaround retired.

**Broke/surprised us:**
- Upstream half-knows: `build-dev.sh`'s comment states the per-core
  nesting requirement verbatim, but the stock `build-firmware` target is
  still broken for multi-core Alif targets. Candidate upstream report
  (alongside the D15 crash).
- The describe-vs-sha10 label format difference was invisible until a
  local build actually embedded one — the S7 flash-verify hardening
  (byte-level readback) was the right call; labels keep proving
  unreliable as fingerprints.

**Next:** Nick runs the manual test (fresh `build_ae3.sh` from clean →
MANIFEST + HE bin ~1.19 MB), then PR. S9 bites 2–3 (ADI-HAL, OA data
path) can now target both cores; S10's HE dependency is unblocked.
→ manual test PASSED (Nick), PR #14 opened.

---

## 2026-08-11 — Sprint S9 (bite 1) — bm_spike code-complete: unmodified bm_core driver runs on host; hardware gates = docker + re-strap

**Branch:** sprint/9-oa-first-light

**Done:**
- Nibble 1 (plan approved): spike designed for two verdicts, not one —
  OA transport proof AND the unmodified-init result. Nick's ask: get as
  far as possible with zero hardware contact.
- `firmware/bm_spike/`: vendored bm_core drivers/adin2111 @ d4ecc38
  byte-for-byte (bm_adin2111.c reference-only — needs bm_os, defines its
  own HAL fn); blocking adi_hal.h shim over MicroPython SPI/Pin
  (S4-proven path); `bm_spike` usermod; `build_spike.sh` stages sources
  into openmv's modules/ wildcard (NO fork/patch — staging + trap
  cleanup exercised); `s9_oa_spike.py` runner; README with verdict
  matrix + run ladder.
- Host harness: clang builds the UNMODIFIED driver against a mock ADIN
  speaking OA-protected control framing (format from adi_spi_oa.c) —
  10 checks PASS, including the identity gate demonstrated compiled
  (25,000 PHYID polls → COMM_TIMEOUT with a 1110 identity; prompt exit
  with 2111's).
- Pre-staged for Nick: openmv.git cloned to ~/openmv-dev/openmv; SDK
  1.6.0 linux-x86_64 downloaded + sha256-verified (setup_mac.sh will
  skip it). Docker still absent (password needed) — the build stops
  there by design.

**Broke/surprised us:**
- **The 2111 identity gate fires inside MAC-layer init, not just full
  init**: MAC_Init → MAC_Reset(MAC_PHY) → waitDeviceReady polls
  PHYID==0x0283BCA1 (adi_mac.c:568/1128). On a 1110, even MAC-only init
  returns COMM_TIMEOUT on perfect hardware. Spike redesigned mid-nibble
  to tolerate it and read PHYID afterwards (handle valid pre-reset;
  MAC_ReadRegister needs state != UNINITIALIZED only).
- ADI's *_DEVICE_SIZE constants are ILP32 hand-counts → adin2111_Init is
  not LP64-host-portable (INVALID_PARAM before SPI). Verdict 2 is
  target-only; documented.
- Vendored-driver quirk pinned by test: control-read path swallows
  PROTECTION_ERROR (spiErr only carries the header-echo check) —
  corruption = SUCCESS + unwritten data. Spike judges the PHYID value.
- MAC_Init is static; the exported route is the macDriverEntry table
  (same as adin2111.c uses).

**SPIKE PASSED (same session, hardware leg):** Nick installed Docker +
re-strapped; Claude drove build→flash→verdicts. Final:
`PHYID=0x0283BC91` through the driver's own OA framing; init refused
only by the 2111 identity gate. En route (all recorded in DESIGN §S9 /
§S8 correction / SPEC open questions): **S8 bench had run on the N6**
(mpremote auto-connect; AE3 re-run same conclusions; by-id-only rule
adopted) · **PROTE dead on our 1110** (measured; `--no-prot` delta
build; driver tests defined-ness — sha-identical `=0` build caught it) ·
CFG0 pad needed a second rework pass (razor; chip had been answering
OA-unprotected) · D23 build leg works but **M55_HE won't link in our
env at any rev** (HP-only flash at installed-HE's rev = workaround;
must fix before S10) · flash-verify tool false-mismatch on
`git describe` version strings (feeds the running hardening task).

**Next:** S9 bite 2 — Alif-native ADI-HAL (SPI + IRQ on P0–P5, DMA
hooks if budget allows). Prereq: fix the HE link (or a decided
HP-only stance) before S10. PR for bite 1 open. NOTE (merge-time): the
flash-verify hardening landed as PR #12 (entry below) — byte-level DFU
readback replaces the label matching whose false-mismatch we hit; our
session's flashes used the pre-hardening ladder deployed on the Pi.

---

## 2026-08-11 — Sprint S7 (flash-verify hardening) — byte-level readback verify replaces label matching

**Branch:** sprint/7-flash-verify

**Done:**
- Investigated the S8 stale-label find from source (openmv.git @ master
  `7d4dbf7`, the rev the board runs): `sys.version`'s "OpenMV \<id\>" is
  git-describe output baked in at build time (openmv/micropython
  `py/makeversionhdr.py`) — degrades to a bare sha10 in tagless checkouts
  and repeats across rebuilds at the same rev. The "v5.0.0" the board
  self-reported is the OTHER channel: `omv.version_string()`, reading the
  static `OMV_FIRMWARE_VERSION` defines (`protocol/omv_protocol.h`), still
  "5.0.0" on post-release dev builds. Labels ≠ fingerprints → label-match
  flash verification can false-pass.
- Fix (nibble-1 plan approved by Nick): `flash_ae3.py` verifies
  byte-for-byte — DFU readback (`dfu-util -U -Z len(bin)`; bootloader
  implements `DFU_UPLOAD`, MRAM reads are memcpy, tail compare capped for
  the 16 B sector round-up) + sha256 vs the exact flashed file; boot gated
  behind the verify via `DFU_DETACH` (`dfu-util -e` → jump, replaces `-R`);
  MANIFEST sha256 preflight cross-check of the local bins. `sys.version`
  demoted to boots+label evidence. Host tests 16 → 25, all green.

- Nibble 3 (Nick delegated the run): LIVE round trip PASSED on nereus000 —
  negative test (corrupted MANIFEST sha256) refused before board contact;
  v5.0.0 flashed + readback-verified both partitions; dev flashed back with
  the full corrected ladder, `PASS: flash verified byte-for-byte`, exit 0;
  fixture firmware restored to `7d4dbf7ab2` and re-confirmed via REPL.

**Broke/surprised us:**
- The S8 DEV_LOG entry the kickoff referenced wasn't on main during the
  session — it landed mid-flight with PR #11 (`sprint/8-npu-bench`, entry
  below) and produced the doc merge conflict resolved in this branch's
  merge commit.
- Today's rolling `development` release still embeds "OpenMV 7d4dbf7ab2"
  (upstream master hasn't moved) — confirming the board's "v5.0.0" report
  came from the static-defines channel, not sys.version.
- **`dfu-util -e` does NOT boot the board** — it only detaches runtime-mode
  devices; silent no-op on a device already in DFU (board parked safely, as
  designed). Boot rung reworked live: 8 KB TOC-partition read carrying `-R`
  (USB reset → `while (tud_mounted())` exits → jump), reset still lands
  only after verification.
- `dfu-util -Z` doesn't bound uploads (0.11) — readback runs to the
  partition-end short frame; the sha256 compare caps at len(bin) instead.

**Next:** nibble 4 — push `sprint/7-flash-verify` (push was
permission-blocked from the agent session) and open the PR. → done: PR #12.

---

## 2026-08-11 — Sprint S8 (bite 1, early ride) — NPU bench: per-tile fast, HD tiling misses the T2 gate

**Branch:** sprint/8-npu-bench

**Done:**
- Nibble 1 (plan approved, scope kept tight for the BM arc): S8 rides
  its TRACKER exception — NPU bench only, rest of S8 stays behind S13.
- `bench/ae3_npu_bench.py` (+18 host tests): no-sensor, reef-ref-scene,
  models discovered live from `/rom`; ml API pinned from docs.openmv.io
  v5.0.0 before coding. Ran it remotely (Nick's ask): 9 models timed,
  1 correctly SKIPped. yolov8n_192 = 21 ms/tile (~47 fps); HD tiled
  (40 tiles @ 32 px overlap) = **1.2 fps < T2 ≥3 fps gate**; only
  yolo_lc_192 meets it (6.3 fps). Single-pass downscale → fish 15–23 px,
  below the 24 px floor. Tables in DESIGN §S8 detail; TRACKER ticked.
- Artifact checks: "0 det" explained by label files (yolov8n/yolo_lc =
  person-only) → **T2 needs a custom Vela-compiled fish detector either
  way (Nick concurs); input size is the tiling lever.**

**Broke/surprised us:**
- Board self-reports `OpenMV v5.0.0` while DEV_LOG says dev
  `7d4dbf7ab2` — Nick: stale version label on the in-development build,
  not a reflash. (Weakens sys.version as a flash-verify signal for dev
  builds carrying release-ish labels — watch item for `pi/ae3_flash`.)
- Tailscale SSH wanted a fresh browser re-auth before the session could
  reach nereus000 (Nick approved mid-session).

**Next:** S7 decision entry (waiting on Sofar), then the BM arc (S9
bite 1). S8 resumes after S13; its next bite when reached = custom
detector (train + Vela compile, larger input) — the bench says the NPU
has the headroom if tiles shrink.

---

## 2026-08-11 — Sprint S7 (spike bite 1) — headless flash SPIKE PASSED: round-trip flash from the nereus000 CLI

**Branch:** sprint/7-headless-flash

**Done:**
- Nibble 1 (plan approved with Nick's amendments: dev on the Mac, docker
  build, VS Code/IDE as manual front-ends): headless flash answer pinned
  from source, not the bench — OpenMV's DFU bootloader runs on EVERY boot
  (USB `37C5:96E3`, 1 s + 1.5 s window), `machine.bootloader()` forces it
  to stay (magic `0xB00710AD` → `0x200FFFFC`), partitions are named DFU
  alts (`HP`/`HE`; `BOOT` never touched → un-brickable at app level),
  `os.uname().version` embeds `OpenMV <sha10>` for verification. SWD and
  SE-UART rejected for the loop (D22). SE-UART = deep recovery only.
- Load-bearing build fact: OpenMV SDK exists only for linux-x86_64 +
  darwin-arm64 → docker-on-Pi would be qemu-emulated; build host = Mac
  under Rosetta (D23, Nick's call).
- Shipped (hardware-untested by design): `firmware/openmv_build/`
  (setup_mac.sh, build_ae3.sh → sha256 MANIFEST with openmv_sha) and
  `pi/ae3_flash/` (flash_ae3.py ladder: preflight refuses active
  t1l-sender → mpremote bootloader entry → DFU wait → dfu-util HP+HE →
  CDC wait → uname hash verify; --dry-run/--recover; fetch_firmware.sh;
  udev rule; 16 host unit tests pass).
- Session constraints held: no mpremote/USB/flash on nereus000, stream
  services untouched (S6 fixture live); D20/D21 numbers left to the S6
  branch, docs appended for clean merge with PR #9.

- **FLASH LEG PASSED same session (Nick's go after the S6 demo):**
  round-trip `7d4dbf7ab2` → `v5.0.0` → `7d4dbf7ab2` from the nereus000
  CLI, sys.version verified each leg, leg 2 = the shipped ladder
  end-to-end green with its own PASS verdict; fixture firmware restored
  to exactly what S6 ran on. Setup done on the Pi (dfu-util, uhubctl,
  udev rule → sudo-free ladder; passwordless sudo made it hands-off).
  Tooling deployed to `~/ae3_flash` (repo checkout on the Pi stays on
  the S6 branch, untouched).

**Broke/surprised us:**
- Verification hook was wrong pre-hardware: the `OpenMV <id>; MicroPython
  <id>` string is **`sys.version`**, not `os.uname().version` (uname has
  only the MicroPython id). And release builds embed version TAGS
  (`v5.0.0`), not sha10s — dev builds embed hashes. Regex relaxed.
- dfu-util `-R` exits 251 on SUCCESS (device drops off the bus during
  the USB reset) — "trust artifacts, not exit codes," literally; script
  now treats CDC re-enumeration + sys.version match as the signals.
- v5.0.0 ships one combined all-boards zip; per-board zips exist only on
  `development`. fetch_firmware.sh handles both now.
- The board had been on the D15-era dev build all along — S6 passed on
  `7d4dbf7ab2`/`11852aa3d0`, not stable v5.0.0.
- Two test bugs self-caught: "-R" substring hides inside "DRY-RUN";
  later the "reset sent (dfu-util ...)" log line collided with the test's
  dfu-util line filter.

**Next:** BM-native arc planned same session (research from bm_core +
bm_sbc source; ladder S9–S13 in TRACKER, Nick approved; S8 resequenced
after the arc). Key finds: bm_sbc branch
`feature/adin_linux_implementation` = raw_eth AF_PACKET transport (the
full-rate Linux attachment, WIP — Nick pinging Sofar CTO); bm_core's OA
driver is ADIN2111-only → S9 bite 1 tests it unmodified on our 1110,
fallback = buy 2111 hw, never port; bm_core needs FreeRTOS/POSIX → HE
core + OpenAMP is the AE3 plan (spike gates it, ≥5 Mbps pipe). Sofar
forum questions drafted for Nick. Immediate next bite: S7 decision
entry after Sofar responds, or S9 bite 1 if Nick wants hardware first.
Mac build leg (setup_mac.sh → build_ae3.sh) still for Nick to exercise
(needs Docker Desktop first-launch password) — it becomes load-bearing
in S9. Untested: `--recover`/uhubctl. ROMFS pairing on big version
jumps = watch item.

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
