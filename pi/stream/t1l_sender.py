#!/usr/bin/env python3
"""t1l_sender.py — S3 sender: AE3 USB frames, paced, across the T1L pair (bite 2).

Runs on the camera-side node (nereus000). One self-healing loop:

    connect TCP to the stream server's ingest port
    -> reboot the AE3 (one stream session per boot — the D15 crash workaround)
    -> start the USB stream at the D16 setting (QVGA q90, free-runs ~36 fps)
    -> pace to --fps by forwarding the next frame at each tick (skips counted)
    -> relay each forwarded frame as the same wire format the source speaks
       (frame JSON header + JPEG), re-sequenced so receiver gaps = real loss

Any failure (USB session ends, board crash, TCP drop) tears the leg down and
the loop rebuilds it from the reboot step, logging what died. Ctrl-C to stop.

Run:  python3 pi/stream/t1l_sender.py                       # defaults per D16
      python3 pi/stream/t1l_sender.py --dest 192.168.7.2 --fps 15
"""

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usb_frame_source import (UsbFrameSource, cp, find_openmv_port,  # noqa: E402
                              reboot_board)

STATUS_EVERY_S = 5.0


class Pacer:
    """Pure 'forward this frame?' decision at a fixed output rate.

    Forwards the first frame at/after each tick; if we fall more than two
    periods behind (stalled source, slow link), resync to now instead of
    bursting stale ticks.
    """

    def __init__(self, fps, clock=time.monotonic):
        self.period = 1.0 / fps
        self._clock = clock
        self._next_due = None

    def should_send(self, now=None):
        now = self._clock() if now is None else now
        if self._next_due is None:
            self._next_due = now + self.period
            return True
        if now < self._next_due:
            return False
        self._next_due += self.period
        if now - self._next_due > 2 * self.period:
            self._next_due = now + self.period
        return True


def encode_frame(seq, frame):
    """One ingest-wire frame: header line + JPEG bytes (StreamParser-compatible)."""
    header = cp.frame_response("t1l", seq, len(frame.data), 0,
                               frame.width, frame.height)
    return cp.encode_message(header) + frame.data


def run_leg(sock, port, fps, framesize, quality):
    """One session: reboot board, stream, pace, relay. Returns on any failure."""
    print("leg: rebooting AE3 for a fresh session", flush=True)
    reboot_board(port)
    pacer = Pacer(fps)
    out_seq = sent = skipped = sent_bytes = 0
    t_status = time.monotonic()
    src = UsbFrameSource(port, framesize=framesize, jpeg_quality=quality,
                         max_seconds=86400)
    with src:
        print("leg: streaming %s q%d, pacing to %.0f fps" %
              (framesize, quality, fps), flush=True)
        for frame in src.frames():
            if not pacer.should_send():
                skipped += 1
                continue
            sock.sendall(encode_frame(out_seq, frame))
            out_seq += 1
            sent += 1
            sent_bytes += len(frame.data)
            now = time.monotonic()
            if now - t_status >= STATUS_EVERY_S:
                span = now - t_status
                print("leg: %.1f fps sent, %.2f Mbps, %d skipped (pacing)"
                      % (sent / span, sent_bytes * 8 / span / 1e6, skipped),
                      flush=True)
                sent = skipped = sent_bytes = 0
                t_status = now
    print("leg: USB stream session ended", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", default="192.168.7.2", help="stream server host")
    ap.add_argument("--ingest-port", type=int, default=8081)
    ap.add_argument("--fps", type=float, default=30.0, help="paced rate (D17: 30)")
    ap.add_argument("--framesize", default="QVGA", help="D17: QVGA")
    ap.add_argument("--quality", type=int, default=90, help="D17: 90 (q80 = margin fallback)")
    ap.add_argument("--serial-port", default="auto")
    args = ap.parse_args()

    while True:
        try:
            port = (find_openmv_port() if args.serial_port == "auto"
                    else args.serial_port)
            print("connecting to %s:%d over the pair" %
                  (args.dest, args.ingest_port), flush=True)
            sock = socket.create_connection((args.dest, args.ingest_port),
                                            timeout=10)
            sock.settimeout(10)
            try:
                run_leg(sock, port, args.fps, args.framesize, args.quality)
            finally:
                sock.close()
        except KeyboardInterrupt:
            print("stopped by user", flush=True)
            return 0
        except Exception as exc:
            print("!! leg died: %s: %s — rebuilding in 2 s"
                  % (type(exc).__name__, exc), flush=True)
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
