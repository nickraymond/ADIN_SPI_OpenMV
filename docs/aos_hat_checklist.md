# AOS BOREALIS hat — verification checklist (S2 bite 1)

*STATUS 2026-08-10: hat #1 validated LIVE instead (probed on nereus000,
PHY ID match, verify 5/5 — see DESIGN.md §S2 detail); working register I/O
supersedes checks #1–#9 for that hat. This checklist remains the procedure
for **hat #2** before it goes on the second Pi, and the debugging tree if
any hat stops probing. §C questions still open.*

*Run once per hat, hat OFF any Pi, unpowered. Multimeter: continuity (beep)
and resistance modes. Expected values derive from the AOS KiCad layout
netlist (`aos-rpi-zero-spe.kicad_pcb`) and the ADIN1110 datasheet p.9 —
see DESIGN.md §AOS hat for the full fact table.*

Header pin numbering: standard Pi 2×20, pin 1 = square pad / nearest the
corner marked on silk. "3.3V" below = header pin 1; "GND" = header pin 6.

## A. Visual (photo each hat for the record)

- [ ] SPI_CFG0 solder jumper (back side, mid-left): **bridged** (solder blob
      joining both pads). Hat #1 appears bridged in the 2026-08-09 photo.
- [ ] SPI_CFG1 solder jumper (back side, lower area): **bridged**.
- [ ] SWPD, TX2P4, MS_SEL jumpers: **open** (bare pads, no solder bridge).
- [ ] LED-0 / LED-1 / LED-LINK / LED-PWR trace jumpers (back, left edge):
      **intact** (uncut).
- [ ] ADIN1110 date code (front, on chip): record it. Hat #1 = `#2204`.
      Early silicon watch item — see DESIGN.md.

## B. Meter checks (per hat)

Positive checks (definite expected values — these catch a wrong or
mirrored header better than beeps to chip pins):

| # | Probe A | Probe B | Expect | Confirms |
|---|---|---|---|---|
| 1 | header 24 | 3.3V (header 1) | ≈ 4.7 kΩ | CS on CE0 (R24 22Ω + R7 4.7k pull-up) |
| 2 | header 11 | 3.3V | ≈ 100 kΩ | RESET on GPIO17 (R29 + R28 100k) |
| 3 | header 11 | GND (header 6), **S1 held down** | ≈ 22 Ω | reset button + R29 chain |
| 4 | header 21 | 3.3V | ≈ 4.7 kΩ | MISO→SDO/SPI_CFG0→bridged jumper→R16→3.3V. **Open here = CFG0 jumper NOT bridged** |
| 5 | header 19 | 3.3V | open / >1 MΩ | MOSI has no pull (distinguishes from #4) |
| 6 | header 15 | 3.3V | open / >1 MΩ | INT_N has **no** board pull-up (why the overlay adds one) |
| 7 | header 15 | GND | open | INT not shorted |
| 8 | header 2 (5V) | 3.3V, then GND | open both | hat is 3.3V-only; 5V unused |
| 9 | SPI_CFG1 jumper pads (back) | across | ≈ 0 Ω | CFG1 bridged |
| 10 | J1 circuit 1 | J1 circuit 2 | ≈ 0 Ω | transformer secondary continuity (winding) |

Notes:
- #10 means DA− and DA+ are DC-indistinguishable at J1 — identify circuit 1
  by the **silk triangle** beside the connector. Layout says circuit 1 = DA−,
  circuit 2 = DA+.
- Bench pair: wire **straight**, ckt 1 ↔ ckt 1 and ckt 2 ↔ ckt 2, and pair
  polarity is a non-issue regardless of PHY auto-correction.
- If any check misses, STOP and report the numbers — don't re-solder on a
  hypothesis (SPEC discipline: one variable at a time).

## C. Open items to resolve while the hats are in hand

- [ ] The soldered wire/pin at J1's edge on hat #1 (front, visible in photo):
      connector leg, or a bodge? What does it touch?
- [ ] Two bare exposed-copper rectangles, top of back side, with solder
      residue: alternate pair-terminal footprint, or removed part?
- [ ] Hat #2: same build state as hat #1? (jumpers, chip date code)

## D. Draft note to Julian / AOS (send when convenient)

> Subject: aos-rpi-zero-spe — INT_N pull-up missing (rev on hand)
>
> While bringing the board up against the mainline Linux adin1110 driver we
> traced the layout netlist and found INT_N (ADIN1110 pin 25 → R8 → Pi
> GPIO22) has no pull-up. The datasheet (p.9, pin 25) specifies INT_N as
> open-drain, active-low, requiring a 1.5 kΩ pull-up to VDDIO. R10 (1.5 kΩ)
> is on TEST1 — which the datasheet does require, so R10 is correct; the
> INT_N pull-up is just absent. We're working around it with the Pi's
> internal ~50 kΩ pull-up on GPIO22; suggest adding a 1.5 kΩ INT_N→VDDIO
> pull-up in the next rev. Happy to share our device-tree overlay and
> bring-up notes.
