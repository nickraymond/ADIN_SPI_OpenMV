#!/usr/bin/env python3
# flash_ae3.py -- headless OpenMV AE3 firmware flash from a Pi (S7 spike).
#
# Ladder (each rung fails loudly with a recovery hint):
#   preflight (incl. MANIFEST sha256 check of the local bins) ->
#   enter bootloader (mpremote: machine.bootloader()) -> wait for DFU device
#   37c5:96e3 -> dfu-util download HP [+ HE] -> read each partition back
#   (dfu-util -U) + sha256 compare vs the flashed file -> boot (TOC read
#   with -R: post-verify USB reset -> bootloader jumps to the app) ->
#   wait for CDC re-enumeration -> sys.version parses, label cross-checked
#   -> PASS/FAIL.
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
#   * VERIFY IS BYTE-LEVEL (S8 stale-label find, 2026-08-11): the version
#     strings are labels, not build fingerprints. sys.version's "OpenMV <id>"
#     is git-describe output baked in at build time (openmv/micropython
#     py/makeversionhdr.py: describe --tags --dirty --always --abbrev=10 on
#     the openmv tree) -- a tagless CI checkout degrades it to a bare sha10,
#     and rebuilds at the same rev repeat it. omv.version_string() is the
#     static OMV_FIRMWARE_VERSION defines (protocol/omv_protocol.h), stuck
#     at the last release ("5.0.0") on dev builds. Label match can
#     false-pass -> the PASS verdict rides the readback hash instead.
#   * the bootloader implements DFU_UPLOAD (boot/src/common/dfu.c:92, and
#     CAN_UPLOAD in desc.c:42); AXI/MRAM partition reads are a plain memcpy
#     (boot/src/ports/alif/alif_flash.c:42). Writes round the image tail up
#     to the 16 B MRAM sector with buffer residue. Live 2026-08-11 (dfu-util
#     0.11): -Z does NOT bound the transfer -- uploads run to the
#     partition-end short frame regardless -- so the sha256 compare caps at
#     len(bin) itself. Upload-after-download in one DFU session works.
#   * boot-after-verify rung: ride -R on a tiny read of the 8 KB TOC
#     partition. The USB reset unmounts the device, the bootloader's
#     while(tud_mounted()) loop exits, and it jumps to the app
#     (boot/src/common/main.c) -- same mechanism as the old -R-on-download,
#     issued only AFTER verify passes. NOT dfu-util -e: live-tested
#     2026-08-11, -e only detaches runtime-mode devices and is a silent
#     no-op on a device already sitting in DFU. A non-zero dfu-util exit as
#     the device drops off the bus is tolerated (-R on downloads exited 251
#     live); CDC + sys.version are the success signals.
#   * sys.version reads "3.4.0; OpenMV <id>; MicroPython <id>"; sha10 on dev
#     builds, version tag on tagged releases -- verified live + in binaries
#     2026-08-11. (os.uname().version carries only the MicroPython id.)
#
# Never run while the t1l-sender service owns the AE3 USB port (preflight
# refuses). See README.md for setup (dfu-util, udev rule) and demo commands.

import argparse
import glob
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DFU_VID = "37c5"
DFU_PID = "96e3"
CDC_GLOB = "/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*"
SYSFS_USB = "/sys/bus/usb/devices"
UNAME_RE = re.compile(r"OpenMV ([^\s;]+); MicroPython ([^\s;]+)")
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
    """Extract (openmv_id, micropython_id) from uname output, or None.

    ids are sha10s on dev builds, version tags on tagged releases."""
    m = UNAME_RE.search(text)
    return (m.group(1), m.group(2)) if m else None


def read_manifest_sha(manifest_text):
    """Pull openmv_sha out of a build MANIFEST.txt (build_ae3.sh format)."""
    for line in manifest_text.splitlines():
        if line.startswith("openmv_sha:"):
            return line.split(":", 1)[1].strip()
    return None


SHA256_LINE_RE = re.compile(r"^([0-9a-f]{64})\s+\*?(\S+)$")


