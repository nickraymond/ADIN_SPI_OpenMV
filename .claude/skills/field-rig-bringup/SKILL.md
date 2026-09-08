---
name: field-rig-bringup
description: Build a nereus002-class field camera rig from a blank SD card — Pi Zero 2 W + LiFePO4wered/Pi+ + IMX708 on CSI + an OpenMV AE3 and N6 on USB. Use when standing up a NEW field rig, when replicating nereus002, after swapping an AE3/N6 board into a rig, or when a field rig is up but a camera, the network, or the power path is misbehaving. Carries the five failure modes that each cost this bench a session.
---

# Field camera rig bring-up

OWNER: **Nick**. First rig: **nereus002**, built S29 (2026-09-06/07).
Hardware: Raspberry Pi Zero 2 W, Debian 13 trixie · **LiFePO4wered/Pi+**
with an 18650 LiFePO4 cell · **IMX708** (wide, autofocus) on the CSI
ribbon · **OpenMV AE3 + N6** on USB through an unpowered Terminus hub.

**Work in Nick's priority order and do not jump ahead:**
**(1) stable bench — wifi + power · (2) cameras online and stable ·
(3) features.** Every hour S29 lost was lost by having features running
on an unstable bench, where a failure has three plausible causes.

---

## The five traps — read these before touching anything

Each was root-caused on hardware and each presents as something else.

| Symptom you will actually see | Real cause | Fix |
|---|---|---|
| Pi is **up but off the network**; ssh dead, Tailscale dead | WiFi **`power_save` on** — the radio dozes, negotiates the floor rate (measured **rx 1.0 Mb/s** vs tx 72.2), and the AP ages out the association | `powersave` unit, §3 |
| "could not enter raw repl" · board vanishes from `lsusb` · board "wedges" at random | **usb-storage MSC livelock.** Both boards expose `/flash` as a disk; a udev probe during board activity fails a SCSI read → USB device reset → re-probe → **~46 resets/min** | `usb-msc` unit, §4 |
| Rig powers off ~5 min after boot with no explanation | **`lifepo4wered-daemon` is not running.** `PI_BOOT_TO` (300 s) is a *boot watchdog*: the Pi+ waits that long for the daemon to signal a good boot, then assumes the boot failed and cuts power | install the daemon, §2 |
| `systemctl reboot` never comes back; needs a physical button press | The Pi+ cuts power `SHDN_DELAY` after UART TX drops, and a Zero 2 W can miss that window. A reboot becomes a **power-off** | **never reboot** — `pi/field/power_cycle.py`, §2 |
| `mpremote: command not found` from a service or recipe | Debian 13 **PEP 668** — pip installs went to a venv at `~/mpv` | launchers pick the interpreter, §5 |

Two smaller ones that still cost time:

- **`iw`, `ip`, `iwconfig` live in `/sbin`**, which is not on the `pi`
  user's PATH. Resolve tools by absolute path, never bare.
- **USB descriptors lie about which board is which.** The names read
  backwards from the guess: the **N6** enumerates as
  `usb-MicroPython_Pyboard_…`, the **AE3** as `usb-OpenMV_OpenMV_Camera_…`.
  **Ask the board** (§6).

---

## 1. Base OS, network, clock

1. Flash Debian 13 (trixie) 64-bit. First boot on the LAN.
2. Tailscale: follow the **`pi-tailscale-setup`** skill. Nick clicks the
   auth URL; the agent never handles credentials. Register the hostname
   (`nereus00N`) before going further — everything below is done over it.
3. **Timezone LA, timestamps UTC.** Nick's standing rule: the OS runs
   local so `ls` is readable at the bench, but *every value written to a
   file is ISO-8601 UTC*.
   ```bash
   sudo timedatectl set-timezone America/Los_Angeles
   ```
   Verify both halves: `date` (local) and `date -u` (UTC) must agree on
   the instant, and the Pi+ RTC must match (§2). nereus002 holds
   **1 s** of skew between `date +%s` and `lifepo4wered-cli get rtc_time`.
