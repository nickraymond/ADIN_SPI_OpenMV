# docs/mockups — reviewed design references

Front-end mockups that Nick has reviewed and approved. They are **design
input, not deliverables**: nothing here is served, deployed, or imported
by production code.

Why they live in git: the S18 mockup was reviewed out of an agent
session's scratchpad under `/private/tmp`, which is wiped. A design
Nick has already approved should not be re-derivable only from memory.

## s18_bench_mockup.html — S18 camera bench web tool

**Approved by Nick 2026-08-16.** The design reference for **S18 bite C**
(`pi/bench_web/`). Open it in a browser; it runs standalone with
simulated data and an embedded reef reference photo.

What bite C should carry over as reviewed:

- the three-column layout, and the compare view dropping the right column
  (the levels/ledger/constants there describe the LIVE frame and are
  stale next to two stored captures)
- the **commanded-vs-actual pill**, fed by the receiver ledger
- the **feasibility warning box** and the video/stills gating
- the **RGB + luma histogram panel** (one plot per channel, OpenMV-IDE
  shape)
- the **gallery + side-by-side compare**

What bite C must change:

- **the data.** The mockup simulates the ledger and synthesises the
  scene; bite C reads the real control socket
  (`pi/bm_bench/bench_ctl.py`) and the real `~/bench_captures/` sidecars,
  and embeds the live `/stream` from the frozen S3 server.
- **the model constants are EXTRAPOLATED.** `MEAS` and `BRIDGE_DERATE`
  come from ONE measured point (QVGA colour reef q50, 15.00 fps —
  S17 bite 0). Everything off that mode is arithmetic, not measurement.
  Label predictions as estimates in the UI until S18 bite B2's matrix
  replaces them with measured numbers.
- **add a click guard.** Until bite B2 lands, a sensor re-init arriving
  too soon after a capture wedges the camera for the rest of the session
  (SPEC §Open questions). The page must disable capture/stream controls
  until the previous capture completes, plus a settle.

Provenance: sha256 `c57f6aaf302e155bfcc06784…`, 304,769 B, byte-identical
to the file reviewed on 2026-08-16.
