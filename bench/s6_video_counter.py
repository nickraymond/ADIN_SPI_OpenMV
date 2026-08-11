#!/usr/bin/env python3
# bench/s6_video_counter.py -- S6 bite 1: reassemble chunked MJPEG frames
# (EtherType 0x88B5, magic BMV6) off a T1L interface, verify the JPEGs,
# report rate + frame loss. Artifact check: saves frames to --save-dir.
#
# Run (raw sockets need root; start BEFORE the AE3 sender):
#   sudo python3 bench/s6_video_counter.py --iface eth1 --duration 60 \
#       --save-dir /tmp/s6
#
# The window starts at the FIRST valid chunk. Exit code: 0 = PASS
# (complete frames arrived, no interior frame lost, every JPEG valid),
# 1 = FAIL. The first/last frame in the window may be cut off by the
# window edges themselves -- reported as edge_partial, not counted lost.

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "firmware", "adin_drv"))
from s6_video import Reassembler  # noqa: E402
import s5_frames  # noqa: E402  (shared EtherType)

SAVE_FIRST_N = 3


def looks_like_jpeg(data):
    return (len(data) > 4 and data[:2] == b"\xff\xd8"
            and data[-2:] == b"\xff\xd9")


# ---------------------------------------------------------------- pure logic

class FrameTracker:
    """Frame-level accounting over the reassembler's completions.

    Window-relative like S5's SeqTracker: expected = max_seq - min_seq + 1
    over every frame seq OBSERVED (any chunk), so attaching mid-stream is
    fine. An incomplete frame at either window edge was cut off by the
    window, not by the link -- excluded from `lost`.
    """

    def __init__(self):
        self.complete = set()
        self.jpeg_bad = 0
        self.jpeg_bytes = 0

    def frame_complete(self, seq, data):
        self.complete.add(seq)
        self.jpeg_bytes += len(data)
        ok = looks_like_jpeg(data)
        if not ok:
            self.jpeg_bad += 1
        return ok

    def summary(self, rasm, elapsed_s):
        if rasm.min_seq is None:
            expected = lost = edge_partial = 0
        else:
            expected = rasm.max_seq - rasm.min_seq + 1
            lost = expected - len(self.complete)
            edge_partial = 0
            for edge in {rasm.min_seq, rasm.max_seq}:
                if edge not in self.complete:
                    lost -= 1
                    edge_partial += 1
        return {
            "elapsed_s": elapsed_s,
            "expected": expected,
            "complete": len(self.complete),
            "lost": lost,
            "edge_partial": edge_partial,
            "jpeg_bad": self.jpeg_bad,
            "chunks": rasm.chunks,
            "chunk_dupes": rasm.chunk_dupes,
            "bad_chunks": rasm.bad_chunks,
            "frames_dropped": rasm.frames_dropped,
            "fps": len(self.complete) / elapsed_s if elapsed_s > 0 else 0.0,
            "mbps": (self.jpeg_bytes * 8 / elapsed_s / 1e6
                     if elapsed_s > 0 else 0.0),
        }


def verdict(s):
    """(passed, line) -- bite-1 gate: frames flow, nothing interior lost,
    every JPEG structurally valid. The fps gate belongs to bite 3."""
    if s["complete"] == 0:
        return False, "FAIL -- no complete video frames received"
    if s["lost"] > 0:
        return False, ("FAIL -- %d of %d frames lost/incomplete"
                       % (s["lost"], s["expected"]))
    if s["jpeg_bad"] > 0:
        return False, ("FAIL -- %d reassembled frames are not valid JPEGs"
                       % s["jpeg_bad"])
    return True, ("PASS -- %d/%d frames complete, all valid JPEGs, "
                  "%.1f fps / %.2f Mbps over %.1f s"
                  % (s["complete"], s["expected"], s["fps"], s["mbps"],
                     s["elapsed_s"]))


# ---------------------------------------------------------------- socket main

def save_frame(save_dir, name, data):
    path = os.path.join(save_dir, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def main():
    ap = argparse.ArgumentParser(description="S6 video frame counter")
    ap.add_argument("--iface", default="eth1")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="measurement window, seconds from first chunk")
    ap.add_argument("--wait", type=float, default=120.0,
                    help="max seconds to wait for the first chunk")
    ap.add_argument("--save-dir", default=None,
                    help="save first %d + latest JPEGs here" % SAVE_FIRST_N)
    args = ap.parse_args()

    import socket
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                             socket.htons(s5_frames.ETHERTYPE))
    except PermissionError:
        sys.exit("s6_video_counter: raw socket needs root -- rerun with sudo")
    sock.bind((args.iface, 0))
    sock.settimeout(0.5)
    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)

    print("listening on %s for BMV6 chunks, window %.0f s from first chunk"
          % (args.iface, args.duration))

    rasm = Reassembler()
    trk = FrameTracker()
    t_start = time.monotonic()
    t_first = None
    last_print = 0.0
    saved = 0
    while True:
        now = time.monotonic()
        if t_first is None and now - t_start > args.wait:
            print(verdict(trk.summary(rasm, 0))[1])
            sys.exit(1)
        if t_first is not None and now - t_first >= args.duration:
            break
        try:
            pkt = sock.recv(4096)
        except (TimeoutError, OSError):
            continue
        done = rasm.feed(pkt)
        if t_first is None and rasm.chunks > 0:
            t_first = time.monotonic()
            print("first chunk seen")
        if done is None:
            continue
        seq, data = done
        ok = trk.frame_complete(seq, data)
        if args.save_dir and ok:
            if saved < SAVE_FIRST_N:
                print("saved %s" % save_frame(args.save_dir,
                                              "frame_%06d.jpg" % seq, data))
                saved += 1
            save_frame(args.save_dir, "last.jpg", data)
        if time.monotonic() - t_first - last_print >= 5.0:
            last_print = time.monotonic() - t_first
            s = trk.summary(rasm, last_print)
            print("  t=%3.0fs  complete %5d  lost %d  bad %d  %5.1f fps  "
                  "%4.2f Mbps"
                  % (last_print, s["complete"], s["lost"], s["jpeg_bad"],
                     s["fps"], s["mbps"]))

    elapsed = time.monotonic() - t_first
    s = trk.summary(rasm, elapsed)
    print("-" * 60)
    for k in ("elapsed_s", "expected", "complete", "lost", "edge_partial",
              "jpeg_bad", "chunks", "chunk_dupes", "bad_chunks",
              "frames_dropped", "fps", "mbps"):
        v = s[k]
        print("%-16s %.3f" % (k, v) if isinstance(v, float)
              else "%-16s %d" % (k, v))
    passed, line = verdict(s)
    print(line)
    if args.save_dir:
        print("artifacts in %s -- open one and LOOK at it" % args.save_dir)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