4. **A narrow passwordless-sudo grant** so an agent can install and
   restart units without holding the password. Nick installs this with
   `visudo -f /etc/sudoers.d/020_field-nopasswd`:
   ```
   pi ALL=(root) NOPASSWD: /usr/bin/systemctl, /usr/bin/apt-get
   ```
   **Verify it after `sudo -k`**, not before — S29 spent real time
   believing sudo was passwordless when calls were actually riding Nick's
   cached credential (`timestamp_type=global`). The grant is deliberately
   narrow: it does NOT cover arbitrary `bash`, so the installer in §3
   still needs Nick for its one root step.

## 2. Power — LiFePO4wered/Pi+ (do this SECOND, before cameras)

Install the vendor CLI/daemon, then set the four registers that matter.
Values below are what nereus002 runs; `pi/field/lifepo4.py` is the
vendored controller (copied from Nick's `nereus-vision-dev`
`system_agent/lifepo4wered_controller.py` — **copied, never imported**,
so that deployed project is neither modified nor depended on).

**Install `lifepo4wered-daemon` FIRST and confirm it is running** — this
is what answers the boot watchdog. Without it the rig powers off ~5 min
after every boot and looks like a hardware fault:

```bash
systemctl is-active lifepo4wered-daemon    # must be "active"
```

Then confirm the registers. These are nereus002's live values, read
2026-09-07 — all four are the vendor defaults except `VBAT_SHDN`, so a
fresh Pi+ mostly needs checking, not changing:

| Register | Value | Why |
|---|---|---|
| `PI_BOOT_TO` (0x21) | `300` | boot watchdog — **leave it**; the daemon satisfies it. Zeroing it hides a failed-boot loop instead of fixing it |
| `AUTO_SHDN_TIME` (0x1E) | `65535` (0xFFFF) | disabled — no timed shutdown |
| `AUTO_BOOT` | `2` (VBAT_SMART) | comes back on its own when the cell recovers |
| `VBAT_SHDN` | `2950` mV | clean cutoff; the rig stopped at 2952 mV — accurate to 2 mV |
| `SHDN_DELAY` | `96` (≈12 s) | seconds from UART TX drop to power cut. Nick raised this from the default |

Writes need `CFG_WRITE` (0x25) = magic `0x46` to persist. I2C addr
`0x43`; unlock byte = `(0x43<<1) XOR 0xC9 XOR REG`.

**Set the RTC and verify the pair.** `pi/field/lifepo4.py verify_clock_pair`
checks the Pi clock and the Pi+ RTC agree; a wake programmed against a
wrong RTC is a rig that does not come back.

**Reboot is `pi/field/power_cycle.py`, never `systemctl reboot`.** It
programs `RTC_WAKE_TIME`, verifies the wake is in the future and
plausible, then shuts down. It also **refuses** when
`vbat < VBAT_SHDN + 250 mV` and there is no VIN — a cycle on a flat cell
may not come back. Exit criterion for a new rig: **5 consecutive cycles,
each returning with both boards on the bus.**

```bash
python3 pi/field/power_cycle.py --check     # read-only: prints the refusal or the plan
```

### Measured power budget (nereus002, 2026-09-07)

| State | Load | CPU |
|---|---|---|
| Idle, no streams | **2.42 W** | 39 °C |
| Two cameras at HD 15 fps | **3.48 W** | 46 °C |
| Peak (composite/HDR runs) | **4.18 W** | 46.7 °C |

`throttled` stayed `0x0` throughout — the Zero 2 W is neither thermally
nor voltage limited on this load. A cell taken from ~3.20 V to the
2950 mV cutoff ran **78 min** at a ~2.7 W mean; from a full charge Nick
measures **~3 h**, which is the number to plan field sessions against.

**Voltage sag goes non-linear near the end.** Idle→HD raised power 1.44×
but the sag rate **6.7×** (4.4 → 29.3 mV/min). That is internal
resistance on a nearly-empty cell: the last few percent of the pack
cannot deliver a camera load. Do not plan to the last 100 mV.

**After the cutoff the Pi+ keeps retrying** — nereus002 attempted 6
reboots over the following 6 h, backing off; the first held 4 min, every
one after died in ~35 s. A field unit that "won't stay on" is doing
this, not failing.

## 3. Bench-stability units (install all three, enabled at boot)

