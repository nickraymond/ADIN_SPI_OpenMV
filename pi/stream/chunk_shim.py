#!/usr/bin/env python3
"""chunk_shim.py — S6 shim: BMV6 chunks off the T1L pair -> frozen ingest (bite 2).

Runs on the receiving node (nereus001), same box as stream_server.py. One
self-healing loop, shaped like t1l_sender.py's:

    open raw AF_PACKET socket on the T1L interface (EtherType 0x88B5)
    -> connect TCP to the stream server's ingest port (localhost:8081)
    -> drain stale queued chunks so the leg starts on fresh frames
    -> reassemble chunks (s6_video.Reassembler) into complete JPEGs
    -> validate (SOI + EOI) and relay each as the frozen wire format
       (frame JSON header + JPEG bytes), re-sequenced by the shim

The frozen server reads only ``seq`` and ``size_bytes`` from the header
(stream_server.py ingest_loop + StreamParser); width/height are protocol
filler and sent as 0. Out-seq survives TCP reconnects so the server's
``resets`` counter keeps meaning "producer restarted", not "TCP blipped".

Raw sockets need CAP_NET_RAW: run with sudo, or install the systemd unit
(pi/install_stream_service.sh shim) which grants the capability.

Run:  sudo python3 pi/stream/chunk_shim.py                  # defaults
      sudo python3 pi/stream/chunk_shim.py --iface eth1 --dest 127.0.0.1
"""

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "firmware", "adin_drv"))
from usb_frame_source import cp, has_jpeg_eoi, looks_like_jpeg  # noqa: E402
from s6_video import Reassembler  # noqa: E402
import s5_frames  # noqa: E402  (shared EtherType)

STATUS_EVERY_S = 5.0


def valid_jpeg(data):
    """Both structural markers present — don't feed the frozen server junk."""
    return looks_like_jpeg(data) and has_jpeg_eoi(data)


def encode_frame(seq, data):
    """One ingest-wire frame: header line + JPEG (StreamParser-compatible)."""
    header = cp.frame_response("s6-shim", seq, len(data), 0, 0, 0)
    return cp.encode_message(header) + data


def open_chunk_socket(iface):
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                             socket.htons(s5_frames.ETHERTYPE))
    except PermissionError:
        sys.exit("chunk_shim: raw socket needs CAP_NET_RAW — run with sudo "
                 "or via the systemd unit (install_stream_service.sh shim)")
    sock.bind((iface, 0))
    sock.settimeout(0.5)
    return sock


def drain(sock):
    """Discard chunks queued while the ingest leg was down (stale frames)."""
    sock.setblocking(False)
    try:
        while True:
            sock.recv(4096)
    except BlockingIOError:
        pass
    finally:
        sock.settimeout(0.5)


def run_leg(raw, tcp, counters):
    """One leg: reassemble + relay until the TCP side dies (raises)."""
    rasm = Reassembler()
    fwd = fwd_bytes = bad = 0
    incomplete0 = rasm.frames_dropped
    t_status = time.monotonic()
    while True:
        try:
            pkt = raw.recv(4096)
        except (TimeoutError, OSError):
            pkt = None
        if pkt is not None:
            done = rasm.feed(pkt)
            if done is not None:
                seq, data = done
                if valid_jpeg(data):
                    tcp.sendall(encode_frame(counters["out_seq"], data))
                    counters["out_seq"] += 1
                    fwd += 1
                    fwd_bytes += len(data)
                else:
                    bad += 1
                    print("leg: frame %d reassembled but not a valid JPEG "
                          "(%d B) — dropped" % (seq, len(data)), flush=True)
        now = time.monotonic()
        if now - t_status >= STATUS_EVERY_S:
            span = now - t_status
            print("leg: %.1f fps forwarded, %.2f Mbps, %d incomplete, "
                  "%d dupe chunks, %d bad JPEG"
                  % (fwd / span, fwd_bytes * 8 / span / 1e6,
                     rasm.frames_dropped - incomplete0, rasm.chunk_dupes, bad),
                  flush=True)
            fwd = fwd_bytes = 0
            t_status = now


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iface", default="eth1", help="T1L interface")
    ap.add_argument("--dest", default="127.0.0.1", help="stream server host")
    ap.add_argument("--ingest-port", type=int, default=8081)
    args = ap.parse_args()

    raw = open_chunk_socket(args.iface)
    print("listening on %s for BMV6 chunks" % args.iface, flush=True)
    counters = {"out_seq": 0}
    while True:
        try:
            print("connecting to %s:%d (ingest)" %
                  (args.dest, args.ingest_port), flush=True)
            tcp = socket.create_connection((args.dest, args.ingest_port),
                                           timeout=10)
            try:
                drain(raw)
                print("leg: up — relaying", flush=True)
                run_leg(raw, tcp, counters)
            finally:
                tcp.close()
        except KeyboardInterrupt:
            print("stopped by user", flush=True)
            return 0
        except Exception as exc:
            print("!! leg died: %s: %s — rebuilding in 2 s"
                  % (type(exc).__name__, exc), flush=True)
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
