#!/usr/bin/env python3
# flash_ae3.py -- headless OpenMV AE3 firmware flash from a Pi (S7 spike).
#
# Ladder (each rung fails loudly with a recovery hint):
#   preflight -> enter bootloader (mpremote: machine.bootloader()) ->
#   wait for DFU device 37c5:96e3 -> dfu-util alt HP [+ HE] -> reset ->
#   wait for CDC re-enumeration -> verify os.uname() embeds the expected
#   OpenMV git hash -> PASS/FAIL.
#
# Protocol facts (sources -- openmv.git @ master 2026-08-11, micropython.git):
#   * bootloader runs FIRST on every boot as USB DFU 37C5:96E3, jumps to the
#     app after ~1 s USB wait + 1.5 s DFU window (boot/src/common/main.c,
#     boards/OPENMV_AE3/boot_config.h:42-45)
#   * machine.bootloader() writes magic 0xB00710AD to 0x200FFFFC and resets;
#     the bootloader then stays in DFU until reset (ports/alif/boards/
#     OPENMV_AE3/board.c:107, boot main.c:62)
#   * DFU alt partitions by name: BOOT HP HE ROMFS1 TOC RWFS ROMFS0 RECOVERY
#     (boot_config.h:112). This tool NEVER writes BOOT -- the DFU window
#     survives any bad app flash, so a power cycle always recovers.
#   * os.uname().version embeds "OpenMV <sha10>; MicroPython <sha10>"
#     (verified in release firmware_M55_HP.bin strings).
#
# Never run while the t1l-sender service owns the AE3 USB port (preflight
# refuses). See README.md for setup (dfu-util, udev rule) and demo commands.

import argparse
import glob
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DFU_VID = "37c5"
DFU_PID = "96e3"
CDC_GLOB = "/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*"
SYSFS_USB = "/sys/bus/usb/devices"
UNAME_RE = re.compile(r"OpenMV ([0-9a-f]{8,12}); MicroPython ([0-9a-f]{8,12})")
MIN_FW_SIZE = 256 * 1024   # smaller than this is not a plausible HP app

def log(msg):
    print(f"[flash_ae3] {msg}", flush=True)


def die(msg, hint=None, code=1):
    print(f"[flash_ae3] FAIL: {msg}", file=sys.stderr, flush=True)
    if hint:
        print(f"[flash_ae3] hint: {hint}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------- pure helpers

def parse_uname_hashes(text):
    """Extract (openmv_sha, micropython_sha) from uname output, or None."""
    m = UNAME_RE.search(text)
    return (m.group(1), m.group(2)) if m else None


def read_manifest_sha(manifest_text):
    """Pull openmv_sha out of a build MANIFEST.txt (build_ae3.sh format)."""
    for line in manifest_text.splitlines():
        if line.startswith("openmv_sha:"):
            return line.split(":", 1)[1].strip()
    return None


def dfu_cmd(alt, path, reset=False):
    """Build the dfu-util argv for one partition download."""
    cmd = ["dfu-util", "-d", f"{DFU_VID}:{DFU_PID}", "-a", alt, "-D", str(path)]
    if reset:
        cmd.append("-R")
    return cmd


def sysfs_has_device(vid, pid, root=SYSFS_USB):
    """True if a USB device vid:pid is enumerated (stdlib, no lsusb dep)."""
    for dev in Path(root).glob("*"):
        try:
            got_vid = (dev / "idVendor").read_text().strip()
            got_pid = (dev / "idProduct").read_text().strip()
        except OSError:
            continue
        if got_vid == vid and got_pid == pid:
            return True
    return False


# ------------------------------------------------------------- device actions

def wait_for(desc, pred, timeout_s, interval_s=0.5):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval_s)
    return False


def find_cdc():
    return sorted(glob.glob(CDC_GLOB))


def run(cmd, dry_run=False, check=True, capture=False, timeout=120):
    log(("DRY-RUN: " if dry_run else "run: ") + " ".join(map(str, cmd)))
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return subprocess.run(cmd, check=check, timeout=timeout,
                          capture_output=capture, text=True)


def preflight(args, mpremote):
    if shutil.which("dfu-util") is None:
        die("dfu-util not installed", "sudo apt install dfu-util")
    if mpremote is None and not args.recover:
        die("mpremote not found", "pipx/pip install mpremote (expected at ~/.local/bin)")
    for p in [args.hp] + ([args.he] if args.he else []):
        if not p.is_file() or p.stat().st_size < MIN_FW_SIZE:
            die(f"firmware file missing or implausibly small: {p}",
                "fetch_firmware.sh <tag>, or scp a build from the Mac")
    # The stream sender owns the AE3 USB port when active -- refuse.
    if shutil.which("systemctl"):
        r = subprocess.run(["systemctl", "is-active", "--quiet", "t1l-sender"])
        if r.returncode == 0:
            die("t1l-sender service is ACTIVE and owns the AE3 USB port",
                "this is the live demo fixture -- do not flash; "
                "if intentional: sudo systemctl stop t1l-sender")


