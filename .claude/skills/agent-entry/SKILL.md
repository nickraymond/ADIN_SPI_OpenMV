---
name: agent-entry
description: Session-start ritual for this repo's agent discipline. Use at the START of every coding session in a repo that has docs/TRACKER.md, or when asked to begin work, pick up a sprint, or continue the project. Enforces read order, bite sizing, nibble gates, branching, and demo-per-sprint.
---

# Agent Entry

You are working under this repo's agent discipline. OWNER (the human gate) is:
**Nick**.

## On session start — in this order, before any code

1. `git checkout main && git pull` — sync main. Install any new skills the repo
   ships in `.claude/skills/`.
2. Read `docs/TRACKER.md` **cover to cover**. Non-negotiable.
3. Skim `docs/SPEC.md` and `docs/DESIGN.md`.
4. Read the top ~3 entries of `docs/DEV_LOG.md`.
5. Identify the single active TODO (first `[~]`, else first `[ ]` whose
   dependencies are met). Confirm it with OWNER before starting.

## How work happens

- **Bites:** ~300 LoC target, one TODO at a time. If SPEC.md is too thin to
  inform the bite, STOP and ask OWNER — do not invent requirements.
- **Nibbles (four, gated):**
  1. **Plan** — explore, write throwaway code, change no files. Present the
     plan. **Wait for OWNER's explicit approval.**
  2. **Code + unit tests** — flag OWNER if the plan must substantially change.
  3. **Manual tests** — OWNER runs them; provide copy-pastable CLI commands.
  4. **Open PR** — description includes the demo commands.
- **Branch:** all new work on `sprint/<n>-<slug>`. Never commit to main.
- **Demo:** a sprint is not done until OWNER runs its live demo successfully.

## Protect the deliverable (S18 bite B lesson)

The bite the sprint is waiting on is the deliverable; everything else is
overhead, and overhead expands to fill a session if you let it.

- **Waiting on hardware is not progress.** Never poll or retry-loop to
  wait for a board or a port — on this bench that actively prevents the
  thing you are waiting for (see `ae3-board-access`). One timed wait, one
  attempt, then stop.
- **Cap the yak-shave.** If a side quest (bench recovery, fixture
  hygiene, a flaky read-back) has not resolved in ~3 attempts, STOP:
  record the exact state in DEV_LOG, hand OWNER the one command, and get
  back to the bite. An S18 session spent its final hours on session-end
  hygiene and shipped no page.
- **A discovered bug does not silently become the sprint.** Investigating
  it far enough to measure and record it is right; fixing it is a NEW
  bite that needs OWNER's gate, and the original bite still owes its
  demo. Say out loud when the scope has moved.

## On session end — always

- Add a DEV_LOG.md entry (newest on top): done / broke / next.
- Update DESIGN.md if architecture changed or a decision was made (append to
  the decision log; never rewrite history).
- Update TRACKER.md state markers.

## Standing rules

- Facts carry sources. Unknowns get flagged in SPEC.md §Open questions — never
  guessed. Hardware pinouts, strap polarities, and register addresses are
  verified against vendor docs before use.
- Obey SPEC.md §Safety rules absolutely.
