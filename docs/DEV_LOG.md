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

## 2026-08-16 — Sprint S18 — bite C1: the bench page is live, and its click guard is enforced on the server

**Branch:** `sprint/18-bench-web` (`14e8446`, cut from `main` @ `18349ed`)
**No fork change, no firmware change, no board contact.** Pi-side python and
a static page only — the AE3 keeps running the S19 artifacts, so there is no
pin move, no ABI lockstep and no size audit in this bite.

**Done:**
- Nibble-1 plan approved with 5 decision points (**D35**). Bite C split into
  **C1** (drive the bench: controls, live view, pill, warnings, guard) and
  **C2** (gallery, side-by-side compare, RGB+luma histograms).
- `pi/bench_web/bench_web.py` (:8090) + `static/bench.html`, carrying the
  approved mockup's layout, CSS and feasibility model and **deleting its
  simulation** — no embedded reef photo, no synthetic scene, no client-side
  JPEG encoder, no fake ledger. Every number comes from `status`.
- The live view is an `<img>` at the frozen S3 server's `/stream`: no frame
  bytes pass through this server, and the single-producer ingest on `:8081`
  is not touched.
- **The click guard is in Python, mirrored in JS.** Two holds — *busy* (one
  camera command at a time) and *settle* (8 s, and ONLY for a command that
  changes resolution or pixel format, because only a genuine delta re-inits
  the sensor). `stop` is never gated.
- `pi/services/bench-web.service` + a `bench-web` arm on
  `install_stream_service.sh`, installed **disabled** like the BM nodes.
- Host tests **42 checks** (`pi/bench_web/test_bench_web.py`), injected clock,
  no hardware. Bite D's 43 still green.
- **Live on nereus001**: checkout moved to the branch, unit installed and
  started, host tests re-run **on the Pi** (42 OK), page served (34,478 B),
  and `/api/status` returning the real ledger through the real socket.

**Broke/surprised us:**
- **`mode_active` is "last commanded", not "currently busy".** Found by
  reading `camera_svc.c` before relying on it: it stays `1` after a still
  completes and only `stop` clears it (`s_mode_active`, lines 74/85). A
  guard keyed on it would never release. Completion comes from bite B's save
  counters instead.
- **`save.state` still reads `saved` from the PREVIOUS capture at the moment
  of arming**, so gating on the string releases the gate one poll after the
  click — i.e. it would have looked like it worked, and let exactly the
  fast second click through. The counters are monotonic; the string is not.
  Both traps now have a named test.
- **Two UI faults only a screenshot caught**, both in states the operator
  stares at: a disabled *primary* button faded to an unreadable blue block
  (dark text at 45% on the accent fill), and the stage collapsed to a sliver
  when no stream was running, clipping its own diagnostic. Fixed in CSS.
- **The live `<img>` is UNVERIFIED in a real browser.** The sandboxed
  browser pane blocked `nereus001:8080` (`ERR_BLOCKED_BY_CLIENT`) while
  serving `:8090` fine. The S3 server itself answers `200` on `/stream` and
  `/frame.jpg` from the Pi, so the endpoint is good — but the embed is
  Nick's to confirm.
- **nereus000 looked dead and was not.** An ssh hung past 120 s and I
  recorded it as unreachable; it was actually blocked on a **Tailscale SSH
  re-authentication prompt**, which is invisible in a piped command. Once
  that was satisfied the same command returned instantly. Worth knowing:
  on this bench a silent 120 s ssh hang is a plausible auth prompt, not
  evidence of a down host — and "unreachable" is a claim that needs the
  same standard of proof as any other.

**Bench state:** **both Pis on `sprint/18-bench-web` @ `8431690`.**
nereus001: `bench-web` installed (disabled at boot) and **RUNNING**;
`bm-telemetry` **RUNNING** (started here — that role never opens the CDC
leg, so zero camera contact); `t1l-stream-server` active. nereus000:
`bm-light` **inactive**, no local modifications, AE3 not staged. AE3
untouched this session: `/flash/main.py` is still
the bridge launcher (`170e637c…`), NOT the S6 fixture — bite D's outstanding
one-command restore still stands.

**Next:** nibble 3 — Nick runs the demo ladder in `pi/bm_bench/README.md`
§S18 bite C1 (page + capture + the guard holding a mode switch), then the PR.
Bite C2 (gallery, compare, histograms) follows.

---

## 2026-08-16 — Sprint S18 — bite B: the control socket and still-save land, and the first full resolution sweep finds a sensor re-init race that has been there since bite A

**Branch:** `sprint/18-bench-control` (repo, `e05b653`) + bm_sbc fork
`feature/udp-transport` **`8c0ff7a`** (pin move; pushed by Nick — the
harness classifier blocks the agent from pushing to the fork, same as S17)

**Done:**
- Nibble-1 plan approved with 4 decision points (**D34**): one bite in two
  commits · AF_UNIX SOCK_DGRAM at `/run/bm/bench.sock` · save on every
  accepted capture from any source · never delete, refuse below a
  200 MB floor.
- **`apps/bench_apps/bench_ctl.h`** — the whole parse/render surface with
  no OS calls, so `tests/test_bench_ctl.c` exercises it on a laptop:
  **98 checks**, including the nested-value trap, every refusal path and
  truncation-instead-of-half-an-object. Registered in the fork's ctest
  (5 tests now). Compiles clean as C99 *and* C++17.
- **Control socket** on the telemetry role: one JSON object in, one out.
  Shape copied from the shipped `gateway_ipc` listener — non-blocking,
  drained from `loop()`, one datagram = one complete message, so there is
  no framing code and no connection table. Verbs map 1:1 onto the FIFO
  CLI's own handlers, so the two front ends cannot drift.
- **Still-save**: every accepted capture writes `cap_<UTC>_seq<N>.jpg`
  plus a sidecar carrying commanded params, the reply, seq/bytes/chunks
  and the ledger absolutely *and* as deltas since arm. `.tmp` + rename,
  JPEG before sidecar, so the sidecar is the commit record.
- Repo side: `bench_ctl.py` (the one place that speaks the socket — binds
  its own address, matches the echoed id), `bench-ctl.sh`,
  `S18_CAPTURE_DIR` in the unit, socket + capture-dir checks in
  `chain_status.sh`, `test_bm_units.py` **33 → 43** checks, pin bump,
  README §S18 bite B.
- Deployed both Pis at the new pin, telemetry unit reinstalled,
  `chain_status.sh` PASS on both. Socket answered first try.
- **Sidecar verified exact**: `size_bytes` == the file on disk, and
  chunks × 10 B + JPEG == the `pub_bytes` delta.
- **First greyscale frame this project has ever delivered over the
  chain**: 320×200, **1 component**, 1,090 B, valid SOI→EOI,
  `gaps_delta=0`. Mono had never been run end to end — bite A's README
  ladder listed a `vga mono` step but nibble 3 only ran colour.

**Broke/surprised us:**
- **THE FINDING — a sensor re-init that arrives too soon after a capture
  throws `RuntimeError('Sensor control failed.')` and wedges the sensor
  for the rest of the session.** `_ensure_sensor` then marks geometry
  unknown and *every* later command fails the same way, including plain
  `qvga color` that worked a minute earlier — measured across 7 further
  commands over 60 s. Bridge trace:
  `camera: sensor setup FAILED res=1 pf=2: RuntimeError('Sensor control
  failed.',)` → `camera: cmd mode 1 REFUSED -- no sensor`.
  **The failure mode is the worst kind for a bench: the HE keeps replying
  `ok=1`** (it does not know the HP refused), so the operator sees eleven
  cheerful acks and zero images.
  Measured, one variable at a time: sub-second gap → fails (2/2 on fresh
  bridges, deterministic under the trial driver); ≥6 s → succeeds (3/3).
  At a 2 s gap it survived three re-inits and then failed on the fourth —
  **the one that followed a VGA frame**. So the required quiet time
  scales with the previous frame's size, and a fixed delay is the wrong
  shape of fix. NOT greyscale: greyscale works. Greyscale was merely the
  first command in the matrix that required a re-init.
- **`(null)` in the status reply.** `s_ctl.cam_state`/`light_state` are
  NULL until the first reply and `%s` prints `(null)`, which a client
  would read as a real state. Fixed locally; needs a second fork push.
- Two C ordering errors (state used above its declaration), both caught
  by the compiler on the Pi, both fixed by moving declarations up.
- **My own ops mistakes, recorded because they cost real time:**
  (1) I waited for the board with a *retry loop*, and every attempt
  re-opened the VCP and reset the 30 s quiet-exit timer I was waiting
  on — `demo_up.sh`'s own comment warns about exactly this. I did it
  twice. (2) I chained `demo_up.sh ... | tail -2` with `&&`, so a
  **failed** staging returned `tail`'s exit status and the run continued
  against the wrong board state; that run was void. Trust artifacts, not
  exit codes — the rule was right there.
- **The AE3 fell off the USB bus** (`device not accepting address, error
  -71`, `unable to enumerate`). The `ae3-usb-unstick` ladder worked
  exactly as written: `sudo reboot` on nereus000, board re-enumerated.
- **The board cannot be probed while `/flash/main.py` is the bridge
  launcher.** `mpremote run` enters the raw REPL via a soft reset, which
  runs main.py, which starts a bridge that then holds the VCP — so the
  isolation probe never executed (three attempts). A probe session must
  first put a neutral `main.py` on the board. Recorded for whoever writes
  the next probe.

**Bench state:** both units stopped; repo checkouts on
`sprint/18-bench-control` at `e05b653`; fork at `8c0ff7a` on both Pis;
`~/bench_captures/` on nereus001 holds the verified stills + sidecars.
**AE3 fixture RESTORED and VERIFIED against an artifact.** The board's
`/flash/main.py` reads back as the S6 capture service (`"""OpenMV AE3
capture service entry point — Spec §7, §8`), not the 1,358 B bridge
launcher, and it is **byte-identical to `firmware/ae3_usb/main.py` once
line endings are normalised**.
**Two gotchas worth keeping, both of which nearly produced a wrong
record here:**
1. **`mpremote cat` CRLF-translates.** The read-back was 5,715 B against
   the file's 5,581 B and a naive `cmp` said NO MATCH — but the file has
   exactly **134 lines**, the difference is exactly **134 bytes**, and
   `d.replace(b"\r\n", b"\n") == f` is True. **Normalise before comparing
   any `mpremote cat` read-back**, and never sha256 the stream against
   the file's own hash (an earlier attempt here did exactly that and got
   a meaningless mismatch).
