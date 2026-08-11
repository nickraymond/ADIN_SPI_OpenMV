# s5_frames.py -- S5 test-frame format (portable; no machine imports)
#
# Shared by s5_frame_tx.py (bite 1) and s5_tx_load.py (bite 2) so neither
# demo has to import the other (demo modules run main() on import when on
# target). bench/frame_counter.py mirrors these constants on the Pi side.

import struct

# Destination = nereus001 eth1 (overlay MAC, NM-cloned -- DESIGN.md S2).
# Unicast to it passes the Linux node's ADIN1110 hardware MAC filter.
DST_MAC = b"\x02\xad\x11\x10\x00\x03"
# Source = this AE3 node: next locally-administered address in the series.
SRC_MAC = b"\x02\xad\x11\x10\x00\x04"
# IEEE Std 802 "Local Experimental EtherType 1" -- safe, collision-free.
ETHERTYPE = 0x88B5
MAGIC = b"BMS5"
SEQ_OFF = 18               # BE32 seq right after 14 B header + 4 B magic
DEFAULT_PAYLOAD_LEN = 486  # -> 500-byte frame (14 B Ethernet header)


def build_eth_frame(seq, payload_len=DEFAULT_PAYLOAD_LEN):
    """Ethernet frame (no FCS -- the MAC appends it): header + MAGIC +
    BE32 seq + deterministic pad."""
    body_fixed = MAGIC + struct.pack(">I", seq)
    pad_n = payload_len - len(body_fixed)
    if pad_n < 0:
        raise ValueError("payload_len %d too small for magic+seq" % payload_len)
    pad = bytes((i & 0xFF for i in range(pad_n)))
    return DST_MAC + SRC_MAC + struct.pack(">H", ETHERTYPE) + body_fixed + pad


def patch_seq(frame_buf, seq):
    """Overwrite the seq field in a prebuilt frame template (bytearray) --
    avoids rebuilding the whole frame per send in the load loop."""
    struct.pack_into(">I", frame_buf, SEQ_OFF, seq)
