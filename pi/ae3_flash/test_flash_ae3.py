# test_flash_ae3.py -- host-side unit tests for the pure helpers and the
# dry-run ladder in flash_ae3.py. Hardware rungs (mpremote, dfu-util,
# enumeration waits) are covered by the S7 manual test on nereus000.
#
# Run:  python3 pi/ae3_flash/test_flash_ae3.py

import io
import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flash_ae3 import (parse_uname_hashes, read_manifest_sha, dfu_cmd,
                       sysfs_has_device, wait_for, main)

# Real uname.version text observed in release firmware_M55_HP.bin (strings,
# development tag, 2026-08-11).
REAL_UNAME = "3.4.0; OpenMV 7d4dbf7ab2; MicroPython 11852aa3d0"


class TestParseUname(unittest.TestCase):
    def test_real_string(self):
        self.assertEqual(parse_uname_hashes(REAL_UNAME),
                         ("7d4dbf7ab2", "11852aa3d0"))

    def test_no_match(self):
        self.assertIsNone(parse_uname_hashes("MicroPython v1.28.0 on 2026-07-02"))

    def test_hash_too_short_rejected(self):
        self.assertIsNone(parse_uname_hashes("OpenMV 7d4dbf; MicroPython 11852a"))


class TestManifest(unittest.TestCase):
    MANIFEST = ("built:      2026-08-11T00:00:00Z\n"
                "rev:        v5.0.0-12-g7d4dbf7ab2\n"
                "openmv_sha: 7d4dbf7ab2\n"
                "sdk:        1.6.0 linux-x86_64\n")

    def test_reads_sha(self):
        self.assertEqual(read_manifest_sha(self.MANIFEST), "7d4dbf7ab2")

    def test_missing_key(self):
        self.assertIsNone(read_manifest_sha("rev: something\n"))


class TestDfuCmd(unittest.TestCase):
    def test_hp_no_reset(self):
        self.assertEqual(dfu_cmd("HP", "fw.bin"),
                         ["dfu-util", "-d", "37c5:96e3", "-a", "HP",
                          "-D", "fw.bin"])

    def test_reset_flag_appended(self):
        self.assertEqual(dfu_cmd("HE", "he.bin", reset=True)[-1], "-R")


class TestSysfsScan(unittest.TestCase):
    def make_dev(self, root, name, vid, pid):
        d = Path(root) / name
        d.mkdir()
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")

    def test_finds_device(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_dev(root, "1-1", "37c5", "96e3")
            self.assertTrue(sysfs_has_device("37c5", "96e3", root=root))

    def test_absent_device(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_dev(root, "1-1", "37c5", "9370")   # CDC app, not DFU
            self.assertFalse(sysfs_has_device("37c5", "96e3", root=root))

    def test_entries_without_ids_are_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "usb1").mkdir()   # hub dirs lack idVendor
            self.make_dev(root, "1-2", "37c5", "96e3")
            self.assertTrue(sysfs_has_device("37c5", "96e3", root=root))


class TestWaitFor(unittest.TestCase):
    def test_immediate_true(self):
        self.assertTrue(wait_for("x", lambda: True, timeout_s=1))

    def test_times_out(self):
        self.assertFalse(wait_for("x", lambda: False, timeout_s=0.05,
                                  interval_s=0.01))


class TestDryRunLadder(unittest.TestCase):
    def ladder(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--hp", "hp.bin", "--dry-run", *extra])
        return rc, buf.getvalue()

    def test_completes_and_orders_partitions(self):
        rc, out = self.ladder("--he", "he.bin")
        self.assertEqual(rc, 0)
        dfu_lines = [l for l in out.splitlines() if "dfu-util" in l]
        self.assertEqual(len(dfu_lines), 2)
        self.assertIn("-a HP", dfu_lines[0])
        self.assertIn("-a HE", dfu_lines[1])
        # Reset rides the LAST download only. (endswith, because the
        # "DRY-RUN" prefix itself contains the substring "-R")
        self.assertFalse(dfu_lines[0].endswith("-R"))
        self.assertTrue(dfu_lines[1].endswith("-R"))

    def test_hp_only_gets_reset(self):
        rc, out = self.ladder()
        dfu_lines = [l for l in out.splitlines() if "dfu-util" in l]
        self.assertEqual(len(dfu_lines), 1)
        self.assertTrue(dfu_lines[0].endswith("-R"))

    def test_expect_flag_reported(self):
        rc, out = self.ladder("--expect", "7d4dbf7ab2")
        self.assertEqual(rc, 0)
        self.assertIn("7d4dbf7ab2", out)

    def test_bootloader_entry_before_dfu(self):
        rc, out = self.ladder()
        lines = out.splitlines()
        boot = next(i for i, l in enumerate(lines) if "machine.bootloader" in l)
        dfu = next(i for i, l in enumerate(lines) if "dfu-util" in l)
        self.assertLess(boot, dfu)


if __name__ == "__main__":
    unittest.main(verbosity=2)
