#!/usr/bin/env python3
"""stream_server.py — S3 receiver: framed JPEGs in, multipart MJPEG out (S3 bite 2).

Runs on the receiving node (nereus001). Two faces:

- **Ingest** (TCP :8081): accepts one producer at a time sending the same wire
  format as the USB capture service — a ``frame`` JSON header line then exactly
  ``size_bytes`` of JPEG (parsed with bite 1's ``StreamParser``, one framing for
  the whole project). S3's producer is ``t1l_sender.py`` across the T1L pair;
  S6's is the Pi shim daemon connecting locally. THIS INTERFACE IS FROZEN AFTER
  S3 (TRACKER) — S6 must plug in unchanged.
- **HTTP** (:8080): ``/`` viewer page, ``/stream`` multipart/x-mixed-replace
  MJPEG of the latest frame (pattern proven in the legacy rig's focus_stream),
  ``/frame.jpg`` single latest frame (artifact checks), ``/stats.json`` live
  counters (frames, bytes, fps, gaps, resets) — the S3 bench measurement hook.

Stdlib only — nothing to install on the receiver node.

Run:  python3 pi/stream/stream_server.py            # :8080 HTTP, :8081 ingest
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usb_frame_source import StreamParser, looks_like_jpeg  # noqa: E402

_PAGE = """<!doctype html><html><head><title>S3 T1L stream</title></head>
<body style="margin:0;background:#111;color:#ddd;font-family:monospace;text-align:center">
<h3 style="margin:8px">BM camera node &mdash; live over 10BASE-T1L</h3>
<img src="/stream" style="max-width:100%%;image-rendering:pixelated"/>
<pre id="s" style="color:#8c8"></pre>
<script>setInterval(async()=>{try{const r=await fetch('/stats.json');
document.getElementById('s').textContent=JSON.stringify(await r.json());}catch(e){}},1000)</script>
</body></html>"""


class StreamStats:
    """Pure counter logic (no I/O) so gap/reset accounting is unit-testable.

    ``gaps`` counts missing sequence numbers on the ingest hop (producer
    re-sequences per sent frame, so a gap = a frame lost in transit).
    A sequence going backwards means the producer restarted — counted in
    ``resets``, not gaps.
    """

    FPS_WINDOW = 30  # frames used for the rolling fps estimate

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._times = []
        self.frames = 0
        self.bytes = 0
        self.gaps = 0
        self.resets = 0
        self.last_seq = None
        self.started = clock()

    def note_frame(self, seq, nbytes):
        now = self._clock()
        self.frames += 1
        self.bytes += nbytes
        if self.last_seq is not None:
            if seq < self.last_seq:
                self.resets += 1
            elif seq > self.last_seq + 1:
                self.gaps += seq - self.last_seq - 1
        self.last_seq = seq
        self._times.append(now)
        del self._times[:-self.FPS_WINDOW]

    def fps(self):
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / span if span > 0 else 0.0

    def snapshot(self, connected):
        return {
            "frames": self.frames,
            "bytes": self.bytes,
            "fps": round(self.fps(), 2),
            "gaps": self.gaps,
            "resets": self.resets,
            "seq": self.last_seq,
            "uptime_s": round(self._clock() - self.started, 1),
            "ingest_connected": connected,
        }


class LatestFrame:
    """Latest JPEG + seq, shared between the ingest thread and HTTP handlers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = b""
        self._seq = -1

    def put(self, seq, data):
        with self._lock:
            self._frame, self._seq = data, seq

    def get(self):
        with self._lock:
            return self._frame, self._seq


def ingest_loop(bind, port, latest, stats, state):
    """Accept one producer at a time; parse frames; update shared state forever."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind, port))
    srv.listen(1)
    print("ingest: listening on %s:%d" % (bind, port), flush=True)
    while True:
        conn, addr = srv.accept()
        print("ingest: producer connected from %s:%d" % addr, flush=True)
        state["connected"] = True
        parser = StreamParser()
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                for kind, msg, payload in parser.feed(chunk):
                    if kind == "frame":
                        seq = msg["frame"]["seq"]
                        if not looks_like_jpeg(payload):
                            print("ingest: seq %d is not a JPEG (%d B) — dropped"
                                  % (seq, len(payload)), flush=True)
                            continue
                        latest.put(seq, payload)
                        stats.note_frame(seq, len(payload))
                    elif kind == "junk":
                        print("ingest: junk line %r" % msg[:80], flush=True)
        except ConnectionError as exc:
            print("ingest: connection error: %s" % exc, flush=True)
        finally:
            state["connected"] = False
            conn.close()
            print("ingest: producer disconnected", flush=True)


def make_handler(latest, stats, state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # keep the console for ingest events
            pass

        def _send_body(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                self._send_body(_PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/stats.json":
                body = json.dumps(stats.snapshot(state["connected"])).encode()
                self._send_body(body, "application/json")
            elif self.path == "/frame.jpg":
                frame, _seq = latest.get()
                if frame:
                    self._send_body(frame, "image/jpeg")
                else:
                    self.send_error(503, "no frame received yet")
            elif self.path == "/stream":
                self._serve_mjpeg()
            else:
                self.send_error(404)

        def _serve_mjpeg(self):
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = -1
            try:
                while True:
                    frame, seq = latest.get()
                    if frame and seq != last:
                        last = seq
                        self.wfile.write(b"--frame\r\n"
                                         b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(b"Content-Length: %d\r\n\r\n" % len(frame))
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--ingest-port", type=int, default=8081)
    args = ap.parse_args()

    latest, stats = LatestFrame(), StreamStats()
    state = {"connected": False}
    threading.Thread(target=ingest_loop,
                     args=(args.bind, args.ingest_port, latest, stats, state),
                     daemon=True).start()
    httpd = ThreadingHTTPServer((args.bind, args.http_port),
                                make_handler(latest, stats, state))
    print("http: serving on %s:%d — open /stream or / in a browser"
          % (args.bind, args.http_port), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
