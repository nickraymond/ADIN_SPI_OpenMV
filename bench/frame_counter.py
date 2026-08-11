#!/usr/bin/env python3
# bench/frame_counter.py -- S5 demo: count seq-numbered raw Ethernet
# frames (EtherType 0x88B5, magic BMS5) on a T1L interface and report
# rate + loss. Pass gate per TRACKER S5: 0% loss at target load for 60 s.
#
# Run (raw sockets need root):
#   sudo python3 bench/frame_counter.py --iface eth1 --duration 60
#
# The 60 s window starts at the FIRST valid frame received, so start this
# before the AE3 sender (firmware/adin_drv/s5_tx_load.py). Exit code:
# 0 = PASS (frames received, 0 lost), 1 = FAIL.

import argparse
import struct
import sys
import time

ETHERTYPE = 0x88B5      # mirrors firmware/adin_drv/s5_frames.py
MAGIC = b"BMS5"
SEQ_OFF = 18
MIN_FRAME = SEQ_OFF + 4


# ---------------------------------------------------------------- pure logic

def parse_frame(pkt):
    """Return the BE32 seq of a valid S5 test frame, else None."""
    if len(pkt) < MIN_FRAME:
        return None
    if struct.unpack_from(">H", pkt, 12)[0] != ETHERTYPE:
        return None
    if pkt[14:18] != MAGIC:
        return None
    return struct.unpack_from(">I", pkt, SEQ_OFF)[0]


class SeqTracker:
    """Loss/dupe/order accounting over a window of observed seq numbers.

    Loss is window-relative: expected = max_seq - min_seq + 1, so it is
    correct even if the counter attaches after the sender started.
    """

    def __init__(self):
        self.seen = set()
        self.received = 0       # every valid frame, dupes included
        self.dupes = 0
        self.out_of_order = 0
        self.bytes = 0
        self.min_seq = None
        self.max_seq = None
        self._last = None

    def feed(self, seq, nbytes):
        self.received += 1
        self.bytes += nbytes
        if seq in self.seen:
            self.dupes += 1
        else:
            self.seen.add(seq)
        if self._last is not None and seq < self._last:
            self.out_of_order += 1
        self._last = seq
        self.min_seq = seq if self.min_seq is None else min(self.min_seq, seq)
        self.max_seq = seq if self.max_seq is None else max(self.max_seq, seq)

    @property
    def expected(self):
        if self.min_seq is None:
            return 0
        return self.max_seq - self.min_seq + 1

    @property
    def lost(self):
        return self.expected - len(self.seen)

    def summary(self, elapsed_s):
        loss_pct = (100.0 * self.lost / self.expected) if self.expected else 0.0
        return {
            "elapsed_s": elapsed_s,
            "expected": self.expected,
            "received_unique": len(self.seen),
            "dupes": self.dupes,
            "out_of_order": self.out_of_order,
            "lost": self.lost,
            "loss_pct": loss_pct,
            "fps": self.received / elapsed_s if elapsed_s > 0 else 0.0,
            "mbps": self.bytes * 8 / elapsed_s / 1e6 if elapsed_s > 0 else 0.0,
        }


def verdict(summary):
    """(passed, line) per the TRACKER gate: traffic present and 0 lost."""
    if summary["received_unique"] == 0:
        return False, "FAIL -- no test frames received"
    if summary["lost"] > 0:
        return False, ("FAIL -- %d of %d frames lost (%.3f%%)"
                       % (summary["lost"], summary["expected"],
                          summary["loss_pct"]))
    return True, ("PASS -- 0%% loss: %d/%d frames over %.1f s"
                  % (summary["received_unique"], summary["expected"],
                     summary["elapsed_s"]))


# ---------------------------------------------------------------- socket main

def main():
    ap = argparse.ArgumentParser(description="S5 raw-frame loss counter")
    ap.add_argument("--iface", default="eth1")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="measurement window, seconds from first frame")
    ap.add_argument("--wait", type=float, default=120.0,
                    help="max seconds to wait for the first frame")
    args = ap.parse_args()

    import socket
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                             socket.htons(ETHERTYPE))
    except PermissionError:
        sys.exit("frame_counter: raw socket needs root -- rerun with sudo")
    sock.bind((args.iface, 0))
    sock.settimeout(0.5)

    print("listening on %s for EtherType 0x%04X, window %.0f s from first "
          "frame" % (args.iface, ETHERTYPE, args.duration))

    trk = SeqTracker()
    t_start = time.monotonic()
    t_first = None
    last_print = 0.0
    while True:
        now = time.monotonic()
        if t_first is None and now - t_start > args.wait:
            print(verdict(trk.summary(0))[1])
            sys.exit(1)
        if t_first is not None and now - t_first >= args.duration:
            break
        try:
            pkt = sock.recv(4096)
        except (TimeoutError, OSError):
            continue
        seq = parse_frame(pkt)
        if seq is None:
            continue
        if t_first is None:
            t_first = time.monotonic()
            print("first frame: seq %d" % seq)
        trk.feed(seq, len(pkt))
        if time.monotonic() - t_first - last_print >= 5.0:
            last_print = time.monotonic() - t_first
            s = trk.summary(last_print)
            print("  t=%3.0fs  rx %6d  lost %d  %6.1f fps  %5.2f Mbps"
                  % (last_print, s["received_unique"], s["lost"],
                     s["fps"], s["mbps"]))

    elapsed = time.monotonic() - t_first
    s = trk.summary(elapsed)
    print("-" * 60)
    for k in ("elapsed_s", "expected", "received_unique", "lost", "loss_pct",
              "dupes", "out_of_order", "fps", "mbps"):
        v = s[k]
        print("%-16s %.3f" % (k, v) if isinstance(v, float)
              else "%-16s %d" % (k, v))
    passed, line = verdict(s)
    print(line)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