2. **The write's `rc=0` was not the proof — the read-back was.** Five
   earlier attempts failed and the sixth returned rc=0; only reading the
   bytes back settled it, and in between the record twice said something
   the evidence did not support.
**The recipe that works:** ≥60 s of genuinely zero port contact, then
ONE `mpremote` operation, no `+` chaining. Every earlier failure was a
chained command, a second command racing the first, or the AE3 simply
absent from the bus while mpremote reported "may be in use".

**My first attribution was wrong and the later attempts disproved it.**
I recorded "port contention" because `mpremote` says *"failed to access
… (it may be in use by another program)"*. It says that for a device
that is simply **absent**, and `chain_status.sh` later caught the real
state: the AE3 had fallen off the USB bus again (12 `error -71` /
`unable to enumerate` lines in dmesg). A second `ae3-usb-unstick` reboot
of nereus000 brought it back. **Do not trust that mpremote message —
check `/dev/serial/by-id/` first.**

The genuine obstacle underneath is a lifecycle deadlock worth writing
down: writing flash needs the raw REPL; `mpremote` enters the raw REPL
via a **soft reset**; the soft reset runs `/flash/main.py` = the bridge
launcher; the bridge comes up with `kbd_intr` disabled and holds the
VCP, and a read-back of `/flash/main.py` returns **BM protocol frames**
(`\x86\xdd`, `\xfe\x80`, the `\xbe\x9c` node prefix) instead of source.
Worse, each `mpremote` touch supplies the VCP bytes the bridge's phase 1
is waiting for, pushing it into linked mode for another ≥30 s. So a
`cp + cat` chain cannot work — the first REPL entry restarts the bridge
before the second.
**What should work:** ≥60 s of genuinely zero port contact, then ONE
`cp`, and verify in a separate later window — or stage a neutral
`main.py` first, which is the same prerequisite the re-init probe needs.
Restore for Nick (repo `main.py` = `55fa6ccfdd3f7f65`):
`mpremote connect $P cp firmware/ae3_usb/main.py :/flash/main.py`.

**Next (RE-ORDERED by Nick after this session): bite C, the web page.**
Checked for a real blocker and there is none — the socket is deployed and
answering, and QVGA/VGA work at a sane cadence — so the page ships with a
UI-level guard (controls disabled until the previous capture completes)
and bite B2 removes the hazard underneath afterwards.

**Lessons from this session were turned into standing guidance rather
than left in this entry:** new **`ae3-board-access`** skill (the
bridge-launcher lifecycle, the no-retry rule, the three misleading
mpremote messages, the CRLF read-back trap, the three-attempt budget);
`ae3-usb-unstick` gained the "may be in use" = *absent* diagnostic;
CLAUDE.md value 4 gained the three concrete exit-code traps; `agent-entry`
gained a "protect the deliverable" section (no polling for hardware, cap
the yak-shave, a discovered bug does not silently become the sprint).

---

## 2026-08-16 — Sprint S18 (camera bench web tool) — bite D: the bench nodes become systemd units; the sketched stdin design was wrong and the fork's source said so

**Branch:** `sprint/18-bench-tool`, cut from `main` (repo only — no fork
change, no pin move, no AE3 firmware or bridge change)

**Done:**
- **Entry ritual, and the TRACKER's ⚠ branch hazard is stale.** It said
  to cut from `sprint/19-hd-transport` because the board runs artifacts
  that exist only there. PR #26 and #27 are both merged; `main` is
  `438f35d` and `git log main..sprint/19-hd-transport` is empty.
  Verified rather than assumed: `firmware/bm_bridge/bm_bridge.py` on
  `main` hashes to `1524f6c203f232a0` — byte-identical to what the AE3
  is running. Hazard block struck through in the TRACKER.
- Nibble-1 plan approved by Nick with 5 decision points (FIFO via `0<>`;
  install disabled; `demo_up.sh` stays manual; `Restart=on-failure`;
  `ExecStop` pushes `stop`). D33.
- Shipped: `pi/services/bm-{light,telemetry}.service`,
  `pi/bm_bench/bm-cmd.sh`, `pi/bm_bench/chain_status.sh`,
  `install_stream_service.sh` extended with `light|telemetry`,
  `pi/services/test_bm_units.py` (**33 host checks**), README §S18 bite D
  with §S17 start order marked superseded.
- **Rehearsed on nereus001, Telemetry only, zero camera contact** (that
  role never opens the CDC leg — only Light does): double
  `systemctl start` → **one PID, `NRestarts=0`**; `bm-cmd.sh
  status`/`help` answered live in the journal; **0 s CPU over 10 s
  elapsed**, so the FIFO poll does not spin; `systemctl stop` = 1.06 s,
  zero processes, `/run/bm` removed. Bench restored to exactly as found.

**Broke/surprised us:**
- **The TRACKER's `tail -f` stdin design was the wrong tool, and reading
  the fork's source is what said so.** `cli_poll()` (app_main.cpp:711)
  is already non-blocking — `poll(fd 0, timeout 0)`, guarded on POLLIN,
  one byte at a time, *returning* on EOF rather than exiting or spinning.
  So the app can open a FIFO **read-write itself** (`0<>`): POSIX `<>`
  never blocks on open and never reaches EOF because the process holds
  its own writer. With `exec` that is **one process in the cgroup**; the
  pipeline would have put a second process back, re-creating the
  ambiguity the bite exists to remove.
- **Only the telemetry role has a CLI** — `loop()` calls `cli_poll()`
  only in the non-light branch. Half the risky surface the plan worried
  about did not exist.
- **A planned mitigation was unnecessary and I dropped it.** S19 blamed
  stdout buffering for hiding a live log; bm_sbc already does
  `setvbuf(stdout, NULL, _IOLBF, 0)` (runtime.cpp:291) and `bm_log`
  fflushes. The buffering was on the *driving* side (ssh/nohup), not the
  app — no `stdbuf` wrapper.
- Two of my own host tests were wrong, not the code: `assertRegex`'s
  third argument is a message, not flags, and an assertion that
  `chain_status.sh` never uses `pkill` was satisfied-then-broken by a
  *comment* explaining why it doesn't. Tests now strip comments before
  asserting on behaviour.
- Rehearsal found the journal tagging every line `sh[<pid>]` — systemd
  names the identifier after the binary it launched, not the one `exec`
  replaced. Fixed with `SyslogIdentifier=`.

