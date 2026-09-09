#!/usr/bin/env python3
"""Host tests for the N6 DFU flash verifier. No board, no dfu-util.

Only the comparison is tested, because the comparison is the part that can
lie -- and on this bench it already has twice in one session (a blob routed
through a shell variable that stripped its trailing newlines, and a CRLF
translation that made a correct restore look like a mismatch). A flash tool
whose verdict cannot be trusted is worse than no flash tool: it converts an
unknown into a confident wrong answer.

    python3 pi/field/test_n6_flash.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import n6_flash as F                                        # noqa: E402

#: The real geometry, measured on nereus002 2026-09-08 (S33 rehearsal).
PART_LEN = 3670020
FW_LEN = 1987840
BLOB_OFF, BLOB_LEN = 0x001F0000, 11984


def partition(image, blob=b"\xAB" * BLOB_LEN, trailer=b"\x01\x02\x03\x04"):
    """A partition shaped like the N6's: image, erased gap, blob, trailer."""
    buf = bytearray(b"\xFF" * PART_LEN)
    buf[:len(image)] = image
    buf[BLOB_OFF:BLOB_OFF + len(blob)] = blob
    buf[PART_LEN - len(trailer):] = trailer
    return bytes(buf)


class VerifyWrite(unittest.TestCase):

    def setUp(self):
        self.image = bytes(range(256)) * (FW_LEN // 256)
        self.base = partition(self.image)

    def test_a_clean_write_passes(self):
        f = F.verify_write(self.base, self.image, self.base)
        self.assertTrue(f["ok"])
        self.assertTrue(f["head_ok"])
        self.assertTrue(f["rest_ok"])

    def test_the_whole_partition_hash_is_NOT_the_test(self):
        """The failure this tool exists to avoid. The partition is 3.6 MB and
        the image is 2.0 MB, so a whole-file sha256 NEVER matches the image
        even on a perfect flash -- and reporting that as a mismatch would send
        someone chasing a flash that actually worked."""
        self.assertNotEqual(F.sha(self.base), F.sha(self.image))
        self.assertTrue(F.verify_write(self.base, self.image, self.base)["ok"])

    def test_a_corrupted_byte_in_the_image_region_fails(self):
        bad = bytearray(self.base); bad[1000] ^= 0xFF
        f = F.verify_write(bytes(bad), self.image, self.base)
        self.assertFalse(f["head_ok"])
        self.assertFalse(f["ok"])

    def test_a_clobbered_blob_outside_the_image_fails_and_says_where(self):
        """A write that lands the firmware correctly but mass-erases the 12 KB
        blob at 0x1F0000 would pass a head-only check. It must not."""
        bad = bytearray(self.base)
        bad[BLOB_OFF:BLOB_OFF + BLOB_LEN] = b"\xFF" * BLOB_LEN
        f = F.verify_write(bytes(bad), self.image, self.base)
        self.assertTrue(f["head_ok"])
        self.assertFalse(f["rest_ok"])
        self.assertFalse(f["ok"])
        self.assertEqual(f["rest_first_diff"], BLOB_OFF)
        self.assertIn("0x001F0000", f["rest_note"])

    def test_a_clobbered_trailer_fails(self):
        bad = bytearray(self.base); bad[-1] ^= 0xFF
        f = F.verify_write(bytes(bad), self.image, self.base)
        self.assertFalse(f["rest_ok"])
        self.assertFalse(f["ok"])

    def test_no_baseline_reports_unknown_rather_than_pass(self):
        """Without a backup, 'nothing else was disturbed' is unverified. It
        must read as unknown, never as a silent pass -- an unchecked claim
        presented as a check is how a rehearsal proves nothing."""
        f = F.verify_write(self.base, self.image, None)
        self.assertTrue(f["head_ok"])
        self.assertIsNone(f["rest_ok"])
        self.assertIn("NOT", f["rest_note"])
        self.assertTrue(f["ok"])          # not a failure, but not a claim

    def test_a_short_readback_is_a_failure_not_a_pass(self):
        """A truncated upload must never satisfy the head check by accident."""
        f = F.verify_write(self.base[:FW_LEN // 2], self.image, None)
        self.assertFalse(f["ok"])
        self.assertIn("SHORTER", f["rest_note"])

    def test_a_baseline_of_the_wrong_size_is_refused(self):
        """Refused either way: a small shortfall trips the read-artefact check
        (the excess is 0xFF, not zeros), a large one trips the size check.
        What matters is that neither passes."""
        near = F.verify_write(self.base, self.image, self.base[:-100])
        self.assertFalse(near["rest_ok"])
        self.assertFalse(near["ok"])
        self.assertIn("NOT the known zero artefact", near["rest_note"])
        far = F.verify_write(self.base, self.image, self.base[:-F.DFU_BLOCK * 2])
        self.assertFalse(far["rest_ok"])
        self.assertIn("not the same partition", far["rest_note"])

    def test_whole_partition_restore_compares_everything(self):
        f = F.verify_write(self.base, self.base, whole=True)
        self.assertTrue(f["ok"])
        bad = bytearray(self.base); bad[BLOB_OFF] ^= 0xFF
        self.assertFalse(F.verify_write(bytes(bad), self.base, whole=True)["ok"])


class ReadWriteSizeMismatch(unittest.TestCase):
    """The partition reads back 4 bytes LONGER than it can be written, and
    handing the backup straight back to dfu-util fails at 96 % with an
    out-of-range error. Found by rehearsing the restore on nereus002, not by
    reading a datasheet -- which is the entire argument for rehearsing it
    before the flash rather than after one fails."""

    def test_alignment_trims_to_a_whole_number_of_blocks(self):
        aligned, dropped = F.align_for_write(b"\x00" * 3670020)
        self.assertEqual(len(aligned), 3670016)
        self.assertEqual(len(aligned) % F.DFU_BLOCK, 0)
        self.assertEqual(len(dropped), 4)

    def test_an_already_aligned_image_is_untouched(self):
        data = b"\xAA" * (F.DFU_BLOCK * 3)
        aligned, dropped = F.align_for_write(data)
        self.assertEqual(aligned, data)
        self.assertEqual(dropped, b"")

    def test_whole_verify_accepts_the_known_zero_artefact(self):
        image = b"\x5A" * (F.DFU_BLOCK * 4)
        readback = image + b"\x00\x00\x00\x00"
        f = F.verify_write(readback, image, whole=True)
        self.assertTrue(f["ok"])
        self.assertIn("artefact", f["rest_note"])

    def test_whole_verify_REFUSES_a_non_zero_tail(self):
        """If the tail is ever real data, the partition is bigger than the
        write covered and the restore is incomplete. That must fail, not be
        waved through as 'the usual four bytes'."""
        image = b"\x5A" * (F.DFU_BLOCK * 4)
        readback = image + b"\xDE\xAD\xBE\xEF"
        f = F.verify_write(readback, image, whole=True)
        self.assertFalse(f["ok"])
        self.assertIn("NOT the known", f["rest_note"])

    def test_an_aligned_backup_verifies_against_a_raw_readback(self):
        """The regression from the first real flash. `backup` stores the
        block-aligned 3,670,016 while a readback is the raw 3,670,020, so
        demanding exact equality rejected a flash whose bytes were correct."""
        image = b"\x11" * F.DFU_BLOCK
        baseline = image + b"\xFF" * (F.DFU_BLOCK * 2)
        readback = baseline + b"\x00\x00\x00\x00"
        f = F.verify_write(readback, image, baseline)
        self.assertTrue(f["head_ok"])
        self.assertTrue(f["rest_ok"], f["rest_note"])
        self.assertTrue(f["ok"])

    def test_an_aligned_backup_still_catches_a_real_change(self):
        image = b"\x11" * F.DFU_BLOCK
        baseline = image + b"\xFF" * (F.DFU_BLOCK * 2)
        bad = bytearray(baseline); bad[F.DFU_BLOCK + 9] = 0x00
        f = F.verify_write(bytes(bad) + b"\x00" * 4, image, baseline)
        self.assertFalse(f["rest_ok"])
        self.assertFalse(f["ok"])

    def test_a_genuinely_different_partition_is_still_refused(self):
        image = b"\x11" * F.DFU_BLOCK
        baseline = image + b"\xFF" * (F.DFU_BLOCK * 2)
        f = F.verify_write(image + b"\xFF" * (F.DFU_BLOCK * 8), image, baseline)
        self.assertFalse(f["rest_ok"])
        self.assertIn("not the same partition", f["rest_note"])

    def test_whole_verify_still_catches_a_bad_body(self):
        image = b"\x5A" * (F.DFU_BLOCK * 4)
        bad = bytearray(image); bad[17] ^= 0xFF
        f = F.verify_write(bytes(bad) + b"\x00" * 4, image, whole=True)
        self.assertFalse(f["ok"])


class SafetyRails(unittest.TestCase):

    def test_the_never_write_alts_are_named(self):
        self.assertIn(0, F.ALT_NEVER_WRITE)     # BOOTLOADER
        self.assertIn(3, F.ALT_NEVER_WRITE)     # ROMFS0
        self.assertEqual(F.ALT_FIRMWARE, 1)

    def test_writing_the_bootloader_alt_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            F.main(["write", "--alt", "0", "--image", "/dev/null", "--yes"])
        self.assertIn("will not write", str(cm.exception))

    def test_writing_the_romfs_alt_is_refused(self):
        """ROMFS0 carries a rig's custom models; the vendor image would
        replace them (S8 bite B2)."""
        with self.assertRaises(SystemExit) as cm:
            F.main(["write", "--alt", "3", "--image", "/dev/null", "--yes"])
        self.assertIn("will not write", str(cm.exception))

    def test_no_kill_dash_nine_anywhere(self):
        src = open(os.path.join(_HERE, "n6_flash.py")).read()
        self.assertNotIn("SIGKILL", src)
        self.assertNotIn("kill -9", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
