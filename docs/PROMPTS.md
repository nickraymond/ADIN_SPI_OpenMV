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
