# PROMPTS.md — Session Kickoff Prompts

*Copy-paste these verbatim; fill only `<N>` and `<slug>`. Never add task
instructions here or in the prompt itself — requirements belong in SPEC.md or
TRACKER.md, where the next session can see them.*

*(No skills installed? Replace the first line of any prompt with: "Follow the
Rules for Agents at the top of docs/TRACKER.md.")*

---

## 1 — New sprint

```
Run /agent-entry. We're starting Sprint S<N>.

Read docs/TRACKER.md cover to cover, skim docs/SPEC.md and docs/DESIGN.md,
and read the top 3 entries of docs/DEV_LOG.md before doing anything else.

Then: create branch sprint/<N>-<slug>, and give me your PLAN for the first
bite of S<N> — nibble 1 only. Throwaway exploration is fine, but change no
files and write no production code until I approve the plan.

Remember: ~300 LoC bites, I run the manual tests (give me copy-pastable
commands), and this sprint is not done until I've run its demo from the
TRACKER and it passes.
```

## 2 — Resume mid-sprint

```
Run /agent-entry. We're mid-Sprint S<N> on branch sprint/<N>-<slug>.
Do the full read ritual, then check the branch diff against the TRACKER
state and the top DEV_LOG entry, tell me exactly where the last session
stopped and which nibble we're in, and wait for my go before continuing.
```

## 3 — Sprint close-out (demo already passed)

```
Sprint S<N> demo passed on my end. Close it out: mark S<N> done in
TRACKER.md, append the DEV_LOG entry, add any DESIGN.md decisions made
this sprint, open the PR with the demo commands in the description,
and show me the diff of all doc changes before committing.
```

## 4 — Capture a task without acting on it

```
Run /capture-task: <one-line description>. Size it, place it (current
sprint / later sprint / icebox), show me the TRACKER diff, and then
return to the current bite — do not start work on it.
```

## 5 — Ready to paste: S22 bite 1 kickoff (written 2026-08-18)

```
Run /agent-entry. Next is S22 bite 1: the HE flood fix — the wire task
goes permanently mute under sustained camera publish, and it is the
last measured bug between the bench and honest ceiling numbers.

Branch sprint/22-he-flood from main.

Start from the evidence, not from scratch:
- SPEC §Open questions (the flood entry, incl. the 2026-08-18 burst
  datum) + TRACKER S22 bite 1 have the measured picture: 315 msg/s
  clean 4/4, 466 marginal 2/3, >=513 fatal 4/4 (one live demo at ~560);
  single-frame bursts: 55 and 68 chunks clean, ~83 lost 54 chunks WHILE
  THE HE PUBLISHED COMPLETELY (pub_errs=0) — the loss is downstream of
  bm_pub.
- Preserved trace ~/bridge_traces/20260818T002807_* on nereus000 shows
  the mute (he2pi_frames frozen, pi2he advancing). Read before board
  contact.
- Suspect territory: the HE netwire TX path under sustained load
  (S19 bite 2's non-blocking pump, firmware/bm_he). The S18 G-probe
  pattern (bench/probes/) is the off-chain reproducer shape: synthetic
  publish at swept rates, no Pi chain.
- The transition bug is FIXED (sticky-fb firmware on the board,
  rollback in ~/fw/development/) — do not re-litigate it; a wedge now
  is finding 1, not B2/B4.

Ops: board access per ae3-board-access; recovery = reboot nereus000 +
demo_up (~2 min, standing permission). Handover rule stands: you
deploy, you bring the chain up, you verify end to end; I get URLs and
results.

Success = sustained QVGA color at the measured 28.07 fps ceiling for
10 min with a ledger-exact run and zero wedges; capture 90 hd mono
(the q90 burst) delivers; the bench UI guardrail constants raised to
the NEW measured boundary (suite updated); the mono-ceiling matrix
rows finding 1 blocked run clean and land in MEAS_FPS.

Nibble 1 = plan first, my gate before code. ~300 LoC bites, short
actionable replies, 10-min status updates.
```
