# TRACKER.md — Sprint Ladder & Rules

*The agent entry point. Newest state lives here.*
*Last updated: 2026-08-20 night (**S25 bites 1+2 SHIPPED AND DEMO'D
(Nick): the workbench page on nereus000:8088 lists recipes, preflights the
boards passively, and starts/stops the S8 ball demo with a single-owner
board lock, health-gated LIVE, and a 35 s settle window after stop** —
born from a live wedge: quick stop→start put the AE3 into the bite-R
power-cycle-only raw-repl refusal, cleared only by physical replug.
`workbench.service` ENABLED at boot, proven across a reboot. Exposure
DECIDED (Nick): bind 0.0.0.0 on the trusted LAN, loud banner, no auth.
Bites 3 (reconciliation) + 4 (second recipe + doc) remain. NEW unowned
observation: the N6 sat in DFU mode for 9 min and self-recovered —
unattributed. Previous:*
*2026-08-20 (**LADDER RESEQUENCED (Nick): CV LEADS —
and S24 is already running it on the N6.** Reconciled with S24 at merge
time: **S24 (N6 CV baseline) stays exactly as it is** — running, Mac-
only, no bench hardware — and it now OWNS the N6 half of the CV work,
including the `stedgeai`-vs-direct-load toolchain question (its bite 3).
**S18 and S19 are DEAD** (tombstoned — S18's units/demo_up/bench-ctl
still run the bench; the compare tool "works well enough", parked).
**S22 CLOSED** on its shipped work, its one debt (bite 1b's q90 burst
loss) carried into the unowned-findings list, not buried. **S8 is
reshaped**: a from-scratch custom detector — "pink ball vs purple ball"
first, to kick the tires before any urchin labelling — that compiles
and deploys to BOTH boards, then end-to-end capture→detect→count at 1 m
vs 2 m with pixels-on-target recorded. S8's old gate (runs after S9–S13)
is overridden: CV is the board-selection input now. **S24's one-class
finding is exactly why S8 bite A exists** — `/rom/yolov8n_192.tflite`
emits `(1, 5, 756)` = 4 box coords + ONE class ("person"), so a pink
ball is unreachable by configuration and a custom model is mandatory,
not a preference. S8's pipeline/alert bites moved to S21. **S23 is
PARKED at GOLD 12.53** (bites S and 3 unstarted) — no more VGA speed
work until detector numbers exist. **S23 bite R** stays open with ONE
unexplained state; three symptoms are explained and the usb-storage
reset livelock has a shipped fix (`pi/ae3_flash/99-ae3-no-msc.rules`).
**S24 was FOLDED INTO S8 2026-08-20 (Nick)** — its bite 1 is delivered
and kept, its remaining bites became S8's A/B1/D, and its verified
hardware facts moved into S8 where the next session will actually read
them. One sprint owns CV now. Execution order: **S8 → S21 → S20**, with
S23's leftovers and bite R slotted at Nick's call. Previous:*
*2026-08-19 latest+4 (**NEW SPRINT S24 — N6 CV BASELINE —
OPENED AND RUNNING (Nick's call, D43). Bite 1 DELIVERS: a headless live
detection stream from the OpenMV N6 into a browser, no OpenMV IDE
(there is no macOS 14 build), plus the first N6 capture-size sweep.**
Measured on fw `OpenMV v5.0.0 / MicroPython v1.28.0-49`: yolov8n_192
inference 20.7 ms @QVGA · 23.7 @VGA · 32.2 @HD, and **capture+inference
end-to-end 47.9 / 41.8 / 30.2 fps** — the vendor's "~30 fps YOLOv8-class"
claim holds, and holds at **HD**, not VGA. Capture is fully DMA-hidden
(0.2 ms); inference is the whole budget. **This runs on the Mac over USB
and touches NO bench hardware** — nereus000/001 and the AE3 are
untouched, so S23 bite R is not blocked, only re-ordered by Nick.
Deliverables: `bench/n6_stream_{board,host}.py` + 19 host tests. Previous:*
*2026-08-19 latest+3 (**S23 GOLD: the invariant is
NAMED — it is the serialized HE round-trip, not capture and not a
fixed 13 ms.** Two instrumented CLEAN rows: 12.15 (counters) → 12.53
(round-2 build: zero-alloc RX ring+pool, ref-stream sensor bypass;
bridge `5071cecd…`, suite 373). Round-2 ledger: enc ~44 + asm 1.6 +
send 33.4 — **ept.send blocks 21.2 ms/frame (was 0.5); the HE clears
20 chunks in ~33 ms then idles through the 44 ms encode (~41%
utilized)**. Route to 15+: overlap the feed (non-blocking chunk
pusher clocked off _rx callbacks; predicted ~48–52 ms cycle = 19–20
fps) — NEW bite below, also attacks HD's 72 ms ept-block. **Ops
rewrite: uhubctl NEVER cuts VBUS on Pi 5 → no cold boot; every
working "recovery" was demo_up's final `mpremote reset`. Cold-boot
recipe = mpremote reset or physical unplug.** Bite R SHRINKS to the
two still-unexplained states (raw-repl refusal through armed windows;
the CDC-RX-stall/empty-ring boot). Nick's 3 GOLD attempts SPENT
(12.53 < 15) — pivot per his standing call, or the overlap bite; his
gate. Previous:*
*2026-08-19 latest+2 (**S23 GOLD bounded + PIVOT
ORDERED (Nick): GOLD gets THREE more bench attempts, then the
attach-refusal/boot-state anomaly becomes the next priority as S23
bite R (below) — a fresh session tackles it.** The 13 ms hunt's
instrumentation SHIPPED (capwait counters: kick→collect, poll-gap
hist, cycle hist, enc_qin; bridge suite 341→359, bridge `44c20573…`
verified on /flash) but NO measured row yet: the whole bench day went
to attach-refusal incidents #4a/b/c — the wedge now has a SHAPE
(§bite R): ~4–6 mpremote attaches after any bridge teardown, refusal
below python, power-cycle-only cure, six incidents all on fw
`1e56071e…`/ELF `39717d44…`. demo_up HARDENED en route (mpr
timeout+armed-exit-retry wrapper, raw-repl-signature retry at every
step, loud inventory failure; units suite 43 green, errno allowlist
fix). Execution order now: **GOLD (≤3 attempts) → bite R → bite 3 →
S21 → S20.** Previous:*
*2026-08-19 latest+1 (**S23 GOLD arc: VGA color
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
NOT closed — Nick's call: GOLD CONTINUES (the ~13 ms hunt, kickoff
prompt = PROMPTS.md §7). **PR for the whole relay+GOLD arc OPEN for
Nick's review; interim-state PR, sprint stays open behind GOLD +
bite 3.** Previous:*
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

### S18 — Camera bench web tool  `[DEAD 2026-08-20 — Nick]`
**Dropped from the ladder.** The compare/gallery tool "works well enough
for now" (Nick); further work is PARKED until the tool needs changes.
Deliberately a tombstone, not a deletion: S18 shipped bench
infrastructure the project still runs on every single session — the
`bm-light` / `bm-telemetry` systemd units (bite D), `demo_up.sh`,
`bench-ctl.sh`, `chain_status.sh`, the ref-scene matrix, and the page's
`MEAS_FPS` provenance model. Killing the provenance of live tooling
would cost the next agent a day. Full record: DEV_LOG 2026-08-15…08-18,
DESIGN §D32 / §D36–D38.

### S19 — HD stills over pub/sub  `[DEAD 2026-08-20 — Nick]`
**Dropped from the ladder.** Bites 1–2 shipped (the HD capture +
transport work that S22/S23's measurements then inherited); bites 3–4
are dropped unshipped. Full record: DEV_LOG 2026-08-16, DESIGN §S19.
**The findings it raised do NOT die with it** — see "Flagged, not owned
by any bite yet" below; those are still unowned.

### S20 — Light intelligence (stub — was S19 in D30)  *(DELAYED behind S21 — Nick 2026-08-18, D39)*
Camera self-detects dark scenes (HP luma stats) → camera node issues
`light/control` requests (HE `bm_service_request`) → light auto-on;
customer never thinks about it. All on bm_service (§6.2).

### S21 — CV: count-and-report (stub — was S20 in D30)  *(PROMOTED above S20 — Nick 2026-08-18, D39; now FOLLOWS S8 — Nick 2026-08-20)*
**Owns the two bites that moved out of S8 2026-08-20**, so the two
sprints stop describing the same work: S8 = the detector and the
numbers; S21 = the product feature built on it.
- [ ] Detect/track/count pipeline vs T2 spec (target ≥24–32 px)
- [ ] Alert + evidence-JPEG path over the existing bridge→pub/sub link
      **← inherits S22 bite 1b's unfixed q90 burst loss** (carried
      2026-08-20; see "Flagged, not owned by any bite yet"). Evidence
      stills are exactly the q90-class payload that defect bites; size
      this bite expecting to fix it, not to discover it.
> **Scoping input from S24 (2026-08-19) — read DESIGN §S24 "what
> detection rate does an application actually need?" before sizing this
> sprint.** The required fps follows from `2 × speed ÷ object length`,
> and it splits the product line in two: **fish and jellyfish are
> throughput problems** (~2–3 fps needed, favouring the N6) while
> **urchins and kelp are energy problems** (1 frame per 80 s to per
> 4 hours — 7 fps is 560× to 10⁵× oversampled, and the AE3's
> 4.3×-better mJ/inference decides it). **No single board wins the
> product line.** Critically, the fish case is squeezed from both sides:
> single-pass downscale is fast enough to track but too coarse to detect
> at range, tiled HD restores the size floor but drops to 0.91 fps,
> below the ~2.8 fps tracking needs — so **a custom detector with a
> larger input is what makes fish counting possible, not an
> optimisation.** Input speeds/sizes are unvalidated estimates (SPEC
> §Open questions) — Nick to check before designing to them.
Urchin/target counting ON THE HP CORE (NPU; HE has no room/NPU access —
D29 context). Requires a custom Vela-compiled detector (S8 finding:
ROM detectors are person-class-only; HD tiled = 1.2 fps). Alerts +
evidence stills ride the existing bridge→pub/sub path. Data collection
for training can use the S18 tool + S17 pipeline.

### S22 — Camera pipeline hardening & headroom  `[x]`  *(**CLOSED 2026-08-20 on the shipped work — Nick.** D39's "RUNS FIRST" is superseded; S8 leads. The sprint's goal — "a camera node that cannot be wedged at any commanded rate, and a measured answer on how much fps headroom the encode path has" — is MET by bites 1 and 2. **What it does NOT deliver: bite 1b's burst-loss fix**, which is investigated but unfixed and is CARRIED FORWARD below rather than buried by this closure.)*
**Goal:** a camera node that cannot be wedged at any commanded rate,
and a measured answer on how much fps headroom the encode path has.

- [x] **Bite 1 — the HE flood fix (finding 1; evidence fully banked).** *(SHIPPED; on-chain ledger-exact at the exact rate+duration that killed the live demo — the closure's main evidence.)*
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
- [!] **Bite 1b — the burst loss — NOT DELIVERED; carried forward 2026-08-20 (see "Flagged, not owned by any bite yet" below). (Nick approved 2026-08-18).
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
- [x] **Bite 2 — encoder-headroom exploration (measure first, decide
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
**Closure note (2026-08-20, Nick):** bite 2's deliverable — the
candidate table with measured/sourced fps deltas and a written
recommendation — was reviewed and ACTED ON: it became S23's bite list,
and the encoder path went 7.41 → 12.53 fps on it. That is the demo,
satisfied in substance. Bite 1's demo is its own on-chain evidence
(10-min QVGA color 28.23 fps, 16,939 frames across ~5 vring wraps,
ledger-exact). Bite 1b is the sprint's honest debt — carried, not closed.

### S23 — Encoder fast path: push delivered fps to the hardware's limit  `[~]`  *(**PARKED 2026-08-20 behind S8 — Nick**: "before I spend any more time trying to make the VGA faster it's important to get the AE3 and N6 running a custom urchin model so that I can benchmark performance." Parked at **GOLD 12.53 fps** VGA color CLEAN; bites S (overlap the HE feed — the named route to 19–20 fps) and 3 (re-measure + guardrails, the sprint's pass/fail line) are UNSTARTED and keep their specs below. Bite R (board-state root cause) is also open — see its entry.)*
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
- [ ] **Bite S — overlap the HE feed (the named route to VGA-15; NEW
      2026-08-19). SEQUENCED BEHIND bite R — Nick's pivot call
      2026-08-19 evening ("Bite R"): the root-cause hunt runs first,
      fresh session, kickoff = PROMPTS.md §8.** Round-2 capwait ledger: the
      HP burst-feeds 20 chunks then waits — ept.send blocks 21.2
      ms/frame, the HE clears its work in ~33 ms (1.65 ms/chunk) and
      idles through the 44 ms encode (~41% utilized). Ship a
      non-blocking chunk pusher: stash the frame's msgs after asm;
      push with `ept.send(timeout=0)` from the `_rx` callback (each
      inbound echo = a freed vring slot, and _rx provably interleaves
      with to_jpeg); main-loop tail drain; re-entrancy guard between
      pusher and control sends (barrier queries — streams never
      re-init, so contention is rare but must be fenced). Predicted
      cycle max(enc 44, HE 33) + residue ≈ 48–52 ms = **19–20 fps**;
      HD's 72 ms/frame ept-block is the same disease. Acceptance:
      vga-color-15 ≥ 15.0 CLEAN ledger-exact, HD mono ≥ 3.5 held,
      10-min soak.
- [~] **Bite R — attach-refusal / boot-state anomaly root cause.
      INCIDENT #7 (2026-08-20 late night, S8 B2 demo) — the first on the
      NON-bridge stack, and it reshapes the suspect list:** AE3 on the
      S18 sticky-fb build (`7d4dbf7ab2.dirty`), NO bridge loaded, only
      pyserial raw-repl streams + mpremote ops (cp, probe run, one
      stream start/stop, then the workbench start probe refused).
      Enumerated, zero holders, dmesg clean of usb-storage resets; one
      serialized 75 s-silence + single `mpremote reset` recovery REFUSED;
      physical replug cleared it. **The bridge lifecycle is therefore NOT
      a necessary condition — the common factor across all seven is
      accumulated raw-repl attach/teardown traffic.** Nick 2026-08-20:
      this is now an issue we need to solve soon.
      ← IN PROGRESS (reproducer + instrument SHIPPED; see DEV_LOG
      2026-08-19/20 night). FINDINGS: (a) load does NOT cause it — 6/6
      clean loaded cycles, ledger-exact both ends; (b) NEW host-side
      failure mode found + reproduced twice: the **usb-storage reset
      livelock** (udev probes the MSC volume, board doesn't answer,
      usb-storage resets the device ~46x/min, each reset re-binds
      cdc_acm — presents EXACTLY as an attach refusal on a healthy
      board; cure = unbind usb-storage from the MSC interface);
      (c) **warm reset does NOT clear SRAM9** — only power loss does,
      so `mpremote reset` != physical unplug, contra the ops recipe;
      (d) MPU cache attributes are IDENTICAL on wedged vs healthy
      boots — evidence AGAINST the 0004/0005 bisect premise;
      (e) the v3 demo_up silent-fail was a SCRIPT defect (fixed).
      REMAINING: the silent refusal with NO resets and no legal bridge
      (22:03 capture) — now the only unexplained state.
      ← NEXT (Nick's pivot call 2026-08-19 evening; fresh session,
      own branch, kickoff = PROMPTS.md §8). SCOPE SHRUNK by the
      uhubctl-VBUS discovery — only two states remain unexplained
      (the §8 prompt lists them); screen every observed refusal
      against the state machine before counting it as anomalous.** Six incidents, all on fw
      `1e56071e…`/ELF `39717d44…` (the SHM-128K/MPU-patch stack).
      **Measured shape:** after a bridge lifecycle ends (he.start()
      … quiet-exit teardown with rp.stop()), the board tolerates ~4–6
      mpremote attaches, then refuses raw-REPL entry — fast
      ("could not enter raw repl") or hanging forever — persistently
      and BELOW python: a Pi reboot does not clear it (Pi 5 never cuts
      VBUS); only a true power cycle does (uhubctl or physical). Twice
      the refusal onset was mid-demo_up, ~4–6 execs after a teardown.
      Incident #2's trace adds: a linked boot that received ZERO VCP
      bytes for 30 s while bm-light heartbeated, and an HE ring dump
      that came back EMPTY — CDC-RX stall + unreadable SHM smells like
      memory-attribute corruption in the USB/rpmsg neighborhood, not
      an app bug. Ladder (2–3 sessions): (1) reproducer — scripted
      cold cycle → repeated bridge lifecycles → count
      attaches-to-wedge; (2) instrument — MPU/cache region config +
      SHM pool state dumped to flash at boot; on refusal, CDC endpoint
      state vs HE-ring readability (splits "USB died" from "SHM
      corrupt"); (3) the convicting bisect — same reproducer on the
      pre-SHM-128K firmware (`7d4dbf7`+sticky-fb): vanishes → patches
      0004/0005 cache attributes are the suspect; persists → Alif
      ROM/TinyUSB territory, mitigate. Ops armor already shipped
      (demo_up mpr wrapper); bite 3's soaks double as the test bed.
- [ ] **Bite 3 — re-measure everything and raise the truth.** Full
      ceiling rows + 10-min soaks at the new maxes (`s22_ceiling_rows`
      + new max-rate rows), MEAS_FPS + guardrail envelope re-derived,
      page provenance flipped. **Targets check: VGA color ≥15, HD
      mono ≥5–6 — the sprint's pass/fail line.** *(Sequenced AFTER
      bite R — Nick 2026-08-19.)*
**Demo (Nick):** bench page → VGA color q50 stream commanded 15+ fps →
the pill shows ≥15 delivered, ledger exact · `capture 90 hd mono`-class
HD mono stream ≥5 fps · the enc-matrix table re-printed on the patched
build next to S22's baseline.
**Interleaves:** S22 bite 1b fork instrumentation (Nick's push) — at
S23's HD-mono rates the frames stay q50-sized (55 chunks, measured
clean), so the burst defect does not block the targets; it still owes
the q90-class stills rung. **Needs:** Mac docker loop, bench; no new
hardware.

### S24 — N6 CV baseline  `[x]`  *(**CLOSED 2026-08-20 — bite 1 DELIVERED, remaining bites FOLDED INTO S8 (Nick).** Opened 2026-08-19 as its own sprint because S8 was then gated behind S13; Nick overrode that gate on 2026-08-20, so the reason for the split dissolved and keeping two sprints describing CV would just split the evidence.)*
**Delivered and kept:** bite 1 — a headless live detection stream from
the N6 into a browser (no OpenMV IDE; there is no macOS 14 build) plus
the first N6 sweep. `bench/n6_stream_{board,host}.py` + 19 host tests,
PR #45 merged. **Demo run by Nick 2026-08-19 — PASS** (six balls
tracked simultaneously at ~2 m indoors; ~1 W board draw, an
order-of-magnitude reading whose method was not recorded — re-measure
deliberately before relying on it). Measured: yolov8n_192 inference
**20.7 / 23.7 / 32.2 ms** at QVGA/VGA/HD, **capture+inference
end-to-end 47.9 / 41.8 / 30.2 fps**, capture DMA-hidden at 0.2 ms so
inference is the whole budget; live stream 22.6 fps at VGA with the
blob overlay on. Full tables: DESIGN §S24 detail.
**Folded into S8 2026-08-20:** bite 1b (multi-colour blob thresholds)
→ S8 bite A · bite 2 (the board-decision number) → S8 bite D · bite 3
(a real multi-class detector + the `stedgeai` question) → S8 bite B1.
**The verified hardware facts moved to S8** — they are operational
guidance the next session needs in front of it, not history.

**DELIVERED AFTER THE FOLD (2026-08-19 evening → 2026-08-20).** This
work landed while S24 was being closed, so it is recorded here and its
open items are routed into S8 rather than re-splitting the sprint:

- `[x]` **AE3 run of the same demo + hardware ranking.** Same scripts,
  board swapped. **The AE3 wins on energy and the N6 on throughput, and
  the ranking inverts by workload** — inference 27.5 vs 23.7 ms (1.2×,
  and confounded by different model binaries) but JPEG encode 73.8 vs
  3.9 ms (19×), so delivered 7.6 vs ~19 fps; against that, **5.5 mJ vs
  23.7 mJ per inference (AE3 4.3× better)** from Nick's ~0.2 W vs ~1.0 W
  readings. Fish/jelly want throughput (N6); urchins/kelp want energy
  (AE3). Tables + caveats: DESIGN §S24 ranking.
- `[x]` **Side-by-side viewer** (`--board LABEL=PORT`, repeatable): both
  boards in one page from one process, per-board supervisor threads,
  panels showing capture geometry, JPEG q **and measured KB/frame**,
  image vs USB-wire Mbps, and **model filename + byte size** — which
  puts the apples-to-apples question on screen and answers it NO
  (1.90 MB AE3 vs 3.08 MB N6 under the same filename). Ran live on
  nereus000. Host suite **53**.
- `[ ]` **OWED — the AE3's inference-only ceiling.** The demo's fps is a
  *streaming* ceiling; an application reporting results never pays the
  encode. Currently a **bound, 26–36/s** (36 if its 11.6 ms capture
  overlaps inference, 26 if serial; the N6's is measured at 42). ~2 min
  of board time. **This is the number a customer's application is
  limited by** — it should not stay a bound.
- `[ ]` **→ S8: frame-validity gate before inference (PRODUCT
  REQUIREMENT).** In the dark the AE3 reports `person 0.87` on a
  full-frame box: its sensor crushes the scene to **90.6% exactly-zero
  pixels** and the network's head emits a fixed confident output when
  fed zeros (the N6, whose exposure yields mean luma 12.1 with no zero
  pixels, correctly detects nothing). **A subsea node sits in darkness
  routinely, so this would emit confident false counts all night.**
  Gate on mean luma / non-zero fraction upstream of `predict()`.
  First confirm in daylight that it vanishes — if it survives good
  light, the model/NPU path is suspect and this diagnosis is wrong.
- `[ ]` **→ S8: AE3 low-light exposure.** Same evidence, separate
  problem: the N6 gets a usable dim image where the AE3 gets zeros. A
  camera that goes blind below some light level bounds the product
  regardless of the detector.
- `[ ]` **→ S8/S21: Pi Zero 2 W + IMX on the same axis** (Nick's next
  step). Measure **mJ per inference**, not fps, so all three boards land
  on one comparable scale. Prior: no NPU, 1–2 W baseline = 5–10× the
  AE3's *total* draw before any work.

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

### S8 — Custom detector: a from-scratch model on AE3 + N6  `[ ]`  ← **NEXT (Nick 2026-08-20)**
*(Was "Edge CV bring-up (T2)". Its old gate — "runs AFTER the BM-native
arc S9–S13; board risk gates CV investment" — is **OVERRIDDEN by Nick
2026-08-20**: CV is now the board-selection input, so it leads.
**S24 is CLOSED and folded in** (2026-08-20) — its round-2 work (the
AE3-vs-N6 head-to-head, the side-by-side viewer, the dark-frame
false-detection finding) merged here via PR #48, and its open items are
routed into S8's bites below. The "S24 is Mac-only, S8 wants the bench"
split is dead with it: **both boards are on nereus000** (D44), so S8
owns one bench and one harness. Encoder
fps work (S23 bites S and 3) waits behind these numbers — Nick: get a
custom model benchmarking on both boards before spending more on VGA.)*
**Goal:** prove the ENTIRE custom-model path — train, compile, deploy,
measure — on both boards, using a target we can count exactly, before
any urchin labelling effort is spent.

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

- [x] **N6 live detection stream + first sweep** *(was S24 bite 1;
      folded here 2026-08-20. Demo PASSED 2026-08-19, PR #45 merged.
      `bench/n6_stream_{board,host}.py` + 19 host tests; yolov8n_192
      20.7/23.7/32.2 ms and end-to-end 47.9/41.8/30.2 fps at
      QVGA/VGA/HD. Tables in DESIGN §S24.)*
- [x] **Bite A — match more than one colour at once** *(was S24 bite
      1b.)* **DEMO PASSED 2026-08-20 (Nick), on the 3D-printed two-camera
      mount.** Shipped:
      repeatable `--blob-thresh NAME:L,L,A,A,B,B`, per-class counts on the
      wire (`bc`/`amb`/`bb`), one palette colour per class, `--blob-scan
      codes|per-class`, `--save-frames` + `index.jsonl` labelled capture,
      and an overlap guard. Folded with PR #48's two-board viewer, so both
      boards report per-class counts in one page. 102 host tests.
      **`b.code` settled by probe** (DESIGN §S8 bite A detail): index
      bitfield, first-matching-threshold-wins per pixel, merge ORs codes —
      so overlapping boxes silently under-count and the repo's own
      documented pink/purple example overlapped by 8.8%. Blob cost measured
      at 10.6 ms (N6) / 15.2 ms (AE3).
      **Tuning outcome (ground truth 11 pink / 10 purple):** both boards
      converge at pink 10 / purple 7 with one ambiguous merge. Thresholds
      AND pixel floors must be PER BOARD — the N6's blue cast puts its pink
      at b≈−16.7, inside the default purple box, which is why one shared
      threshold gave 5 blobs on one board and 18 on the other. Known
      remaining error modes, all measured: touching balls merge into one
      blob (the "2×" case), and both boards lose balls at the FOV edges —
      the N6 noticeably more than the AE3.
      **Nick's product read (2026-08-20), recorded because it moves the
      board decision:** the AE3 is NOT out of the running. Its lower fps and
      absence of a hardware JPEG encoder were expected to disqualify it, and
      on this task they do not — its detection accuracy matches or beats the
      N6, which struggles more at the edges of its field of view.* Nick threw pink balls into a purple-tuned scene and they
      were correctly ignored: the threshold is a single LAB box and
      pink's `b` sits outside the purple range. `find_blobs` already
      takes a LIST of thresholds; the board script passes one and the
      host exposes a single `--blob-thresh`. Small change: repeatable
      `--blob-thresh`, one colour per box, each drawn in its own colour.
      **Why it leads:** this is the CLASSIC-CV CONTROL the ML numbers
      get compared against — "what does a LAB threshold already achieve
      on this exact scene at 1 m and 2 m" — and without it bite C's
      table has no baseline. Until it lands, `--tune` reads a threshold
      off whatever object is really in front of the lens, which is the
      honest way to get one anyway.
- [x] **Bite B1 — the toolchain. CLOSED 2026-08-20: both boards run our
      own compiled models.** No new tooling was needed — OpenMV ships both
      compilers in the SDK we already had, driven by its own
      `tools/modelc.py`. AE3 = vela 5.0.0 → copy to `/flash` → 1.66 ms
      (vendor's own copy of the same model: 1.81 ms). N6 = stedgeai 4.0.0 →
      ROMFS image over USB DFU (alt 3, ROMFS0; no ST-LINK) → 2.75 ms
      (vendor's: 2.76 ms). Compile verified byte-identical to the vendor's
      shipped artifacts (`person_detect` 274,272 B, `fomo_face_detection`
      64,064 B). The N6 canNOT load a model from `/flash` — stedgeai's
      relocatable binary wants its params in XIP flash — so its deployment
      is a partition flash, not a file copy. Recipes: `ml/compile_model.sh`,
      `ml/build_romfs_n6.sh`, `ml/deploy_probe.py`. Detail in `ml/README.md`.
- [x] **Bite B0 — Mac training host.** Python 3.11 venv, torch 2.12.1 on
      MPS, ultralytics 8.4.124, 83 packages pinned; datasets/runs/venvs live
      in `~/nereus_ml`, outside the repo (worktrees). `ml/chain_proof.py`
      re-verifies train→export→inspect after any version bump. **Known
      blocker for B2:** ultralytics 8.4.124's only TFLite path is LiteRT,
      which emits NCHW float32; the boards want NHWC uint8 (OpenMV's own
      source models are `(1,192,192,3)` uint8, scale 1/255). OpenMV's
      maintainers report stock Ultralytics INT8 export failing ST's
      compiler outright and point at ST's YOLOv8-STEdgeAI / Roboflow's
      `ultralytics-openmv` fork — so YOLO is the wrong FIRST target; a small
      classifier or FOMO-style detector clears the path with far less risk.
- [x] **Bite B2 — the from-scratch two-colour detector. DEMO RUN BY NICK
      2026-08-20 (late night) — PASS (on the second click; the first hit
      bite R incident #7, cleared by replug). PR #55 OPEN.**
      Delivered on branch `claude/two-colour-detector-s8-b2-ea5ca7`:
      693-frame two-board capture (one 10-min bench window) → offline
      relabel (`ml/fomo/relabel.py` — board labels had measured defects:
      N6 pink collapse, shirt-as-purple) → from-scratch Conv/BN/ReLU FOMO
      (`ml/fomo/train.py`, 119 KB int8 uint8-io; int8≈float, P/R ~0.73/0.87
      vs noisy auto-labels) → compiled+deployed by the B1 routes →
      **measured on-board: AE3 5.51 ms (/flash, sha verified), N6 6.36 ms
      (ROMFS DFU alt 3, partition read-back MATCH, /rom 18 entries
      intact)** — the NPU class on both; acceptance met. Harness grew a
      FOMO model mode (mc counts on the wire, model-vs-blob side by side);
      suite 134. Recipe `s8-two-colour-model` ships models[] sha256 for
      S25 bite 3. **Known model debts (sized, not hidden): learned the
      USPS-box lettering as purple (label noise), dim-pink recall on the
      AE3, exact-count weak vs noisy labels. All data ~1 m (Nick's call
      at capture time).** Keras BN-momentum trap recorded in DEV_LOG.
      **B1 — does a stock int8 `.tflite` run on the NPU?** *(was S24
      bite 3, promoted by Nick's "sports ball" question.)* The ROM
      model's `(1, 5, 756)` output proves it carries ONE class, so no
      configuration reaches the other 79 and a custom/multi-class model
      is mandatory. Concrete first target: ST's int8 COCO `yolov8n` for
      STM32N6 (the 192 px variant matches the ROM model's input
      exactly). **Settle FIRST, by test not by reading: does OpenMV's
      `ml.Model` load a stock int8 `.tflite` and run it on Neural-ART,
      or must it be compiled with `stedgeai`?** Sources conflict, and
      the answer decides whether this is an afternoon or a toolchain
      bite. Cheap experiment: put one candidate on `/flash`, load it,
      print `output_shape`, time `predict` against the ROM model's
      23.7 ms — **a CPU fallback will be obvious in the number.**
      The AE3 half is Vela (2× Ethos-U55, SPEC §140) and is S8's own.
      **Needs Nick's go: this is the first bite that writes to the board
      and the first that downloads a model.**
      **B2 — the detector**: collect + label the two-ball set, train,
      export, compile per target, deploy to both boards.
      **Acceptance — the trap this bite exists to avoid: prove the model
      runs ON THE NPU, not silently on the CPU.** A `.tflite` the
      accelerator rejects still returns correct answers at a fraction of
      the speed; "it inferred" is not the artifact. The evidence is a
      measured per-inference time consistent with the tables above.
      **GATE: if either board has no viable path, STOP and re-plan with
      Nick before any dataset effort is spent.**
- [ ] **Bite B3 — label-review GUI (Nick, 2026-08-20, after the B2 demo
      passed; NEXT working session).** Nick reviews ALL training frames
      himself in a GUI, corrects the auto-label boxes by hand, and can
      label beyond colour classes (the class list must not be hard-wired
      to pink/purple — bite E's urchins ride the same tool). Scope: browse
      the capture set (frames + labels.jsonl), draw/move/delete/reclass
      boxes, keyboard-fast, save back to the same labels.jsonl format so
      `ml/fomo/train.py` consumes corrections with zero changes. The B2
      debts this directly attacks: the learned USPS-lettering false
      purple, dim-pink misses, and exact-count weakness vs noisy labels.
      *Verifiable:* Nick corrects ≥50 frames in one sitting; retraining on
      the corrected set moves val precision measurably; the corrected
      labels.jsonl round-trips through the trainer unchanged in format.
- [ ] **Bite C — end-to-end metrics: capture → detect → count, at 1 m
      and 2 m (Nick 2026-08-20).** Not inference-only — the whole chain,
      which is exactly what the per-tile numbers could not tell us.
      Report per distance, per board, and **per method (blob baseline
      from bite A vs the custom model)**: end-to-end fps, the per-stage
      split (capture / preprocess / infer / count), and count accuracy
      against known ground truth (N pink, N purple).
      **Record pixels-on-target next to the distance.** The metre figure
      is bench-specific; the px figure is what transfers to the urchin
      case and to the T2 floor (target ≥24–32 px). 1 m → 2 m roughly
      halves the target's pixel size, which is the real variable under
      test. Nick's demo already saw six balls at ~2 m with the blob
      path, so that is the number bite C must beat or explain.
- [ ] **Bite D — the number that decides the board** *(was S24 bite 2.)*
      Re-run the HD tiled-coverage arithmetic on the N6 latencies and
      put it next to the AE3's, so "N6 vs AE3 for edge CV" is a measured
      comparison rather than two tables from different sessions. **Must
      carry the model-variant confound explicitly** — the two boards
      ship different yolov8n binaries (1,994,976 B AE3 vs 3,233,408 B
      N6), which is exactly the confound bite B's single custom model
      finally removes. Feeds Nick's board decision; no new hardware.
- [ ] **Bite D2 — surface model confidence values (Nick's question,
      2026-08-21).** The FOMO head computes a per-cell class probability
      and the harness currently throws it away at the margin threshold.
      Put a confidence on each model detection: `mb` boxes carry a conf
      field, the overlay label reads "pink 0.87", the page HUD shows it.
      NOTE the blob baseline CANNOT have one — `find_blobs` is a hard
      LAB threshold, a pixel passes or it does not; nearest analogue is
      pixel count, already shown. Document that asymmetry on the page
      rather than inventing a fake blob confidence. *Verifiable:* live
      page shows per-detection conf for the model panels; a ball
      half-out of threshold shows visibly lower conf than a centred one.
- [ ] **Bite E — the urchin model.** Once the path is proven and a
      labelled set exists, same pipeline against the real target; the
      T2 accuracy question (urchin ≥24–32 px) rides here.
      **Demo bar set by Nick 2026-08-21: a truly custom urchin model
      running on BOTH boards with a screen showing the urchins — the
      project's HIL. This demo is the GATE for sprint S26 below.**
      What carries over from B2 unchanged: the whole toolchain (capture
      rig, labels.jsonl format, trainer, int8 export, both compile+
      deploy routes, recipe/page). What is genuinely different: labels
      cannot come from a colour threshold (urchins are texture/shape,
      not a LAB box — B3's GUI is the labelling path), the scene is
      underwater (turbidity/lighting; pixels-on-target ≥24–32 px sets
      range and may force a larger input than 192 — the S24 finding),
      and the tiny colour-separable net will likely need more capacity
      (bigger backbone; hue augmentation becomes legal again since
      colour is no longer the class).
      **Dataset source EXISTS (Nick, 2026-08-21): thousands of urchin
      images on hand; label status UNCONFIRMED — Nick to confirm.
      Treated as solved-by-source come training time. If they arrive
      labelled, the new work is a one-bite converter (their format →
      labels.jsonl) and B3's GUI becomes review/spot-fix rather than
      from-scratch labelling; if unlabelled, B3 is the labelling path.
      Either way B3 gets built first and against these images.**

**BENCH TOPOLOGY CHANGED 2026-08-20 (Nick, D44): BOTH boards are on
nereus000's USB.** The Mac holds no board — it is the training and
toolchain host (Docker, dataset work, model compilation) and artifacts
reach the boards *through the Pi*. Board identities, verified live by
reading both banners, are in SPEC §Board identity on nereus000; the
short version, because it is the opposite of the obvious guess:
**the N6 enumerates as `usb-MicroPython_Pyboard_Virtual_Comm_Port…`
(`37c5:1206`) and the AE3 as `usb-OpenMV_OpenMV_Camera_0829c14…`
(`37c5:16e3`)**. Always the by-id path, never `ttyACM<n>`.
Consequence for the bites below: A/B1/D are **no longer Mac-only**, and
`bench/n6_stream_host.py` resolves ports by globbing `/dev/cu.usbmodem*`
— Mac-only, so it needs a by-id path before it runs on the Pi.

**Hardware facts, all verified live this session — do not re-litigate:**
- Board reports `OpenMV v5.0.0; MicroPython v1.28.0-49`, built
  2026-07-02, `OpenMV N6 with STM32N657X0`, free heap **25.6 MB**. On
  macOS it enumerated as `/dev/cu.usbmodem1101` (VID `37c5`) — that was
  the 2026-08-19 topology; see the by-id paths above for the bench.
- **The two boards do not run the same firmware**: AE3 is the S18
  patched build `v5.0.0-52.g7d4dbf7ab2.dirty` (D38), N6 is stock
  `v5.0.0`; free heap differs ~7.7× (25,393,136 vs 3,281,488 B at VGA
  with yolov8n_192 loaded). Bite D's confound list grows by one.
- **Verify firmware with `sys.version`, NOT `os.uname()`** — uname's
  `release` is the MicroPython version (`1.28.0`) and carries no OpenMV
  build. Same trap the S7 flash work recorded.
- `/rom` carries 9 `.tflite` + 3 cascades; `yolov8n_192.tflite` is
  **3,233,408 B** — the N6 variant, ~1.6× the AE3's 1,994,976 B, so
  cross-board latency is model-variant-confounded (DESIGN §S8
  correction). `force_int_quant.tflite` has a non-image input `(1, 36)`
  and is correctly SKIPped.
- **`yolov8n_192` and `yolo_lc_192` are single-class ("person")** — the
  label files read `person` and nothing else. Zero detections on any
  non-person scene is the CORRECT artifact, not a broken run.
- **The sensor letterboxes to 16:10 at every size**: QVGA = 320×200,
  VGA = **640×400**, HD = 1280×800. `SXGAM` and `WQXGA2` are exported
  by the `csi` module but the sensor REFUSES them (`Sensor control
  failed.`) — the module's constant list is not the sensor's ladder.
- **OpenMV v5 `draw_*` takes a TUPLE first argument**
  (`draw_rectangle((x,y,w,h))`, `draw_string((x,y), s)`,
  `draw_cross((x,y))`); the older `x, y, w, h` spelling raises
  `TypeError: object 'int' isn't a tuple or list`. Blob fields and
  `get_statistics()` means are **attributes**, not methods (`b.rect`,
  `st.l_mean`). Found the hard way — see bite 1's latent-bug note.
- Model load is **~2.2 ms**: the tflite is memory-mapped from ROM, not
  copied into the heap. Loading is not a cost worth optimizing.
- **`/rom/yolov8n_192.tflite` is a ONE-CLASS model, and the output
  tensor proves it**: `output_shape` is `(1, 5, 756)`. A YOLOv8 detect
  head emits `(batch, 4 + num_classes, anchors)`, so 5 = 4 box coords +
  **1 class**; an 80-class COCO export would be `(1, 84, …)`. (756 =
  24²+12²+6², the anchor count for a 192 px input at strides 8/16/32 —
  the shape is fully accounted for.) **"sports ball" is therefore
  unreachable by any configuration change** — there is no channel for
  it. An 80-class detector means shipping a different model: ST
  publishes an int8 COCO `yolov8n` for STM32N6 at 192/256/320/416 —
  that is bite 3's subject, and whether OpenMV loads it directly or it
  needs an `stedgeai` compile pass is UNVERIFIED (sources conflict) —
  flag, don't guess.
- **Nothing autostarts on the board.** `/flash/main.py` is the stock LED
  blinker and stays that way; the stream script is pushed into the raw
  REPL and runs from RAM. So a power cycle or replug leaves the board
  blinking and NOT streaming — the host viewer is what must be
  (re)started. It now reconnects by itself, including across a replug,
  because **the device node is not stable**: this board moved from
  `usbmodem1101` to `usbmodem1201`, so the port is re-resolved on every
  attempt rather than cached.
- **A nudged USB connector ends the stream** (`SerialException: [Errno 6]
  Device not configured` — seen just from moving the camera). That used
  to kill the reader thread silently while the page kept serving the
  last frame with live-looking stats. The viewer now catches it,
  reconnects, and reports `stale_s` + a red NOT LIVE banner. **Three
  distinct bugs in this bite produced the same symptom — a plausible
  still image** (unexercised draw path, mpremote decay, serial drop); a
  frozen stream and a motionless scene are indistinguishable by eye, so
  liveness has to be measured and displayed, never inferred.
- **Stop the stream viewer with Ctrl-C, never `kill -9`.** A SIGKILL
  skips the board teardown and leaves it streaming into a closed
  endpoint from inside the raw REPL; measured once, it took the N6 off
  the USB bus completely (device node gone, absent from
  `system_profiler`) and needed a **physical replug** — a Mac cannot
  power-cycle the port the way `uhubctl` can on the Pi. SIGTERM/SIGHUP
  are now handled and unwind through the clean path.
- **`mpremote run` is fine for bounded output and unusable as a
  continuous transport** — it accumulates and rescans the script's whole
  output, so a stream decays with total bytes (measured: ~20 fps → <2
  fps, wedged by frame ~703, board-side work flat at 38.5 ms
  throughout). The stream viewer drives the raw REPL over **pyserial**
  instead; the sweep tables were collected with mpremote and are fine.
  Firmware is **v5.0.0, the current stable release** (published
  2026-07-02, matching the board's build date; the newer `development`
  tag is explicitly unstable). Not updated, per scope.

**Demo (Nick):** the same custom two-colour model running on AE3 **and**
N6; one table of end-to-end capture→detect→count fps and count accuracy
at 1 m and 2 m for both boards and for both methods (blob baseline vs
custom model), pixels-on-target recorded alongside; plus the board-
decision table from bite D reviewed together.
**Needs:** bench time; **both boards on nereus000** (D44); the Mac set up
as the training/toolchain host (Docker + ML env — unstarted, see bite
B0); **S24's bite-3 answer for the N6 compile path** (do not re-derive
it, it is bite B1 here).
**Reuse before rewriting:** S24 shipped `bench/n6_stream_{board,host}.py`
(+19 host tests) — a headless capture→infer→view harness for the N6.
Bite B's end-to-end measurement should extend that and
`bench/ae3_npu_bench.py`, not start a third harness.
**Note:** the old S8 bites "detect/track/count pipeline" and "alert +
evidence-JPEG path" MOVED to S21 — S8 is detector + numbers, S21 is the
product feature on top. **S24 was FOLDED IN here 2026-08-20** (bite 1
delivered and kept; 1b/2/3 became bites A/D/B1) — one sprint owns CV so
the evidence stops splitting.

---

### S25 — nereus000 as the machine-vision workbench  `[~]`  ← **RUNNING (bites 1+2 DONE, demo PASSED by Nick 2026-08-20 night)**
**Goal:** boot the Pi, open a page, pick a test, and the Pi puts the OpenMV
hardware into that test's known-good state and runs it — no hand-flashing, no
remembering command lines, no agent required. Every released test setup adds a
menu entry.

**Why now (Nick):** "We have been producing and will continue to produce bench
testing, UIs and tests that run on the Pi and OpenMV board... instead of asking
you to flash and setup and run everything, the Pi should be programmed to
reconfigure and run the software needed." This is the interactive way to get
the hardware back to a known-good state before a test.

**Reuse before rewriting — S18 already shipped most of the substrate**
(tombstoned, not deleted, for exactly this reason): `pi/bench_web/bench_web.py`
+ its tests, `pi/services/bench-web.service` and five sibling units, and
`pi/bm_bench/{demo_up,bench-ctl,chain_status,deploy}.sh`. This sprint is a
**menu and a runner on top of those**, not a new web application. A rewrite
needs a measured reason.

- [x] **Bite 1 — recipe format, registry, and the page on boot.**
      → **DONE 2026-08-20 (night), proven across Nick's reboot.** Strict-schema
      TOML (unknown keys are ERRORS — the format is being proven), registry
      re-read per request (drop a file in, refresh; broken file = red card
      naming the error, never a silent absence), passive preflight (by-id
      presence → waiting/ready/held with /proc-scanned holders named; NO
      serial-port contact by design), `workbench.service` ENABLED at boot on
      :8088. Per Nick: friendly titles (sprint tags demoted to the recipe
      line), per-recipe `thumbnail` (live-captured, confined /thumbs/ route),
      and per-recipe `services` replacing the six-unit panel (three of those
      units belong to the parked two-Pi T1L bench). A "test
      setup" is a declarative file in the repo naming: which boards it needs,
      the state they must be in (firmware label, models present + sha256,
      `/flash` contents), the command to run, the URL to open, and its health
      check. The home page lists every recipe plus a **preflight panel**
      (boards enumerated by their by-id paths, ports free, disk, services).
      Autostarts on boot via a systemd unit on the `bench-web.service`
      pattern. **No deploying yet** — listing and preflight only, so the
      format is proven before anything drives hardware.
      *Verifiable:* fresh boot of nereus000 → the page answers on the LAN and
      lists the recipes with a correct preflight verdict for both boards.
- [x] **Bite 2 — the runner, and the board lock.**
      → **DONE 2026-08-20 (night); demo RUN BY NICK — PASS** (start → LIVE
      with hyperlink → stop → settle countdown → start again). Health-gated
      LIVE (recipe [health] URL must answer 200), one demo at a time (409),
      foreign port holders refused BY NAME and never killed, SIGINT→SIGTERM→
      STUCK ladder with `signal.SIGKILL` pinned out of the source by a test,
      pidfile adoption across workbench restarts (proven through the unit
      install). **Hard-won addition: SETTLE=35 s window** — Nick's quick
      stop→start wedged the AE3 into S23 bite R's power-cycle-only raw-repl
      refusal (two serialised recovery attempts failed; physical replug
      cleared it), so the runner refuses restarts onto just-stopped boards
      and the page counts down. 61 host tests. Start/stop a selected
      recipe from the page, with live status and log tail. **A single-owner
      lock on the boards is a REQUIREMENT of this bite, not a nicety:** this
      session repeatedly wedged a board by letting two processes touch one
      port, and a menu that can launch anything makes that trivially easy.
      First recipe = the S8 two-colour ball demo (fully specified by PR #50,
      including per-board thresholds and pixel floors).
      *Verifiable:* click the S8 recipe on a cold bench → both boards stream
      with the tuned thresholds; click stop → ports free, boards enumerated;
      a second start attempt while one is running is refused, loudly.
- [ ] **Bite 3 — state reconciliation ("put it back to known-good").** Before
      running, verify what is actually on the hardware — model present, sha256
      matching, firmware label, `/flash` contents — and repair only what has
      drifted. Report drift explicitly rather than silently fixing it. The
      deploy routes are already proven (S8 B1): AE3 = copy to `/flash`, N6 =
      ROMFS image over USB DFU alt 3.
      *Verifiable:* delete a model from a board, click the recipe, and the
      page reports the drift and restores it; re-running with nothing drifted
      does no writes at all.
- [ ] **Bite 4 — a second recipe + the "how to add a test" doc.** Proves the
      format generalises beyond the one it was designed around, and writes
      down the release step so adding a menu entry is a documented act.

**Open questions for Nick, flagged not guessed:**
- **Exposure — DECIDED (Nick, 2026-08-20):** bind 0.0.0.0 on the trusted
  LAN, loud banner, no auth. Consequence honoured in the runner: it refuses
  anything it cannot undo — never DFU alt 0 (the schema cannot even express
  a DFU target), never SIGKILL, never kill a foreign port holder.
- **Sudo steps.** Installing/enabling units and udev rules needs sudo, which
  the agent does not have — those stay one-time copy-pasteable commands for
  Nick, exactly as `pi/ae3_flash/README.md` already does.
- **Boot ordering.** USB enumeration can lag service start; the preflight must
  treat "no boards yet" as a normal early state, not a failure.

**Demo (Nick):** cold-boot nereus000, open the page from a laptop or phone,
pick the S8 ball demo, watch both boards come up streaming with the tuned
per-board thresholds, stop it, and see the ports released.
**Needs:** bench time; sudo for the one-time unit install.


### S26 — solo ML pipeline: take the training wheels off  `[ ]`  *(stub — added 2026-08-21 at Nick's direction. **GATED behind S8 bite E's demo**: a truly custom urchin model running on both boards with the urchin HIL screen. Runs only after that passes.)*
**Goal:** Nick drives dataset → train → evaluate → deploy **solo, no agent
in the loop**. B2 built the plumbing; this sprint builds the judgment
layer — every check the agent performed by hand becomes a printed verdict.
- [ ] **Report cards.** `relabel`/labelling emits a label sanity report
      (count distributions vs expected, flagged frames); `train.py` emits
      a training report (metrics, worst-frame overlays, pass/fail vs the
      previous model). *Verifiable:* "is this model good?" answerable
      from the report alone.
- [ ] **One-command pipeline driver.** `ml/pipeline` wraps train → export
      → compile (both targets) → NPU acceptance check into one command
      with one PASS/FAIL summary; runbook section in `ml/README.md`.
- [ ] **Deploy from the page.** Rides S25 bite 3 reconciliation: bump the
      model sha256 in the recipe, the workbench repairs the drift. No
      hand-driven DFU/mpremote in the happy path.
- [ ] **Scored evaluation on the page.** Bite C's harness surfaced as a
      page view: counts vs ground truth, per board, per method.
**Demo (Nick):** starting from a folder of new images, Nick ships a
retrained model to both boards and reads its scorecard — without Claude.
**Needs:** S8 bites B3/C/E shipped (the GUI, the metrics harness, the
urchin HIL demo); S25 bite 3.

## Flagged, not owned by any bite yet
*(Was "Flagged during S19" — retitled 2026-08-20 when S19 died and S22
closed; each item now names its own origin. Nothing here is owned by a
live bite, and nothing here should be assumed benign because it is old.)*

- **The q90 burst loss — S22 bite 1b, investigated but UNFIXED
  (carried 2026-08-20 at S22's closure).** Every hop is EXONERATED
  except the telemetry fork's internals: the q90 ref frame is really
  149 chunks; "gaps=54" was reassembler TAIL-LENGTH (one chunk lost,
  usually idx ~95), not 54 lost; HE published all, bridge relayed all
  (qdrops=0), uart clean, and tcpdump saw all 149 on the UDP wire in
  order with valid inner checksums. The fix needs fork instrumentation
  (pin discipline — Nick's push). **Why it did not block the closure:**
  at S23's HD-mono rates frames stay q50-sized (55 chunks, measured
  clean), so the defect does not block those targets — but it still
  owes the **q90-class stills rung**, which is exactly what S21's
  "alert + evidence-JPEG path" will ride on. Whoever picks up that S21
  bite inherits this.


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
  requirement matrix; AE3 confirmed as this project's platform (Nick).
  **The CV half of this item was pulled out and scheduled 2026-08-19 as
  sprint S24 (N6 CV baseline); the H.264/VENC question stays iceboxed.**
- SG JP1/JP4 breakout confirmation (would clean up the S4 harness)
- Power-gating architecture (AE3 supervisor + load switch) from board-selection analysis
- ~~bm_core port (post-S7 decision)~~ → scheduled 2026-08-11 as sprint S10
