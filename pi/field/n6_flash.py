#!/usr/bin/env python3
"""Flash the N6's FIRMWARE partition over DFU, byte-verified, with a rollback.

    # take the rollback artefact FIRST -- this is the thing that gets you home
    python3 pi/field/n6_flash.py backup --out ~/fw/n6_stock_partition.bin

    # write an image and prove it landed
    python3 pi/field/n6_flash.py write --image ~/fw/n6-h264/firmware.bin \\
        --expect-unchanged ~/fw/n6_stock_partition.bin

    # put the board back exactly as it was
    python3 pi/field/n6_flash.py write --image ~/fw/n6_stock_partition.bin --whole

ROUTE B, AND IT IS NOW PROVEN (S33, rehearsed stock->stock on nereus002 before
any H.264 image existed on the board). What the rehearsal settled, because
N6_H264_FLASH.md listed it as unverified and said not to assume it:

  * The bootloader wants the RAW firmware.bin at alt 1. The partition read
    back byte-identical to the stock image over its first 1,987,840 bytes.
  * A download to alt 1 erases and writes ONLY the pages it touches. The
    partition is not mass-erased, which matters because it is not just the
    firmware.

THE PARTITION IS NOT JUST THE FIRMWARE, and a naive whole-file compare would
have called a correct flash a failure:

    0x00000000  1,987,840 B  firmware.bin, byte for byte
    0x001F0000     11,984 B  a 12 KB blob that lives in openmv.bin, NOT in
                             firmware.bin -- untouched by a firmware write
    0x00380000          4 B  trailer
    (the remaining 99.3 % of the tail is erased 0xFF)

So verification compares the head against the image, and the REST against a
prior backup, separately. That distinction is the whole point: "the bytes I
wrote are there" and "I disturbed nothing else" are different claims, and a
single sha256 over 3.6 MB answers neither of them usefully.

ALT 0 (BOOTLOADER) AND ALT 3 (ROMFS0) ARE NEVER WRITTEN. Alt 0 is what makes
every mistake recoverable: the DFU window survives a bad application write, so
a board that will not boot can always be re-entered and rewritten. Alt 3 is
where a rig's custom models live (S8 bite B2) and the vendor build would
overwrite them with its own.

A dfu-util exit code is NOT evidence here. `-R` returns 251 because resetting
the device is indistinguishable from losing it; the proof that the board came
back is that it re-enumerates and answers os.uname(), which this checks.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

#: The N6's bootloader in DFU mode.
DFU_VID_PID = "37c5:9206"
ALT_FIRMWARE = 1

#: Never written by this tool, and named so a reader does not have to infer it.
ALT_NEVER_WRITE = {0: "BOOTLOADER (the only thing that makes this recoverable)",
                   3: "ROMFS0 (a rig's custom models live here)"}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run(argv, timeout=600):
    """Run a command, returning (rc, output). Never raises on a bad rc."""
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 255, str(e)


# ---------------------------------------------------------------------------
# Verification -- pure functions, because this is the part that can lie.
# ---------------------------------------------------------------------------

def verify_write(partition, image, baseline=None, whole=False):
    """Did `image` land in `partition`, and was anything else disturbed?

    Returns a dict of findings. Deliberately reports the two questions
    separately: a flash can put the right bytes at offset 0 and still have
    clobbered something further in, and a whole-partition hash conflates the
    two into one number that is wrong for both.
    """
    out = {"image_bytes": len(image), "partition_bytes": len(partition)}
    if whole:
        out["mode"] = "whole-partition"
        out["head_ok"] = partition == image
        out["head_sha"] = sha(partition)
        out["rest_ok"] = True          # there is no "rest"
        out["rest_note"] = "whole partition written; nothing outside it"
        out["ok"] = out["head_ok"]
        return out

    out["mode"] = "image-at-offset-0"
    n = len(image)
    if len(partition) < n:
        out["head_ok"] = False
        out["ok"] = False
        out["rest_ok"] = False
        out["rest_note"] = ("partition read back SHORTER than the image "
                            "(%d < %d) -- the write did not complete"
                            % (len(partition), n))
        return out

    head = partition[:n]
    out["head_ok"] = head == image
    out["head_sha"] = sha(head)
    out["image_sha"] = sha(image)

    if baseline is None:
        out["rest_ok"] = None
        out["rest_note"] = ("no baseline given, so 'nothing else was disturbed'"
                            " was NOT checked -- run `backup` before a write")
    elif len(baseline) != len(partition):
        out["rest_ok"] = False
        out["rest_note"] = ("baseline is %d bytes, partition read back %d -- "
                            "not the same partition" % (len(baseline),
                                                        len(partition)))
    else:
        out["rest_ok"] = partition[n:] == baseline[n:]
        if out["rest_ok"]:
            out["rest_note"] = "everything past the image is unchanged"
        else:
            diffs = [i + n for i, (a, b) in
                     enumerate(zip(partition[n:], baseline[n:])) if a != b]
            out["rest_note"] = ("%d byte(s) outside the image CHANGED, first "
                                "at 0x%08X" % (len(diffs), diffs[0]))
            out["rest_first_diff"] = diffs[0]

    out["ok"] = bool(out["head_ok"]) and out["rest_ok"] is not False
    return out


def describe(findings):
    lines = ["   mode        : %s" % findings["mode"],
             "   image       : %d bytes" % findings["image_bytes"],
             "   partition   : %d bytes" % findings["partition_bytes"],
             "   bytes match : %s" % ("YES" if findings["head_ok"] else "NO"),
             "   rest of it  : %s" % findings["rest_note"]]
    if findings.get("head_sha"):
        lines.append("   sha256      : %s" % findings["head_sha"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Board + bench interaction
# ---------------------------------------------------------------------------

def runner_is_idle(url="http://127.0.0.1:8088"):
    """One owner per port, ever. Returns (ok, state)."""
    try:
        with urllib.request.urlopen(url + "/api/runner", timeout=8) as r:
            state = json.load(r).get("state", "?")
    except Exception:                                       # noqa: BLE001
        return True, "workbench not answering (assumed idle)"
    return state in ("idle", "failed"), state


def dfu_present():
    rc, out = run(["dfu-util", "-l"], timeout=60)
    return ("alt=%d" % ALT_FIRMWARE) in out and DFU_VID_PID in out, out


def find_n6_port():
    import discover                                          # noqa: E402
    found = discover.discover().get("found", {})
    n6 = found.get("N6") or {}
    return n6.get("port") or ""


def enter_dfu(mpremote, port, settle=5.0):
    """Ask the board to jump to its bootloader.

    mpremote exits nonzero here BY DESIGN -- the serial device disappears
    mid-command, which surfaces as an I/O error. The evidence that this worked
    is a DFU device on the bus, not the exit code.
    """
    run([mpremote, "connect", port, "exec", "import machine; machine.bootloader()"],
        timeout=60)
    time.sleep(settle)
    return dfu_present()


def read_partition(out_path, alt=ALT_FIRMWARE):
    if os.path.exists(out_path):
        os.unlink(out_path)                 # dfu-util refuses an existing file
    rc, out = run(["dfu-util", "-a", str(alt), "-U", out_path], timeout=600)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        return None, "dfu-util produced no file (rc=%d): %s" % (rc, out[-400:])
    with open(out_path, "rb") as f:
        return f.read(), ""


def boot_out_of_dfu(settle=12.0):
    """Any read plus -R boots it. rc is meaningless; enumeration is the proof."""
    run(["dfu-util", "-a", "2", "-U", "/tmp/n6_fs_throwaway.img", "-R"], timeout=300)
    try:
        os.unlink("/tmp/n6_fs_throwaway.img")
    except OSError:
        pass
    time.sleep(settle)


BOARD_CHECK = """
import os
u = os.uname()
print("VERSION:", u.version)
print("MACHINE:", u.machine)
import machine; print("UID:", machine.unique_id().hex())
print("ROMFS:", len(os.listdir("/rom")))
try:
    import codec; print("CODEC: present")
