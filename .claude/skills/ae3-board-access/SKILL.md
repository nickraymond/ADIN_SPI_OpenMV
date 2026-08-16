---
name: ae3-board-access
description: Drive the AE3 over USB with mpremote when the bench software exists — read/write /flash, restore the S6 fixture, run a probe. Use BEFORE any `mpremote` command against nereus000's AE3, and whenever mpremote reports "could not enter raw repl", "may be in use by another program", or a read-back that does not match the file you wrote. Covers the bridge-launcher lifecycle deadlock, the no-retry rule, and the CRLF trap that makes a correct restore look wrong.
---

# AE3 board access — one command, then hands off the port

OWNER: **Nick**. Earned live 2026-08-16 (S18 bite B), where these rules
cost most of a session to learn the hard way. `ae3-usb-unstick` covers
the board falling OFF the bus; this covers everything you do while it is
ON it.

## The one-paragraph model

`/flash/main.py` is usually the **bridge launcher** (that is what
`demo_up.sh` stages). `mpremote` enters the raw REPL via a **soft
reset** — which runs `main.py` — so **every mpremote command can start a
bridge**. That bridge disables `kbd_intr`, holds the VCP, and only exits
after **30 s of no VCP receive**. Worse, the attach bytes are exactly
what its phase 1 is waiting for, so *touching the port extends the very
wait you are in*.

## Rules

1. **NEVER poll or retry to wait for the board.** An
   `until mpremote ...; do sleep 3; done` loop resets the quiet-exit
   timer every iteration and will hang forever. I did this twice in one
   session. Wait with **zero port contact** — a single timed wait, then
   one attempt.
2. **One `mpremote` operation per invocation. No `+` chaining.** A
   `cp ... + cat ...` chain cannot work: the first REPL entry soft-resets
   into the bridge launcher, and the second operation meets a running
   bridge. Do the write; wait again; do the read-back separately.
3. **Serialise everything.** Never let a second command (a status check,
   another ssh) touch the port while one is in flight. Most "port busy"
   failures are self-inflicted collisions.
4. **60+ s of silence before each attempt.** Longer than the 30 s
   quiet-exit, because the clock restarts on any contact.

## Three misleading signals, and what they actually mean

| What you see | What it usually means |
|---|---|
| `failed to access … (it may be in use by another program)` | **The device is ABSENT.** Check `ls /dev/serial/by-id/` FIRST; if it is gone, this is `ae3-usb-unstick`, not a busy port. |
| `could not enter raw repl` | A bridge is running and holding the VCP. Wait 60 s untouched, retry **once**. |
| A read-back of a `.py` that is **binary junk** (`\x86\xdd`, `\xfe\x80`, `\xbe\x9c…`) | You are reading the **BM protocol stream**, not the file — a bridge is transmitting. Same fix as above. |

## The CRLF trap — a correct restore that looks wrong

**`mpremote cat` converts LF to CRLF.** A read-back of an N-line file is
**exactly N bytes larger** than the file, so `cmp` and `sha256` both
report a mismatch that is not real. Measured: `firmware/ae3_usb/main.py`
is 5,581 B / 134 lines and reads back as 5,715 B (= 5,581 + 134).

Normalise before comparing, and **never** hash the mpremote stream
against the file's own hash:

```bash
python3 -c "d=open('/tmp/readback.py','rb').read(); f=open('/tmp/fixture_main.py','rb').read(); print('match:', d.replace(b'\r\n', b'\n') == f)"
```

## Restoring the S6 fixture (the standing session-end rule)

Repo source: `firmware/ae3_usb/main.py`. Copy it to the Pi first — `/tmp`
does not survive the reboots this bench does.

```bash
scp firmware/ae3_usb/main.py pi@nereus000:/tmp/fixture_main.py
```

Then, with the port untouched for 60+ s, **one** operation:

```bash
ssh pi@nereus000 'export PATH=$PATH:~/.local/bin; mpremote connect /dev/serial/by-id/usb-OpenMV_OpenMV_Camera_0829c14000000000-if00 cp /tmp/fixture_main.py :/flash/main.py'
```

Wait again, then read back separately and compare **normalised**.
`cp` returning `rc=0` is **not** proof the file is right — measured: an
`rc=0` write sat next to a read-back that had to be normalised before it
agreed. Trust the bytes.

## Running a probe on the board

**A probe cannot run while `/flash/main.py` is the bridge launcher** —
`mpremote run` soft-resets, main.py starts a bridge, and the bridge eats
the port. Stage a neutral `main.py` (the S6 fixture, or an empty file)
**before** the probe session, and restore afterwards. An S18 probe was
written, attempted three times, and never executed for exactly this
reason.

Also: stop `bm-light` / `bm-telemetry` first (they hold the CDC leg), and
put probe scripts in `~/bm_bench/`, **never `/tmp`** — the unstick ladder
reboots the Pi and wipes it.

## Budget, and when to stop

Board wrangling is not progress. **If a flash write or read-back has not
worked in three properly-serialised attempts, stop**, record the exact
state in DEV_LOG, and hand Nick the single command. An S18 session spent
its last hours on fixture-restore hygiene that delivered nothing the
sprint needed.
