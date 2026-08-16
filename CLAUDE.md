# CLAUDE.md — BM Camera Node: Native ADIN1110 Video Path

## Start here, every session

This repo runs on the agent discipline in **docs/TRACKER.md**. Before any other
work: run **/agent-entry** (or follow the Rules for Agents at the top of
docs/TRACKER.md). Owner and approval gate: **Nick**.

Docs map — read per the ritual, don't skip it:

- `docs/SPEC.md` — what we're building; verified facts; safety rules; open questions
- `docs/TRACKER.md` — rules + sprint ladder (the entry point)
- `docs/DESIGN.md` — as-built architecture + decision log
- `docs/DEV_LOG.md` — session log, newest first
- `docs/PROMPTS.md` — Nick's kickoff prompts

Layout: `firmware/` (AE3 MicroPython, later C) · `pi/` (overlays, services,
shim, stream server) · `bench/` (benchmarks, counters) · `docs/diagrams/`.

## Engineering values (apply to every bite)

1. **Boring, debuggable engineering.** Small modules, explicit control flow,
   visible logs, plain formats. No speculative abstraction; build for the
   current sprint, not an imagined future.
2. **Reuse before rewriting.** Mainline adin1110 driver, SG's overlay,
   Microchip oa-tc6-lib, Sofar's bm_core exist. Inspect and adapt the smallest
   working piece; document what was reused. A rewrite needs a measurable reason.
3. **Never invent hardware facts.** Register addresses, pinouts, strap
   polarities, GPIO mappings: verify against vendor docs or measure, else flag
   in SPEC.md §Open questions. This project has already been burned by
   plausible guesses.
4. **Trust artifacts, not exit codes.** A capture that "succeeded" but produced
   an empty file failed. Verify outputs exist, sizes are plausible, images
   open, counters moved. Three traps that have each cost a session:
   - **A pipeline returns the LAST command's status.** `script.sh | tail -2`
     exits 0 even when the script failed, so `&&` chains march on against a
     broken state. Capture the script's own `rc`, or grep its output for the
     verdict it prints.
   - **A tool's success message is not the artifact.** `mpremote cp` returning
     `rc=0` sat next to a file that still needed a normalised read-back to
     prove it. Read the bytes back.
   - **A comparison can lie about a correct result.** `mpremote cat`
     CRLF-translates, so a read-back is N bytes larger than its N-line source
     and `cmp`/`sha256` report a mismatch that is not real. Understand the
     transport before believing a diff.
5. **One variable at a time.** Bench debugging dies when wiring, straps,
   driver code, and stream settings change together. Change one; keep the
   known-good path recorded before touching it.
6. **Fail loudly and usefully.** Errors name the device, action, register/pin,
   and a recovery hint. Partial failure never destroys good data.
7. **Respect MicroPython constraints.** AE3 code: small allocations, bounded
   buffers, streaming, no CPython idioms that don't exist there.
8. **Safety rules in SPEC.md are absolute.** No powered Spotter/BM bus contact;
   3.3 V only on AE3 pins; meter-check power before first energize.

> Never trust a script just because it exits successfully. Trust the artifacts.