except ImportError:
    print("CODEC: absent")
"""


def check_board(mpremote, port, timeout=120):
    rc, out = run([mpremote, "connect", port, "exec", BOARD_CHECK], timeout=timeout)
    got = {}
    for line in out.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            if k.strip() in ("VERSION", "MACHINE", "UID", "ROMFS", "CODEC"):
                got[k.strip()] = v.strip()
    return got, out


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["backup", "write", "check"])
    ap.add_argument("--out", default="", help="backup: where to write the partition")
    ap.add_argument("--image", default="", help="write: the file to flash")
    ap.add_argument("--expect-unchanged", default="",
                    help="write: a prior backup; everything past the image must "
                         "still match it")
    ap.add_argument("--whole", action="store_true",
                    help="write: the image IS the whole partition (a restore)")
    ap.add_argument("--alt", type=int, default=ALT_FIRMWARE)
    ap.add_argument("--mpremote", default=os.path.expanduser("~/mpv/bin/mpremote"))
    ap.add_argument("--port", default="", help="N6 serial port (default: by role)")
    ap.add_argument("--workbench", default="http://127.0.0.1:8088")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)

    if a.alt in ALT_NEVER_WRITE and a.action == "write":
        sys.exit("n6_flash: alt %d is %s -- this tool will not write it."
                 % (a.alt, ALT_NEVER_WRITE[a.alt]))

    ok, state = runner_is_idle(a.workbench)
    if not ok:
        sys.exit("n6_flash: workbench runner is %r -- stop the demo from the "
                 "page first (never kill it), then re-run." % state)
    print("workbench runner: %s" % state)

    port = a.port or find_n6_port()
    in_dfu, _ = dfu_present()
    if not in_dfu:
        if not port:
            sys.exit("n6_flash: no N6 found by role and no DFU device on the bus")
        print("N6 at %s -- entering DFU" % port)
        in_dfu, listing = enter_dfu(a.mpremote, port)
        if not in_dfu:
            sys.exit("n6_flash: board did not appear in DFU mode.\n%s" % listing[-600:])
    print("DFU: alt %d (FIRMWARE) present" % a.alt)

    if a.action == "backup":
        out = a.out or os.path.expanduser("~/fw/n6_partition_%s.bin"
                                          % time.strftime("%Y%m%dT%H%M%S"))
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        data, err = read_partition(out, a.alt)
        if data is None:
            sys.exit("n6_flash: %s" % err)
        print("backed up %d bytes -> %s" % (len(data), out))
        print("sha256: %s" % sha(data))
        print("\nTHIS FILE IS THE ROLLBACK. Restore it with:")
        print("  python3 pi/field/n6_flash.py write --image %s --whole" % out)
        boot_out_of_dfu()
        got, _ = check_board(a.mpremote, port) if port else ({}, "")
        print("board after boot: %s" % (got or "not re-checked (no port)"))
        return 0

    if a.action == "check":
        tmp = "/tmp/n6_partition_check.bin"
        data, err = read_partition(tmp, a.alt)
        if data is None:
            sys.exit("n6_flash: %s" % err)
        print("partition: %d bytes  sha256 %s" % (len(data), sha(data)))
        boot_out_of_dfu()
        return 0

    # ------------------------------------------------------------ write
    if not a.image:
        sys.exit("n6_flash: write needs --image")
    with open(os.path.expanduser(a.image), "rb") as f:
        image = f.read()
    baseline = None
    if a.expect_unchanged:
        with open(os.path.expanduser(a.expect_unchanged), "rb") as f:
            baseline = f.read()
    print("image: %s  %d bytes  sha256 %s"
          % (a.image, len(image), sha(image)))
    if baseline is None and not a.whole:
        print("WARNING: no --expect-unchanged given, so this cannot prove the "
              "write disturbed nothing else.")
    if not a.yes:
        print("\nAbout to write alt %d. Alt 0 and alt 3 are untouched, so a bad "
              "write stays recoverable." % a.alt)
        try:
            if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                return 1
        except EOFError:
            sys.exit("n6_flash: no tty for confirmation -- pass --yes")

    rc, out = run(["dfu-util", "-a", str(a.alt), "-D",
                   os.path.expanduser(a.image)], timeout=900)
    print("dfu-util download rc=%d" % rc)
    if "Download done" not in out:
        sys.exit("n6_flash: download did not report completion:\n%s" % out[-600:])

    tmp = "/tmp/n6_partition_after.bin"
    data, err = read_partition(tmp, a.alt)
    if data is None:
        sys.exit("n6_flash: could not read back: %s" % err)

    findings = verify_write(data, image, baseline, whole=a.whole)
    print("\n== verification ==")
    print(describe(findings))
    if not findings["ok"]:
        sys.exit("n6_flash: VERIFICATION FAILED -- the board is still in DFU "
                 "and can be rewritten.")

    boot_out_of_dfu()
    got, raw = check_board(a.mpremote, port) if port else ({}, "")
    print("\n== board after boot ==")
    for k in ("VERSION", "MACHINE", "UID", "ROMFS", "CODEC"):
        print("   %-8s %s" % (k, got.get(k, "?")))
    if not got:
        sys.exit("n6_flash: board did not answer after boot:\n%s" % raw[-500:])
    print("\nn6_flash: OK -- written, byte-verified, and the board is back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
