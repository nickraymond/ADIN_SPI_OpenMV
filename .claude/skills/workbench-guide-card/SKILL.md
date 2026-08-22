---
name: workbench-guide-card
description: Add a "cookbook chapter" to the nereus000 workbench menu — a guide card that opens step-by-step instructions instead of running a demo. Use when releasing a tool or procedure that runs OFF the Pi (Mac-side GUIs, toolchain steps, manual deploy routes) or any multi-step human procedure worth a menu entry. Covers the guide= recipe schema, the Pi-served chapter HTML, the live reachability badge, and the hostname/absolute-path traps that broke the first chapter.
---

# Workbench guide cards ("cookbook chapters")

Nick's call (2026-08-21, S8 B3): procedures that cannot be one-click demos
become **chapters in the cookbook** — a card on `http://nereus000:8088`
that opens a Pi-served instructions page. First chapter: the label-review
GUI (`pi/workbench/recipes/guides/label-review.html`). Copy it; do not
redesign it.

## The pattern

1. **Recipe** `pi/workbench/recipes/<name>.toml`:

   ```toml
   name = "label-review"           # NAME_RE: ^[a-z0-9][a-z0-9-]*$
   title = "Label review (GUI on the Mac)"
   summary = "One-paragraph card text. Say WHERE the tool runs."
   guide = "guides/<file>.html"    # GUIDE_RE: ^guides/[A-Za-z0-9._-]+\.html$
   ```

   A guide card carries **no boards, no [run], no [health], no services**
   — the schema REFUSES those combinations, and `/api/start` refuses the
   card with 409. It is documentation, not a demo. `thumbnail` is allowed.

2. **Chapter** `pi/workbench/recipes/guides/<file>.html`: self-contained
   HTML (dark palette matching the workbench), numbered steps, served
   confined at `/guides/<file>` — read from disk per request, so **edits
   need only a `git pull` on the Pi, no workbench restart**.

3. **Reachability badge** (when the chapter points at a service on another
   machine): a no-cors `fetch` of the target URL — resolves iff something
   answers; cannot read the response. Keep the URL in ONE place (the
   "Open →" link's href) and derive the probe from it. See the JS at the
   bottom of `label-review.html`.

## Traps (each cost a round-trip on chapter #1)

- **Never guess a hostname.** The Mac's mDNS name comes from
  `scutil --get LocalHostName` (→ `Nicks-MacBook-Pro`, so
  `nicks-macbook-pro.local`) — NOT from `hostname`, which returns the
  useless `Nicks-MBP.localdomain`. Verify with a `curl` against the
  running service before committing. `.local` is resolved by the READER'S
  browser, not the Pi, so test from a Mac.
- **Absolute paths in every command.** The reader runs them from a random
  terminal in a random directory. `python3 ml/foo.py` is broken; give
  `python3 ~/Documents/GitHub/ADIN_SPI_OpenMV/ml/foo.py`. Note that a
  file on an unmerged branch does not exist in the reader's main checkout.
- **Warn about destructive re-runs.** If a script can flatten the work the
  chapter produces (e.g. `ml/fomo/relabel.py` regenerating auto-labels
  over hand corrections), the chapter carries the warning AND the script
  carries a refusal guard.

## Release checklist

- Tests: extend `pi/workbench/test_workbench.py` — the shipped-recipes
  test already requires zero problems; add a `TestShippedRecipes` case
  asserting the chapter file exists and names the real tool/port
  (pattern: `test_label_review_guide_ships_and_names_the_gui`).
- Deploy = `git pull` on nereus000. Only schema/route changes need a
  workbench restart (`sudo systemctl restart workbench` — Nick's hands).
- Verify the served page: `curl http://nereus000:8088/guides/<file>.html`
  and grep for the URL/command you just wrote.