def read_manifest_sha256s(manifest_text):
    """{basename: sha256hex} from the sha256sum/shasum lines in MANIFEST.txt
    (both build_ae3.sh and fetch_firmware.sh write them)."""
    sums = {}
    for line in manifest_text.splitlines():
        m = SHA256_LINE_RE.match(line.strip())
        if m:
            sums[Path(m.group(2)).name] = m.group(1)
    return sums


def sha256_file(path, limit=None):
    """Stream-hash a file; limit caps the byte count (readbacks may carry
    MRAM sector-padding residue past the image tail -- see header facts)."""
    h = hashlib.sha256()
    remaining = limit
    with open(path, "rb") as f:
        while remaining is None or remaining > 0:
            chunk = f.read(65536 if remaining is None else min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return h.hexdigest()


def readback_matches(fw_path, rb_path, size):
    """True if the first size bytes of the readback equal the firmware file."""
    return sha256_file(fw_path) == sha256_file(rb_path, limit=size)


def dfu_cmd(alt, path):
    """dfu-util argv: download one partition. No -R here -- booting the app
    is a separate detach rung so verification can gate it."""
    return ["dfu-util", "-d", f"{DFU_VID}:{DFU_PID}", "-a", alt, "-D", str(path)]


def dfu_upload_cmd(alt, path):
    """dfu-util argv: read a partition back out (DFU_UPLOAD). Reads to the
    partition-end short frame (-Z does not bound the transfer -- live
    dfu-util 0.11 fact); the sha256 compare caps at len(bin) instead."""
    return ["dfu-util", "-d", f"{DFU_VID}:{DFU_PID}", "-a", alt, "-U", str(path)]


def dfu_boot_cmd(scratch):
    """dfu-util argv: boot the verified app -- tiny TOC-partition read with
    -R so the USB reset (-> bootloader jump) lands only after verification.
    (dfu-util -e is a silent no-op on DFU-mode devices; live 2026-08-11.)"""
    return ["dfu-util", "-d", f"{DFU_VID}:{DFU_PID}", "-a", "TOC",
            "-U", str(scratch), "-R"]


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
    # mpremote exits non-zero with an I/O-error traceback when the board
    # drops the USB connection on reset -- that is the expected success
    # signature here (observed live 2026-08-11), so output is captured
    # and discarded rather than splattering the console.
    cmd = [mpremote, "connect", dev, "exec",
           "import machine; machine.bootloader()"]
    try:
        run(cmd, dry_run=dry_run, check=False, capture=True, timeout=20)
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
    ap.add_argument("--expect", default=None, help="expected OpenMV id (sha10 or tag) in sys.version (default: read MANIFEST.txt next to --hp)")
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

    # Partition worklist, shared by download / readback / preflight rungs.
    parts = [("HP", args.hp)] + ([("HE", args.he)] if args.he else [])

    # Resolve the label cross-check target before touching the board.
    manifest = args.hp.parent / "MANIFEST.txt"
    manifest_text = manifest.read_text() if manifest.is_file() else ""
    expect = args.expect or (read_manifest_sha(manifest_text) or None)
    if expect:
        log(f"will cross-check sys.version against OpenMV id: {expect}")
    else:
        log("no --expect and no MANIFEST.txt openmv_sha -- label not cross-checked "
            "(readback hash is the verify either way)")

    # Guard the artifacts before flashing: MANIFEST sha256 lines catch a
    # corrupted or mixed-up copy of the bins onto the Pi.
    if not args.dry_run and manifest_text:
        sums = read_manifest_sha256s(manifest_text)
        for _, path in parts:
            want = sums.get(path.name)
            if want is None:
                continue
            got = sha256_file(path)
            if got != want:
                die(f"{path.name} sha256 {got[:12]}... does not match MANIFEST "
                    f"{want[:12]}...",
                    "the local file is not the build the MANIFEST describes -- "
                    "re-copy from the Mac or re-run fetch_firmware.sh")
            log(f"MANIFEST sha256 OK: {path.name}")

    # Rung 1: get the board into the bootloader.
    if args.recover:
        recover_power_cycle(args, args.dry_run)
    else:
        cdc = args.device or (find_cdc() or [None])[0]
        if cdc is None and not args.dry_run:
            die("no AE3 CDC device found", f"glob: {CDC_GLOB}; is the board on USB? try --recover")
        enter_bootloader(mpremote or "mpremote", cdc or "<cdc>", args.dry_run)

    # Rung 2: DFU device appears.
    if not args.dry_run:
        if not wait_for("DFU", lambda: sysfs_has_device(DFU_VID, DFU_PID),
                        args.timeout, 0.2):
            die(f"DFU device {DFU_VID}:{DFU_PID} never enumerated",
                "power-cycle and retry with --recover (bootloader window is 1.5 s)")
        log("DFU device present")

    # Rung 3: download partitions. No reset here -- the board boots only
    # after rung 3.5 has verified what landed on flash.
    for alt, path in parts:
        run(dfu_cmd(alt, path), dry_run=args.dry_run, check=True, timeout=300)

    # Rung 3.5: byte-level verify. Read each partition back over DFU_UPLOAD
    # and sha256-compare against the exact file just flashed. On mismatch
    # the board is deliberately left sitting in DFU -- never boot an
    # unverified image; bootloader is untouched, power cycle recovers.
    with tempfile.TemporaryDirectory(prefix="ae3_readback_") as tmpdir:
        for alt, path in parts:
            rb = Path(tmpdir) / f"readback_{alt}.bin"
            run(dfu_upload_cmd(alt, rb), dry_run=args.dry_run,
                check=True, timeout=300)
            if args.dry_run:
                continue
            size = path.stat().st_size
            if not readback_matches(path, rb, size):
                die(f"readback mismatch on {alt}: flash contents != {path.name}",
                    "board left in DFU (NOT booted); power-cycle recovers; "
                    "re-run the flash -- if it repeats, suspect the USB path")
            log(f"readback verify OK: {alt} == sha256({path.name})")

        # Rung 4: boot the verified app -- TOC read with -R (see header:
        # USB reset -> bootloader loop exits -> jump; -e does NOT work on a
        # DFU-mode device). Non-zero dfu-util exit tolerated; CDC +
        # sys.version are the signals.
        run(dfu_boot_cmd(Path(tmpdir) / "toc_scratch.bin"),
            dry_run=args.dry_run, check=False, timeout=60)
        log("boot reset sent (dfu-util non-zero exit here is tolerated)")
    if not args.dry_run:
        if not wait_for("CDC", lambda: len(find_cdc()) > 0, args.timeout, 0.5):
            die("board did not re-enumerate as CDC after flash",
                "power-cycle (--recover or by hand); bootloader is untouched, "
                "board is recoverable")
        time.sleep(2)   # let the port settle before opening it

    # Rung 5: prove the verified image actually boots and runs. sys.version
    # is evidence of "alive + label consistent", not the verify itself --
    # labels are not build-unique (see header facts).
    cdc = args.device or (find_cdc() or ["<cdc>"])[0]
    r = run([mpremote or "mpremote", "connect", cdc, "exec",
             "import sys; print(sys.version)"],
            dry_run=args.dry_run, check=False, capture=True, timeout=30)
    if args.dry_run:
        log("DRY-RUN complete")
        return 0
    uname = (r.stdout or "").strip()
    log(f"sys.version: {uname or '<empty>'}")
    hashes = parse_uname_hashes(uname)
    if hashes is None:
        die("could not parse OpenMV/MicroPython ids from sys.version",
            "board may be running but odd -- check manually with mpremote")
    if expect and hashes[0] != expect:
        die(f"label mismatch: running OpenMV {hashes[0]}, expected {expect}",
            "flash bytes verified by readback -- so the MANIFEST/--expect "
            "label is stale or describes a different build dir")
    label = ("label matches" if expect else
             "label not cross-checked; labels are not build-unique anyway")
    log(f"PASS: flash verified byte-for-byte; board runs OpenMV {hashes[0]}, "
        f"MicroPython {hashes[1]} ({label})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
