---
name: capture-task
description: Add new work to this repo's docs/TRACKER.md the disciplined way. Use when a new task, bug, idea, or scope change comes up mid-session — sizes it into bites, places it in the right sprint or the icebox, and checks the devlog for duplicates.
---

# Capture New Task

OWNER: **Nick**. New work goes through the tracker — never straight into code.

## Procedure

1. **Check for duplicates:** search `docs/TRACKER.md` (including Icebox) and
   recent `docs/DEV_LOG.md` entries. If it exists, update that item instead.
2. **Size it:**
   - Fits in one ~300 LoC bite → single TODO checkbox.
   - Bigger → break into bites; if it has its own demo, propose it as a new
     sprint (OWNER approves sprint additions).
   - Not this project's scope (see SPEC.md §Non-goals) → Icebox with one line
     of context.
3. **Place it:**
   - Belongs to the current sprint's goal → add under that sprint.
   - Belongs later → the sprint whose goal it serves.
   - Someday/maybe → Icebox.
4. **State it well:** every TODO is verifiable ("X prints Y", "demo Z passes"),
   not vague ("improve X"). If it changes requirements, update SPEC.md too and
   tell OWNER.
5. **Mid-bite discipline:** capturing a task does NOT mean doing it now. Finish
   the current bite; new work waits its turn unless OWNER re-prioritizes.