def enter_bootloader(mpremote, dev, dry_run):
    # mpremote exits non-zero when the board drops the USB connection on
    # reset -- that is the expected success signature here.
    cmd = [mpremote, "connect", dev, "exec",
           "import machine; machine.bootloader()"]
    try:
        run(cmd, dry_run=dry_run, check=False, timeout=20)
    except subprocess.TimeoutExpired:
        log("mpremote hung entering bootloader (tolerated; checking DFU)")


def recover_power_cycle(args, dry_run):
    """uhubctl power-cycle so the boot-time 1.5 s DFU window can be caught."""
    if shutil.which("uhubctl") is None:
        die("uhubctl not installed",
            "sudo apt install uhubctl -- or power-cycle the board by hand")
    run(["uhubctl", "-l", args.hub_location, "-p", args.hub_port,
         "-a", "cycle", "-d", "2"], dry_run=dry_run, check=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Headless AE3 flash over the OpenMV DFU bootloader")
    ap.add_argument("--hp", type=Path, required=True, help="firmware_M55_HP.bin")
    ap.add_argument("--he", type=Path, default=None, help="firmware_M55_HE.bin (recommended: avoids core version skew)")
    ap.add_argument("--expect", default=None, help="expected OpenMV sha10 in uname (default: read MANIFEST.txt next to --hp)")
    ap.add_argument("--device", default=None, help="CDC device (default: by-id glob, never ACM numbers)")
    ap.add_argument("--recover", action="store_true", help="power-cycle via uhubctl and catch the 1.5 s boot DFU window instead of mpremote entry")
    ap.add_argument("--hub-location", default="1-1", help="uhubctl -l value (verify with uhubctl on the Pi)")
    ap.add_argument("--hub-port", default="1", help="uhubctl -p value")
    ap.add_argument("--timeout", type=int, default=30, help="seconds to wait for each enumeration step")
    ap.add_argument("--dry-run", action="store_true", help="print commands, touch nothing")
    args = ap.parse_args(argv)

    mpremote = shutil.which("mpremote") or (
        str(Path.home() / ".local/bin/mpremote")
        if (Path.home() / ".local/bin/mpremote").is_file() else None)

    if not args.dry_run:
        preflight(args, mpremote)

    # Resolve the verification target before touching the board.
    expect = args.expect
    if expect is None:
        manifest = args.hp.parent / "MANIFEST.txt"
        if manifest.is_file():
            expect = read_manifest_sha(manifest.read_text())
    if expect:
        log(f"will verify uname against OpenMV sha: {expect}")
    else:
        log("no --expect and no MANIFEST.txt -- will print uname, not verify")

    # Rung 1: get the board into the bootloader.
    if args.recover:
        recover_power_cycle(args, args.dry_run)
    else:
        cdc = args.device or (find_cdc() or [None])[0]
        if cdc is None and not args.dry_run:
            die("no AE3 CDC device found", f"glob: {CDC_GLOB}; is the board on USB? try --recover")
        enter_bootloader(mpremote, cdc or "<cdc>", args.dry_run)

    # Rung 2: DFU device appears.
    if not args.dry_run:
        if not wait_for("DFU", lambda: sysfs_has_device(DFU_VID, DFU_PID),
                        args.timeout, 0.2):
            die(f"DFU device {DFU_VID}:{DFU_PID} never enumerated",
                "power-cycle and retry with --recover (bootloader window is 1.5 s)")
        log("DFU device present")

    # Rung 3: download partitions. HP last-with-reset when no HE given.
    parts = [("HP", args.hp)] + ([("HE", args.he)] if args.he else [])
    for i, (alt, path) in enumerate(parts):
        last = i == len(parts) - 1
        run(dfu_cmd(alt, path, reset=last), dry_run=args.dry_run, check=True,
            timeout=300)

    # Rung 4: board comes back as CDC.
    if not args.dry_run:
        if not wait_for("CDC", lambda: len(find_cdc()) > 0, args.timeout, 0.5):
            die("board did not re-enumerate as CDC after flash",
                "power-cycle (--recover or by hand); bootloader is untouched, "
                "board is recoverable")
        time.sleep(2)   # let the port settle before opening it

    # Rung 5: verify the running build. Trust the artifact (uname text).
    cdc = args.device or (find_cdc() or ["<cdc>"])[0]
    r = run([mpremote or "mpremote", "connect", cdc, "exec",
             "import os; print(os.uname().version)"],
            dry_run=args.dry_run, check=False, capture=True, timeout=30)
    if args.dry_run:
        log("DRY-RUN complete")
        return 0
    uname = (r.stdout or "").strip()
    log(f"uname.version: {uname or '<empty>'}")
    hashes = parse_uname_hashes(uname)
    if hashes is None:
        die("could not parse OpenMV/MicroPython hashes from uname",
            "board may be running but odd -- check manually with mpremote")
    if expect:
        if hashes[0] != expect:
            die(f"hash mismatch: running OpenMV {hashes[0]}, expected {expect}")
        log(f"PASS: board is running OpenMV {hashes[0]} (matches expected)")
    else:
        log(f"DONE (unverified): board runs OpenMV {hashes[0]}, MicroPython {hashes[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
