# Spec — is custom N6 firmware for hardware H.264 worth the squeeze?

**Owner (decision gate): Nick.** **Investigating agent: desk only.**
**Opened:** 2026-09-07, alongside sprint S30 (video DOE) on nereus002.

---

## 0. HARD CONSTRAINTS — read before anything else

1. **Touch NO hardware.** nereus002, the AE3, the N6, the IMX708 and the
   bench Pis are owned by a concurrent session and are actively running a
   measurement. Do not ssh to any `nereus*` host, do not open a serial
   port, do not flash anything, do not run `mpremote`. If you believe you
   need a board to answer a question, **that is a finding** — write it
   down as "needs hardware" and move on.
2. **This is a desk investigation on the Mac.** Reading source, building
   firmware, reading vendor documentation, and arithmetic.
3. **Building firmware is in scope. Deploying it is NOT.** Produce the
   artifact and the instructions; a later session with the bench flashes it.
4. **Do not modify `nereus-vision-dev`** — deployed units run from it.
5. Work on a branch `sprint/31-n6-h264` off `main`. Never commit to main.
6. Follow `docs/TRACKER.md` §Rules for Agents and the `/agent-entry`
   ritual as normal. This spec replaces the *bite selection* step only.

---

## 1. The question

The OpenMV N6 (STM32N657) is believed to carry a hardware video encoder
that OpenMV's MicroPython layer does not expose. Every camera on this
project therefore ships **MJPEG**, which has no inter-frame coding and so
pays full price for every frame.

**The question is not "can it be done".** It is:

> Given Nick's standing policy — *"test the stock hardware (stock + small
> fixes found along our dev path), but if we need to spin custom firmware
> to get a feature, we bail"* — does the measured H.264 win justify
> breaking that policy for the N6?

Nick has explicitly authorised this investigation as a **scoped exception
to evaluate the trade**, not as approval to adopt custom firmware. The
adoption decision remains his and is made on your numbers.

---

## 2. What is already believed, and what you must re-verify

Everything in this section came from a prior session's reading and is
**unverified at the source**. Treat it as a lead, not a fact. This repo
has been burned repeatedly by plausible guesses (CLAUDE.md rule 3).

| Claim | Status | How to settle it |
|---|---|---|
| The STM32N657 has a hardware video encoder (VENC) | **UNVERIFIED** | ST reference manual / datasheet for STM32N657. Name the document and section. |
| The OpenMV tree vendors a VC8000 (VeriSilicon/Hantro) encoder driver | **UNVERIFIED** | The OpenMV firmware repo. Give the path and the commit. |
| There are **zero** MicroPython bindings for it | **UNVERIFIED** | Grep the bindings layer. Absence must be shown, not assumed. |
| MJPEG→H.264 is worth ~4.0x | **MEASURED, but on the wrong camera** | 26.43 → 6.60 Mbps, IMX708 720p15, S29. Indicative only — a different sensor, encoder and scene. |

**Gate A — kill the investigation here if the premise fails.** If the
STM32N657 has no usable hardware encoder, or the OpenMV tree carries no
driver for it, stop and report that. That is a complete and valuable
answer; do not substitute a software H.264 encoder as a consolation
(it will not fit the frame budget, and proving that is a separate ask).

---

## 3. The baseline you must beat

Measured on nereus002 the same week, on the actual boards. **The probe
that produced these deliberately excludes USB transfer**, so they are
the camera's own capture+encode ceiling, not a link measurement:

| Camera | VGA q30 encode rate | bytes/frame |
|---|---|---|
| **N6** | **68.6 fps** | 13.7 KB |
| IMX708 | 25.6 fps | 18.1 KB |
| AE3 | 13.7 fps | 11.0 KB |

The N6's free heap at VGA is **25.6 MB** — relevant, because an encoder
needs reference frames and a bitstream buffer.

The full matrix — three cameras x QVGA/VGA/HD x six quality levels, with
bytes per 5 s clip and the sustained bandwidth to send one per hour —
lands in `~/video_doe/<run>/results.json` on nereus002 and is summarised
in the S30 PR. **Ask Nick for the final table rather than re-deriving it,
and do not go and fetch it from the rig yourself.**