```bash
sudo pi/install_stream_service.sh powersave   # wifi power_save off      (trap 1)
sudo pi/install_stream_service.sh usb-msc     # usb-storage off both boards (trap 2)
sudo pi/install_stream_service.sh workbench   # the recipe page on :8088
sudo pi/install_stream_service.sh power-log   # 10 s power/thermal CSV (optional)
```

These are **fixtures, not demos** — each exists because its absence cost
a session, so all four are enabled at boot. Verify, do not assume:

```bash
systemctl is-active wifi-powersave-off field-usb-msc-off workbench
/sbin/iw dev wlan0 get power_save                   # must say "Power save: off"
ls /sys/bus/usb/drivers/usb-storage/ | grep -c ':'  # must be 0
lsusb | grep 37c5   # 37c5:16e3 = AE3, 37c5:1206 = N6 -- both present
```

## 4. The usb-storage rule in detail

`pi/field/usb_msc_off.sh` installs `99-openmv-no-msc.rules` and unbinds
anything already attached. It matches **VID 37c5 and interface CLASS
08/06/50**, not a product ID, so a firmware update that reorders
interfaces cannot silently re-enable the disk. It also drops in the S7
DFU rule so firmware can be flashed without root.

This is the single highest-value item in this document. Before it, the
AE3 and N6 fell off the bus repeatedly on healthy hardware and every
failure looked like a different bug.

## 5. mpremote and the interpreter gap

Debian 13 refuses `pip install` into the system Python (PEP 668).
mpremote lives in a venv at **`~/mpv`**. Anything that shells out to it —
a systemd unit, a workbench recipe — must pick the interpreter, not
assume `python3`. Copy the pattern in `pi/field/run_field_stream.sh`:

```bash
for py in "${FIELD_PYTHON:-}" "$HOME/mpv/bin/python" "$(command -v python3)"; do
  [ -n "$py" ] && [ -x "$py" ] || continue
  "$py" -c "import mpremote, serial" >/dev/null 2>&1 && { echo "$py"; break; }
done
```

It must fail **loudly** and name the fix, never fall through to a
python that cannot attach the boards — a page showing one working
camera and two dead panels is exactly the plausible-but-wrong artifact
this repo keeps paying for (CLAUDE.md rule 6).

**Do not "fix" this with `pip install --break-system-packages`.** A venv
is the correct answer and the permission classifier blocks the other one
anyway.

## 6. Cameras — identity, then proof

**Never address a board by `by_id`.** The boards get swapped between rigs
(nereus002's *are* nereus000's old boards), so a by-id string identifies
a chip, not a role, and both hosts' configs can name the same string.
`pi/field/discover.py` probes every `/dev/serial/by-id/*-if00` and asks:

```python
import omv; omv.board_type()      # -> "AE3" / "N6"
```

**There is deliberately no cache** — a cached role→port map is stale in
exactly the swap scenario it would exist for.

```bash
cd ~/ADIN_SPI_OpenMV && /home/pi/mpv/bin/python -c "
import sys; sys.path.insert(0,'pi/field')
import discover
f,p = discover.discover()
for k,v in f.items(): print(k, '->', v['port'].split('/')[-1], '|', v['machine'])
for x in p: print('PROBLEM:', x)"
```

Expect exactly two roles. `OpenMV-AE3 with AE302F80F55D5AE` and
`OpenMV N6 with STM32N657X0` are nereus002's; a new rig will report its
own IDs — record them in SPEC.

### Then prove capture, not enumeration

Enumerating is not working. Start the streams card and pull real bytes:

```bash
curl -s -X POST http://nereus00N:8088/api/start \
  -H 'Content-Type: application/json' -d '{"name":"field-streams","params":{}}'
# wait for state "live", then:
curl -s http://nereus00N:8090/api/sources          # fps + Mb/s per camera
curl -s -o /tmp/c1.jpg http://nereus00N:8090/s/1/frame.jpg
```

A frame must have SOI `ffd8` / EOI `ffd9`, plausible size, and the
dimensions you asked for — **and you must look at it.** A stream that
reports fps while delivering black frames has happened here.