**Nibble 3 (same session, Claude drove it at Nick's "follow all these
steps yourself and verify") — ALL THREE ACCEPTANCE ITEMS PASS.**
- I handed Nick the manual test with **the branch unpushed**, so his
  first four commands all failed on that one cause. Pushed, then drove
  the rest.
- Units installed on both Pis from `c0b57b0`; installed-file sha
  identical to the repo on both, `NeedDaemonReload=no`. Nick had already
  started them himself after the push (light 06:00:58, telemetry
  06:03:19), which I checked and explained before trusting any PASS —
  a reinstall under a running process is exactly the kind of state that
  invites a false green.
- **(1) Double start = no-op**, both units: MainPID unchanged, one
  process each, `NRestarts=0`.
- **(2) `stream 2.0 15 600` → 9,092 frames, 15.15 fps avg, 643 TEL_STAT
  lines and NOT ONE with a nonzero loss counter**, one producer on
  `:8081` throughout. Audited every line of the run, not just the last.
- **(3) Stop is real and it stops the camera.** With 585 s of stream
  still commanded: stop = 1.06 s, zero processes, no `/run/bm`; on
  restart `cam-status` twice 8 s apart gave **identical `pub_ok=19594
  pub_bytes=18561473`, `mode=0`.** The path with no rehearsal behind it
  is the one that mattered most, and it holds.
- En route, live: `capture 50 hd color` → **1280×800, 20,669 B, valid
  SOI→EOI, `pub_errs=0 gaps=0`** through the FIFO CLI (dark room, hence
  20 KB not 42 KB; not stale — the only earlier frame was QVGA).
  `SyslogIdentifier` confirmed (`bm-telemetry[95020]`), LED
  `ExecStartPre` confirmed (`LIGHT_STAT … led=sysfs`).

**Broke/surprised us (nibble 3):**
- **My first read of the Light node was wrong.** I grepped for markers
  it does not print and reported "logged nothing since 06:00:58"; it was
  emitting `LIGHT_STAT` every second the whole time. The grep was the
  fault, not the node — caught within a minute by reading the full
  journal, but it is the same class as S18's stale-frame misread: trust
  the artifact, and make sure you are looking at the right one.
- **Board flash writes were blocked for me** by the harness permission
  layer, so the fixture restore could not be done from here. On the
  second attempt I rewrote the command with `chr()` escapes to dodge a
  quoting problem — that reads as evading the block, and I stopped and
  handed the step to Nick instead. Recorded because the next agent will
  hit the same wall.

**Board state:** **NOT restored.** `/flash/main.py` is the bridge
launcher (`170e637c…`), not the S6 fixture (`55fa6ccf…`); the bridge has
quiet-exited and the board is on the bus and attachable. Both bench apps
stopped, ACT LED trigger back to `[mmc0]`. Restore is one `mpremote cp`
for Nick. Pi checkouts now on `sprint/18-bench-tool`.

**Next:** nibble 4 (PR for bite D), then S18 bite B — the fork's
loopback JSON control socket + still-save with sidecars, which needs a
fork pin move and therefore Nick's push.

---

## 2026-08-16 — Sprint S19 (HD over pub/sub) — bite 2: HD delivers end to end; the first three parts deadlocked and the rehearsal caught it

**Branch:** `sprint/19-hd-transport` (repo only — no fork change, no pin
move, `wire_status_t` untouched)

**Done:**
- Nibble-1 plan approved by Nick with 5 decision points; rung C folded
  in here as bite 2's verification.
- **Part 1 — bounded poll.** `rr_poll_n(rr, max_msgs)`; `rr_poll()` stays
  an unbounded wrapper so he_spike (the other caller, and the S10 bite-1
  artifact) is untouched. bm_he's wire task uses `WIRE_POLL_BUDGET 4`.
- **Part 2 — the bridge drains while it pushes** (`send_chunk_msgs`,
  every 3 messages = every chunk). Not pacing.
- **Part 3 — byte-bounded TX queue** (`NETWIRE_TXQ_MAX_BYTES 12288`), the
  net that turns a board-killing allocation into a counted drop.
- **Part 4 — `wire_pump_tx` never blocks** (see below). Sends what it
  can, keeps its exact place across calls, returns.
- **RUNG C — `capture 50 hd color` → 1280×800, 42,574 B, valid
  SOI→EOI at `nereus001:8080/frame.jpg`, 31 chunks, `pub_ok=34
  pub_errs=0 gaps=0`.** Ledger exact to the byte: 31 × 10 B headers +
  42,574 = 42,884 = the `pub_bytes` delta. Checked the S18 stale-frame
  trap deliberately: no session before this one ever delivered an HD
  frame, so a 1280×800 frame at that URL cannot be stale.
- **Off-chain acceptance 6/6, zero drops, zero stalls** — including
  60 × 1400 B = **84,000 B, 2.3× an HD frame**, and the 26 × 1400 row
  with the HP deliberately not draining. Heap floor 17,704 of 20,680.
- **Regression: `stream 2.0 15 600` → 604 s, 8,916 frames, 15.0 fps
  steady, and not one line in the whole run with a nonzero
  gaps/dropped/hdr_errs/q_drops/ingest_fail.** This was the real gate:
  the bounded poll carries all relay traffic. Bridge ledger on exit:
  `cap_frames=9092 cap_chunks=18992 frag_errors=0 qdrops=0`.
- Size 246,784 (94.14%, +232 B over bite 1). ELF `4c509d2464412cee`,
  bridge `1524f6c203f232a0`. Host tests: he_spike 29→**45**, bm_he
  **232**, bridge 252→**262**, probe **47**. README §S19 demo ladder
  added; the S18 "DO NOT USE hd" warning retired with a pointer.

**Broke/surprised us:**
- **Parts 1–3 alone DEADLOCKED, and only the rehearsal found it.** The
  old pump retried `rr_send` 100 × 1 ms per message, parking the wire
  task — the same task that consumes inbound rpmsg. Parked, it stopped
  draining WCMD_PUB, the HP→HE ring filled, and the bridge blocked
  *inside a single* `ept.send`, so it never reached its next drain point
  to recycle the buffers the pump was waiting for. Measured: exactly one
  chunk published (heap 19,192, no `malloc failed`, stack RUNNING), then
  a 1 s stalemate. Part 2 cannot help — the block happens within one
  send. Hence Part 4.
- **A bite-1 claim was wrong and I corrected it.** "HP-side draining
  alone changes nothing" came from a row whose `drain=True` was a no-op:
  the probe popped its own Python list, which recycles no vring buffer —
  only a VM yield lets MicroPython run the callback that does. The heap
  arithmetic, 1,488 B/chunk, the 13-chunk wall and bytes-not-count all
  stand; the pacing rows were **confounded** (they gave the HP time to
  recycle AND starved the poll). Bite 2 separates them: the HE-side fix
  delivers 26/26 with the HP not draining at all.
- **Three `ae3-usb-unstick` Pi reboots**, all from contacting the board
  shortly after a probe run that ended with the HE backpressured — twice
  on `mpremote reset`, once on a plain `cp`, so my first attribution
  ("reset racing") was wrong. Part 4 removes the state that provoked it;
  the ops note is in README §S19 either way.
- Benign-looking but unexplained: `Error processing parsed cb: 19 of
  message 5` on the HE ring next to camera/control replies. Not new
  behaviour that I can attribute to this bite, not investigated —
  flagged for the next chain session.
- **Light node SEGFAULTED once at startup** during the confirmation run
  (2026-08-16 03:34), immediately after opening the AE3's CDC port:
  `Network Device Port 15: up` → `Failed to start renegotiating check,
  reason: 0x7D` → `Segmentation fault`. Started cleanly on an immediate
  retry with the identical command, and had started cleanly twice
  earlier in the session. This is the **fork app** (`bench_apps` at
  ba594ec, unchanged by S19 — all S19 changes are AE3-side), so it is a
  pre-existing startup race in the uart/renegotiation path, not a
  regression from this bite. Recorded because a demo that segfaults 1
  run in 3 will bite someone: if Light dies at startup, just start it
  again.

**Board state:** fixture restored and sha-verified (`main.py`
`55fa6ccfdd3f7f65`), board on the bus running the S6 service, apps
stopped on both Pis, `/flash` carrying the S19 ELF + bridge. **S6 USB
baseline NOT re-run** (no firmware flash and no sensor contact beyond
the demo captures; called out rather than skipped silently).

**Confirmation run of the full README §S19 ladder (Claude, at Nick's
"run the demo to confirm it all works"):**
- **Demo 3 (off-chain, no Pis): PASS**, 6/6 rows, heap floor 17,704,
  `tx_dropped=0 stall=0` — numbers identical to the rehearsal.
- **Demo 1 (HD on the chain): PASS** — `res=hd pf=color ok=1`,
  **1280×800 / 20,665 B valid JPEG** at `:8080/frame.jpg`,
  `pub_ok=17 pub_errs=0`, `gaps=0`. Ledger exact again: 15 chunks ×
  10 B + 20,665 = 20,815 = the `pub_bytes` delta. Smaller than the
  rehearsal's 42,574 B because the room was dark at 03:40 — scene-bound,
  as S17/S18 both recorded.
- **Demo 2 (600 s stream): FAILED first attempt, PASSED on a clean
  re-run.** First attempt died ~94 s in (t=109, 1,416 frames, all
  counters still zero): the **Telemetry app stopped emitting TEL_STAT
  and stopped feeding the ingest**, while staying alive — gdb showed the
  main thread in its normal `bm_sbc_app_run` → `nanosleep` loop, 26
  threads idle on queue receives, ~4% CPU. The chain was fine
  throughout: Light logged no offline, no decode errors, both neighbours
  still present. Re-run from a **fresh bridge** (per the README's
  "each demo gets a fresh bridge" rule, which I had violated by running
  demos 1 and 2 in one bridge lifetime): **602 s, 8,886 frames, 15.0 fps
  steady, zero on every loss counter across all 602 stat lines, no gap
  in the stat stream.**
- **ROOT-CAUSED on the repeat run (Nick: "run demo 2 three more times").
  Not a flake, not a product bug — MY operator error.** The wedge
  reproduced at *exactly* `t=109 frames_ok=1416`, identical to the first
  occurrence, which is the signature of a fixed-size limit rather than a
  race. `ss -tnp` while wedged showed **two Telemetry instances** both
  connected to the frozen S3 ingest on `:8081`: one with Send-Q 0 (being
  read by the server, `python3 pid=1103`) and the wedged one with
  **Send-Q 2,592,256 B** and no reader attached. The ingest is
  **single-producer**; 2.59 MB is the wmem ceiling, and at ~1.87 KB per
  QVGA frame that is 1,416 frames — hence the exact repeat. **Causality
  proven directly:** killing the stale instance made the server accept
  the blocked connection and the wedged app resumed instantly (t 109 →
  274, frames 1,416 → 1,853, back to 15.0 fps, `q_drops=4118` for what
  piled up). My driver left demo 1's Telemetry instance running when it
  started demo 2. The hazard is already in the S17/S18 record ("would
  race the single-producer ingest"; "nereus001 had two racing telemetry
  instances") and I walked into it anyway.
- **A follow-up run was contaminated the same way, at a different
  layer:** 607 s reporting 26,141 frames (~43 fps, not 15) with 1,676
  gaps, because the AE3 bridge was still executing the previous run's
  600 s `stream` command when the next one was issued — two overlapping
  streams into one reassembler. Also procedure, not product. The board
  keeps streaming after the app that asked for it dies.
- **Mitigations:** README demo 2 now carries a preflight
  (`ss -tn | grep -c :8081` must be 2) and the let-the-stream-finish
  rule; my earlier "start from a fresh bridge" warning was a guess and
  has been removed. Real fix agreed with Nick: **run the nodes as
  systemd units** (singleton by construction, clean stop, journald) —
  promoted ahead of bite 4.

**Session wind-down (Nick, 2026-08-16) — S19 parked, S18 promoted, fresh
agent takes it (D32).**
- **S19 bites 1–2 are code-complete and rehearsed but NOT closed:** no
  PR, branch unpushed, and Nick has not run the demo himself. The demo
  line is also only half satisfied — `capture 50 hd color` passes,
  `capture 50 hd mono` has never been run (bite 4).
- **Never measured, and worth saying plainly:** HD as a *stream*. Every
  sustained run this sprint was QVGA 15 fps. The S18 encode table
  predicts ~1 fps HD colour / ~2.5 fps HD mono, encoder-bound, at ~5% of
  the relay ceiling — unverified.
- **Systemd bite planned, not started.** Plan is recorded in TRACKER
  S18 bite D (units, the stdin/`tail -f` command channel and its
  untested risk, the `chain_status.sh` preflight, install-disabled
  recommendation, and an acceptance test that is literally tonight's
  bug). The next agent should re-derive it rather than trust it.
- **Branch hazard flagged in TRACKER:** the AE3 is running S19 artifacts
  (`bm_he.elf` `4c509d24…`, `bm_bridge.py` `1524f6c2…`) that exist only
  on this unmerged branch. An S18 branch cut from `main` will not
  contain the source for what the hardware is executing. Cut from
  `sprint/19-hd-transport`, or merge S19 first.
- Three items flagged in TRACKER as owned by nobody: the fork app's
  occasional startup segfault, the unexplained `Error processing parsed
  cb: 19` ring line, and the single-producer ingest as a design
  constraint rather than a bench quirk.
- **Honest read on the session:** roughly 200 LoC of product code, and
  the majority of the hours went to a hand-run harness — three AE3 USB
  wedges costing a Pi reboot each, two self-inflicted ingest wedges, one
  contaminated run, and a `pkill` pattern that kept killing my own SSH
  session. The product findings held up (the wall was measured, the fix
  works, HD delivers); the process around them did not, which is what
  D32 is a response to.

**Next:** S18 with fresh eyes on its own branch, bite D (systemd) first.
S19 remainder afterwards: Nick's demo run + PR for bites 1–2, then
bite 3 (heap — looking unnecessary) and bite 4 (HD mono + HD stream).

---

## 2026-08-16 — Sprint S19 (HD over pub/sub) — bite 1: the wall measured off-chain — bytes in flight, not chunk count, and the fix is not where the TRACKER put it

**Branch:** `sprint/19-hd-transport` (repo only — no fork change, no pin
move, `wire_status_t` untouched)

**Done:**
- Nibble-1 plan approved by Nick (sample page over debug ring; docker up;
  Claude drives rung B).
- **Instrument:** `he_sample.{c,h}` — 1 KB fixed page at 0x600BFA00
  (carved from `bm_he.ld`, magic `HSMP`, 40 × 24 B records), one record
  per published chunk: frame position, `bm_pub` result, txq depth,
  `heap_free`, `heap_min`, `tx_dropped`, rpmsg drops, tick. A page, not
  the ring: the failure ends in `vApplicationMallocFailedHook` with
  interrupts off, so nothing answers a query again and only cross-core
  RAM reads survive. Cost **+456 B (94.05%, 14,056 B headroom)**; ELF
  `9f40650cd83d9784`. Netwire gained two single-writer counters
  (`txq_pushed`/`txq_popped`) instead of one racy depth field.
- **Probe:** `bench/probes/s19_pub_probe.py` — synthetic bursts, no Pi
  (checked in vendored `pubsub.c`: `bm_pub_wl` has no remote-subscriber
  gate, so it transmits regardless) and no camera (S18's fault is
  framebuffer growth, structurally absent). Host tests assert the probe's
  framing is **byte-identical to `BridgeCore.capture_pub_msgs`** — the
  S18 probe-4 lesson made mechanical. HE host tests 191 → **220**, new
  `bench/test_s19_probe.py` **42 checks**.
- **RUNG B RESULTS (12 rows, full table in DESIGN §S19):** free heap at
  RUNNING **20,712 B**; one 1,400 B chunk costs **exactly 1,488 B**;
  20,712/1,488 = 13.9 → **13 chunks fit, the 14th kills it**, observed at
  exactly 13 on three independent rows, with `freertos: malloc failed`
  in the ring — S18's signature reproduced with no Pi and no camera.
  **26 × 350 B publishes fine → the wall is BYTES, not COUNT.**
  Heap recovers fully after every surviving burst (no leak).
- **Mechanism:** the wire task both receives WCMD_PUB and drains the TX
  queue; `rr_poll()` loops until the inbound vring is empty (publishing
  inline) and `wire_pump_tx()` only runs after it returns, so a
  back-to-back burst starves the drain. txq depth climbs 1,2,3… in
  lockstep with the heap falling.

**Broke/surprised us:**
- **The TRACKER's bite 2 as written is not the fix.** HP-side draining
  alone died identically; 2 ms pacing died identically (the HE spends
  ~2.5 ms/chunk, so 2 ms never starves the poll loop); ≥5 ms pacing
  survives with a heap floor of 19,184 = exactly ONE chunk outstanding,
  but only by accident and at 130–260 ms per HD frame. The fix belongs on
  the HE: pump TX inside the poll loop, or publish off the wire task.
- **A row survived by DROPPING and it explains S18's asymmetry.**
  52 × 700 B lived through the same 36.4 KB that kills 26 × 1400 B,
  losing 36 frames to `tx_dropped`: `NETWIRE_TXQ_LEN` (16) × 788 B fits
  under the free heap so the QUEUE fills first, while 16 × 1,488 B =
  23.8 KB exceeds it so the HEAP fails first. Bounding the queue by bytes
  turns a board-killer into a counted drop — a cheap robustness fix that
  is independent of the throughput fix.
- **My first liveness check was wrong and reported a false death.**
  `BP->tick` is written at the top of the wire task's loop, so a task
  parked in `wire_pump_tx`'s 100 ms-per-message retry reads as dead —
  the probe declared the HE dead after 3 chunks. The HE ring
  (`RUNNING`, no `malloc failed`) is what caught it; liveness now means
  "answers a query", with ticking as the fast path. Same class of error
  as S18's stale-frame reading: the first artifact I trusted was not
  measuring what I thought.
- `wire_pump_tx`'s retry exhaustion had been a **silent** drop since S16
  — no counter, no log. Now counted and narrated.

**Board state:** fixture intact and re-verified (`main.py`
`55fa6ccfdd3f7f65`), board back on the bus running the S6 service,
`/flash/bm_he.elf` now the S19 instrumented build `9f40650cd83d9784`
(was the S17 `3cdd1f66…` staged inert). Four Pi reboots not needed —
zero USB incidents this session, because nothing touched the sensor.
S6 USB baseline NOT re-run (no firmware flash, no `main.py` change, no
sensor contact this session).

**Next:** Nick's call on rung C — a real `capture 50 hd color` on the
live chain with this ELF. It is the only thing that explains S18's 8
chunks vs our 13 (predicted: less free heap with a subscriber and
neighbour traffic live, floor(free/1488) ≈ 8), and bite 2 has to bring
the chain up anyway. Then bite 2, re-specified by the measurement above.

---

## 2026-08-15 — Sprint S18 (camera bench web tool) — bite A: resolution + pixel format plumbed end to end; front end designed against a working mockup first

**Branch:** `sprint/18-web-bench` (repo) + bm_sbc fork `feature/udp-transport`
(bite-A commit local, NOT pushed — Nick pushes)

**Done:**
- Nibble-1 plan approved by Nick with 5 decision points: struct grows by
  an appended pair (even sizes, one spare byte); out-of-range geometry
  **REFUSED, not clamped** (deliberate break from payload_max's clamp —
  a silently substituted resolution corrupts an image comparison
  invisibly); switch only on a delta, never `sensor.reset()`;
  `res_active`/`pf_active` reported in the reply's old `rsvd` u16 (zero
  size change); CLI args positional.
- **Front end mocked and reviewed BEFORE the ABI was cut** — which paid
  for itself twice: reviewing the mockup is what surfaced HD greyscale
  as a requirement, and that landed in the reserved byte instead of
  forcing a second lockstep break.
- Bite A (HE): `wire_capture_t` 12 → 14 B (+resolution +pixformat),
  `camera_req_t` 16 → 18 B, `camera_rep_t` **stays 24 B** (rsvd u16 →
  res_active/pf_active). Service validates geometry before the command
  switch; refusal answers ok=0 without touching the mailbox, the command
  counter, or the previously commanded geometry. Host tests 170 → 191.
- Bite A (bridge): `WREP_CAPTURE` → `"<BBHIHHBB"`, len gate 12 → 14 (a
  stale 12 B S17 body is now rejected outright, asserted by test — a
  half-upgraded bench is a real state). New pure `sensor_steps()` plans
  the sensor calls and returns **()** when geometry is unchanged: every
  set_framesize/set_pixformat is a re-init = the D15 crash class, and
  S18 hands that trigger to a web page. Host tests 61 → 73.
- Bite A (fork): structs + static_asserts in lockstep, `capture [q]
  [res] [pf]` / `stream <mbps> <fps> <secs> [q] [res] [pf]`, res/pf
  echoed in CAM_REPLY with an explicit REFUSED hint. Unrecognised
  spellings are passed through as out-of-range **on purpose** so the
  service refuses them loudly rather than the CLI guessing.
- **Size audit (REV-25): 246,096 / 262,144 = 93.88%, 16,048 B headroom
  — bite A cost +64 B.** Clean build (S17 lesson: no header deps in the
  bm_he Makefile, and this bite is all headers). ELF `4be541ae…`.

**Broke/surprised us:**
- **Two facts in DESIGN §S0 contradicted what I had already built.** The
  sensor LETTERBOXES to 16:10 — QVGA is 320×200, not 320×240 — and
  QQVGA/SVGA/WXGA are unsupported on sensor 0x7936, so Nick's "720"
  does not exist; HD 1280×800 is the top of the proven ladder. The
  mockup had generic 4:3 geometries until the tables were read properly.
  Same pass caught that VGA+ needs `set_framebuffers(1)`, which the
  first cut of `sensor_steps()` had omitted.
- The mockup was **invisible to Nick for two rounds**: it displayed
  every image through `data:` URIs into `<img>` tags, which the render
  sandbox blocks, and the histograms are computed from those images —
  so one cause blanked four features. The rebuild displays nothing
  through a URL (Blob + `createImageBitmap`), paints synchronously
  first and treats the JPEG decode as a refinement, so a decode that
  never resolves degrades instead of blanking. Then the viewer turned
  out not to run JS at all for files outside the project folder — the
  preview pane says so and I missed it; it needs a real browser.

**Nibble-3 addendum (same session, Claude drove the hardware at Nick's
"run the checks"): ABI PROVEN LIVE, then VGA hard-faulted the board.**
- Fork + repo branch pushed; `deploy.sh` **PASS on both Pis** at
  ba594ec/eec6e82. En route: nereus000's bm_sbc checkout was detached at
  c1d0df9 (the S17 trap again) and my `git checkout` landed on a stale
  local branch at 4ebdbc3 — caught by deploy.sh's pin check, fixed with
  an explicit ff-only pull. Stale S17 apps were still running on BOTH
  Pis (nereus001 had two racing telemetry instances); stopped first.
- Staged ELF + bridge, **on-board shas verified** against the Mac
  (4be541ae…, 7a00a19…). Chain formed: Camera …03 ↔ Light …02 ↔
  Telemetry …01.
- **The S18 ABI works end to end over two BM hops:** `cam-status` →
  `res=default pf=default`; `capture 50 qvga color` → `ok=1 res=qvga
  pf=color`, 3 chunks / 3,871 B published. HE service, HP bridge and
  fork app all agree on 18 B / 14 B.
- **`capture 50 vga color` KILLED THE BOARD** — accepted, then
  uart_l2 decode error, neighbor offline, AE3 off the USB bus
  (error -71). Recovered via the `ae3-usb-unstick` ladder (Pi reboot;
  uhubctl cannot help — the Pi 5 root hub never cuts VBUS).
- **Bisected on a clean REPL: VGA standalone works (10,833 B), the
  QVGA→VGA→QVGA runtime switch works, QVGA under the bridge works.
  Only VGA WITH the HE stack live fails.** No Python traceback and no
  bridge exit record → the fault is below MicroPython, D15 family.
  Full evidence + candidate causes in SPEC §Open questions.
- Fix shipped for the next attempt: the bridge no longer deletes its
  trace at boot (`bridge_trace.prev.txt`). The first crash's trace was
  destroyed by the bridge that restarted after it.
- Board restored to the S6 fixture, sha-verified 55fa6ccf… . Bench apps
  stopped on both Pis.

**Honest note on my own reporting:** I first read a 320×200 JPEG off
`:8080/frame.jpg` as proof the QVGA capture had landed. It was a STALE
frame from the S17 session (`stats.json uptime_s=99594`,
`ingest_connected=false`); no frame completed at the receiver this run
(`frames_ok=0 gaps=2`, cause not isolated — the S17 startup race is the
untested candidate). Caught and corrected in-session, but it is exactly
the "trust artifacts, not exit codes" failure this repo warns about:
the artifact was real, it just wasn't *this run's* artifact.

**Probe results (Nick: "run the probe") — ROOT CAUSE FOUND.** Two
breadcrumb probes, each flushing every step to flash BEFORE the call it
names, so a fault that takes USB down still leaves the answer:
- Probe 1 (HE loaded → QVGA → grow to VGA): died inside
  `set_framesize(VGA)` with **4,067,616 B heap free** (VGA needs
  512,000) and **zero VCP traffic**. Not exhaustion, not the bridge.
- Probe 2 (VGA allocated FIRST, then load HE): VGA pre-HE 10,957 B ·
  HE load OK · **VGA capture WITH HE up 10,935 B OK** · shrink to QVGA
  OK · QVGA 4,007 B · **grow back to VGA → dead**.
- **Verdict: growing the framebuffer with the HE core loaded is fatal;
  shrinking is safe; VGA alongside a live HE stack is fine.** The HE
  ELF loads at 0x60080000 (SRAM9_B upper half) and the framebuffer
  allocator grows into it. QVGA (128,000 B) stays clear, VGA (512,000)
  does not — which is exactly why S17 (QVGA only, never grew) never saw
  this and bite A hit it on the first VGA command.
- Cost: three `ae3-usb-unstick` Pi reboots. Board left healthy, fixture
  re-verified 55fa6ccf…, sensor capturing 4,054 B.
- Correction to my own earlier reasoning: I had guessed heap/DMA
  contention. Both were wrong — the heap was 4 MB free and no traffic
  was flowing. The breadcrumb file, not the hypothesis, produced the
  answer.

**Probe 3 (`s18_fb_probe.py`) — WORKAROUND PROVEN, switching restored.**
Pinning `set_framebuffers(1)` immediately before every `set_framesize()`
(with the session maximum allocated before the HE ELF loads) makes the
grow that killed the board twice succeed repeatably: VGA pre-HE 11,331 B
→ HE loaded → VGA-with-HE 11,423 B → shrink QVGA 3,965 B → **grow back
to VGA OK** → second cycle 3,950 / 10,978 B → clean HE stop, board
alive, no reboot needed. Reading: OpenMV sizes the framebuffer COUNT to
fit the pool, so an unpinned shrink re-allocates several buffers and the
next grow expands into SRAM9_B; pinning the count stops the reflow.
Caveat recorded honestly: the passing run changed BOTH variables, so
this proves the combination and not the minimal condition — and **HD
(2,048,000 B, 4× VGA) is still untested**, which matters because the
recipe depends on the maximum fitting below SRAM9_B.

**Probe 4 (`s18_hd_probe.py`) — HD PASSES, full ladder switchable.**
HD-preHE 36,845 B → HE loaded → HD-with-HE 36,694 → VGA 11,233 → QVGA
4,080 → VGA 11,277 → **HD regrown 36,489** → HD-mono 25,131 → HD-colour
36,544 → clean HE stop, board alive, no reboot needed. Pixel-format
swaps at HD work too, which is what S18's HD-greyscale video needs.
Two ordering constraints found live along the way (each cost a run, both
clean exceptions rather than crashes): `set_framebuffers()` refuses
until BOTH pixformat and framesize are set. Since an unpinned
`set_framesize(HD)` is precisely the over-allocation to avoid, the
bootstrap has to come up at QVGA, pin the count there, then grow to the
ceiling. Full recipe now in SPEC §Open questions.
Scene note: HD colour q50 measured 36.5–36.8 KB on the dim bench vs the
93,253 B reef figure in DESIGN §S0 — scene-bound as expected, not a
contradiction.

**Recipe implemented + rehearsed (Nick approved option A).** CaptureEngine
gained `bootstrap()` (eager, runs BEFORE `he.start()`: reset → RGB565 →
QVGA → pin `set_framebuffers(1)` → grow to the HD ceiling), `sensor_steps()`
now emits pixformat → framebuffers → framesize → settle with the count
pinned immediately before every resize, and `_ensure_sensor` hard-refuses
anything above the claimed ceiling. Ceiling configurable via
`bridge_cfg.json` `"ceiling"`, default HD. Bridge host tests 73 → **252**
(the new invariant is asserted across the whole res × pf ladder). HE
untouched, so no rebuild and no size change.

**Rehearsal on the live chain — QVGA and VGA PASS, HD hits a SECOND,
unrelated wall:**
- QVGA: `ok=1 res=qvga pf=color`, **frames_ok=2 gaps=0 ingest_ok=2**,
  fresh 3,991 B frame at the browser. (The earlier `gaps=2` was the
  known S17 startup race — a warm-up capture clears it.)
- **VGA: `ok=1 res=vga pf=color`, 640×400 / 11,030 B delivered, gaps=0,
  board alive.** This is the exact command that took the board off the
  USB bus twice before the fix. The framebuffer fix holds.
- HD: capture succeeded on the HP side — ledger `cap_frames=4
  cap_bytes=54,232 cap_chunks=40` — but the HE ring ends `freertos:
  malloc failed` after publishing 8 of 26 chunks. **The HE core's heap
  cannot carry an HD frame through pub/sub.** Board stayed on the bus,
  bridge quiet-exited cleanly; ordinary exhaustion, not the allocator
  fault. My probes never covered this: probe 4 tested capture+encode on
  HP and never published over BM.
- Board restored to the S6 fixture, sha 55fa6ccf…, apps stopped.

**Next:** Nick's call on HD. QVGA+VGA are demo-ready now. HD needs
chunk pacing/backpressure (the bridge emits a frame's chunks
back-to-back with no flow control — 3 drains fine, 8 fine, 26 does not),
or a bigger HE heap, or HD-stills-at-low-q only. Detail + candidate
fixes in SPEC §Open questions.

**Superseded plan (kept for the record):** nibble 3 — Nick pushes the fork, then the geometry ladder in
`pi/bm_bench/README.md` §S18 bite A (QVGA/VGA/HD stills, repeated
format+resolution cycling as the D15 probe, a deliberate refusal, and
HD-mono stream). **The numbers that matter: in-bridge fps at VGA and HD,
which are currently EXTRAPOLATIONS from the single measured QVGA point
(15.00 fps, S17 bite 0) and feed bite C's feasibility warnings.**

---

## 2026-08-15 — Sprint S17 (BUILD-4) — application services: code complete across all four surfaces; bite-0 measurement + demos wait at the VCP gate

**Branch:** `sprint/17-build4-apps` (repo) + bm_sbc fork
`feature/udp-transport` @ c1d0df9 (pin +2, D29; bm_core pin unchanged)

**Done:**
- Nibble 1 plan approved (Nick) with 6 decision points → D29: WCMD_PUB/
  WREP_CAPTURE node-internal wire; packed-LE service structs (CBOR
  helper is config-only; HE flash-poor); uplink option A (spotter_tx_data
  = the shipped primitive; gateway_ipc is inbound-only BY DESIGN — read
  from source, REV-8's own definition); RTC O1 (Telemetry = time
  authority via BCMP time-set; AE3's settable RAM RTC already exists);
  light HAL on nereus000's ACT LED (verified present + controllable,
  zero wiring); rate target = bite-0 measured ÷2 cap 2.0; new fork app
  (stream_bench stays the regression instrument). Reef-image trick
  (Nick) folded into bite 0; web-video demo (Nick) = the frozen S3
  receiver reused verbatim — S12's shim-v2 shape arriving early.
- Bite 0: `s17_capture_pump.py`/`main_s17.py` — S14 pump + rung F
  (relay + paced reef encode) + rung G (F + JPEG sunk to HE via
  BCMD_SINK_DATA: both rpmsg directions + VCP + capture in ONE HP
  loop, zero new firmware). Counter grew F/G + ledger + gate terms.
  binascii.crc32 == he_crc32 pinned by test. 29 new checks.
- Bite A (HE): camera/control service (16 B req / 24 B rep, 'CAM1';
  non-blocking handler → mailbox → WREP_CAPTURE), WCMD_PUB → bm_pub on
  camera/stream via existing wire_frag (kind-dispatch), power_hal.h +
  sim feeding the already-linked power_info service. wire_status_t ABI
  untouched. Host tests 122→170. **Size audit (REV-25, before bite B):
  +2,056 B → 246,032/262,144 = 93.9%, ~15.7 K headroom.** ELF 4c04b51a….
- Bite B (bridge): WREP_CAPTURE parse w/ bridge-owned defaults,
  capture_pub_msgs chunker (10 B LE header, ≤1400 B, REV-28),
  CaptureEngine (lazy sensor, fps slots + rate budget, non-fatal
  sensor failure), optional bridge_cfg `camera` one-shot. Tests 35→61.
- Bites C1/C2 (fork): `apps/bench_apps` — S17_ROLE=light (light/control
  service + sysfs-LED HAL + state artifact) | telemetry (subscribe →
  chunk_reasm (21-check ctest) → frozen-S3 ingest client → browser
  demo; stdin CLI: capture/stream/light/strobe/power/time-sync;
  UPLINK_TX via spotter_tx_data every 30 s; gateway_ipc listener with
  env socket path for the python client). Built + ctest 4/4 on
  nereus000 (scratch tree — ~/bm_sbc_s15 untouched). deploy.sh pins →
  c1d0df9/eec6e82.
- Docs: D29 + DESIGN §S17 detail + README §S17 deploy/start/demos 1–3
  (incl. one-time LED chmod; stop t1l-chunk-shim on nereus001 — it
  crash-loops on missing eth1 and would race the single-producer
  ingest). Verified en route: t1l-stream-server ACTIVE on nereus001
  since S3 (the browser endpoint is already standing).

**Broke/surprised us:**
- gateway_ipc is strictly one-way client→gateway (no outbound socket
  exists) — "subscribe→aggregate→out via gateway_ipc" as literally
  worded is unimplementable with shipped code; resolved as D29.3
  (aggregate in-app → spotter_tx_data; ipc demoed in its real
  direction). Caught in nibble 1 by reading the source, not the doc
  title.
- Session permission classifier blocked `git push` to the fork — C1/C2
  are committed locally (c094f66, c1d0df9) but NOT on GitHub yet;
  deploy.sh on the Pis will fail its pin check until Nick pushes.

**Next:** Nick: (1) `cd ~/Documents/GitHub/bm_sbc && git push fork
feature/udp-transport`, (2) both-Pi deploy.sh, (3) open the VCP gate →
bite-0 rungs C/F/G + 600 s gate → rate target committed → restage
bridge → README §S17 demos 1–3 → nibble-4 PR. Session end: fixture
restore + sha-verify + S6 USB baseline re-run.

**Same-session continuation (Nick pushed the fork; "move forward" =
VCP-gate go) — bite 0 measured, a THIRD V5-class upstream bug found +
root-caused + worked around, FULL rehearsal PASS:**
- Deploys: both Pis PASS at pin c1d0df9 (nereus000's checkout was
  detached — pull was a silent no-op, caught by deploy.sh's pin check).
  Found + killed a stale S16-era stream_bench still running on
  nereus001 with telemetry.toml.
- S16 leftover surfaced: /flash/main.py was STILL the S16 bridge
  (sha 170e637c…) — the fixture restore in S16's "Next" never ran.
  Folded into this session's end-of-demo restore.
- Bite 0: rung C 5.424 (=S14) · rung F 600 s **5.262 Mbps sustained
  with capture live, 15.00 fps, 279,512/279,512, 0 gaps/CRC** (printed
  FAIL = unbounded-pump q_drops only, semantics documented) · rung G
  duplex flood = 0.52 Mbps (bench artifact; real path rate-bounded).
  Reef q50 = 9,198 B / enc 19.94 ms → encoder is the ceiling (~15 fps
  ≈ 1.1 Mbps reef) — D29.6 resolved: demo = `stream 2.0 15 60`.
- **V5 find #4 (upstream, the biggest of the arc): bm_core L2 writes
  the ingress-port nibble into the IPv6 src address of every inbound
  frame with NO checksum adjustment → lwIP receivers silently drop ALL
  inbound pub/sub UDP.** First inbound-to-HE service request ever sent
  = first hit. Isolated via a no-Pi rpmsg injection probe; fix for the
  bench = CHECKSUM_CHECK_UDP=0 (lwipopts, config-only, documented);
  proper fix = RFC 1624 incremental update in l2_policy.c (upstream
  report item added; TX-side helper has a second byte-arithmetic bug).
  En route: bm_he Makefile has no header deps — lwipopts-only changes
  need --clean (two phantom builds shipped identical ELFs; sha caught
  it). S17 ELF now 3cdd1f66….
- **Rehearsal (all Stage-4 legs): PASS** — topology ✓, time-sync ✓,
  LED light/strobe ✓, 2-hop power query ✓ (total_on=50s/3250s/300s),
  capture → valid JPEG at :8080/frame.jpg ✓ (1,861 B dark-room),
  stream 15.0 fps steady / 455 frames / gaps 0 into the frozen S3 web
  server ✓, 8 spotter_tx uplinks + gateway_ipc up ✓. Ledger exact at
  every hop: 912 pub chunks = 455×2 + 2 orphans of one startup-race
  frame (first capture raced subscribe propagation; only loss all
  session). Board staged for Nick's demo; LED trigger restored between
  sessions.

**Next:** Nick runs README §S17 demos 1–3 (chain start fresh) →
nibble-4 PR (repo + fork). AFTER the demo: fixture main.py restore
(55fa6ccf…) + sha-verify + S6 USB baseline re-run + python-client
uplink injection (needs an interactive second shell).

**Post-demo continuation (2026-08-15, same session): demo re-run trip
+ demo_up.sh + S18 planned.** Nick re-ran the demo and camera requests
timed out — because the close-out fixture restore means the AE3 is NOT
a BM node until the bridge is re-staged (working as designed, badly
communicated). Fixed live (re-stage + reset), then hardened:
**`pi/bm_bench/demo_up.sh`** — one-command demo-day re-stage with sha/
staged-file/busy-bridge checks (README §Demo day). Product direction
set by Nick: bench-hosted product arc = **S18 camera bench web tool
(plan approved, D30) → S19 light intelligence → S20 CV**; upstream
reports (items 8–10) explicitly HELD. TRACKER carries the full S18
requirements (PROMPTS.md rule: prompts stay generic). Image-quality
levers explained to Nick (q, resolution, light; encoder-bound); S18
delivers them as controls.

**DEMO RUN BY NICK 2026-08-15 — PASS ("this is fantastic"):** live
interactive session from his own terminals — `stream 2.0 15 60` from
the Telemetry CLI → CAM_REPLY ok=1 → 15.0 fps steady, dropped=0,
ingest 191+ frames while he watched, browser stream live at
nereus001:8080 (daylight frames ~5 KB vs the rehearsal's 1.9 KB night
frames — scene-bound rate demonstrated in the wild). Close-out:
apps stopped, LED trigger restored (mmc0), bridge quiet-exited
(ops-rule refresher en route: a polling mpremote loop feeds the quiet
timer and deadlocks the exit — true zero-contact silence required),
**fixture restored + sha-verified 55fa6ccf… (clears S16's pending
restore too) + S6 USB baseline re-run PASS (QVGA q90: 33.0 fps,
0 gaps, 0 bad JPEGs)**. Board state: S6 fixture service standing; S17
stack staged inert on /flash (bm_he.elf 3cdd1f66…, bridge, pumps,
reef bmp). PR opened (nibble 4).

---

## 2026-08-14 — Sprint S16 (BUILD-2) — AE3 joins the chain: code complete, both Pis deployed; live bring-up waits at the VCP gate

**Branch:** `sprint/16-ae3-chain` (repo) + bm_sbc fork
`feature/udp-transport` @ 4ccbf95 (pin +1, D28)

**Done:**
- Nibble 1 plan approved (Nick) incl. 3 decision points: rename to
  bm_net_wire; stream_bench RX_STAT tx_drops fork commit (pin move);
  stream trigger via /flash/bridge_cfg.json.
- Bite A (HE promotion, `firmware/bm_he`): bm_net_mock → bm_net_wire —
  link-up ONLY from retry_negotiation (REV-12; measured: l2 passes the
  1-BASED port_num at l2.c:425, link_change wants 0-based, REV-1 —
  both asserted in host tests); send() enforces 1514 + counter
  (REV-14); `wire_frag.{c,h}` (first msg carries TOTAL length,
  WCMD_FRAG continuations, ≤492 B frames byte-identical to the S10
  wire — 2a/2b regression wire-stable); node id 0xbe9c000000000003 /
  "bm_camera"; middleware always-on (AUDIT flag retired); WCMD_STREAM
  quota-paced publisher on s15/stream; wire_status_t 72→88 B with the
  drop ledger. Host tests 72→122 checks; ELF builds: **243,976 B of
  262,144 (93.1%, ~18.2 K headroom; +4.0 K over the V15 audit image).**
- Bite B (HP bridge, `firmware/bm_bridge`): bm_bridge.py — BridgeCore
  (pure data plane, 35 host checks incl. duplex + noise/CRC cases) +
  service loop: HE load-once w/ stale-HE refusal, link held DOWN until
  first VCP bytes (bm_sbc's gateway heartbeats on open → pipe quiet
  while unowned), zero prints while pumping, bridge_cfg.json one-shots
  (stream/ping), trace + HE-ring dump to flash, every exit cause to
  /flash/bridge_crash.txt (main_bridge.py, BUILD-2b rule).
- Bite C (Pi side): light.toml + uart-device (by-id) — factory
  composes gateway over udp (verified in source; port 15 math checked,
  V13(b) defused). Fork commit 4ccbf95: RX_STAT gains tx_drops (the
  Light transit ledger); deploy.sh pin updated from `git rev-parse`
  (not hand-expanded — S15 lesson), **deploy.sh PASS on BOTH Pis**
  (ctest 3/3 each; repo branch checked out on both). README: S16
  deploy/start-order/demos 1–3 ladder; S15 demos retitled + regression
  note (light.toml now opens the CDC port — comment out uart-device
  for two-Pi-only runs).
- Staged for the gated deploy: bm_he.elf (sha ee4be49f… = MANIFEST) +
  bridge files + bridge_cfg.json on nereus000:/tmp. Docs: D28 +
  DESIGN §S16 detail; TRACKER item 5 → [~].

**Broke/surprised us:**
- l2's renegotiation timer passes the 1-BASED port number into
  retry_negotiation (timer id seeded from port_num 1..N) while
  link_change wants 0-based — the same convention split behind REV-1/
  V13, now pinned by host tests on our device.
- Nothing else: host tests and the cross-build passed first try; both
  Pi deploys green.

**Same-session continuation — VCP gate opened (Nick: "Go"), staged +
FULL CHAIN REHEARSED:**
- Staging: fixture sha recorded (55fa6ccf… confirmed), five files
  sha-verified on board, warm reset. First bring-up DIED instantly
  when Light spoke — **V5 find #1: MicroPython's console scans inbound
  VCP bytes for 0x03 (kbd interrupt) and COBS frames contain 0x03
  freely → bm_sbc's first heartbeat injected KeyboardInterrupt into
  the pump.** (Crash file made it a 2-minute diagnosis.) Fix:
  `micropython.kbd_intr(-1)` for the service's life + NEW STOP MODEL —
  bridge exits itself after 30 s VCP silence (heartbeats every 10 s
  while alive) or 10 min unattached; ctrl-C can't stop a linked
  bridge; one bridge lifetime per demo (cfg one-shots re-arm on warm
  reset).
- **Chain rehearsal PASS (all three demo shapes):** (1) topology —
  Light NEIGHBOR_UP …01 port 1 AND …03 port 15; Telemetry neighbors
  ONLY Light, yet 🏓 from …02 (0 ms) and …03 (9–10 ms, 2 hops) —
  never-a-star holds; ×2 runs. (2) forwarded pub/sub — Camera's
  2 Mbps/1400 B stream: **Telemetry RX_STAT steady 1.99–2.02 Mbps,
  21.1 MB/15,084 msgs; Light transit ledger tx_drops=0, rx_drops=0;
  ZERO uart decode errors both sessions (22.4 MB + 27.7 MB relayed,
  every ~1490 B frame crossing the 4-msg rpmsg frag path;
  frag_errors 0, qdrops 0).** REV-12 live on silicon ("Renegotiated
  on port: 1" on the HE ring). (3) Camera-sourced 2-hop ping — **V5
  find #2: ll-multicast (ff02::1) ping never crossed Light (REV-6
  measured live)**; WCMD_PING switched to `multicast_global_addr`
  (ff03::1, what multinode itself pings), ELF rebuilt/restaged →
  **🏓 16 bytes … payload "S16 camera 2-hop" accepted by ping.c.**
- Known-cosmetic: newlib-nano %llx/%lu artifacts in ring prints
  ("…lx", "time=lu"); "Unable to load configs from flash" ×3 =
  RAM-stub config, expected (REV-27).
- Board state: bridge staged as /flash/main.py (board at REPL, HE
  stopped), demo cfg armed (stream 2.0/1400/600 s delay 15; ping
  target …01 delay 30). ELF on board = 45a9615d… (global-addr ping).

**Same-session continuation 2 (2026-08-15) — Nick's demo hit a real
crash; root-caused, fixed, ALL demos re-run by Claude (Nick's request)
and PASS:**
- **V5 find #3 (the big one): upstream heap corruption on the L2
  TX-overflow path.** Nick's demo run (stream_bench TX 15 Mbps on Light
  while forwarding the Camera stream + carrying the uart leg) hit the
  FIRST real `bm_l2_tx` queue overflow on a Pi → glibc "corrupted
  double-linked list" abort within ms. Cause: bm_l2_tx frees the L2
  reference itself on enqueue failure (the contract lwIP forces), but
  BOTH bm_linux TX paths freed again on the error return (bm_udp_tx
  even documented "must free twice") — one over-free. Can never fire
  from a lone publisher (S15 measurement), which is why it survived all
  prior testing. Fix: bm_core fork +1 commit (`eec6e82`, error-path
  frees deleted with the ownership contract documented), bm_sbc
  submodule bump (`1a806c7`), deploy.sh pins moved, both Pis rebuilt
  green. AE3 unaffected (compiles bm_lwip.c). Upstream-PR-worthy.
- **Crash repro on the fixed build: PASS** — same overload hit the same
  `evt queue full, dropped frame` line and ran to completion: TX_STAT
  `ok=3792 enomem=2 l2_drops=2`, drops counted + surfaced (`Unable to
  publish, err 12`), no abort. The D27 observability told the story.
- **Full demo re-run (fixed pins): d1 topology PASS (Light neighbors
  01+03, Telemetry only 02, 2-hop 🏓 10–11 ms both ways) · d2 PASS
  (both hops steady 1.99–2.00, 23.6/24.7 MB, all-zero ledgers) ·
  d3 PASS — 600 s @ 2.00 Mbps, Camera sent 107,142 = Telemetry
  received 107,142, ZERO loss/CRC/drops at every hop, ledger
  consistent end-to-end (bridge 107,215 frames / 158.25 MB,
  frag_errors 0).**
- En-route ops finds (now in the ae3-usb-unstick SKILL + READMEs):
  (a) the AE3 fell OFF the USB bus (error -71) after mpremote was
  pointed at a phase-1 bridge concurrently with a reset — uhubctl
  could NOT recover it because **the Pi 5 root hub's ppps never
  actually cuts VBUS** (measured: the bridge session survived a Pi
  reboot still blocked mid-write); **`sudo reboot` on the Pi = the
  fix** (fresh xhci re-enumerates; Nick's call). (b) by-id
  lingers→drops→reappears bit the start ladder once more — the full
  absent→present→settle dance is mandatory, and demo-to-demo
  transitions must wait out the bridge's 30 s quiet-exit before any
  mpremote contact.

**Next:** Nick's call — bless Claude's re-run as the S16 demo or run
README §S16 demos 1–3 himself (board staged + armed either way) →
nibble 4 PR (repo + both fork branches). Session end: fixture restore
+ sha-verify + S6 USB baseline re-run.

---

## 2026-08-14 — Sprint S15 (BUILD-1+3) — udp transport + factory: two-Pi bench live, zero-loss limiter rehearsal both directions

**Branch:** `sprint/15-udp-transport` (repo) + bm_sbc fork
`feature/udp-transport` + bm_core fork `bench/d4ecc38-obs` (D27)

**Done:**
- Physical gate: Nick ran the direct eth0↔eth0 cable; verified
  1000/full carrier both ends before any config. Bench IPs live per
  BENCHSPEC (nereus001=.1 Telemetry, nereus000=.2 Light,
  never-default, IPv6 off); dev access stayed on wlan/tailnet
  throughout (verified during every run).
- REV-23 pin check: bm_sbc 17ea904 pins bm_core d4ecc38 = our
  firmware/bm_he vendor exactly — zero drift. (Upstream main moved to
  6a4d73c; src delta 1 line; we stay pinned.)
- Bite 1 (factory, BUILD-3): `transport =` TOML key / `--transport`
  CLI (virtual|udp|serial|adin), construction extracted from
  runtime.cpp into transport_factory; default = virtual; singleton +
  callbacks-sharing constraints honored. Their full validate.sh green
  with the refactor (ctest, loopback 6/6, multinode 13/13, IPC 15/15).
- Bite 2 (udp device, BUILD-1): udp_port_device derived
  member-for-member from virtual_port_device (REV-11 constant 15
  ports; REV-12 link-up only from retry_negotiation, configured-peer
  check; REV-14 1514 enforced both directions, oversize logged with
  true length via MSG_TRUNC); token-bucket shaper (virtual-clock,
  integer-exact, default 10 Mbps) + device stats; stream_bench app
  (offered-rate publisher / receiver ledger, D21); host tests: 18
  (transport_kind) + 24 (parse+shaper); udp_multinode_test.sh 15/15
  incl. 3-node chain with ends-do-NOT-neighbor invariant.
- bm_core observability commit (the ONE patch, D27): TX + RX L2
  queue-drop counters + accessors; log 1st + every 256th.
- Bite 3 (two-Pi): nereus001 toolchain installed (cmake, socat),
  pinned clone via bench cable (Tailscale SSH blocks plain git —
  bench-IP ssh key provisioned instead), build + ctest green on both
  Pis. Cross-cable rehearsal: NEIGHBOR_UP with peer node id + 🏓
  bcmp_seq= BOTH ends; limiter 15 Mbps offered → 9.30 payload
  (=10.0 wire exactly), **36,622/36,622 delivered, zero loss**;
  control 8 Mbps → 8.00/20.0 s unshaped, 19,532/19,532. pcaps
  written by --pcap on both nodes.
- Repo: `pi/bm_bench/` (node TOMLs w/ fixed IDs be9c…01/02/03,
  deploy.sh with hard pin verification, README demo ladder);
  housekeeping rider: stale `nereus001-1` refs fixed in
  s5_tx_load.py, DESIGN §S6 URL note, TRACKER S3 demo (DEV_LOG
  history untouched).

**Broke/surprised us:**
- **REV-13's silent TX BmENOMEM drop cannot fire from a lone
  publisher on a Pi**: POSIX queue enqueue blocks ≤10 ms vs ~0.9 ms
  service at 10 Mbps → overload becomes blocking backpressure
  (offered 15 → achieved 9.3 over 32.2 s wall; 200 Mbps loopback →
  244,141/244,141, zero drops anywhere). Real silent-drop sites: RX
  zero-timeout enqueue (now counted), S16's forward path (L2 thread
  enqueues into its own queue — guaranteed timeout; THE transit
  ledger), device oversize (logged). Demo verdict reframed to
  "zero loss + counters consistent," honest per measurement.
- bcmp ping replies log at debug level — invisible at the TOMLs'
  initial info level; looked like a real cross-cable ping failure
  for one run. TOMLs now set log-level=debug with a comment.
- Tailscale SSH intercepts inter-Pi git (interactive auth URL);
  fixed by real authorized_keys over the bench IPs — which also
  makes deploys ride the 1 GbE cable instead of WiFi.

**Next:** Nick: create the two forks (`gh repo fork bristlemouth/bm_sbc
--clone=false`, same for bm_core), then I push the branches; nibble 3 =
Nick runs `pi/bm_bench/README.md` demos 1–3; nibble 4 = repo PR + fork
PRs. Then S16 (BUILD-2: AE3 joins via rpmsg + HP bridge).
→ Same-day close-out: forks created + branches pushed (4ebdbc3 /
e031f11); deploy.sh PASS both Pis (pin check caught + fixed a wrong
hand-expanded sha); **demos 1–3 run by Nick + re-confirmed by Claude,
identical numbers = S15 demo PASS; PR #22 open.** Demo-1 start-window
gotcha (one-shot ping at t+3 s) documented in the README. Upstream PRs
to bristlemouth (factory + udp device; drop counters) = a separate
decision for Nick, not opened. Next: merge #22 → S16.

---

## 2026-08-14 — Sprint S14 (bench rung 0) — V16 relay gate PASS (5.4 Mbps sustained); V15 middleware fits AND runs (91.6%)

**Branch:** `sprint/14-bench-rung0` (worktree, from merged PR #20)

**Done:**
- Nibble 1 (plan presented; Nick opened the bench = go): V16 relay
  bench + V15 size audit + bm_sbc rung 0.
- `firmware/bm_bridge/uart_codec.py`: bm_sbc uart_l2 codec, dual-runtime
  (viper/CPython), byte-exact vs Sofar's C — golden vectors generated by
  compiling their frame_codec/cobs/crc32c on the Mac. On-target: crc32c
  7.34 MB/s, full encode 10.1 Mbps. Host tests 50 checks.
- Relay bench (pump service on HP as /flash/main.py + Pi counter):
  **rung B (framing+USB) 13.1 Mbps · rung C (full relay) 5.5 Mbps ·
  agg=3 5.4 Mbps · rung E crc32c==crc32==none 5.55 Mbps · rung D
  600 s: 5.425 Mbps, 288,162/288,162 frames, 864,487 rpmsg msgs,
  0 gaps/drops/in-stream errors, HE alive — GATE PASS (2.7×, verdict
  from the shipped counter).** Rung A regression unchanged (13.1/5.5).
- V15: middleware slice vendored (d4ecc38, +bm_common_messages
  helpers) behind AUDIT_MIDDLEWARE=1, bm_sbc init order → **240,000 B
  of 262,144 (91.6%), +8.5 K over baseline, ~21.6 K headroom — FITS.**
  Baseline sha-identical without the flag.
- V15 bonus: audit image BOOTS — full 2b A–E bench PASS on it,
  "audit: middleware slice up" on the ring, heap cost ~4.4 K; 2b
  artifact restored byte-identical.
- Rung 0 DONE on nereus000: bm_sbc @ 17ea904, ctest 100%,
  validate.sh 15/15 (~/bm_sbc = standing checkout).
- Fixture restored + verified: main.py sha = repo ae3_usb copy; S6
  baseline QVGA q90 35.0 fps / 0 gaps / 0 bad. (First restore attempt
  silently failed while the old service held the VCP — redo from
  REPL/cold state, sha-verify after; README rule now.)
- S7 open item answered en route: uhubctl works on nereus000 —
  hub 3 port 1 (not the guessed 1-1); cycle+warm-reset is the
  documented recovery pair.

**Broke/surprised us (all now rules in firmware/bm_bridge/README.md):**
- **Cold boot does not run main.py on this build** — every "cold
  recovery" in fixture history was followed by a protocol reboot, so
  nobody had ever seen it. Warm `mpremote reset` is the service entry.
- mpremote attach kills the service (injected KeyboardInterrupt);
  pyserial attach is harmless. Crash/trace persistence to /flash
  (BENCHSPEC's BUILD-2b rule, adopted early) is what made every one of
  these failures diagnosable — silent otherwise.
- HE lifecycle triad: 2nd stop→load cycle per boot loses the ns
  announcement; stale-idle-HE restart is fine; stale-mid-burst HE
  blocks in C. Load once + drain every rung end.
- z/n crc modes "hung" on exactly one frame: the S14END summary was
  encoded with default CRC — mode must cover the terminator too.
- Enumeration after reset: by-id symlink lingers→drops→reappears;
  wait absent→present→settle. Counter now handshakes (newline → fresh
  banner) before sending config.

**Next:** nibble 3 = Nick runs the three demo commands (in the PR
body) → PR merge → S15 (BUILD-1+3; needs nereus001 back on the
tailnet — bench check). Board state: fixture service standing, s14
tooling staged inert on /flash, HE stopped.

---

## 2026-08-14 — S11 nibble-1 plan + BENCHSPEC review — bench arc adopted (docs PR)

**Branch:** claude/s11-interim-3-uart-gateway-97913a (worktree) → PR to main

**Done:**
- S11 INTERIM 3 nibble-1 plan researched + presented (no code): bm_sbc
  gateway fully mapped (raw L2 / COBS / CRC-32C / 0x00 delim, `--pcap`
  built in; bm_core pinned d4ecc38 = our vendored rev). KEY FIND: **no
  stock mote firmware speaks it** — counterpart = `native_serial_bridge`
  on bm_protocol branch `feat/uart-sbc` (open PR #378, never
  hardware-validated by Sofar; baud hardcoded 115200, PLUART/LPUART1).
  Dev kit facts (sourced): 24 V wall-charger powered via bus ports (USB
  cannot power it), console = native USB-C CDC ×2 (CLI = port ending 1),
  payload UART on dev-board terminals 1/13/14 at 3.3 V, flash = ROM DFU
  (no SWD rig needed). Bite = flash that branch's app; plan incl. bench
  meter checklist + golden-capture diff via s10_peer.py parsers.
  **Nick deferred the bite** (kept on ladder, item 7).
- BENCHSPEC v2 (three-node bench, agent-drafted outside this repo)
  reviewed against project context: topology/BUILD-1/3/4 sound — its
  REV-1/REV-12 findings independently match our INTERIM-2a live
  experience. **BUILD-2 (HE core claims USB) rejected**: HP's stock
  firmware owns the one USB controller and the whole dev loop rides it
  (REPL, remoteproc ELF load, DFU flash, recovery). Replacement = the
  D25 rpmsg seam promoted to a real wire + HP CDC bridge (uart_l2 codec
  over the VCP, crash-persistence rule) + bm_sbc `--uart` on
  /dev/ttyACM* (zero new Pi transport code).
- Docs landed (Nick approved drafts in chat): **docs/BENCHSPEC.md v3**
  (REV-20..28, V15/V16 gates, V11/V12 resolved, host mapping
  nereus000=Light / nereus001=Telemetry), TRACKER interim ladder →
  S14–S17 bench sprints (+ S11 kept, upstream reports kept), D26.

**Broke/surprised us:**
- Sofar's own uart-gateway doc ends in a TODO — "Not yet validated on
  physical hardware." Whoever runs it first validates it (us, either
  via the S16 CDC leg or the S11 dev-kit bite).
- v1's bmcam pipeline (bm_cam_legacy) speaks **bm_serial** — a
  different serial protocol (typed pub/sub, CRC16) than bm_sbc's
  uart_l2 (raw L2, CRC-32C). Easy to conflate; now recorded in both
  BENCHSPEC and the S11 plan.
- The two bench-spec agents' only critical error traced to one missing
  fact (HE stack is runtime-loaded via stock HP firmware, nothing
  flashed) — REV-22 now pins it so nobody re-derives the USB mistake.

**Next:** merge this docs PR → S14 nibble 1 (relay-throughput bench
plan — the V16 gate everything else hangs on), then S15 (udp device).

---

## 2026-08-12 — Sprint S10 (INTERIM 2b) — BCMP converses: python peer node, neighbor table + ping both ways; rehearsal PASSES ×2

**Branch:** `sprint/10-bcmp-2b` (worktree `s7-headless-ae3-flash-73104e`,
branched from merged PR #18)

**Done:**
- Nibble 1 (plan approved by Nick): peer = python on the HP end of the
  2a fake wire; verdicts C (neighbor table via BcmpNeighborTableRequest
  — the same query real BM topo tooling uses), D (ping peer→HE), E
  (ping HE→peer via new WCMD_PING, acceptance proven by ping.c's debug
  ring line). bm_core stays byte-identical.
- Nibble 2: `s10_peer.py` (pure builders/parsers, byte-exact BCMP,
  CPython-testable), WCMD_PING in src/main.c (+~30 LoC C), runner grown
  to A–E with both directions in the pcap. Host tests 72 → 112 (new
  test_peer.py: checksum ones-complement invariant, ingress-nibble
  round trip; wire_ping_t ABI locks). Build 231.5 K (~88 %, +0.4 K).
- Rehearsal (Claude, twice, identical): **A–E ALL PASS, first try** —
  neighbor formed + online from 5 s peer heartbeats, both pings
  answered/accepted, pcap = full 15-frame two-node conversation
  (tcpdump-clean). First live RX-path exercise (l2→lwIP→bcmp) worked
  immediately. S6 USB baseline re-verified after (34.1 fps, 0 gaps,
  0 bad, sample JPEG SOI/EOI valid).

**Broke/surprised us:**
- Nothing broke. Checksum byte-order question resolved from lwIP source
  before first injection (native-store = network bytes → 2a's
  "swapped" compare branch was the live one) — no live calibration
  needed; every injected frame accepted on the first run.
- Bonus behavior: HE fires an unprompted BcmpDeviceInfoRequest at its
  new neighbor (bm_core's discovery path) — now in the pcap.
- ping.c prints reply seq_num via PRIu32 on a u16 field + %llx node ids
  (nano-printf garbage) — cosmetic upstream quirks; runner matches
  stable text instead.

**Next:** Nick runs the 2b demo (`bm_he/README.md` ladder — build/scp
optional since artifacts are staged; two cp + one run + pcap pull) =
the INTERIM-2 demo proper → nibble 4 PR.
→ **demo PASSED (Nick, same day — A–E identical to both rehearsals) =
INTERIM 2 DONE; PR opened.** ("Unable to load configs from flash." ×3
in the ring = bm_core's normal first-boot line — the stub config store
is RAM, born empty per load; persistence is a hardware-day concern.)

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
