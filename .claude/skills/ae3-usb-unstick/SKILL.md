---
name: ae3-usb-unstick
description: Recover the AE3 when it falls off nereus000's USB bus (enumeration fails with "device not accepting address, error -71", /dev/serial/by-id gone). Use when uhubctl cycles do NOT bring it back — the fix is `sudo reboot` on the Pi, because the Pi 5 root hub never actually cuts VBUS. Also covers the safe re-entry sequence around a running S16 bridge.
---

# AE3 USB unstick — reboot the Pi, not the port

OWNER: **Nick**. Found live 2026-08-15 (S16 chain bring-up). The AE3
wedged so hard its USB stopped answering enumeration:

```
usb 3-1: Device not responding to setup address.
usb 3-1: device not accepting address NN, error -71
usb usb3-port1: unable to enumerate USB device
```

and `/dev/serial/by-id/` disappeared entirely. **Four uhubctl cycles —
including a 10-second `-a off` window — did not recover it.**

## Why uhubctl doesn't work for this class

The Pi 5 root hub advertises per-port power switching (`ppps`) and
uhubctl reports "power off", but **VBUS is not actually cut** — measured:
an AE3 bridge session survived a Pi `sudo reboot` still running (its HP
service resumed pumping mid-write when the new xhci came up). uhubctl's
port cycles clear *protocol-level* wedges (the documented D15 ladder,
which is why they worked historically) but cannot power-cycle the board.
When the device side of the USB stack itself is wedged (stuck mid-write,
setup-address failures), a port cycle does nothing.

## The fix

```bash
ssh pi@nereus000 'sudo -n reboot'
```

Wait for the Pi to return (tailnet, ~40 s), then:

```bash
ssh pi@nereus000 'ls /dev/serial/by-id/'
```

The fresh xhci host controller re-enumerates the board. Note the board
itself may NOT have rebooted (VBUS persisted): a wedged-but-alive
service can resume where it blocked. If the S16 bridge was running, its
quiet-exit (30 s of VCP silence) or phase-1 timeout (10 min) returns
the board to the REPL on its own — **wait for that; do not point
mpremote at a possibly-running bridge** (kbd_intr is disabled; the
attach bytes read as link traffic, and racing a reset against it is
exactly what caused the original wedge).

Bench rules still apply: `sudo poweroff`/`reboot`, never pull power
(SPEC §Safety); eth1's ADIN driver is kernel-locked — a plain reboot is
safe, but if apt upgraded the kernel see `pi-kernel-upgrade`.

## Escalation ladder (in order)

1. Board enumerated but service wedged → documented D15 ladder:
   `sudo uhubctl -l 3 -p 1 -a cycle -d 3` + warm `mpremote reset`
   (protocol-level wedges; cold boot does not run main.py).
2. Board OFF the bus (error -71, by-id gone) → `sudo reboot` the Pi
   (this skill).
3. Still dead after a Pi reboot → physical unplug/replug at the bench
   (needs hands — the only true power cycle the fixture has).