Reference numbers from nereus002, VGA colour @15: IMX708 15.0 fps /
2.93 Mb/s · AE3 **11.4** fps / 1.41 Mb/s · N6 15.8 fps / 2.08 Mb/s. The
AE3 is the slow one by a wide margin at colour (HD colour is **2.6 fps**);
it does far better at mono (5.3 fps HD).

## 7. Board etiquette (unchanged from the bench rules)

- **One owner per board port, ever.** Check
  `http://nereus00N:8088/api/runner` and `/api/preflight` before any
  board contact; stop demos **from the page**, never by killing the
  process. Two processes on one port wedges the board.
- **The AE3 needs ~35 s of port silence after any stream stops.** The
  workbench enforces this settle. A quick reattach lands in a raw-repl
  refusal.
- Before any manual `mpremote` against the AE3, use the
  **`ae3-board-access`** skill. One operation per invocation, no `+`
  chaining, never poll to wait for a board.

## 8. Bring-up done — the checklist

Do not call a rig ready until every line is *observed*:

- [ ] Tailscale reachable by hostname; survives a power cycle
- [ ] `iw dev wlan0 get power_save` → **off**; unit enabled at boot
- [ ] `lifepo4wered-daemon` active, and the rig is still up 30+ min after boot
- [ ] Pi clock and Pi+ RTC agree; files carry ISO-8601 **UTC**
- [ ] `power_cycle.py` returns the rig **5/5**, both boards present
- [ ] usb-storage bound to **neither** board; unit enabled at boot
- [ ] `discover.py` reports exactly two roles, AE3 and N6
- [ ] A real JPEG pulled from **all three** cameras, and eyeballed
- [ ] Workbench answers on `:8088` after a cold boot

## Known-open on this rig class

- **The AE3 refuses the REPL where the N6 never does** — three failures
  in one S29 session on identical code and the same PAG7936 sensor, zero
  for the N6. Cleared only by a full power cut. Cause unknown; this is
  the top open issue for a field rig, because "power cycle the camera" is
  not an acceptable field recovery.
- **wlan0 AP mode is not set up.** The recipe is field-proven in
  `nereus-vision-dev/device/docs/nereus_wlan0_ap_setup.md` (NetworkManager
  AP on 10.42.0.1). **Unsettled trade-off: a rig in AP mode is no longer
  a wifi client, so Tailscale access is lost** — this likely wants AP as
  a second interface or a toggle, not a replacement.
- **No H.264 anywhere in the shipping rig** — all three cameras stream
  MJPEG. **The rest of this entry was corrected by S31 (2026-09-07), which
  measured it on this rig; do not act on the old version.**
  - The N6's encoder is a Hantro **VC8000NanoE**, and it is **already the
    N6's JPEG encoder** (`ports/stm32/stm_jpeg.c:287-294`). So the N6's
    ~5× JPEG advantage over the AE3 is this silicon, and the AE3's figure
    is *software* JPEG (`OMV_JPEG_CODEC_ENABLE (0)`).
  - MicroPython bindings **exist**, in upstream PR openmv/openmv#3247 —
    but it is still a **DRAFT**, unmerged, and there is **no v5.1.0
    release** (it is a milestone label with no due date). So H.264 today
    still means a non-release firmware. The policy question is live; what
    changed is that the code would be upstream's, not a fork of ours.
  - **The 4.0× is quality-dependent and does not survive "max quality".**
    Measured on the N6 at HD 1280×800, 30 fps: quality-matched H.264 is
    only **1.5×** smaller than MJPEG q90. The win is real at a quality
    ceiling — 3.2× at 32 Mbps, 6.1× at 16, 11.9× at 8 — because at a fine
    QP the encoder spends its bits coding **sensor noise**, which is
    uncorrelated frame to frame and so has no temporal redundancy to
    exploit. Proven: re-encoding one identical frame costs **43 bytes** a
    P-frame; live capture of a *static* scene costs **257,704**.
  - Two blockers if anyone tries to record it here: `mp4.py` cannot mux HD
    at 30 fps on this board (**13.7 fps**; the encoder alone does 50), and
    **this rig's N6 has no SD card** (`/sdcard` ENODEV, `/flash` 3 MB free),
    so video has to leave over USB.
  - Detail: `docs/N6_H264_FINDINGS.md`, `bench/n6_h264/`, decision **D49**.
