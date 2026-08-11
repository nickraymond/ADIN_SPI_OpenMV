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

from flash_ae3 import (parse_uname_hashes, read_manifest_sha,
                       read_manifest_sha256s, sha256_file, readback_matches,
                       dfu_cmd, dfu_upload_cmd, dfu_detach_cmd,
                       sysfs_has_device, wait_for, main)

# Real uname.version text observed in release firmware_M55_HP.bin (strings,
# development tag, 2026-08-11).
REAL_UNAME = "3.4.0; OpenMV 7d4dbf7ab2; MicroPython 11852aa3d0"


class TestParseUname(unittest.TestCase):
    def test_real_string(self):
        self.assertEqual(parse_uname_hashes(REAL_UNAME),
                         ("7d4dbf7ab2", "11852aa3d0"))

    def test_release_format_uses_tags_not_hashes(self):
        # Tagged releases embed version tags (observed in v5.0.0 binary
        # strings on flash day, 2026-08-11).
        self.assertEqual(
            parse_uname_hashes("3.4.0; OpenMV v5.0.0; MicroPython v1.28.0-49"),
            ("v5.0.0", "v1.28.0-49"))

    def test_no_match(self):
        self.assertIsNone(parse_uname_hashes("MicroPython v1.28.0 on 2026-07-02"))


HP_SHA = "a" * 64
HE_SHA = "b" * 64


class TestManifest(unittest.TestCase):
    MANIFEST = ("built:      2026-08-11T00:00:00Z\n"
                "rev:        v5.0.0-12-g7d4dbf7ab2\n"
                "openmv_sha: 7d4dbf7ab2\n"
                "sdk:        1.6.0 linux-x86_64\n"
                f"{HP_SHA}  firmware_M55_HP.bin\n"
                f"{HE_SHA}  firmware_M55_HE.bin\n")

    def test_reads_sha(self):
        self.assertEqual(read_manifest_sha(self.MANIFEST), "7d4dbf7ab2")

    def test_missing_key(self):
        self.assertIsNone(read_manifest_sha("rev: something\n"))

    def test_reads_sha256_lines(self):
        self.assertEqual(read_manifest_sha256s(self.MANIFEST),
                         {"firmware_M55_HP.bin": HP_SHA,
                          "firmware_M55_HE.bin": HE_SHA})

    def test_sha256s_ignore_non_hash_lines(self):
        # openmv_sha is a sha10, not 64 hex chars -- must not match.
        self.assertEqual(read_manifest_sha256s("openmv_sha: 7d4dbf7ab2\n"), {})


class TestSha256Verify(unittest.TestCase):
    FW = b"firmware image bytes" * 100

    def write(self, root, name, data):
        p = Path(root) / name
        p.write_bytes(data)
        return p

    def test_sha256_file_streams_whole_file(self):
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            p = self.write(root, "fw.bin", self.FW)
            self.assertEqual(sha256_file(p),
                             hashlib.sha256(self.FW).hexdigest())

    def test_readback_with_mram_sector_padding_matches(self):
        # axi_flash_write rounds the tail up to the 16 B MRAM sector with
        # buffer residue -- the limit= compare must ignore those bytes.
        with tempfile.TemporaryDirectory() as root:
            fw = self.write(root, "fw.bin", self.FW)
            rb = self.write(root, "rb.bin", self.FW + b"\xff" * 7)
            self.assertTrue(readback_matches(fw, rb, len(self.FW)))

    def test_readback_corrupt_byte_fails(self):
        with tempfile.TemporaryDirectory() as root:
            fw = self.write(root, "fw.bin", self.FW)
            bad = self.FW[:50] + b"\x00" + self.FW[51:]
            rb = self.write(root, "rb.bin", bad)
            self.assertFalse(readback_matches(fw, rb, len(self.FW)))

    def test_short_readback_fails(self):
        with tempfile.TemporaryDirectory() as root:
            fw = self.write(root, "fw.bin", self.FW)
            rb = self.write(root, "rb.bin", self.FW[:-1])
            self.assertFalse(readback_matches(fw, rb, len(self.FW)))


class TestDfuCmd(unittest.TestCase):
    def test_download_has_no_reset(self):
        self.assertEqual(dfu_cmd("HP", "fw.bin"),
                         ["dfu-util", "-d", "37c5:96e3", "-a", "HP",
                          "-D", "fw.bin"])

    def test_upload_bounds_size(self):
        self.assertEqual(dfu_upload_cmd("HE", "rb.bin", 1193536),
                         ["dfu-util", "-d", "37c5:96e3", "-a", "HE",
                          "-U", "rb.bin", "-Z", "1193536"])

    def test_detach_cmd(self):
        self.assertEqual(dfu_detach_cmd()[-1], "-e")


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

    def test_completes_and_orders_rungs(self):
        rc, out = self.ladder("--he", "he.bin")
        self.assertEqual(rc, 0)
        dfu_lines = [l for l in out.splitlines() if "DRY-RUN: dfu-util" in l]
        # Downloads, then readback uploads, then the detach that boots.
        self.assertEqual(len(dfu_lines), 5)
        self.assertIn("-D hp.bin", dfu_lines[0])
        self.assertIn("-D he.bin", dfu_lines[1])
        self.assertIn("-a HP -U", dfu_lines[2])
        self.assertIn("-a HE -U", dfu_lines[3])
        self.assertTrue(dfu_lines[4].endswith("-e"))

    def test_no_reset_flag_anywhere(self):
        # Boot goes through detach AFTER verify; -R must be gone entirely.
        for extra in ([], ["--he", "he.bin"]):
            _, out = self.ladder(*extra)
            for l in out.splitlines():
                if "DRY-RUN: dfu-util" in l:
                    self.assertNotIn(" -R", l)

    def test_hp_only_ladder(self):
        rc, out = self.ladder()
        dfu_lines = [l for l in out.splitlines() if "DRY-RUN: dfu-util" in l]
        self.assertEqual(len(dfu_lines), 3)   # download, readback, detach
        self.assertIn("-D hp.bin", dfu_lines[0])
        self.assertIn("-U", dfu_lines[1])
        self.assertTrue(dfu_lines[2].endswith("-e"))

    def test_uploads_are_size_bounded(self):
        _, out = self.ladder("--he", "he.bin")
        up_lines = [l for l in out.splitlines()
                    if "DRY-RUN: dfu-util" in l and "-U" in l]
        self.assertEqual(len(up_lines), 2)
        for l in up_lines:
            self.assertIn("-Z", l)

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
