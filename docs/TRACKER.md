# TRACKER.md — Sprint Ladder & Rules

*The agent entry point. Newest state lives here.*
*Last updated: 2026-08-19 latest+1 (**S23 GOLD arc: VGA color
PLATEAUS at 12.2–12.3 — five levers measured, four falsified** (fb=2
SLOWER at 11.47 = capture-DMA/encoder contention; exposure caps
engaged+flat; fused one-pass COBS+CRC viper cut wire cost 676→499
µs/msg with cycle unchanged; early capture kick flat at VGA) — **an
invariant ~13 ms/frame is the open question** (next: kick→collect
wall-time counters; firmware levers = CSI pixclk 24 MHz, Huffman MVE).
**HD mono climbed 3.15→3.62** from the same send-path levers, all rows
CLEAN ledger-exact. Bench: attach-refusal struck twice more; recovery
proven = uhubctl cycle + ≥5 min ZERO contact + one demo_up. Deployed:
bridge `79c9ab4f…` + codec `ebcfb87d…`, MEAS_FPS 12.23/3.62. VGA-15
NOT closed — Nick's call: instrument the 13 ms vs firmware levers vs
call 12.3 the MicroPython-path ceiling. Previous:*
*2026-08-19 latest (**S23 drain fast path SHIPPED —
VGA color 12.30 fps / HD mono 3.37, 60 s rows CLEAN ledger-exact
(738×20, 202×55); sprint ladder 7.41→…→10.73→12.30.** Zero-alloc
relay (`he_frame_wire` + memoryview encode; suite 328) after the
split counters measured he_msg at 87% of the 1.26 ms/msg pump cost;
enc/msg 1102→~680 µs. VGA send leg now 17.5 ms/frame — **VGA-15 is
one snapshot-wait overlap away (~16 fps predicted)**; HD is now
HE-round-trip-bound (ept-block 72 ms/frame), overlap alone reaches
only ~3.8 — HD-5 needs HE-side batching or the C path. NEXT =
overlap re-test (frame-ready poll; set_framebuffers(2) A/B gated on
Nick — S18 off-bus hazard) → bite 3. Previous:*
*2026-08-19 (**S23 relay regression RESOLVED-AS-
EXPLAINED — clean-boot HD mono 3.15 fps ×2 ledger-exact, ABOVE the
3.10 stock baseline; the 2.72 was a boot-state anomaly.** Relay-split
counters shipped (bridge `b3543cc7…`, suite 310); measured: VCP is
~24 MB/s on a HIGH-SPEED USB link — the "675 KB/s floor" is the
~1.25 ms/msg python drain (he_msg+COBS), and at HD ept.send blocks
1.14 ms/msg avg on the 32-slot vring. Nick's 4:2:0 eyeball PASSED →
bite 0 CLOSED (PR #42 merged). MEAS_FPS HD mono 2.72→3.15. NEXT:
capture/encode overlap re-test (D21) with the python-drain lever
(viper COBS) as the new top HD candidate → bite 3 re-measure + PR.
Previous:*
*2026-08-18 latest+3 (**S23 bite 0 nibbles 1–3 DONE —
4:2:0 forced on every color encode (one kwarg, color only, Nick
approved), delivered VGA color 7.41→7.93 fps ledger-exact.** Bridge
`552812ba…` byte-verified on the bench via demo_up; the A/B reef pair
is byte-exact to the model (29,148→27,021 B); page+server byte models
moved to the 4:2:0 anchors in lockstep (HD color q50 68→62 chunks);
bridge suite 292 / bench_web 81. Remaining: Nick's 4:2:0 quality
eyeball (compare view, seq000207 vs seq000000) → PR. Then bite 1 (MVE
color-convert, STOP-gate <1.5×). Previous:*
*2026-08-18 latest+2 (**NEW SPRINT S23 — ENCODER FAST
PATH — RUNS NEXT (Nick, D42).** PRs #38 + #39 MERGED (+ #40 catch-up
pending: a merge-ordering artifact stranded the bite-1b commits on the
sprint branch — it is exactly those commits, no new work). Nick's
targets: **VGA color q50 ≥ 15 fps and HD mono ≥ 5–6 fps delivered,
then push to the hardware's true max**. Route measured in the S22
window: bite 0 = ship 4:2:0-at-q50 (one kwarg, ~14% encode) · bite 1 =
MVE/Helium-vectorize jpege (the big lever, 2–4×) · bite 2 = C publish
path (kill the ~2 ms/KB tax) · bite 3 = re-measure ceilings. Execution
order now **S23 → S21 (CV) → S20 (light)**; S22 leftovers (bite 1b
fork instrumentation — Nick's push; PR #38 demo) interleave. Side-door
key INSTALLED (Tailscale outages no longer block the bench). Kickoff
prompt: PROMPTS.md §S23. Previous:*
*2026-08-18 latest+1 (**S22 bite 1 nibbles 1–3 DONE — the
HE flood wedge was a u16 vring-index wrap at message 65,536 in
`rr_poll_n`, fixed with ONE cast (D40).** ELF `fea65304…` on the bench;
10-min QVGA color soak 28.23 fps ledger-exact at the exact rate+duration
that killed the live demo; first true ceilings QVGA mono 30.30 / VGA
mono 13.27 / HD mono 3.10; VGA color 7.41 confirmed at 10 min;
guardrails re-derived (315→1200 envelope cap) + deployed, suite 81.
NEW bite 1b fenced: the ~83-chunk burst loss is a SEPARATE bug (54
chunks lost on the fixed stack — backpressure fix candidate, Nick to
size). Bite 2 desk work banked: E3 has NO hardware JPEG (datasheet),
q50 color = 4:2:2 (4:2:0 one kwarg away), jpege.c not MVE-vectorized.
Remaining: bite 1 nibble 4 (PR) + Nick's demo. Previous:*
*2026-08-18 latest (**LADDER RESEQUENCED — Nick, D39.**
PRs #35 + #36 MERGED (bench_ctl reconnect; the whole HD-stability bite
incl. guardrails + Danger Zone). Execution order is now: **S22 bite 1
(HE flood fix) → S22 bite 2 (encoder-headroom exploration, NEW) →
S21 (CV, promoted) → S20 (light, delayed — not a product offering
yet)**; S18 still owes D2 + the sprint demo, interleavable. Numbers
stay, order changes — the D30/D32 precedent. NEXT SESSION = S22
bite 1. Previous:*
*2026-08-18 late (**S18 bite B4 HD-stability: nibbles 1–3
done, PR opening.** HD root-caused to an OpenMV firmware defect
(per-resize framebuffer free+malloc degrades under a resident HE core),
FIXED by the sticky-fb patch (flashed, byte-verified, soak 40/40 vs
fail-at-#22 stock), and measured: HD stills byte-exact through the
chain (75,324 / 93,253 B), **B2's 20 s constant HD-CERTIFIED (cert rung
20.02 s)**, first HD stream numbers (ref HD mono 1.50 fps / 0.91 Mbps
exact; sensor HD color ~1.4 fps = S19 bite 4's number; VGA mono
4.98 fps clean). HD-ref guard lifted on measurement; guard-order bug
fixed (+ tests). Two artifact-evidenced follow-ups fenced: ≥ ~83-chunk
single-frame bursts lose chunks in the relay (finding 1's bite) and
ref-HD-color reloads fail on fragmented heaps (preload mini-bite).
Bench healthy, chain up under units, scene=sensor. See bite B4.
Previous:*
*2026-08-18 (**S18 reef-matrix bite: nibbles 1–3 done, PR
pending.** Branch `sprint/18-bench-matrix`. The bench page's model is
now MEASURED for QVGA/VGA: all six non-HD stills land **byte-identical
to S0's reef encode table** through the real chain; streams measured
clean = regression 15.15 fps / 1.12 Mbps (×3), **QVGA color ceiling
28.07 fps / 2.08 Mbps**, **VGA color 7.40 fps / 1.74 Mbps** (each ×2
identical); measured bridge derate 0.56–0.58 (the old extrapolation was
~2× pessimistic). Machinery shipped: bridge `scene:"ref"` source,
demo_up staging/trace-preservation/sha-sync arms, `bench/s18_matrix.py`
row-isolated driver. **Three findings fence off the rest** (SPEC §Open
questions + DESIGN §S18 reef-matrix detail): the HE wire task goes
permanently mute under sustained publish ≥ ~513 rpmsg msg/s (blocks
true mono ceilings); ref-mode HD hard-faults the board (bridge now
REFUSES HD ref, guard tested); sensor-mode HD wedges the leg on the B2
bridge — **HD has never completed on any PublishGate build, so the 20 s
constant's daylight-HD certification rung could not run.** HD rows +
cert stay owed behind those findings — Nick to size them as bites.
Bench left healthy: chain up under units, scene=sensor, measured page
live at nereus001:8090. NEXT = matrix PR (nibble 4), then bite D2 +
the S18 demo. Previous:*
*2026-08-16 (**S18 bite C2 DEMO RUN AND PASSED BY NICK — the
gallery, compare view, histograms and the failure banner all work in a real
browser; PR open (nibble 4).** Branch `sprint/18-bench-gallery` from `main` @
`db82181`: gallery from bite B's **sidecars**, side-by-side compare,
RGB+luma histograms, and the C1 follow-up banner for a non-`ok`
`cam_reply.state`. Pi-side only, **zero board contact**; host tests
42 → **67**. **NEXT = bite B2** (the sensor re-init race) and **bite D2**
(demo ladder + docs); after those S18 can close. **The AE3 fixture restore is
deliberately NOT done: it is folded into bite B2's single board window
AFTER this demo**, because `demo_up.sh` re-stages the bridge launcher
anyway. **Corrected from an artifact: `capture 50 hd mono` HAS run once**
(1280×800, 1 component, 24,207 B, `gaps_delta=0`) — S19 bite 4 still owes
the sustained run and HD-as-a-stream. Previous: **S18 bite C1 DEMO RUN AND APPROVED BY NICK —
the bench page works end to end and the PR is open.** Page at
`http://nereus001:8090/` driving bite B's control socket, fast-click guard
**enforced on the server** rather than in the browser; verified frame
`SOF0 320×200, 3 components`, `gaps=0`. Bite C split into C1 + C2 (D35).
**NEXT = bite C2** (gallery / compare / histograms) — it is on the critical
path, the sprint's demo line needs it. **New standing ops rule: command the
camera within ~30 s of starting `bm-light`** or the bridge quiet-exits in
phase 1 (measured; a light command does not count). Pi-side only — the AE3
still runs the S19 artifacts.
Previous: **PRIORITY CHANGE — D32: S18 is NEXT, with a
FRESH AGENT ON ITS OWN BRANCH, and S18's systemd units (bite D) are
promoted to first and made mandatory.** S19 bites 1–2 delivered: HD
stills work end to end — `capture 50 hd color` lands a valid 1280×800
JPEG at the browser, pub_errs=0, gaps=0 — after the wall was measured
(bytes in flight, not chunk count: 20,712 B free heap, 1,488 B per
chunk, the 14th died) and fixed in four parts, the fourth forced by a
rehearsal deadlock. **S19 is NOT closed**: no PR, branch unpushed, Nick
has not run the demo, `capture 50 hd mono` never run, and HD as a
*stream* never measured. ⚠ **The AE3 is running artifacts that exist
only on the unmerged `sprint/19-hd-transport`** — cut the S18 branch
from it or merge S19 first. Previous:*
*2026-08-15 (**S18 bite A DONE for QVGA + VGA** — capture
geometry + pixel format plumbed end to end and demoed on the live chain;
branch `sprint/18-web-bench`, PR open. Two hardware facts bought with
three board lock-ups: the sensor letterboxes to 16:10 (QVGA = 320×200)
and, with the HE ELF loaded at 0x60080000, GROWING the framebuffer takes
the board off the USB bus — fixed by an eager ceiling claim + pinned
`set_framebuffers(1)`. **HD captures but cannot be published — the HE
heap dies at 8 of 26 chunks — and that is now the ENTIRE scope of S19**,
with S18 bites B–D paused behind it. Previous: S17 BUILD-4 demoed, all
BENCHSPEC stages 0–4 complete.)*
*(older header retained below)*
*2026-08-15 (S17 BUILD-4 code complete on
`sprint/17-build4-apps` + fork `feature/udp-transport` @ c1d0df9;
bite-0 measurement + demos wait at the VCP gate; fork push = Nick.
Previous: S16 BUILD-2 demoed end-to-end — three-node chain, 600 s @
2.00 Mbps, zero loss, PR #23 merged. INTERIM MODE → BENCH arc: T1L bench down —
both AOS hats condemned, replacement PCBAs ~1 month out. Active work =
the **three-node software bench per docs/BENCHSPEC.md (v3, Nick
approved 2026-08-14)**: real bm_core on three nodes over UDP +
USB-CDC, ADIN swap-in on hardware day; ladder = interim items 3–6
below (sprints S14–S17). S11 INTERIM 3 (dev-kit reference) stays on
the ladder as an interleave bite — nibble-1 plan presented
2026-08-14, deferred by Nick. All ADIN-touching work parked behind
RESUME-ON-HARDWARE. S9 bite 3: code done + proven to the wire, demo
deferred to hardware arrival. Hat #2 currently strapped generic
(bisect state); AE3 carries the bite-3 alif build. SPEC flag
standing: AE3 P0–P5 ride level translators — see §Open questions +
the S13 item.) · Owner/gate: **Nick***

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
**Demo (Nick):** open `http://nereus001:8080/stream` in a browser → live
video that crossed the pair (page with stats at `/`; hostname was
`nereus001-1` until the 2026-08-14 tailnet cleanup).
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
- [~] Flash-verify hardening (S8 fallout, plan approved by Nick
      2026-08-11): version labels are not build-unique — sys.version =
      build-time git-describe (degrades to sha10 / repeats across
      rebuilds), omv.version_string() = static defines still "5.0.0" on
      dev builds → label match can false-pass. Fix: byte-level DFU
      readback verify (dfu-util -U + sha256, boot gated behind verify,
      MANIFEST sha256 preflight) in `pi/ae3_flash/`. Host tests 16→24.
      LIVE round trip PASSED on nereus000 2026-08-11 (v5.0.0 ↔ dev,
      readback verify OK ×4, fixture firmware restored); caught live:
      dfu-util -e is a no-op on DFU-mode devices → boot rung = 8 KB TOC
      read + -R; -Z doesn't bound uploads (0.11). Branch
      `sprint/7-flash-verify`; remaining: PR (nibble 4).
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

### INTERIM — T1L bench down (2026-08-12 → hardware arrival)  `[~]`
*USB-only ladder (Nick-approved re-sequencing 2026-08-12). Constraint:
no working T1L link. Everything here is developable AND testable with
only: the AE3 over USB on nereus000 (by-id mpremote ONLY), the Pis,
the Mac docker build env (D23/D24), and host tests. The S6
USB/MicroPython baseline stays intact as the regression reference
(restore = S7 flash ladder to dev `7d4dbf7ab2`). Order:*

1. `[x]` **S10 bite 1** — FreeRTOS-on-HE + OpenAMP pipe spike (see
   S10) — no ADIN dependency; the interim's first bite
   → DONE 2026-08-12: demo run by Nick — PASS (A/B/C, ≥5 Mbps gate
   cleared 2.6×–44×); PR #17
2. `[x]` **S10 bite 2** — bm_os/lwIP/BCMP on HE vs mock/loopback
   NetworkDevice (see S10) — no ADIN dependency
   → split 2a/2b (Nick approved 2026-08-12, incl. trait-level mock
   over chip-level promotion + fetch-and-pin sys_arch). **2a DONE
   2026-08-12: demo run by Nick — PASS (A/B, identical numbers to
   both rehearsals); PR #18 merged. 2b DONE 2026-08-12: demo run by
   Nick — PASS (A–E, identical to both rehearsals) = the INTERIM-2
   demo proper; branch `sprint/10-bcmp-2b`, PR opened.**
3. `[~]` **S14 — bench rung 0: gates before code** (BENCHSPEC.md
   V15/V16, Stage 0)
   - bm_sbc @ main builds + ctest + `validate.sh` — run on **nereus000**
     (the new Light host; nereus001 unreachable on the tailnet
     2026-08-14 — bench check for Nick, needed by S15)
   - [x] Relay throughput bench → **V16 MEASURED 2026-08-14: full relay
     (HE→rpmsg→HP uart_l2 framing→VCP→Pi) = 5.4–5.5 Mbps sustained,
     600 s run 5.425 Mbps / 288k frames / 0 gaps / 0 drops — 2.7× the
     2 Mbps gate.** Framing+USB alone 13.1 Mbps; rpmsg drain is the
     ceiling (= he_spike's 5.6). CRC-32C in viper is free (rung E:
     identical vs no-CRC). `firmware/bm_bridge/` + `bench/
     s14_relay_counter.py`; ops rules bench-earned (cold boot ≠
     main.py, mpremote kills service, HE load-once — DESIGN §S14).
   - [x] HE size audit → **V15 FITS: 240,000 B of 262,144 (91.6%),
     slice cost +8.5 K, ~21.6 K headroom** (AUDIT_MIDDLEWARE=1 build;
     baseline byte-identical without it). DESIGN §S14.
   **Demo (Nick):** printed relay Mbps + PASS/FAIL verdict; size table.
   **Either gate failing re-plans BUILD-2/BUILD-4 before code.**
4. `[~]` **S15 — BUILD-1+3: udp_port_device + transport factory**
   (BENCHSPEC Stage 1) — two-Pi bench, nereus000 eth0 ↔ nereus001
   eth0 direct, 10.42.0.0/24 (bench check DONE: cable in 2026-08-14,
   1000/full both ends); neighbors + ping + rate limiter measured +
   REV-13 drop counters.
   → CODE + FULL REHEARSAL DONE 2026-08-14 (nibbles 1–2; plan approved
   by Nick): bm_sbc fork `feature/udp-transport` (base 17ea904) =
   BUILD-3 factory (`transport=` key, validate.sh all green) +
   BUILD-1 `udp_port_device` (VPD-derived, REV-11/12/14 invariants
   preserved, 10 Mbps token bucket) + `stream_bench` app +
   `udp_multinode_test.sh` 15/15 (incl. chain ends-do-NOT-neighbor
   invariant). bm_core fork `bench/d4ecc38-obs` = d4ecc38 + ONE
   observability commit (TX/RX L2 drop counters — D27; REV-23
   verified: 17ea904 pins d4ecc38 exactly, zero drift). Bench IPs up
   (.1/.2, never-default; dev stays wlan). Cross-cable rehearsal:
   NEIGHBOR_UP + 🏓 bcmp_seq both ends; limiter 15→9.30 Mbps payload
   (=10.0 wire) with 36,622/36,622 zero loss (throttling = wall
   stretch, NOT drops — measured semantics in D27 + bm_bench README);
   8→8.00 unshaped 19,532/19,532. Node IDs fixed: be9c…01/02/03.
   Repo side: `pi/bm_bench/` (TOMLs, deploy.sh w/ pin verify, README
   demo ladder). Forks created (Nick) + branches pushed; deploy.sh
   PASS on both Pis (its pin check caught a wrong hand-expanded sha
   first — fixed).
   → **DEMOS 1–3 RUN BY NICK 2026-08-14 + re-run/confirmed by Claude
   (identical numbers, both 🏓 paddles, pcap tcpdump-clean): PASS.
   PR #22 open (nibble 4). Remaining: merge.** Demo-1 gotcha now in
   README: multinode pings once at t+3 s → start both nodes within
   ~10 s (or restart the quiet one).
   **Demo (Nick):** NEIGHBOR_UP + bcmp_seq across the cable; limiter
   numbers. = `pi/bm_bench/README.md` demos 1–3. ✅
5. `[~]` **S16 — BUILD-2: AE3 joins the chain** (BENCHSPEC Stages
   2–3) — HE trait device promoted (rpmsg = real wire), HP CDC bridge
   (uart_l2 codec, crash-persistence rule), Pi side = bm_sbc `--uart`
   on the AE3's by-id CDC device. Three-node chain; pub/sub from
   Camera lands at Telemetry (transits Light = L2 forwarding; 2-hop
   BCMP ping labeled as BCMP re-tx); then 2 Mbps ≥10 min, zero CRC
   errors, drop ledger.
   → CODE COMPLETE 2026-08-14 (nibbles 1–2; plan + 3 decision points
   approved by Nick): bites A (bm_net_wire promotion + wire_frag +
   WCMD_STREAM publisher + middleware always-on, node id be9c…03,
   host tests 122, ELF 93.1% of region) · B (bm_bridge.py BridgeCore
   pump + crash/trace persistence, 35 host checks) · C (light.toml
   uart-device; bm_sbc pin +1 commit 4ccbf95 = RX_STAT tx_drops
   transit ledger, deploy.sh PASS both Pis; README S16 demo ladder).
   Design record: D28 + DESIGN §S16 detail. Branch
   `sprint/16-ae3-chain`. **NEXT: VCP gate (Nick's go) → AE3 staging
   + live chain rehearsal → nibble 3 demos → PR.**
   **Demo (Nick):** chain topology + forwarded pub/sub + sustained-rate
   verdict. = `pi/bm_bench/README.md` §S16 demos 1–3.
6. `[x]` **S17 — BUILD-4 apps** (BENCHSPEC Stage 4) *(demo run by Nick
   2026-08-15 — PASS: interactive stream 15 fps to the browser +
   services live; fixture restored + S6 baseline 33.0 fps PASS after;
   PR open. BENCHSPEC Stages 0–4 ALL COMPLETE — stage 5 = ADIN swap-in
   on hardware day)* — light/camera
   services on bm_service/pubsub, gateway_ipc uplink, power HAL sim;
   time sync gated on RTC-backend decision (BENCHSPEC §9.5).
   → CODE COMPLETE 2026-08-15 (nibbles 1–2; plan + 6 decision points
   approved by Nick — D29): bite 0 (capture-relay bench, reef-encode
   rungs F/G — the V16-with-capture number that commits the stream
   rate target) · A (HE camera/control service + WCMD_PUB publish
   path + power HAL sim; size audit 93.9%, ~15.7 K headroom; host
   tests 170) · B (bridge CaptureEngine + chunker; 61 checks) ·
   C1/C2 (bm_sbc fork `apps/bench_apps`: light service on the ACT-LED
   HAL, telemetry subscribe→reassemble→frozen-S3-ingest → browser at
   nereus001:8080, operator CLI, spotter_tx_data uplink + gateway_ipc
   listener; ctest 21 checks; pin move +2 → c1d0df9). RTC = O1 (RAM
   stub + BCMP time-set from Telemetry, zero new code). Demo ladder =
   `pi/bm_bench/README.md` §S17.
   → LIVE 2026-08-15 (fork pushed by Nick; VCP gate opened): both-Pi
   deploy PASS @ c1d0df9 · bite 0 measured (relay-with-capture
   5.262 Mbps/600 s, 15.00 fps held; encoder = the ceiling; D29.6 →
   `stream 2.0 15 60`) · **V5 find #4: upstream bm_core L2
   ingress-nibble vs UDP checksum bug — root-caused via injection
   probe, worked around config-only (CHECKSUM_CHECK_UDP=0), upstream
   item 10 below** · **FULL Stage-4 rehearsal PASS** (LED, 2-hop
   power, capture→browser JPEG, 15 fps stream into the frozen S3 web
   server, uplinks; ledger exact, one known startup-race frame).
   **NEXT: Nick runs README §S17 demos 1–3 → nibble-4 PR. After the
   demo: fixture restore (incl. S16's pending one) + S6 baseline.**
   **Demo (Nick):** capture triggered → stream at Telemetry (browser,
   through Light) → light commanded (LED) → uplink out via
   gateway_ipc — with the drop ledger + the bite-0 number in the
   verdict.
7. `[ ]` **S11 INTERIM 3** — dev-kit-mote reference: bm_sbc + UART
   gateway (see S11). **HARD SAFETY GATE: meter the mote's port cold
   + Nick's explicit sign-off before ANY connection (SPEC §Safety
   absolute); UART side only — the mote's T1L port never touches our
   bench boards.** Needs Nick at the bench — interleave anytime.
   Nibble-1 plan presented 2026-08-14 (deferred, stands as written);
   now doubles as hardware validation of the same uart_l2 gateway
   wire format the S16 CDC leg rides.
8. `[ ]` Upstream report to OpenMV: **D24** — stock `build-firmware`
   docker target flattens per-core build dirs, breaking multi-core
   Alif HE links (root cause + repro + fix in DESIGN D24)
9. `[ ]` Upstream report to OpenMV: **D15** — second `start_stream`
   session per boot hard-faults the AE3 (repro:
   `firmware/ae3_usb/README.md`; re-validate on current build first;
   USB-only, restore fixture after)
10. `[ ]` Upstream report/PR to Bristlemouth: **bm_core L2
   ingress-nibble mutation invalidates inbound UDP checksums on lwIP
   receivers** (found S17 2026-08-15, first-ever inbound pub/sub into
   a bm_core lwIP node): `l2_policy.c bm_l2_policy_rx_apply` mutates
   the IPv6 src addr with no checksum fix-up → udp_input drops every
   pub/sub datagram; TX-side `network_add_egress_port` UDP branch also
   does byte-wise arithmetic on half the 16-bit checksum. Proper fix =
   RFC 1624 incremental update at both mutation sites. Bench
   workaround = CHECKSUM_CHECK_UDP=0 (firmware/bm_he lwipopts.h,
   documented). Repro = the S17 injection probe (DESIGN §S17
   addendum). Pairs with the S16 over-free report.

## Product arc on the bench (added 2026-08-15, Nick) — S18+

*Core-product development on the working S17 bench; all of it
transport-independent (survives the ADIN swap unchanged). Sequence set
by Nick: web bench tool → light intelligence → CV. Upstream bug
reports (items 8–10 above) explicitly HELD for now (Nick).*

**RESEQUENCED 2026-08-15 (Nick), after S18 bite A found HD undeliverable:
S18 bite A is done for QVGA+VGA, then **S19 = HD over pub/sub (whole
sprint)**, then back to S18 bites B–D (the web tool), then light
intelligence and CV. The old S19/S20 stubs are renumbered S20/S21;
D30's text still says S19/S20 and is not being rewritten — history
stays as written (DESIGN rule).*

### S18 — Camera bench web tool  `[~]`  ← **NEXT (D32, fresh agent, own branch)**  *(plan approved by Nick
2026-08-15 — D30; branch `sprint/18-web-bench` from main AFTER PR #24
merges)*
**Goal:** a web control panel on the Telemetry Pi that drives the
camera/light over the BM chain — the standing instrument for image-
quality comparisons.
- [~] Bite A — stack plumbing: **resolution (QVGA|VGA|HD) + pixel
      format (color|mono)** through camera_req_t → wire_capture_t →
      bridge CaptureEngine, **q exposed on the stream command**;
      lockstep ABI update (camera_svc.h + fork structs + BridgeCore),
      host tests, size audit (REV-25 standing).
      → CODE + TESTS DONE 2026-08-15 (nibbles 1–2; plan + 5 decision
      points approved by Nick — **D31**). Front end mocked and reviewed
      BEFORE the ABI was cut, which is what surfaced HD greyscale in
      time to land it in the reserved byte instead of forcing a second
      lockstep break. `camera_req_t` 16→18 B, `wire_capture_t` 12→14 B
      (`"<BBHIHHBB"`), `camera_rep_t` unchanged at 24 B (rsvd u16 →
      res_active/pf_active). Out-of-range geometry **REFUSED (ok=0)**,
      not clamped. Bridge switches the sensor **only on a delta** via a
      pure host-tested `sensor_steps()` planner (D15 guard); VGA+ gets
      `set_framebuffers(1)` per the S0 measurement. Host tests HE
      170→191, bridge 61→73, all green; fork ABI offsets verified to
      match byte-for-byte. **Size: 246,096/262,144 = 93.88%, 16,048 B
      headroom (+64 B).** ELF `4be541ae…`.
      → **DONE 2026-08-15 for QVGA + VGA; HD deferred to S19.**
      Nibble 3 run by Claude on the live chain (Nick: "run the
      checks"): QVGA `frames_ok=2 gaps=0` fresh frame at the browser ·
      **VGA 640×400 / 11,030 B delivered, gaps=0** — the command that
      took the board off the USB bus twice before the fix · refusal
      path returns ok=0 as designed. Fork pushed `ba594ec`; both Pis
      deployed; board restored to the S6 fixture 55fa6ccf… .
      **Three board lock-ups en route bought the sprint's biggest
      fact** (SPEC §Open questions): with the HE ELF loaded at
      0x60080000 (SRAM9_B upper half), GROWING the framebuffer takes
      the board off the USB bus with no catchable error. Fixed by an
      eager `bootstrap()` that claims the ceiling before `he.start()`
      plus a pin of `set_framebuffers(1)` immediately before every
      resize; a hard ceiling guard means a web click can never brick
      the bench. Bridge host tests 61 → **252**.
      **HD then hit a SECOND, unrelated wall** — it captures fine but
      the HE core's heap dies mid-publish (`freertos: malloc failed`
      after 8 of 26 chunks). That is now all of S19.
*(Bites B–D were PAUSED behind S19 — Nick, 2026-08-15. **UNPAUSED
2026-08-16 (Nick): S18 is now NEXT**, ahead of S19's remaining bites,
and it goes to a **fresh agent on its own branch**. The pause existed
because a bench tool that cannot show the top of the resolution ladder
answers the wrong question — HD now works (S19 bites 1–2), so the
reason is gone.*

***Bite D is promoted to FIRST and is no longer optional*** *— see the
plan recorded under it. S19's session lost hours to hand-run processes:
two Telemetry instances wedged the single-producer ingest twice, and a
leftover 600 s stream corrupted a third run. Both are impossible once
the nodes are systemd units. Do the harness before the features.)*

> **~~⚠ BRANCH HAZARD~~ — RESOLVED 2026-08-16, before S18 resumed.** It
> read: `sprint/19-hd-transport` is unmerged, so a branch cut from `main`
> would not contain the source for the artifacts the AE3 is running. That
> is no longer true — **PR #26 (S18 bite A) and PR #27 (S19) are both
> merged; `main` is at `438f35d`** and `git log main..sprint/19-hd-transport`
> is empty. Verified rather than assumed: `firmware/bm_bridge/bm_bridge.py`
> on `main` hashes to **`1524f6c203f232a0`**, byte-identical to what the
> board is running. The S18 branch (`sprint/18-bench-tool`) is cut from
> `main`. **The Pi checkouts are still stale** — both are on
> `sprint/18-web-bench` and need moving to the S18 branch before a demo.
- [~] Bite B — fork app: **loopback-only control socket** on the
      telemetry role (JSON command in / JSON status out: last replies,
      current params, live receiver ledger) + **still-save** to
      `~/bench_captures/` with JSON sidecars (all params + measured
      stats at capture time). Fork pin move — Nick pushes.
      → **CODE + TESTS DONE 2026-08-16** (nibbles 1–2; plan + 4 decision
      points approved by Nick — **D34**). Branch
      `sprint/18-bench-control` (`e05b653`), fork
      `feature/udp-transport` **`8c0ff7a`** (pushed by Nick; the harness
      classifier blocks the agent from pushing to the fork).
      **Pi-side only — no camera_svc.h, wire or HE firmware change**, so
      the AE3 keeps running the S19 artifacts and there is no ABI
      lockstep and no size audit in this bite.
      Shipped: `apps/bench_apps/bench_ctl.h` (the whole parse/render
      surface, no OS calls) + `tests/test_bench_ctl.c` **98 checks**
      registered in the fork's ctest · the AF_UNIX SOCK_DGRAM socket at
      `/run/bm/bench.sock` on the gateway_ipc pattern · still-save with
      sidecars (`.tmp`+rename, JPEG before sidecar — the sidecar is the
      commit record) · repo side `bench_ctl.py` / `bench-ctl.sh` /
      `S18_CAPTURE_DIR` in the unit / socket + capture-dir checks in
      `chain_status.sh` / `test_bm_units.py` 33 → **43** checks / pin
      bump / README §S18 bite B.
      → **LIVE 2026-08-16**: both Pis deployed at the new pin, telemetry
      unit reinstalled, `chain_status.sh` PASS on both, socket answered
      first try. **Sidecar verified exact** (`size_bytes` == the file on
      disk; chunks × 10 B + JPEG == the `pub_bytes` delta). Verified
      stills: QVGA colour 320×200 3,936 B/3 chunks · **QVGA mono
      320×200, 1 component, 2,910 B — the first greyscale frame this
      project has carried over the chain** · VGA colour 640×400
      10,909 B/8 chunks. Every row checked against the JPEG's own SOF
      header (geometry AND component count), not against an exit code.
      → **MERGED as PR #29.** The `(null)` reply-state fix was part of it;
      no live status read during the bite C1 demo (2026-08-16) showed
      `(null)` — `cam_reply.state` renders `none`/`ok`/`timeout` — but
      that was observed, not diffed against the fork source, so confirm
      before relying on it.
- [~] **Bite B2 — the sensor re-init race (NEW, found by bite B's trial
      matrix). Sequenced AFTER bite C (Nick, 2026-08-16)** — it is a
      fast-click hazard that bite C mitigates in the UI, not a blocker
      for having a page. It still owes the sprint the full 9-row matrix
      and the first stream numbers, which is what turns bite C's
      feasibility model from extrapolated into measured. A sensor re-init arriving too
      soon after a capture throws `Sensor control failed.` and wedges the
      sensor for the bridge's whole life, while the HE keeps replying
      `ok=1` — full measurement in SPEC §Open questions. It has been
      there since bite A; nothing caught it because **mono was never run
      end to end** (bite A's README ladder lists a `vga mono` step, but
      nibble 3 only ran colour + the refusal path).
      **Fix lives in `firmware/bm_bridge/bm_bridge.py` — bridge only, so
      NO fork push and NO HE rebuild.** Shape depends on the mechanism,
      which is NOT yet established: measure first (S0 discipline).
      **Nibble 1 = run `bench/probes/s18_reinit_probe.py` off-chain with
      the HE core NOT loaded** (written this session, never executed —
      it needs a neutral `/flash/main.py`, because `mpremote run` soft-
      resets into the bridge launcher, which then holds the VCP).
      **ONE BOARD WINDOW, and it belongs to this bite (planned 2026-08-16,
      bite C2 nibble 1).** The AE3's `/flash/main.py` is still the bridge
      launcher (`170e637c…`), not the S6 fixture (`55fa6ccf…`) — bite D's
      outstanding restore. Do **not** restore it before C2's demo:
      `demo_up.sh` stages the bridge launcher, so a restore now is
      overwritten within the hour and costs three port sessions instead of
      one. Correct order: C2's demo runs on the bench as staged → **then**
      one window that stages the neutral `main.py`, runs the probe, and
      **leaves the fixture in place**, which also discharges the standing
      session-end restore. Per `ae3-board-access`: ≥60 s of zero port
      contact, ONE `mpremote` operation per invocation, no `+` chaining,
      no retry loops, and a normalised read-back is the proof, not `rc=0`.
      If the sensor pipeline is the cause the fix is local waiting or a
      flush; if the HE is the cause, the bridge already parses
      `wire_status_t.stream_sent` and can gate the re-init on it.
      **Decided with Nick 2026-08-16:** propagating the bridge's refusal
      into the HE reply (`ok=0` instead of a lie) is a **lockstep ABI
      change and is deferred** — parts 1+2 stop the wedge happening, so
      the lie stops happening in practice. File it, don't ship it now.
      → **NIBBLE 1 DONE 2026-08-16 (plan approved by Nick; branch
      `sprint/18-reinit-race` from `main` @ `ba2f4c6`). MECHANISM FOUND,
      and the severity is worse than bite B recorded.** One board window,
      three rungs, one ingredient at a time: **no HE core = 12/12 PASS**
      (so the sensor pipeline is NOT it, and "quiet time scales with
      frame size" does not survive — 0 ms after a 35.7 KB HD frame is
      fine); **HE loaded but idle = 9/9 PASS**; **HE loaded AND
      publishing = the board went off the USB bus on the first measured
      re-init** (4,051 B QVGA frame → its real 3-chunk WCMD_PUB burst,
      0 send timeouts → `set_pixformat(GRAYSCALE)` → `error -71`,
      `unable to enumerate`; recovered by `sudo reboot` on nereus000).
      **So the fix cannot catch-and-recover — with a publish in flight
      there is no Python exception to catch. It must PREVENT the
      overlap**, which is the `stream_sent` gate the bridge can already
      see. Full record: DEV_LOG + SPEC §Open questions.
      **Until it lands, bite C1's 8 s settle guard is safety equipment,
      not politeness** — it is what stands between a fast double-click
      and a bench that needs a reboot. Do not shorten it.
      **Nibble 1 also left a reproducer worth as much as the finding:**
      `bench/probes/s18_reinit_probe{,_b,_c}.py` reproduce this
      **off-chain in ~4 minutes with no Pi chain**, and rung C is
      nibble 2's acceptance test.
      **The AE3 fixture restore outstanding since bite D is DISCHARGED** —
      `/flash/main.py` is the S6 fixture, proved by an on-board sha256
      (`55fa6ccf…`) both before the probes and after the recovery reboot,
      with no HE core loaded.
      **The 9-row matrix and the first stream numbers are deferred behind
      the fix** (they need the chain and a healthy board; the hazard
      closes first). Remaining: nibble 2 (the fix + host tests) → 3 → 4.
      → **NIBBLES 2–3 DONE 2026-08-17 (an honest, winding road — full
      record in DEV_LOG).** The clever fixes died by experiment: rung D
      (gate on "publish drained") opened exactly as designed and the
      board died anyway; rung E (add measured rpmsg quiescence, zero
      late messages) failed politely and reproduced bite B's wedge
      off-chain. **The hazard is wall-clock time since the publish**,
      scaling ~1.5 s/KB of published bytes across every measured point.
      **Shipped fix (`PublishGate` + `REINIT_MIN_QUIET_MS = 20 s` flat,
      Nick's call) + catch-and-self-heal** (0/4 observed successes — the
      latch on failed heal is the allocator rule and stands). **Proven
      on-chain:** the exact bite-B fast pair delivers both frames (mono
      held 6.3 s under the earlier 6 s build, artifacts header-verified);
      ladder: VGA-source 10 s FAIL / 15 s PASS (dark frames);
      **HD unmeasured — the reef-matrix session owes it before anyone
      lowers or trusts the constant for HD.** `bench_web` settle raised
      to 20 s to match. Bridge tests 262→419; bench_web 67.
      **New standing recovery (Nick):** camera stuck → reboot nereus000
      (USB teardown + quiet-exit + demo_up chip reset ≈ 2 min); uhubctl
      remains measured-useless on Pi 5. ~~**`/flash` still carries the 6 s
      build — the 20 s build deploys at the next chain session.**~~
      → deployed 2026-08-18 by the matrix session (demo_up now sha-syncs
      the bridge every run). Remaining: nibble 4 (PR), then ~~the matrix
      + stream numbers~~ → ran as bite B3 below; the HD certification
      rung could NOT run (see B3's findings) — the constant keeps its
      uncertified-for-HD caveat, reason now recorded.
- [~] **Bite B3 — the reef-scene matrix** (Nick approved plan
      2026-08-17; branch `sprint/18-bench-matrix`). **Nibbles 1–3 DONE
      2026-08-18** — plan, code + tests (bridge `scene:"ref"` source,
      demo_up staging/sha-sync/trace-preservation arms, row-isolated
      `bench/s18_matrix.py`; bridge tests 419→**436**, driver **37**,
      bench_web 67→**70**), five live runs driven end to end per the
      handover rule. Measured: all six non-HD stills **byte-identical
      to S0's reef table**, q-curve points, regression 15.15 fps ×3,
      **QVGA color ceiling 28.07 fps / 2.08 Mbps**, **VGA color
      7.40 fps / 1.74 Mbps** (each ×2 identical), in-bridge encode
      corroborating S0 within ~2%, measured derate 0.56–0.58. Page
      MEAS_FPS filled + provenance labels flipped where measured;
      verified through the page's own endpoints. Full record: DESIGN
      §S18 reef-matrix detail + D37, SPEC §Open questions, DEV_LOG.
      **Still owed, fenced off by the findings (not by this bite):**
      the 3 HD still rows, true mono/HD stream ceilings, and B2's 20 s
      certification rung. Unblocking them = two candidate bites for
      Nick to size: **(a) the HE flood wedge** (wire task goes mute
      ≥ ~513 rpmsg msg/s, mechanism traced in a preserved trace),
      **(b) HD stability on the PublishGate bridge** (ref-mode hard
      fault — bridge now REFUSES HD ref, guard tested; sensor-mode leg
      wedge; **HD has never completed on any post-B2 build**).
      **Remaining: nibble 4 (PR).**
- [~] **Bite B4 — HD stability (branch `sprint/18-hd-stability`,
      2026-08-18). NIBBLES 1–3 DONE — root-caused, FIXED in firmware,
      certified and measured end to end; remaining: nibble 4 (PR).**
      Nibble 1 (five probes G–G5, `bench/probes/s18_hd_gate_probe*`):
      matrix findings 2+3 were ONE fault — **sensor transitions degrade
      the board only while the HE core is resident** (no-HE control
      52/52; HE-resident died at #10/#22 with zero traffic; publish/
      barrier/HD/mono/grow each exonerated; in-session heal 0/6) —
      source-corroborated to OpenMV's per-resize framebuffer
      free+malloc (framebuffer.c:158). Also closed from source: the
      PAG7936 ladder is exactly QVGA/VGA/HD — nothing between VGA and
      HD exists (pag7936.c:691,933–940). Nibbles 2–3 (Nick: plan A,
      shape A2, guard-until-measured, upstream HELD): **sticky-fb
      firmware patch** (`firmware/openmv_patches/`, +9/−1) built via
      D23/D24 at `7d4dbf7`+patch, S7-ladder flashed byte-verified
      (rollback = `~/fw/development/`); **G3 soak 40/40 HE-loaded**
      (stock died at #22); bridge `df82aa70…` = refusals hoisted above
      the re-init (order bug) + HD-ref guard lifted on probe G6's
      measurement (both HD refs load+encode, HE resident); host suite
      436→**444**, negative control proves the new tests catch the old
      order. **On-chain: `capture 50 hd color`/`hd mono` ×3 each,
      SOF-verified, ledger exact — HD's first completions on a
      PublishGate build. Matrix HD stills byte-exact (mono 75,324 B /
      color 93,253 B vs the in-bridge encode); B2 CERT RUNG PASS
      (20.02 s) — the 20 s constant is HD-certified; first HD stream
      numbers: ref HD mono 1.50 fps / 0.91 Mbps ledger-exact, sensor
      HD color ~1.4 fps (S19 bite 4's number), VGA mono 4.98 fps
      clean.** Fenced to other bites, artifact-evidenced: HD q90-class
      bursts (≥ ~83 chunks/frame) lose chunks in the relay (finding 1);
      ref-mode HD-color reloads need 2 MB contiguous MP heap and fail
      in long sessions (fix candidate: preload refs at bridge boot).
      Page MEAS_FPS for HD deferred deliberately — the measured HD
      streams are FLOORS and the page model has no floor label yet.
      Session detail: DEV_LOG ×2 entries, SPEC §Open questions
      (resolution + ladder), DESIGN D38.
- [x] **Bite B5 — bench_ctl survives a bm-telemetry restart** *(was
      also numbered B4 by its own session — renumbered here at the
      merge; **MERGED as PR #35**)* (bug
      observed live on nereus001 2026-08-18 during the board-probe
      session; fix shape specified by Nick with the capture). A connected
      AF_UNIX SOCK_DGRAM socket points at the *socket*, not the path, so
      restarting bm-telemetry (which rebinds `/run/bm/bench.sock`) killed
      the held connection permanently: every later request raised raw
      `OSError` ENOTCONN (107), and bench_web — which only catches
      `BenchCtlError` — dropped the HTTP connection (HTTP 000) instead of
      answering 503. **Pi-side only, no board contact.**
      → **CODE + TESTS DONE 2026-08-18** (branch
      `sprint/18-benchctl-reconnect` from `main`). Fix in
      `bench_ctl.request()` alone: dead-socket errno → close + reopen
      once + retry the same request; rebuild failure → the existing
      socket-down `BenchCtlError` (bench_web's tested 503 path); all
      other socket OSErrors wrapped, so no caller ever sees a raw
      OSError. bench_web unchanged. New host suite
      `pi/bm_bench/test_bench_ctl.py` (**6 checks**, real AF_UNIX
      sockets, socket-recreated-between-requests simulated; the key test
      FAILS on the pre-fix client). bench_web 70 checks still green.
      Cross-platform errno fact measured before coding: Linux =
      ECONNREFUSED/ENOTCONN, macOS = ECONNRESET/EDESTADDRREQ —
      `RECONNECT_ERRNOS` carries all four. Nibble 3 (Nick's manual
      restart test) folded into the merge; the HD-stability session's
      "bounce bench-web after a telemetry restart" ops note is retired
      by this fix.
- [ ] **Bite C — NEXT (Nick, 2026-08-16). The page comes before the
      re-init fix.** Checked for a real blocker and there is none: the
      control socket bite C talks to is deployed and answering, and
      QVGA/VGA stills + streams work at a sane command cadence. The
      re-init race (bite B2) is a **fast-click hazard, not a wall** — so
      bite C carries the mitigation instead of waiting for the fix:
      **the UI disables its capture/stream controls until the previous
      capture completes, plus a settle**, which is what an operator would
      want anyway. Bite B2 then removes the hazard underneath.
      Constants: build against the **approved mockup, now in the repo at
      `docs/mockups/s18_bench_mockup.html`** (Nick approved it
      2026-08-16; `docs/mockups/README.md` lists what carries over and
      what must change). It carries the reviewed layout, `RES`/`MEAS`
      tables, `BRIDGE_DERATE`, histogram panel, warning box, pill and
      compare view). Its fps model is EXTRAPOLATED from one measured
      point — label it as such in the UI until B2's matrix replaces it
      with measured numbers.
- [~] **Bite C1 — the page that drives the bench.** *(Bite C split into C1
      + C2, Nick approved 2026-08-16 — **D35**. The TRACKER's single bite C
      was ~700 LoC; the split is on the seam the mockup itself has, live
      control vs stored captures.)*
      → **CODE + TESTS DONE 2026-08-16** (nibbles 1–2; plan + 5 decision
      points approved by Nick). Branch `sprint/18-bench-web` (`14e8446`),
      cut from `main` @ `18349ed`. **Pi-side only — no fork change, no
      `camera_svc.h`, no wire, no bridge or HE firmware**, so no pin move,
      no ABI lockstep, no size audit, and no board contact.
      Shipped: `pi/bench_web/bench_web.py` (:8090, stdlib `http.server`,
      driving bite B's socket through `bench_ctl.py` so the front ends
      cannot drift) · `static/bench.html` carrying the approved mockup's
      layout, CSS and model and **deleting its simulation** · the live view
      as an `<img>` at the frozen S3 server's `/stream` (no frame bytes
      through this server, `:8081` untouched) · commanded-vs-actual pill and
      receiver ledger from real `status` · warnings labelled
      **EXTRAPOLATED** everywhere · `pi/services/bench-web.service` +
      installer arm, installed **disabled** · **42 host checks**.
      **The click guard is enforced in PYTHON and only mirrored in JS** — a
      reload or a second tab walks past a browser-side guard, and what it
      prevents is a camera wedged for the bridge's life. Two holds: *busy*
      (one command at a time) and *settle* (8 s, **only** for a command that
      changes res or pf, because only a genuine delta re-inits the sensor).
      `stop` is never gated.
      **Two traps found by reading source rather than assuming**, both now
      tested: `mode_active` is *last commanded*, not *currently busy* (it
      stays 1 after a still; only `stop` clears it), and `save.state` still
      reads `saved` from the PREVIOUS capture at the moment of arming — so
      the obvious gate would have released one poll after the click and let
      the fast second click through. Completion comes from the monotonic
      save counters.
      → **LIVE on nereus001 2026-08-16**: checkout on the branch, unit
      installed + started, host tests re-run **on the Pi** (42 OK), page
      served, `/api/status` returning the real ledger through the real
      socket, and the socket-down path answering 503 with the fix.
      **NOT verified: the embedded `<img>`** — the agent's sandboxed browser
      blocked `nereus001:8080` (`ERR_BLOCKED_BY_CLIENT`) while serving
      `:8090` fine; the S3 server answers 200 on `/stream` and `/frame.jpg`
      from the Pi, so that is Nick's to confirm in a real browser.
      → **DEMO RUN AND APPROVED BY NICK 2026-08-16 → nibble 4 (PR).**
      Live proof after re-staging: `cam_reply state=ok ok=1 cmds=3
      pub_ok=3 pub_errs=0`, ledger `frames_ok=2 gaps=0 dropped=0
      ingest_ok=2`, save `saved`, `/frame.jpg` 200, and the artifact
      checked against its OWN header — `SOI ffd8 … EOI ffd9`,
      `SOF0: 320×200, 3 components`, not against a status field.
      **The demo also found the phase-1 quiet-exit trap** (below) and one
      gap in the page, filed for C2: **a `cam_reply.state` that is not
      `ok` is reported too quietly.** Every capture logged `200 ok`
      because the socket ACCEPTED it; the camera's real answer
      (`timeout`) sat in a stats row, so seven captures went into a dead
      node before anyone noticed. Deliberately NOT patched into this
      bite — the demo passed on the code as it stands, and changing the
      demoed artifact after approval needs its own gate.
      **NEW STANDING OPS RULE (measured this session, README §Start
      order): command the camera within ~30 s of starting `bm-light`.**
      The bridge quiet-exits after 30 s of no VCP traffic and can do so
      in **phase 1, after the BM neighbor is already up** — measured
      18:33:10 neighbor up → 18:34:00 `UART link down`, with
      `bridge_crash.txt` recording `exit: main() returned cleanly` (it
      quit, it did not crash). A **light command does not count**: that
      service runs on nereus000's own Pi and never crosses the CDC leg.
      **Both Pis are on the branch @ `8431690`**; nereus000's `bm-light` is
      inactive and the AE3 is not staged, so the camera half of the demo
      starts at `demo_up.sh`. (An ssh to nereus000 hung past 120 s and was
      recorded here as "unreachable" — it was a **Tailscale SSH re-auth
      prompt**, invisible in a piped command. Corrected the same session.)
- [~] **Bite C2 — gallery, compare, histograms, banner.** Gallery from
      `~/bench_captures/` sidecars, side-by-side compare view, RGB+luma
      histograms (canvas, client-side), plus the C1 follow-up: a non-`ok`
      `cam_reply.state` raises a banner instead of sitting in a stats row.
      **On the critical path: the S18 demo line requires the compare view
      and histograms, so the sprint cannot close without this bite.**
      → **CODE + TESTS DONE 2026-08-16** (nibbles 1–2; plan + 5 decision
      points approved by Nick — **D36**). Branch `sprint/18-bench-gallery`,
      cut from `main` @ `db82181`. **Pi-side only — no fork change, no
      `camera_svc.h`, no wire, no bridge or HE firmware, and NO BOARD
      CONTACT**, so no pin move, no ABI lockstep, no size audit.
      Shipped: `/api/captures` (enumerates **sidecars**, newest first) ·
      `/captures/<name>.jpg`, this server's first file-reading route,
      fenced three independent ways (decode-then-whole-name-match; a
      sidecar must exist; `realpath` must land back on the assembled path —
      the only fence that catches a symlink planted *inside* the dir) ·
      `/api/frame.jpg`, a same-origin proxy of the frozen S3 server's
      cached frame, **required** because an `<img>` on :8080 taints the
      canvas and `getImageData` throws · the histogram engine carried from
      the approved mockup, live and per-still, with **greyscale decided by
      the pixels (R=G=B), not by the commanded format** · compare view
      dropping the right column · the banner, three triggers, clearing only
      on a good reply. Host tests **42 → 67**.
      **Two things reading the fork's source changed:** on a timeout
      `cam_seen` is set true and `cam_state="timeout"` while the reply
      struct is **not** updated (`ctl_note_cam(nullptr, ...)`), so every
      other `cam_reply` field is the last GOOD reply — the banner keys on
      `state` alone and the page labels the rest STALE. That staleness is
      exactly why the C1 failure read as normal.
      **Found by testing against a running server rather than unit tests
      alone:** a non-ASCII character in a `send_error` *message* lands in
      the HTTP status line, fails latin-1, and **drops the connection
      instead of answering 404**. Fixed (reason moved to the body) and
      regression-tested.
      ~~**NOT verified: the page in a real browser.**~~ The agent could not
      render it (the sandboxed pane refuses `localhost`, the Chrome
      extension was not connected), so it shipped with host-side structural
      tests only — every `getElementById` target exists, braces/backticks
      balance. **Closed by the demo below**, which is a real browser.
      → **DEMO RUN AND PASSED BY NICK 2026-08-16 → nibble 4 (PR).** The
      page renders, the gallery lists the stored captures, the compare view
      and the histograms work, and the banner fires on a dead camera node.
- [ ] Bite C (original scope, for reference) — `pi/bench_web/` (stdlib python, S3-server pattern):
      controls (resolution/q/fps/rate/secs, capture/stream/stop, light
      level+strobe), embedded live `/stream`, **commanded-vs-actual
      pill** (receiver-ledger fps + Mbps, ~1 Hz), **feasibility
      warnings** (client-side model from measured constants — encode
      ms/resolution, chunk overhead, 5.26 Mbps relay ceiling; yellow =
      fps will cap, red = exceeds transport; labeled estimates),
      **gallery + side-by-side compare view**, **RGB+luma histograms**
      (canvas, client-side, live + per-still — the OpenMV-IDE-style
      levels view).
- [x] **Bite D — DO THIS FIRST (promoted 2026-08-16, Nick). systemd
      units for the bench nodes — no longer optional.** *(acceptance run
      on the live chain 2026-08-16 by Claude at Nick's "follow all these
      steps yourself and verify" — all three items PASS; PR pending.)*
      Plan below was
      written at the end of the S19 session and reviewed by Nick; the
      fresh agent should re-derive rather than trust it blindly, but it
      is a starting point, not a blank page.
      **Why it is first:** the S19 session lost hours to hand-run
      processes. A systemd unit is a **singleton by construction**, and
      that alone removes the failure that wedged demo 2 twice (two
      Telemetry instances on the single-producer S3 ingest; the loser's
      socket buffer fills at 2,592,256 B ≈ 1,416 frames and it hangs at
      exactly `t=109` — DEV_LOG 2026-08-16). It also kills `pkill -f`
      pattern games (the pattern kept matching the driving SSH command
      line), `nohup`/stdout-buffering workarounds, and manual start
      ordering.
      Shape (≈150 LoC): `pi/services/bm-{light,telemetry}.service` on the
      `t1l-stream-server.service` pattern, `Restart=on-failure`
      (absorbs the fork app's occasional startup segfault, below);
      **stdin problem** — the apps take `capture`/`stream` on stdin and
      a service has none, so `ExecStart=/bin/sh -c 'tail -f
      /run/bm/telemetry.cmd | exec …bench_apps …'` plus a
      `bm-cmd.sh` helper that appends (**verify the app tolerates that
      stdin before writing the units** — untested); extend the existing
      role-dispatching `pi/install_stream_service.sh` rather than adding
      an installer; **`chain_status.sh` preflight** (unit states,
      instance counts, `ss -tn | grep -c :8081` = one producer, AE3
      by-id present); README start order rewritten to `systemctl start`
      + `journalctl -u bm-telemetry -f`.
      Decisions Nick has NOT ruled on: install **disabled** (auto-start
      would open the AE3's CDC port at boot and fight the fixture/dev
      loop) vs enabled; whether AE3 staging stays the manual
      `demo_up.sh` (recommended — starting a service should not rewrite
      board flash) or becomes a oneshot unit.
      **Acceptance = the bug that caused it:** `systemctl start
      bm-telemetry` twice leaves ONE process and ONE ingest producer;
      then a 600 s demo-2 run under units; `systemctl stop` provably
      leaves zero processes.
      → **CODE + PARTIAL REHEARSAL DONE 2026-08-16** (nibbles 1–2; plan
      + 5 decision points approved by Nick — **D33**). Branch
      `sprint/18-bench-tool`, cut from `main` (hazard above resolved).
      **The plan was re-derived and the TRACKER's sketch changed on two
      points, both from reading the fork's source rather than trusting
      the note:** (1) **only the telemetry role has a CLI** —
      `bench_apps loop()` calls `cli_poll()` only in the non-light
      branch, so `bm-light` needs no command channel at all; (2)
      **`tail -f` is the wrong mechanism** — `cli_poll()` is already
      non-blocking (`poll(fd 0, timeout 0)`, guarded on POLLIN,
      returning on EOF rather than exiting), so the app can open a FIFO
      **read-write itself** (`sh -c 'exec … 0<>/run/bm/telemetry.cmd'`):
      POSIX `<>` never blocks on open and never reaches EOF, and `exec`
      keeps **one process in the cgroup** where a pipeline would put a
      second one back. Also measured, retiring a planned mitigation:
      bm_sbc already does `setvbuf(stdout, _IOLBF)` and `bm_log`
      fflushes, so **the S19 "stdout buffering" was on the driving side,
      not the app** — no `stdbuf` wrapper needed.
      Shipped: `pi/services/bm-{light,telemetry}.service` ·
      `pi/bm_bench/bm-cmd.sh` (refuses to write when the unit is down —
      a command into an unread FIFO looks exactly like one that worked) ·
      `pi/bm_bench/chain_status.sh` (finds processes by
      `/proc/<pid>/exe`, **never** by command-line pattern — the S19
      `pkill -f` trap made structurally impossible) ·
      `install_stream_service.sh` extended with `light|telemetry`
      installed **disabled** · `pi/services/test_bm_units.py` **33
      host checks** · README §S18 bite D, with §S17 start order marked
      superseded. Two additions beyond the approved list, both one-line:
      `ExecStartPre=+/bin/chmod` on the ACT LED sysfs (retires the manual
      per-boot chmod in §S17 deploy) and `SyslogIdentifier=` (the journal
      tagged lines `sh[pid]` without it — found in rehearsal).
      **REHEARSED on nereus001, Telemetry only, ZERO camera contact**
      (that role never opens the CDC leg): double `systemctl start` →
      **one PID, `NRestarts=0`** · `bm-cmd.sh status`/`help` answered
      live in the journal · **0 s CPU over 10 s elapsed** (the FIFO poll
      does not spin) · `systemctl stop` = 1.06 s, **zero processes**,
      `/run/bm` removed. Bench restored to exactly as found (unit
      uninstalled, no processes, stream server active).
      → **NIBBLE 3 DONE 2026-08-16 on the live chain — ALL THREE
      ACCEPTANCE ITEMS PASS.** Units installed on both Pis from
      `c0b57b0`; installed-file sha verified identical to the repo on
      both, `NeedDaemonReload=no`.
      **(1) Double start = no-op.** `systemctl start` twice on each unit:
      MainPID unchanged (telemetry 95020, light 4289), **one process
      each, `NRestarts=0`.** The wedge cannot be re-created by hand.
      **(2) 600 s stream under units: `stream 2.0 15 600` → 9,092 frames,
      15.15 fps avg, 643 TEL_STAT lines and NOT ONE with a nonzero
      `dropped/gaps/hdr_errs/q_drops/ingest_fail`**, one producer on
      `:8081` throughout, zero restarts. Same frame count as S19's
      bridge ledger for the equivalent run.
      **(3) Stop is real, and it stops the camera.** With **585 s of
      stream still commanded**, `systemctl stop` took 1.06 s and left
      **zero processes, no `/run/bm`, ingest released**; on restart
      `cam-status` twice 8 s apart returned **identical `pub_ok=19594
      pub_bytes=18561473` with `mode=0`** — the AE3 had stopped, which is
      S19's second contaminator eliminated. (This was the one path with
      no rehearsal behind it.)
      Also proven live en route: the FIFO CLI carries real camera work —
      `capture 50 qvga color` then **`capture 50 hd color` → 1280×800,
      20,669 B, valid SOI→EOI at `:8080/frame.jpg`, `pub_errs=0
      gaps=0`** (20 KB not 42 KB because the room is dark, matching the
      S19 record; it cannot be a stale frame — the only earlier frame
      this session was QVGA). `chain_status.sh` PASS on both hosts before
      and after. `SyslogIdentifier` confirmed live (`bm-telemetry[95020]`,
      not `sh[...]`); the LED `ExecStartPre` confirmed by
      `LIGHT_STAT … led=sysfs`.
      **Teardown:** both units stopped (zero processes both hosts), ACT
      LED trigger restored to `[mmc0]` and permissions tightened.
      **AE3 NOT restored — `/flash/main.py` is still the bridge launcher
      (`170e637c…`), not the S6 fixture (`55fa6ccf…`).** Board flash
      writes were blocked for the agent this session; the restore is one
      command for Nick (README §S18 bite D / DEV_LOG). **Remaining:
      nibble 4 (PR).** → **DONE: merged as PR #28.**
- [ ] Bite D2 — demo ladder + docs (the remainder of the old bite D).
**Demo (Nick):** `demo_up.sh` → open the bench page → capture q50 and
q90 stills → compare view shows both + histograms → start a VGA stream
→ the warning predicts and the pill confirms the fps drop.
**Scope calls (Nick may override):** ~~resolutions QVGA+VGA only
(HD-mono later)~~ — **OVERRIDDEN by Nick 2026-08-15 at the mockup
review: HD 1280×800 is in now**, offered for stills in colour and for
**video in greyscale** (HD colour ≈1 fps in-bridge vs HD mono ≈2.5 —
low, and useful to watch live). The tool switches pixel format rather
than dropping resolution when you start a stream on HD. Measured limits
that set this ladder (DESIGN §S0): the sensor letterboxes to 16:10
(QVGA = **320×200**, not 320×240), QQVGA/SVGA/WXGA are unsupported on
sensor 0x7936 — so there is **no 720 mode** — and nothing above HD has
been tested, so nothing above HD is offered. Warnings stay client-side
from measured constants, not camera-queried.

### S19 — HD stills over pub/sub  `[~]`  *(PARKED 2026-08-16 behind S18 — D32. Bites 1–2 done (code+rehearsal), bites 3–4 wait. Scope set by Nick
2026-08-15 after the S18 bite-A rehearsal. **This is the WHOLE sprint** —
no web-tool work, no new features: just make HD capture and transport.
S18 bites B–D wait behind it, because a bench tool for comparing image
quality is not worth much if it cannot show the top of the ladder.)*
**Goal:** `capture <q> hd color` and `capture <q> hd mono` land a
complete, valid HD JPEG at the Telemetry node over BM pub/sub, with an
exact chunk ledger and no `malloc failed`.

**The known failure, measured (SPEC §Open questions, S18 rehearsal):**
HD *capture* already works — the HP ledger shows `cap_bytes=54,232`,
26 chunks — but the HE core dies publishing them: **`freertos: malloc
failed` after 8 of 26 chunks**, then the camera service stops
answering until the bridge restarts. QVGA (3 chunks) and VGA (8) drain
fine. The board stays on the USB bus: ordinary heap exhaustion, not the
S18 allocator fault.

- [~] Bite 1 — **measure before fixing** (S0 discipline): instrument the
      HE heap (`xPortGetFreeHeapSize` / minimum-ever) and log it per
      published chunk, so the drain curve is a number rather than a
      theory. Find the actual chunk count / rate where it falls over,
      at QVGA and VGA too — is 26 the wall, or is it bytes-in-flight?
      → **RUNG B DONE 2026-08-16 (Claude drove it; nibbles 1–2 approved
      by Nick). ANSWER: BYTES IN FLIGHT, not chunk count.** Instrument =
      `he_sample.{c,h}`, a 1 KB fixed page at 0x600BFA00 written one
      record per published chunk (+456 B → **94.05%, 14,056 B
      headroom**; no ABI change, no fork pin move). Probe =
      `bench/probes/s19_pub_probe.py`, synthetic bursts with **no Pi and
      no camera**, framing asserted byte-identical to the production
      chunker. Measured: free heap at RUNNING **20,712 B**, one 1,400 B
      chunk costs **exactly 1,488 B**, → **13 chunks fit, the 14th dies**
      (three independent rows, `freertos: malloc failed` in the ring =
      S18's signature reproduced off-chain). **26 × 350 B is fine**, so
      count is not the wall. No leak — the heap recovers fully after
      every surviving burst. **Mechanism: the wire task both receives
      WCMD_PUB and drains the TX queue; `rr_poll()` loops until the
      inbound vring is empty, publishing inline, and `wire_pump_tx()`
      only runs after it returns — a back-to-back burst starves the
      drain.** Full table + arithmetic: DESIGN §S19 detail.
      **Rung C folded into bite 2** (Nick, 2026-08-16) and run there.
      **Correction to this bite's mechanism claim:** the `drain=True`
      rows were invalid — the probe popped its own list without yielding
      to MicroPython, so no vring buffer was recycled and "HP draining"
      never happened. The heap arithmetic, the 1,488 B/chunk, the
      13-chunk wall and bytes-not-count all stand; the pacing rows were
      confounded (they gave the HP time to recycle AND starved the poll).
      Resolved in bite 2, which delivers 26/26 with the HP not draining.
- [~] Bite 2 — ~~**flow control on the WCMD_PUB burst** (first candidate,
      cheapest): the bridge emits a frame's chunks back-to-back with no
      backpressure. Pace them, or have the HE acknowledge drain, so
      bm_pub keeps up.~~ **RE-SPECIFIED by bite 1's measurement — HP-side
      pacing is NOT the fix:** draining on the HP alone died identically,
      2 ms pacing died identically (the HE spends ~2.5 ms/chunk, so 2 ms
      never starves the poll loop), and ≥5 ms survives only by accident,
      at 130–260 ms per HD frame. The fix belongs on the **HE**: pump TX
      from inside the `rr_poll` loop, or publish from a task other than
      the one that drains. Watch the REV-28 1400 B ceiling and the
      ≤492 B rpmsg budget. **Plus a cheap, independent robustness fix:**
      bound the netwire TX queue by BYTES rather than frames —
      `NETWIRE_TXQ_LEN` (16) × 1,488 B = 23.8 KB exceeds the free heap,
      so at the production chunk size the fatal malloc beats the
      survivable queue-full drop (at 700 B the queue fills first and the
      node lives, lossy but counted).
      → **CODE + REHEARSAL DONE 2026-08-16 (nibbles 1–2; plan + 5
      decision points approved by Nick). HD DELIVERS END TO END:**
      `capture 50 hd color` → **1280×800, 42,574 B, valid SOI→EOI at
      nereus001:8080/frame.jpg, 31 chunks, pub_ok=34 pub_errs=0
      gaps=0** (rung C, folded in here). Four parts, not three —
      **the rehearsal found that parts 1–3 alone DEADLOCK**: the old
      `wire_pump_tx` retried `rr_send` 100 × 1 ms and parked the wire
      task, which is also the task that consumes inbound rpmsg, so the
      HP blocked inside a single `ept.send` and never reached its next
      drain point (measured: exactly one chunk published, no
      `malloc failed`, stack RUNNING). Part 4 = a non-blocking pump that
      keeps its place across calls. Off-chain acceptance 6/6 including
      **60 × 1400 B = 84,000 B, 2.3× an HD frame**, zero drops, heap
      floor 17,704 of 20,680. Sustained regression `stream 2.0 15 600`
      held 15.0 fps, 0 gaps/drops. Size 246,784 (94.14%), ELF
      `4c509d24…`, bridge `1524f6c2…`; no ABI change, no fork pin move.
      Host tests: he_spike 29→45, bm_he 232, bridge 252→262, probe 47.
      Detail: DESIGN §S19 bite 2. **Remaining: nibble 3 (Nick runs the
      demo) → nibble 4 PR.**
- [ ] Bite 3 — only if bite 2 is not enough: raise
      `configTOTAL_HEAP_SIZE` on the HE (RAM, distinct from the ~16 KB
      flash headroom; ELF is at 94.05% of its 256 KB region after bite
      1 — check both budgets) and/or trim pbuf/queue pools. Re-run the
      REV-25 size audit. Bite 1 measured the input this bite needs:
      20,712 B free of the 64 KB heap at RUNNING, ≈43 KB held by task
      stacks and queues.
- [ ] Bite 4 — HD **mono** as well as colour (~25 KB, ~18 chunks — never
      reached in S18), then a sustained multi-frame HD run with the
      ledger exact end to end.
      **Sequenced AFTER S18 bite D (Nick, 2026-08-16)** — run it on a
      systemd harness so it cannot repeat S19's operator failures. Two
      concrete asks: `capture 50 hd mono` (completes the S19 demo line)
      and `stream 2.0 1 60 50 hd mono` (the first HD *video* number this
      project will have — predicted ~2.5 fps, encoder-bound).
**Demo (Nick):** `capture 50 hd color` then `capture 50 hd mono` from
the Telemetry CLI → both open as valid 1280×800 JPEGs at
`http://nereus001:8080/frame.jpg`, `gaps=0`, `pub_errs=0`, and the HE
heap floor reported alongside.
**Status 2026-08-16: HALF DEMONSTRATED.** `capture 50 hd color` passes
(1280×800, 42,574 B lit / 20,665 B dark, `pub_errs=0 gaps=0`, ledger
exact). ~~**`capture 50 hd mono` has never been run**~~ — **CORRECTED
2026-08-16 (S18 bite C2 nibble 1), from an artifact:** it HAS been run
once, during the C1 demo session. `~/bench_captures/
cap_20260816T223333Z_seq000395` on nereus001 is `req {q:50, res:"hd",
pf:"mono"}` and the JPEG checks out against **its own header** —
`SOI ffd8 … EOI ffd9`, `SOF0: 1280×800, 1 components`, 24,207 B — with
`chunks:18`, `pub_errs:0`, `gaps_delta:0`. So the *command* is proven end
to end. Bite 4 still owes the parts that were never run: a **sustained
multi-frame HD run** with the ledger exact, and **HD as a stream**
(`stream 2.0 1 60 50 hd mono`, predicted ~2.5 fps, still unverified).
Nick's call whether that half-closes the demo line.
**Also never measured: HD as a STREAM.** Every sustained run this sprint
was QVGA 15 fps (the relay regression). From S18's encode table the
expectation is **~1 fps HD colour / ~2.5 fps HD mono, encoder-bound**
(299.2 / 117.6 ms per frame) at ~0.3 / 0.15 Mbps — i.e. ~5% of the
5.26 Mbps relay ceiling, so the transport is not the constraint. That
prediction is UNVERIFIED; bite 4 turns it into a number.
**Do NOT skip the measurement bite.** S18 lost a day to a probe that
covered capture but never published a frame over BM, and cleared HD on
that basis. Prove the whole path or claim nothing.

> **EXECUTION ORDER RESEQUENCED 2026-08-18 (Nick, D39) — numbers stay,
> order changes (the D30/D32 precedent):**
> **S22 bite 1 (HE flood fix) → S22 bite 2 (encoder exploration) →
> S21 (CV) → S20 (light).** Light intelligence is DELAYED — "not
> really a product offering yet" — and CV is the product; the two
> S22 bites come first because CV development wants a camera that
> cannot be wedged and honest fps headroom numbers.
>
> **UPDATED same day (Nick, D42), after S22 delivered the fix and the
> encoder numbers: S23 (encoder fast path) → S21 (CV) → S20 (light).**
> S22's leftovers (bite 1b fork instrumentation, PR #38 demo)
> interleave; they do not gate S23.

### S20 — Light intelligence (stub — was S19 in D30)  *(DELAYED behind S21 — Nick 2026-08-18, D39)*
Camera self-detects dark scenes (HP luma stats) → camera node issues
`light/control` requests (HE `bm_service_request`) → light auto-on;
customer never thinks about it. All on bm_service (§6.2).

### S21 — CV: count-and-report (stub — was S20 in D30)  *(PROMOTED above S20 — Nick 2026-08-18, D39)*
Urchin/target counting ON THE HP CORE (NPU; HE has no room/NPU access —
D29 context). Requires a custom Vela-compiled detector (S8 finding:
ROM detectors are person-class-only; HD tiled = 1.2 fps). Alerts +
evidence stills ride the existing bridge→pub/sub path. Data collection
for training can use the S18 tool + S17 pipeline.

### S22 — Camera pipeline hardening & headroom  `[ ]`  ← **RUNS FIRST (before S21/S20 — Nick 2026-08-18, D39)**
**Goal:** a camera node that cannot be wedged at any commanded rate,
and a measured answer on how much fps headroom the encode path has.

- [~] **Bite 1 — the HE flood fix (finding 1; evidence fully banked).**
      → **NIBBLE 1 DONE 2026-08-18 (branch `sprint/22-he-flood`; Phase A
      approved by Nick): ROOT-CAUSED — a u16 vring-index wrap in
      `rr_poll_n` (rpmsg_remote.c:295, shared into bm_he): u32 cursor vs
      u16 avail->idx with no cast (rr_send HAS it), so at 65,536
      cumulative inbound rpmsg messages the poll loop consumes phantom
      work forever.** Reproduced off-chain by
      `bench/probes/s22_flood_probe.py` (+27 host checks): frag_errors
      ignited in the exact 10 s window containing message 65,536 and
      ran to 362,959; heap flat throughout (NOT a memory problem; the
      S19 byte-bound holds). "≥513 msg/s" was a proxy for
      time-to-65,536; all four real events match the arithmetic. Full
      record: DEV_LOG + SPEC flood entry.
      → **NIBBLES 2–3 DONE 2026-08-18 (Nick approved).** Fix = ONE
      wrap-safe cast in `rr_poll_n` + host regression [8] (fails
      pre-fix with the live signature) + ELF `fea65304…` staged
      byte-verified (+ Pi deploy copy refreshed). Off-chain: the
      killer ladder ran 507k msgs / 7.7 wraps, frag=0. On-chain,
      ledger-exact to the chunk: **10-min QVGA color 28.23 fps
      (16,939 frames, ~565 msg/s across ~5 wraps — the exact
      rate+duration that killed the live demo)** · 10-min VGA color
      **7.41** · first true ceilings: **QVGA mono 30.30** (sensor-
      capped), **VGA mono 13.27 @ ~717 msg/s**, **HD mono 3.10 @ 990
      msg/s commanded**. Guardrails re-derived + deployed
      (SAFE_STREAM_MSGS 315→1200 as a measured-envelope cap,
      suite 76→81; MEAS_FPS filled) — D40. **The demo line's
      `capture 90 hd mono` rung did NOT close: the burst variant is a
      SEPARATE bug** (54 of ~83 chunks lost on the FIXED stack —
      rpmsg arrival ~2× VCP drain, byte-bounded txq sheds; SPEC).
      Remaining: nibble 4 (PR) + Nick's demo.
- [~] **Bite 1b — the burst loss (Nick approved 2026-08-18).
      INVESTIGATED same day: every hop EXONERATED except the telemetry
      fork's internals — the fix now needs fork instrumentation (pin
      discipline, Nick's push).** The opening model (HE txq sheds) was
      falsified live along with four successors; the full chain, each
      step artifact-proven (SPEC burst entry): the q90 ref frame is
      really **149 chunks**; "gaps=54" was reassembler TAIL-LENGTH
      (one chunk lost, usually idx ~95), NOT 54 lost; HE published all
      (pub counters exact) · bridge relayed all (qdrops=0) · uart
      clean · **tcpdump: all 149 on the UDP wire, in order, inner
      checksums valid after outer-fragment reassembly** · kernel + all
      fork counters zero. Loss = 1 chunk/burst inside bm_sbc telemetry
      (suspects: bm_ip Linux backend RX, pubsub cb delivery), needs
      burst scale (q50 55-chunk stills deliver byte-exact throughout).
      SHIPPED as hardening (both measured harmless, neither the
      mechanism): HE netwire RX backpressure (high/low-water gate,
      +24 host checks, ELF `89cc92ff…` staged byte-verified, off-chain
      fatal-513 clean through it) and bridge `RPMSG_QUEUE_CAP`
      256→**1024** (`e50a34b8…` deployed via demo_up sha-sync, +1
      host check sizing it to the largest legal burst).
      SAFE_BURST_CHUNKS stays 68. Remaining: fork-side instrumentation
      bite (Nick sizes/pushes) → then the acceptance rung
      (`capture 90 hd mono` ledger-exact) closes.
      The HE wire task goes permanently mute under sustained camera
      publish ≥ ~513 rpmsg msg/s (4/4 fatal incl. a live demo at ~560;
      466 = 2/3 marginal; 315 = clean 4/4), and single-frame bursts of
      ~83 chunks lose chunks in the relay (55/68 measured clean).
      Suspect territory: the HE netwire TX path under sustained load
      (S19 bite 2's non-blocking pump). Evidence to start from:
      preserved trace `~/bridge_traces/20260818T002807_*` (he2pi_frames
      frozen while pi2he advances), matrix JSONs, the q90 single-frame
      boundary (published complete, 54 chunks lost). Bench access per
      ae3-board-access; off-chain reproducer likely = a G-probe-style
      synthetic publish at swept rates. **Verifiable / demo (Nick):**
      sustained QVGA color at the measured 28.07 fps ceiling for
      10 min, ledger exact, zero wedges; `capture 90 hd mono` (the q90
      burst) delivers; the UI guardrail constants
      (`SAFE_STREAM_MSGS`/`SAFE_BURST_CHUNKS`) raised to the NEW
      measured boundary with the suite updated; the mono-ceiling
      matrix rows finding 1 blocked (true QVGA/VGA mono ceilings) run
      clean and land in MEAS_FPS.
- [~] **Bite 2 — encoder-headroom exploration (measure first, decide
      second; AFTER bite 1 — Nick 2026-08-18).**
      → **MEASUREMENT WINDOW RUN 2026-08-18 (Nick approved; targets
      set: VGA @ 15 fps, HD @ 5–6 fps).** Banked, all vendor-verified
      or board-measured (DESIGN D41 + §S22 detail):
      (d) **the E3 has NO hardware JPEG/video codec** (datasheet —
      D/AVE 2D GPU + NPUs only) → HD color 5–6 is N6 territory, dead
      on this SoC; (a) `bench/probes/s22_enc_matrix.py` measured the
      full subsampling × quality × mode table on the reef refs —
      to_jpeg at q50 rides 4:2:2 today; forcing **4:2:0 buys ~14%
      encode + ~7% bytes** (VGA color 77.0→66.4 ms; HD color
      300.7→258.5), one `to_jpeg` kwarg; (b) q35 adds little beyond
      auto-420; **the binding constraint after (a) is the non-encode
      tax, measured at ~2 ms/KB** (publish/chunk CPU + capture:
      QVGA-C 15.6 ms, VGA-M 44, VGA-C 58, HD-M 204 incl. single-fb
      capture 33) — subsampling alone moves VGA color only 7.4→~8;
      (e) jpege.c has NO Helium/MVE vectorization though the M55
      build enables `+mve.fp` — the 2–4× encode lever exists but is
      real firmware work. **Recommendation for Nick's gate: VGA mono
      13.27 is near the 15 target now; VGA color 15 and HD mono 5–6
      both require the C-path/MVE combination (attack encode AND the
      2 ms/KB tax); 4:2:0-at-q50 is a cheap immediate win worth
      shipping regardless; HD color 5–6 impossible on this SoC.**
      Remaining: Nick reviews the table → go/no-go on the C/MVE
      follow-on bites.
      *(original scope below)* Delivered fps is
      encode-bound, not transport-bound (28.07 fps QVGA uses 2.08 of
      the 5.26 Mbps relay): in-bridge encode 20.1 / 78.5 / 299.2 ms
      per frame (QVGA/VGA/HD color) on the one HP core, measured
      bridge derate 0.56–0.58. This bite BUYS NUMBERS, not code:
      measure each candidate's real delta and recommend go/no-go.
      Candidates to measure (one variable at a time): (a) JPEG
      parameter space — subsampling/quality trades at equal visual
      quality on the reef reference; (b) capture/encode overlap — how
      much of the 0.56 derate is recoverable (capture is DMA-hidden,
      D21 said encode/tx cannot overlap in MicroPython — re-verify on
      the current stack); (c) the MicroPython-bridge tax — what a
      C-side capture→encode→chunk path would save (spike-level
      estimate, the S9/D23 custom-firmware loop exists); (d) hardware
      JPEG/2D-accel on the AE3 SoC — **verify against the Alif
      datasheet, never assume** (the SENSOR has no JPEG; whether the
      SoC does is an open hardware fact to flag in SPEC §Open
      questions). **Verifiable / demo (Nick):** a printed table —
      candidate × measured-or-sourced fps delta × LoC/risk estimate —
      reviewed together, with a written recommendation; TRACKER gains
      follow-on bites only if a candidate clears the product bar.
**Needs:** S18 closed enough to free the bench (D2 can interleave);
no new hardware.

### S23 — Encoder fast path: push delivered fps to the hardware's limit  `[ ]`  ← **NEXT (Nick, 2026-08-18 — D42)**
**Goal:** VGA color q50 ≥ **15 fps** and HD mono ≥ **5–6 fps**
delivered through the chain, ledger-exact — then find and record the
true max fps per mode. The S22 window measured the route
(DESIGN §S22 detail): `delivered ≈ 1000 / (enc_ms + ~2 ms/KB publish
tax + capture)`; today VGA color = 7.41 (77 ms enc + 58 ms tax),
HD mono = 3.10 (118 + 204 incl. 33 ms single-fb capture). HD color
≥5 is OFF the table on this SoC (no hardware codec — vendor-verified,
SPEC). Facts base: enc matrix (`bench/probes/s22_enc_matrix.py` — the
acceptance instrument for every encoder bite), measured ceilings
(`bench/s22_ceiling_rows.py`), D23/D24 build loop, sticky-fb patch
precedent for repo-carried firmware patches.
- [~] **Bite 0 — ship 4:2:0 at q50 (cheap, immediate).** One
      `to_jpeg(subsampling=...)` kwarg in the bridge encode call
      (bm_bridge.py:1093) + host tests + a page-model/bytes note
      (bytes drop ~7%, so chunk predictions shift). Measured buy:
      color encode −14% (VGA 77.0→66.4 ms, HD 300.7→258.5), delivered
      VGA color 7.4→~8. Acceptance: enc-matrix rows reproduce; one
      on-chain VGA color ceiling row re-run; MEAS_FPS/provenance
      updated. Decision recorded: refusal-vs-quality trade is Nick's
      to eyeball once on the reef refs (4:2:0 chroma cost at q50).
      → **NIBBLES 1–3 DONE 2026-08-18 (plan + "force 420 at every q,
      color only" approved by Nick; branch `sprint/23-encoder-fastpath`,
      carries the S23 ladder commit).** Shipped: color-only
      `subsampling=JPEG_SUBSAMPLING_420` resolved at `command()` (mono
      NEVER gets the kwarg — no grayscale knob, unmeasured); page MEAS
      + server `REEF_BYTES_Q50` moved to the 4:2:0 anchors in lockstep
      (HD color q50 68→62 predicted chunks, clear of the burst guard);
      bridge suite 288→**292**, bench_web **81** (pins re-derived).
      **On-chain, scene=ref, bridge sha `552812ba…` byte-verified via
      demo_up:** the A/B still pair is BYTE-EXACT to the model — VGA
      color q50 = 29,148 B/21 chunks before (seq000207) vs **27,021 B/
      20 chunks after** (seq000000); ceiling row `vga-color-15` =
      **7.93 fps delivered (476 frames/60 s), gaps=0 dropped=0,
      pub_ok=9,540 = 476×20 chunks ledger-exact** (was 7.41 on 4:2:2;
      model predicted 8.0, delta 1.4%). MEAS_FPS updated (7.41→7.93;
      QVGA/HD color annotated as pre-420 floors for bite 3).
      Bench note: recovery detour recorded in DEV_LOG — a phase-1
      bridge waits FOREVER until first VCP contact arms its 30 s
      quiet-exit; the failed attach IS step one of the recovery.
      **Remaining: Nick's 4:2:0-vs-4:2:2 eyeball on the reef pair
      (gallery compare view, seq000207 vs seq000000) — the recorded
      decision gate for force-420-at-every-q — then nibble 4 (PR).**
      → **DONE 2026-08-19: Nick ran the visual review — quality
      satisfactory; force-420 stands. PR #42 MERGED (e3bc81e).
      Bite 0 CLOSED.**
- [~] **Bite 1 — MVE/Helium-vectorize the JPEG encoder (the big
      lever).** jpege.c is plain C; `+mve.fp` is already in the Alif
      port CFLAGS. Ships as a repo-carried openmv patch (sticky-fb
      pattern, `firmware/openmv_patches/`), built via D23/D24,
      S7-ladder flashed byte-verified, stock rollback kept. Order
      inside the bite: (a) color-convert vectorization first (cheapest
      ~30–40%), measure; (b) DCT second, measure; golden-image
      regression (byte-exact where the algorithm is unchanged, PSNR
      bound where rounding differs) BEFORE any on-chain use. Target:
      ≥2× color encode (VGA 66→~30 ms, HD mono 118→~55). Gate: if (a)
      lands <1.5× alone, STOP and re-plan with Nick before investing
      in (b). Off-chain acceptance = the enc matrix on the patched
      build; risk = SIMD correctness, contained by the golden tests.
      → **(a) DONE 2026-08-18 (Nick's "Go"; the separate profile flash
      cycle was skipped — (a)'s own before/after IS the profile).**
      Shipped `0002-jpege-mve-colorconvert.patch`: Helium fast path in
      `jpeg_get_mcu`'s RGB565 case, 8 px/iteration, arithmetic
      bit-identical to the scalar SWAR (audited lane-by-lane incl. the
      packed->>7 cross-lane bleed); MVE presence proven in the built
      object (vldrh.u16/vmla.i16). Plus `0003` (docker git safe.dir
      env fix — the D24 dev target broke on a Docker ownership drift)
      and `bench/probes/s23_enc_golden.py` (sha256/row golden gate).
      **GOLDEN PASS: all 44 rows byte-identical stock-vs-MVE, mono
      0.99x (untouched).** Speedup uniform ~1.29× color encode (VGA
      420 q50 65.9→51.2 ms; HD 420 q50 256.8→197.7; QVGA 17.5→13.8)
      → conversion was ~30% of encode, now ~5 ms residual.
      **On-chain: vga-color-15 row = 9.03 fps delivered (542 frames/
      60 s), gaps=0, pub ledger exact to the BYTE** (pub_ok 10,840 =
      542×20; pub_bytes/frame 27,221 = JPEG+headers). MEAS/MEAS_FPS
      updated (7.93→9.03; enc anchors to MVE values).
      **→ (a) landed 1.29× < 1.5× — STOPPED per the gate. Re-plan
      presented to Nick (see DEV_LOG): both targets need the tax AND
      the DCT; recommendation = bite 2 (C publish path, the 58 ms
      lever, helps mono too) BEFORE (b) (the 46 ms lever, color only
      at VGA). (b) waits on Nick's order call.**
      → **(b) DONE 2026-08-19 (Nick's "Go for it", after bite 2):
      MVE DCT + quantization, GOLDEN PASS 44/44 byte-identical.**
      Row pass via widening byte gathers (4 rows/group), column pass
      contiguous, quant = float mul + VCVTN (== fast_roundf's VCVTR
      under FPSCR RN). Encoder vs stock: **VGA color 1.55×
      (65.9→42.4 ms), HD color 1.56×, mono 1.23×** (no color convert
      to win back). Patch 0002 is now the FULL jpege vectorization.
      On-chain: **VGA color 10.73 fps CLEAN, pub_ok 644×20 exact**
      (sprint ladder 7.41→7.93→9.03→9.50→10.73). En route the SHM
      grew 64K→128K with 32 vring slots after 16 slots measurably
      starved HD bursts — which broke the chain until the HARDCODED
      `METAL_MPU_REGION_SIZE` (mpmetalport.h) was found leaving the
      grown pool's upper half cacheable on the HP (patches 0004/0005;
      full saga in DEV_LOG 2026-08-19). **OPEN: HD mono 2.72 vs 3.10
      stock — the relay leg regressed (~3.1 ms/msg at HD sizes,
      profiled); next session's first job.**
      → **RESOLVED-AS-EXPLAINED 2026-08-19 (relay-profile session,
      Nick's Phase-A gate): the 2.72 did NOT reproduce — clean-boot
      HD mono = 3.15 fps TWICE (189 frames/60 s each, pub_ok 189×55
      ledger-exact), ABOVE the 3.10 stock baseline; VGA color control
      10.62 held.** The old run's own trace shows it steady-slow from
      its first HD snapshot (181/174/175 ms/frame send) — a boot-state
      anomaly, not the rpmsg-1544/MVE geometry. Relay-split counters
      shipped (bridge `b3543cc7…`, suite 294→310): cap_send_us =
      ept.send + pump split, VCP writes metered globally. **Measured
      physics rewrite: VCP throughput is ~24 MB/s (USB enumerates
      HIGH-SPEED, 480M, lsusb) — the "~675 KB/s VCP floor" was never
      USB; it is the ~1.25 ms/message PYTHON drain cost (he_msg + COBS
      _encode; usb.write itself ~55–61 µs). At HD, ept.send additionally
      blocks (avg 1.14 ms/msg, 26% >1 ms, max 30.8 ms — 55 chunks
      overflow the 32-slot vring and ride HE pace); at VGA it is free
      (23 µs, 20 chunks fit).** HD budget at 3.15 = enc ~96 + capture
      ~33 + pump-python ~69 + ept-block ~63 + asm ~12 + misc. New
      levers ranked: (1) cut the per-msg python drain (viper/native
      COBS — helps every mode), (2) overlap, (3) ept pacing. MEAS_FPS
      2.72→3.15 (page + suite 81).
      → **LEVER (1) SHIPPED same day (Nick's "go for it"): the drain
      fast path.** Stage-2 counter measured he_msg = 87% of the pump
      cost (1.10 of 1.26 ms/msg) → the three ~1.5 KB per-message
      allocations were the suspects → `he_frame_wire` zero-alloc path
      (aliasing memoryview of `_wire`, consumed before next encode) +
      memoryview slice-assign in `frame_encode_into` (the `bytes()`
      detour deleted). Equivalence/fallback/aliasing pinned (suite
      328, codec 38). **Measured: VGA color 12.30 fps (738×20 exact)
      · HD mono 3.37 (202×55 exact), both CLEAN; enc/msg 1102→~680 µs.
      VGA send leg 17.5 ms/frame, ept 0.5 (never blocks); HD send 120
      = ept-block 72 + pump 47 — HD is now HE-round-trip-bound.**
      Route from here: VGA-15 = overlap the ~19 ms snapshot wait
      (predicted ~16); HD-5 = the HE round trip (batching or C path),
      overlap alone reaches only ~3.8.
- [~] **Bite 2 — C publish path (kill the tax).** Move the per-frame
      chunk/publish CPU out of MicroPython: C-side capture→encode→
      chunk→rpmsg on the HP (custom-firmware module; S9/S17 loop
      precedents). Target: tax ~2 → ≤0.5 ms/KB. Measured first
      (profile WHERE the 2 ms/KB goes: chunk assembly vs ept.send vs
      framing) — the bite may shrink to a C helper for the hot loop
      only. Acceptance: ledger-exact streams at the new rates;
      regression 15 fps QVGA untouched.
      → **RUN 2026-08-18 (Nick's go, sequenced BEFORE the DCT — the
      1a stop-gate re-plan). Profile first (bridge cap_asm_us/
      cap_send_us/cap_msgs counters, now permanent): the "2 ms/KB" was
      per-MESSAGE (~0.57 ms × 59 msgs/frame at VGA color), plus a
      ~19 ms sensor-cadence wait inside snapshot().** Shipped, all
      measured on-chain (60 s rows, ledger exact):
      (1) **one rpmsg message per chunk** — buffers 512→1544 ×16
      (he_spike.h + micropython patch `0004`; pool arithmetic fits the
      unchanged 64 KB SHM; rr_send's per-descriptor capacity check
      makes ELF/host mismatch degrade loudly, never corrupt);
      MSG_PAYLOAD 492→1524, CHUNK_DRAIN_EVERY 3→1, MSGS_PER_CHUNK
      3→1, SAFE_STREAM_MSGS 1200→400 (same byte envelope; bite 3
      re-derives). Wire shape proven: cap_msgs == cap_chunks exactly.
      HP fw `70ef9e0f…` + ELF `fbe74b80…` flashed/staged byte-verified.
      **Honest result: only +0.14 fps — the send loop is DRAIN-bound
      (per-msg wall time tripled to 1.45 ms; the HP blocks on the HE/
      relay pace), falsifying the per-message-overhead model.**
      (2) **one-copy chunk assembly** (pack_into, no intermediate
      payload objects): asm 7.1→~3 ms/frame. **Ceiling row: 9.50 fps
      delivered, 570 frames/60 s, pub_ok 11,400 = 570×20 EXACT.**
      Sprint ladder so far: 7.41 → 7.93 (bite 0) → 9.03 (1a) →
      **9.50**. Remaining frame budget at VGA color ≈ 105 ms: enc 51
      + send/drain ~28 + snapshot ~19 + asm ~3 + misc ~4. **The
      C-path's remainder (moving the loop to C) attacks only the
      ~3 ms python residue — NOT worth it; the real remaining levers are
      the DCT (bite 1b, −21 ms) and capture/encode overlap (the
      snapshot wait + drain interleave, re-opens D21). Re-planned
      route to 15: DCT → ~12 fps, then overlap → 15+.**
- [ ] **Bite 3 — re-measure everything and raise the truth.** Full
      ceiling rows + 10-min soaks at the new maxes (`s22_ceiling_rows`
      + new max-rate rows), MEAS_FPS + guardrail envelope re-derived,
      page provenance flipped. **Targets check: VGA color ≥15, HD
      mono ≥5–6 — the sprint's pass/fail line.**
**Demo (Nick):** bench page → VGA color q50 stream commanded 15+ fps →
the pill shows ≥15 delivered, ledger exact · `capture 90 hd mono`-class
HD mono stream ≥5 fps · the enc-matrix table re-printed on the patched
build next to S22's baseline.
**Interleaves:** S22 bite 1b fork instrumentation (Nick's push) — at
S23's HD-mono rates the frames stay q50-sized (55 chunks, measured
clean), so the burst defect does not block the targets; it still owes
the q90-class stills rung. **Needs:** Mac docker loop, bench; no new
hardware.

---

**RESUME-ON-HARDWARE (first thing when PCBAs arrive):** S9 bite-3
demo — rebuild a link fixture (new hats / SG-shield-as-OA reshuffle /
ADIN2111 eval), re-strap per DEV_LOG fixture notes, then the README
bite-3 ladder (`s9_oa_datapath.py`, one command) → nibble 3 → merge.

### S9 — OA first light in C (custom firmware + driver spike)  `[!]`
*(bites 1–2 done; bite 3 code done + PR open, demo blocked on
replacement link hardware — see INTERIM above)*
**Goal:** prove the C dev loop end-to-end and OA mode on our silicon.
- [~] Bite 1 — **1110-vs-2111 verify spike**: re-strap hat #2 to OA
      (default straps; D13 jumpers reversible), minimal C module in a
      custom OpenMV firmware calling bm_core's adin2111 driver
      **unmodified** for an OA register/PHY-ID read. Mac docker build
      (S7 env) → S7 headless flash → REPL/log verdict.
      **Decision point on fail: buy ADIN2111 bench hardware; do NOT
      port the driver to 1110 (throwaway — production goes 2111).**
      → **SPIKE PASSED 2026-08-11 (live, Nick re-strapped + Claude drove
      the loop): verdict 1 = PHYID 0x0283BC91 read through the driver's
      OWN OA framing (SUCCESS); verdict 2 = adin2111_Init refused ONLY
      by the 2111 identity gate (COMM_TIMEOUT at waitDeviceReady,
      exactly as source-predicted).** Full 1110-vs-2111 delta list =
      **2 items**: (1) OA control protection — **PROTE (CONFIG0 bit 5)
      will not set on our 1110** (measured; other bits/regs write fine)
      so bm_core must build WITHOUT `CONFIG_SPI_PROT_EN` for 1110 bench
      work (`build_spike.sh --no-prot`; driver's unprotected path is
      native); flagged in SPEC §Open questions pending datasheet
      cross-check; (2) `RSTVAL_MAC_PHYID` 0x0283BCA1 vs our 0x0283BC91.
      **Recommendation for the decision point: NOT a fail — no 2111
      purchase forced for bite 2/3;** carry the 2-item delta as build
      config (production 2111 unaffected). Also en route: first real
      exercise of the D23 Mac docker build (works; HE image doesn't
      link in our env at any rev — HP-only flash at the installed HE's
      rev sidesteps it, environmental debug deferred → RESOLVED
      2026-08-11 same day, D24 / `sprint/9-build-fix`: stock docker
      target's `BUILD=` command-line override flattened the per-core
      build dirs so HE linked HP objects; `build_ae3.sh` moved to
      `build-firmware-dev` + `--incremental`, HE links at ~1.19 MB,
      HP-only workaround retired — S10's HE dependency unblocked); S8 bench
      mis-attribution found + corrected (N6 vs AE3, DESIGN §S8
      correction). Detail: DESIGN §S9, `firmware/bm_spike/README.md`.
      Demo satisfied: REPL prints the OA-mode PHY-ID verdict.
- [x] ADI-HAL implementation for Alif (SPI + IRQ on P0–P5; DMA hooks
      exist in silicon — SPI_DMACR + DMA0/DMALOCAL engines, vendor
      headers in openmv tree — wire up if bite budget allows, else S10)
      → IN PROGRESS 2026-08-11 (branch `sprint/9-adi-hal`, nibble-1 plan
      approved by Nick; **DMA explicitly deferred to S10, Nick's call**).
      `bm_spike_hal_alif.c`: bare-metal SPI0 FIFO-burst engine (≤16
      frames in flight vs the per-word lock-step D8 ceiling — present in
      BOTH machine_spi.c and Alif's own spi_transfer_blocking), real
      INT_N IRQ via machine_pin.c's GPIO0_IRQ4 dispatch (vector table
      is const in MRAM; riding the dispatch avoids a fork), real
      critical sections. `build_spike.sh --hal mp|alif` stages exactly
      one HAL; bite-1 mp build stays the regression baseline. Host
      tests 10 → 16 (bench plumbing). Pin facts verified @ 7d4dbf7ab2:
      P0/P1/P2 = P5_1/P5_0/P5_3 = SPI0 MOSI(AF4)/MISO(AF4)/SCLK(AF3),
      P3 = P5_2 GPIO CS (D2), P5 = P0_4 → GPIO0_IRQ4_IRQn.
      → CODE + REHEARSAL DONE same day (Claude ran the demo twice,
      identical PASS): PHYID over OA via native HAL; INT_N → hard IRQ →
      driver callback proven; bench 45.9k reads/s @5 MHz (2.0× the mp
      HAL's 22.9k), 83.8k @10 MHz, 0 stalls; bite-1 regression green.
      En route findings (DESIGN §S9 bite-2 detail, SPEC §Open questions
      amended): 20 MHz OA rung reads garbage AND can flip CONFIG0.PROTE
      via misclocked frames (runner sanitizes + runs it last);
      **P4 reset line measured ineffective on the rig** (bench check
      flagged for Nick); INT_N/W1C/LOFE semantics measured; C statics
      survive soft resets (`fresh()` guard).
      → **DONE 2026-08-11: demo run by Nick — PASS.** DMA formally
      deferred to S10. PR #15. Bite-3 starters banked: read_reg/
      write_reg passthrough, RX_SAMPLE_DELAY knob for the 20 MHz
      finding, level-trigger conversion option.
- [!] OA data-path smoke: one frame TX via OA chunks → tcpdump on
      nereus001 (Pi side untouched, generic SPI + kernel driver)
      → IN PROGRESS 2026-08-11 (nibble-1 plan approved by Nick; nibble-2
      code done): `bm_spike_datapath.c` init bridge (driver still
      byte-identical — waitDeviceReady/macInit replicas + one-line state
      nudge past the identity gate in spike-owned memory), dp_* Python
      API, `s9_oa_datapath.py` runner, host tests 16 → 41 (mock grew
      MDIO/PHY emulation + OA data-chunk parsing w/ byte-exact TX
      capture). Rehearsal on hardware: **bridge PASSES live — MDIO-over-OA
      proven (DEVID 0x0283/0xBC91 through the driver's own PHY layer),
      PHY init + SyncConfig + powerdown-exit all SUCCESS** — but **link
      never comes up: measured NO far-side energy (bite-2's continuous
      LOFE relatch is now absent; both PHYs advertise, neither sees AN
      pages). Physical pair fault suspected (unplugged since the bite-2 /
      S6-demo bench work?). BENCH CHECK NEEDED (Nick): J1 pair seated
      both ends.** Verification one-liner after re-seat = re-run the
      runner (README bite-3 ladder). 1110-vs-2111 delta item 3 recorded:
      adin2111-level init also blocks on a port-2 PHY wait → 1110 needs
      MAC/PHY-layer entry-point bring-up (the bridge).
      → **PAUSED 2026-08-12 (Nick): demo blocked on replacement PCBAs.**
      Full-day isolation concluded ≥2 of 3 line interfaces dead — both
      AOS hats condemned (likely rework transient through the connected
      pair; SPEC §Open questions has the verdict + new bench rules).
      Code is COMPLETE and hardware-proven to the wire (init bridge,
      MDIO-over-OA, TX submit all PASS live); PR opened (nibble 4) so
      the branch doesn't rot. RESUME when hardware arrives: rebuild a
      link fixture (new hats, or SG-shield-as-OA + role reshuffle, or
      ADIN2111 eval hw), then the README bite-3 ladder — the runner is
      one command. NOTE: hat #2 is currently strapped GENERIC (bisect
      state); AE3 still carries the bite-3 alif build. Interim work
      pivots to the USB-only track (S10 spike needs no ADIN hardware).
**Demo (Nick):** custom-firmware AE3 prints `PHY ID — OK (OA mode)`;
a seq-numbered frame lands in tcpdump across the pair.
**Needs:** S7 flash loop, Mac build env, hat #2 re-strap.

### S10 — bm_core boots on the AE3 (HE core)  `[ ]`
**Goal:** BM stack alive on the camera board; camera side untouched.
- [x] **INTERIM 1** — Spike first, one bite: FreeRTOS on M55_HE + OpenAMP
      HP↔HE pipe — measure pipe throughput (**gate: ≥5 Mbps**) and
      confirm HE can own SPI0 + its IRQ (pinmux/EWIC). Fallback if HE
      loses: bm_core on HP alongside MicroPython (invasive — price it
      before choosing). No ADIN hardware needed. Branch
      `sprint/10-he-pipe-spike`.
      → CODE + REHEARSAL DONE 2026-08-12 (nibbles 1–3 rehearsed; Claude
      ran the demo twice, identical PASS): **gate answered at rung 0 —
      stock python↔python pipe = 219 Mbps (44× the gate), zero custom
      firmware**; then `firmware/he_spike/` (FreeRTOS V11.3.0 on HE,
      runtime-ELF-loaded into SRAM9_B via stock remoteproc — NOTHING
      flashed; hand-rolled ~250-line device-role rpmsg vs open-amp+
      libmetal glue), runner verdicts **A PASS** (FreeRTOS app serves
      rpmsg), **B PASS** (13.2 Mbps HP→HE / 5.6 HE→HP, python-end-
      bound, 0 loss/crc errs over 37k msgs), **C PASS** (HE pinmux
      write+readback, SPI0 init, IRQ 137 on HE NVIC; RX-with-real-data
      deferred to hardware day — SRL loopback tied off in silicon,
      pad-pull test inconclusive). Host tests 29 checks (fake-SHM host
      driver incl. the live-measured recycle semantics). Hardware facts
      measured live: vring roles + desc-offset addressing + used.len
      capacity contract (DESIGN §S10), SPI0 SRL absent, pinconf-from-HE
      works. HE-loses fallback MOOT. Bench check ANSWERED (Nick,
      2026-08-12): nothing wired to the board → the pad floats high
      and DRIVER_DISABLED pulls don't steer an AF-mode input on this
      pad (DESIGN §S10 fact 6); pull test stays non-gating diagnostic.
      → **DONE 2026-08-12: demo run by Nick — PASS (A/B/C, identical
      numbers to the rehearsal). PR #17.**
- [~] **INTERIM 2** — bm_os(FreeRTOS) + lwIP + NetworkDevice glue on HE;
      BCMP up (heartbeat, neighbors, ping) — interim scope: against a
      mock NetworkDevice; real ADIN swap-in is a hardware-day bite.
      Split 2a/2b (Nick approved 2026-08-12). Mock revised from the
      TRACKER's "S9 chip-level mock promoted on-target" parenthetical
      to a **trait-level mock with rpmsg as the fake wire** (Nick
      approved; rationale in DESIGN D25 — the bite's risk is the stack,
      the driver is S9-hardware-proven, and a scriptable wire rehearses
      S12's HP↔HE topology + INTERIM 3's golden-capture replay).
      → **2a (stack boots + talks) code + rehearsal DONE 2026-08-12**:
      `firmware/bm_he/` — bm_core @ d4ecc38 vendored (BCMP slice),
      lwIP 2.2.1 by reference + pinned contrib sys_arch, RAM/tick
      stubs, mock device, runner `s10_bcmp_bench.py` + pcap. Claude
      ran the bench twice, identical PASS: init ladder RUNNING (err 0,
      node id + fe80::/fd00:: addrs correct), 2 heartbeats/25 s
      wire-verified (csum, src node id, egress nibble, monotonic
      boot-µs), pcap reads clean in tcpdump (bonus: MLD6 join of
      ff03::1 visible). Size: 231 K of 256 K region (~88%). Host tests
      72 checks (clang+ASan). S6 USB baseline re-verified after.
      → **2a demo PASSED (Nick, 2026-08-12 — identical numbers to
      both rehearsals); PR #18 merged.**
      → **2b code + rehearsal DONE 2026-08-12** (branch
      `sprint/10-bcmp-2b`): `s10_peer.py` python peer node (byte-exact
      BCMP builders/parsers, CPython-testable) + one new firmware
      surface (`WCMD_PING` in src/main.c — bm_core still vendored
      byte-identical, zero patches). Rehearsal PASSES ×2 identical:
      A/B (2a regression) · **C neighbor table lists the peer online**
      (peer heartbeats every 5 s → BcmpNeighborTableReply) · **D ping
      peer→HE echoed** (id/seq/payload, csum) · **E ping HE→peer
      accepted** (WCMD_PING → echo request on wire → peer reply →
      ping.c's 🏓 acceptance line on the debug ring). First live
      exercise of the RX path (l2→lwIP→bcmp) — worked first try;
      bonus: HE fires BcmpDeviceInfoRequest at its new neighbor. pcap
      = full 15-frame two-node conversation, tcpdump-clean. Size
      231.5 K (~88%, +0.4 K). Host tests 72 → 112 checks. S6 USB
      baseline re-verified after (34.1 fps, 0 gaps, 0 bad).
      → **2b demo PASSED (Nick, 2026-08-12 — A–E identical to both
      rehearsals) = INTERIM 2 demo DONE; PR opened.**
- [!] Validate against reference hardware: dev-kit mote (on hand) sees
      the AE3 as a BM neighbor — needs a live T1L path
      (RESUME-ON-HARDWARE)
**Demo (Nick):** BCMP ping to the AE3 answered (from mote or Pi);
heartbeats visible in tcpdump.
**Needs:** S9 bites 1–2 (done) + D24 build env. S9's bite-3 *demo* is
NOT a blocker for the interim bites.

### S11 — Pi becomes a BM node (bm_sbc)  `[ ]`
**Goal:** nereus001 running bm_sbc, attached at full rate.
- [ ] **INTERIM 3** — bm_sbc mainline on the Pi + stock UART-gateway
      cross-check vs the dev-kit mote (reference bite — needs only the
      dev kit, not S10). **HARD SAFETY GATE (SPEC §Safety absolute):
      meter the mote's port cold + Nick's explicit sign-off before ANY
      connection; UART side only; never onto a powered bus.** Bonus
      deliverable: golden BCMP captures to validate INTERIM 2's mock.
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
*Bench rehearsal: BENCHSPEC BUILD-4's camera service + 2 Mbps stream
(S16/S17) is S12's dry run — same chunking, same pub/sub path,
transport swapped later.*

### S13 — soak, numbers, production notes  `[ ]`
**Goal:** the decision package for the production camera node.
- [ ] 10-min+ soak at T1 settings: fps/loss/latency, CPU headroom on
      both cores, SPI utilization (did DMA land? measured effect)
- [ ] ADIN2111 switchover notes: every 1110/2111 delta hit during the
      arc; what the production PCBA needs (feeds Nick's 2111 move)
- [ ] AE3 SPI translator characterization (SPEC §Open questions
      2026-08-12: P0–P5 ride NXS0104/NXS0102 open-drain translators,
      10 kΩ pull-ups, 24 Mbps max — part-to-net mapping UNVERIFIED,
      EE-confirm first): measure true SCLK + rise time at the B2B
      connector at 5/10/20 MHz; verifiable = scope shots + a
      max-reliable-SCLK number recorded in DESIGN.md, and the S9
      20 MHz-OA-garbage finding either explained or exonerated (pairs
      with the banked RX_SAMPLE_DELAY sweep). Gates the v2 PCBA layout
      release and the S12/S13 throughput model.
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

## Flagged during S19, not owned by any bite yet (2026-08-16)

- **`bench_apps` (fork) segfaults occasionally at startup.** Once in
  four starts this session, on the Light role, immediately after opening
  the AE3's CDC port: `Network Device Port 15: up` → `Failed to start
  renegotiating check, reason: 0x7D` → SIGSEGV. Clean on an immediate
  retry. Pre-existing (fork at `ba594ec`, untouched by S19). `Restart=`
  in S18 bite D's units masks it operationally; the underlying race in
  the uart/renegotiation path is still unexplained. Worth a core dump
  next time it happens.
- **`Error processing parsed cb: 19 of message 5`** on the HE debug ring,
  next to camera/control replies. Seen throughout the S19 chain runs
  with no observable ill effect. Not attributed, not investigated —
  do not assume it is benign just because it is old.
- **The frozen S3 ingest is single-producer and fails silently under a
  second writer** (S19 root cause, DEV_LOG 2026-08-16). S18 bite D's
  preflight covers the bench; if the web tool ever adds a second
  consumer path, this is a real design constraint, not a bench quirk.

## Icebox (captured, not scheduled)

- lwIP netif integration in OpenMV firmware (C) — MicroPython sockets over T1L
- N6 evaluation for H.264 path (needs OpenMV answer on VENC MicroPython API)
  — now formally owns the public-stream cell (720p ≥24 fps) of the SPEC
  requirement matrix; AE3 confirmed as this project's platform (Nick)
- SG JP1/JP4 breakout confirmation (would clean up the S4 harness)
- Power-gating architecture (AE3 supervisor + load switch) from board-selection analysis
- ~~bm_core port (post-S7 decision)~~ → scheduled 2026-08-11 as sprint S10