---

## 4. The decision frame — read this before you start measuring

**The value of H.264 depends entirely on duty cycle, and the current use
case has a very low one.** Nick's stated need is *one 5-second clip per
hour*. Worked from the N6's measured numbers:

| Scenario | Payload | Sustained link |
|---|---|---|
| N6 VGA q30, 30 fps, 5 s clip, MJPEG | 1.95 MB | **4.55 kbps** |
| Same clip, if H.264 delivers the indicative 4x | ~0.49 MB | **1.14 kbps** |
| **Absolute saving** | ~1.46 MB/hour | **~3.4 kbps** |

So the honest framing: **at one clip per hour, MJPEG already fits in a
few kbps, and custom firmware buys back single-digit kbps.** H.264 gets
interesting when the duty cycle rises — continuous streaming, longer
clips, higher resolution, or many rigs sharing one backhaul.

**Your report must therefore answer "at what duty cycle does this pay
for itself?", not just "how much smaller is the file?"** A ratio with no
duty cycle attached is not a decision input. State the crossover
explicitly: the clip length / frequency / resolution at which the
engineering cost is repaid.

---

## 5. The ladder

Each rung ends with a written finding. Stop at any gate that fails.

**Rung 1 — Premise (Gate A).** Settle the four claims in §2 at their
sources, with document names, file paths and commit hashes.

**Rung 2 — The gap.** What exactly is missing between the driver and
MicroPython? Name the files that would have to change, the API surface a
`h264` or `venc` module would need, and whether OpenMV's build system
already has a slot for it. Estimate the work in LoC and in the kind of
work it is (glue vs. new driver bring-up vs. vendor blob integration).

**Rung 3 — Cost of ownership.** This is where "worth the squeeze" is
usually decided, and it is routinely underestimated:
  - Does OpenMV upstream want this? Check issues/PRs/release notes for an
    existing effort — **inheriting an in-flight upstream change is a
    completely different cost from carrying a private fork.**
  - What breaks at the next OpenMV release? This project already carries
    one firmware patch (the S18 sticky-framebuffer build) and has felt
    the drift.
  - Does a custom build jeopardise anything else the boards do today
    (model loading, ROMFS, the DFU ladder in `pi/ae3_flash/`)?

**Rung 4 — Predicted win, with its error bars.** From the encoder's
documented capability and the measured baseline, predict: achievable fps
at QVGA/VGA/HD, bytes per 5 s clip, and the duty-cycle crossover from §4.
**Be explicit about what is prediction and what is measurement** — no
predicted number may be presented in the same table as a measured one
without a column saying which it is.

**Rung 5 — Build it (only if rungs 1-4 clear).** Produce a flashable
firmware image and a written flash procedure. **Do not flash it.**

**Rung 6 — The bench plan.** A test the hardware-owning session can run
that compares like with like against §3: same scene, same resolutions,
same quality ladder, same 5 s clips, same link-budget arithmetic. Say
what would falsify the win.

---

## 6. Deliverable

A PR on `sprint/31-n6-h264` containing:

1. `docs/N6_H264_FINDINGS.md` — the rungs, each with its sources.
2. **A recommendation in one sentence**, of the form *"Adopt / do not
   adopt, because <the number that decides it>."* Nick reads this first.
3. The duty-cycle crossover from §4, stated as a number.
4. If you got to rung 5: the firmware artifact and its flash procedure.
5. If you did not: exactly which gate stopped you, and what it would take
   to pass it.
6. SPEC.md §Open questions updated with anything that could not be
   settled at the desk.

**A well-argued "not worth it" is a successful outcome.** The cheapest
result this investigation can produce is closing the question for good,
and the project is better off with that than with a fork nobody costed.

---

## 7. Reporting

Follow CLAUDE.md §Reporting to Nick — three headings, **two bullets
maximum each**, comparisons in a table. Nick reads these to decide, not
to follow along.
