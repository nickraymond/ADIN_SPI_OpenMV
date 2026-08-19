#!/usr/bin/env python3
"""n6_stream_host.py -- watch the N6's detection stream in a browser (S24 bite 1).

Headless replacement for the OpenMV IDE's framebuffer view: pushes
``bench/n6_stream_board.py`` into the N6's raw REPL over the serial port,
decodes the frames it prints, and serves them as multipart MJPEG so any
browser can watch live.

    python3 bench/n6_stream_host.py                 # then open http://localhost:8090/
    python3 bench/n6_stream_host.py --tune          # centre LAB readout
    python3 bench/n6_stream_host.py --blob-thresh 20,70,10,50,-60,-15 \
                                    --blob-label ball

Nothing is written to the board -- the script runs from RAM and ``/flash`` is
never touched. Needs pyserial; stdlib otherwise.

**Ctrl-C to stop, never ``kill -9``.** The clean path interrupts the board and
leaves the raw REPL; SIGTERM/SIGHUP are handled and unwind the same way. A
SIGKILL cannot be caught, and leaving the board streaming into a closed
endpoint has taken it off the USB bus entirely -- which needs a physical
replug, because a Mac cannot power-cycle the port.

Why base64 rather than the project's framed-binary wire format (S3's
``StreamParser``): the raw REPL ends its output on byte 0x04 and JPEG payloads
contain 0x04. Base64 costs ~33% bandwidth and removes the whole class of
problem. Why the port is driven directly rather than through ``mpremote run``:
mpremote accumulates and rescans the script's entire output, so a long stream
decays with total bytes (~20 fps -> <2 fps, measured -- see DESIGN S24). The
MJPEG/stats half follows ``pi/stream/stream_server.py`` deliberately, so the
two viewers behave the same way.
"""

import argparse
import base64
import binascii
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOARD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "n6_stream_board.py")

_PAGE = """<!doctype html><html><head><title>OpenMV N6 &mdash; live detection</title>
<style>body{margin:0;background:#111;color:#ddd;font-family:ui-monospace,monospace;
text-align:center}img{max-width:100%;image-rendering:pixelated}
#s{color:#8c8;white-space:pre-wrap;font-size:13px}h3{margin:8px;font-weight:600}</style>
</head><body>
<h3>OpenMV N6 &mdash; yolov8n_192 + colour blobs</h3>
<img src="/stream"/>
<pre id="s">connecting&hellip;</pre>
<script>setInterval(async()=>{try{const r=await fetch('/stats.json');const j=await r.json();
document.getElementById('s').textContent=
 'fps '+j.fps+'   frames '+j.frames+'   det '+j.det+'   blobs '+j.blobs+
 '   resyncs '+j.resyncs+'\\n'+
 'capture '+j.cap_ms+' ms   inference '+j.inf_ms+' ms   blobs '+j.blob_ms+
 ' ms   encode '+j.enc_ms+' ms\\n'+
 (j.lab ? 'centre patch LAB  L='+j.lab[0]+'  A='+j.lab[1]+'  B='+j.lab[2]+
   '   -> --blob-thresh '+j.suggest+'\\n' : '')+j.info;
}catch(e){}},500)</script></body></html>"""


#: Half-width of the suggested LAB box around a measured centre-patch mean.
#: Wide enough to survive the lighting change from turning your hand, narrow
#: enough not to swallow the wall behind the object.
LAB_MARGIN = (25, 20, 20)
LAB_LIMITS = ((0, 100), (-128, 127), (-128, 127))


def suggest_threshold(lab, margin=LAB_MARGIN, limits=LAB_LIMITS):
    """Turn a measured centre-patch LAB mean into a --blob-thresh argument.

    Pure arithmetic so the clamping is unit-testable without a board.
    """
    parts = []
    for value, half, (lo_lim, hi_lim) in zip(lab, margin, limits):
        lo = max(lo_lim, int(value) - half)
        hi = min(hi_lim, int(value) + half)
        parts += [lo, hi]
    return ",".join(str(p) for p in parts)


class Latest:
    """Newest JPEG + seq, shared between the reader thread and HTTP handlers."""

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


class Stats:
    """Rolling counters. Pure arithmetic (no I/O) so the maths is testable."""

    WINDOW = 30

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._times = []
        self.frames = 0
        self.bytes = 0
        self.det = 0
        self.blobs = 0
        self.info = ""
        self.junk = []
        self.lab = None
        self.resyncs = 0        # frames dropped to a framing/length fault
        self._acc = {"cap_us": 0, "inf_us": 0, "blob_us": 0, "enc_us": 0}

    def note(self, hdr, nbytes):
        self.frames += 1
        self.bytes += nbytes
        self.det = hdr.get("det", 0)
        self.blobs = hdr.get("blobs", 0)
        self.lab = hdr.get("lab") or None
        for k in self._acc:
            self._acc[k] += hdr.get(k, 0)
        self._times.append(self._clock())
        del self._times[:-self.WINDOW]

    def fps(self):
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / span if span > 0 else 0.0

    def _mean_ms(self, key):
        return round(self._acc[key] / self.frames / 1000.0, 1) if self.frames else 0.0

    def snapshot(self):
        return {
            "frames": self.frames,
            "bytes": self.bytes,
            "fps": round(self.fps(), 1),
            "det": self.det,
            "blobs": self.blobs,
            "lab": self.lab,
            "suggest": suggest_threshold(self.lab) if self.lab else "",
            "resyncs": self.resyncs,
            "cap_ms": self._mean_ms("cap_us"),
            "inf_ms": self._mean_ms("inf_us"),
            "blob_ms": self._mean_ms("blob_us"),
            "enc_ms": self._mean_ms("enc_us"),
            "info": self.info,
        }


def build_board_script_text(cfg):
    """The board script with a ``_CFG`` prelude injected. Returns source text."""
    with open(BOARD_SCRIPT) as fh:
        return "_CFG = %r\n" % (cfg,) + fh.read()


def build_board_script(cfg, dest_dir):
    """Same, written to a file -- handy for inspecting what actually ran."""
    path = os.path.join(dest_dir, "n6_stream_run.py")
    with open(path, "w") as fh:
        fh.write(build_board_script_text(cfg))
    return path


def reader_loop(out, latest, stats, state):
    """Consume the board's output: ``#I`` banner, ``#F`` header + base64 payload.

    ``out`` is anything with a bytes ``readline()`` -- a ``SerialBoard`` or a
    plain stream in the tests.

    Junk lines are surfaced rather than swallowed -- a board that is unwell
    prints tracebacks, and silently dropping them is how a dead stream looks
    like an idle one (CLAUDE.md rule 6).
    """
    while True:
        line = out.readline()
        if not line:
            break
        line = line.rstrip(b"\r\n")
        if not line:
            continue
        if line.startswith(b"#F "):
            try:
                hdr = json.loads(line[3:].decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                stats.junk.append(line[:120])
                continue
            # The board writes "\n"; the CDC/mpremote path returns "\r\n", so
            # the payload line carries a trailing CR that is NOT part of the
            # base64 (the CRLF trap in CLAUDE.md rule 4, seen again here).
            payload = out.readline().rstrip(b"\r\n")
            if len(payload) != hdr.get("b64", -1):
                stats.resyncs += 1
                stats.junk.append(b"short payload seq %d: %d of %d"
                                  % (hdr.get("seq", -1), len(payload),
                                     hdr.get("b64", -1)))
                continue
            try:
                jpg = base64.b64decode(payload)
            except (ValueError, binascii.Error):
                stats.junk.append(b"undecodable payload seq %d" % hdr.get("seq", -1))
                continue
            if not (len(jpg) >= 4 and jpg[0] == 0xFF and jpg[1] == 0xD8):
                stats.junk.append(b"seq %d is not a JPEG" % hdr.get("seq", -1))
                continue
            latest.put(hdr.get("seq", 0), jpg)
            stats.note(hdr, len(jpg))
        elif line.startswith(b"#I "):
            stats.info = line[3:].decode("utf-8", "replace")
            print("board: %s" % stats.info, flush=True)
        elif line.startswith(b"#D "):
            print("board: stream done %s" % line[3:].decode("utf-8", "replace"),
                  flush=True)
        else:
            text = line.decode("utf-8", "replace")
            stats.junk.append(line[:120])
            print("board: %s" % text, flush=True)
    state["alive"] = False
    print("board: stream ended", flush=True)


def make_handler(latest, stats):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _body(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                self._body(_PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/stats.json":
                self._body(json.dumps(stats.snapshot()).encode(), "application/json")
            elif self.path == "/frame.jpg":
                frame, _ = latest.get()
                if frame:
                    self._body(frame, "image/jpeg")
                else:
                    self.send_error(503, "no frame yet")
            elif self.path == "/stream":
                self._mjpeg()
            else:
                self.send_error(404)

        def _mjpeg(self):
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
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                        self.wfile.write(b"Content-Length: %d\r\n\r\n" % len(frame))
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


class SerialBoard:
    """Own the N6's serial port directly and run a script in the raw REPL.

    This replaces ``mpremote run`` **in the data path**, and the reason is
    measured, not stylistic: ``mpremote run`` accumulates the script's whole
    output and rescans that buffer for the end-of-execution marker, so a
    long-running stream degrades as total output grows. It started at ~20 fps
    and decayed to under 2 fps after a few thousand frames, while the board's
    own per-stage timings stayed flat at 38.5 ms/frame -- the board was never
    the problem. mpremote is a fine tool for bounded benchmark output; it is
    not a transport. Driving the port ourselves is also what
    ``pi/stream/usb_frame_source.py`` already does for the AE3.

    Nothing is written to the board: the script is pushed into the raw REPL
    and executed from RAM, exactly as ``mpremote run`` would.
    """

    #: pyboard.py chunks raw-REPL writes so the board's input buffer keeps up.
    CHUNK = 256
    CHUNK_PAUSE_S = 0.01

    def __init__(self, port, baudrate=115200):
        import serial                      # lazy: unit tests need no pyserial
        self._serial = serial
        self.port = port
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=0.1)
        self._buf = bytearray()

    def _read_until(self, token, timeout_s=5.0):
        deadline = time.monotonic() + timeout_s
        seen = bytearray()
        while time.monotonic() < deadline:
            seen += self.ser.read(256)
            if token in seen:
                return bytes(seen)
        raise TimeoutError("N6 %s: never sent %r during raw-REPL entry "
                           "(saw %r)" % (self.port, token, bytes(seen[-80:])))

    def start(self, script_text):
        """Interrupt whatever is running, enter the raw REPL, run the script."""
        self.ser.write(b"\r\x03\x03")       # Ctrl-C twice: stop main.py
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self.ser.write(b"\r\x01")           # Ctrl-A: raw REPL
        self._read_until(b"raw REPL; CTRL-B to exit")
        payload = script_text.encode("utf-8")
        for i in range(0, len(payload), self.CHUNK):
            self.ser.write(payload[i:i + self.CHUNK])
            time.sleep(self.CHUNK_PAUSE_S)
        self.ser.write(b"\x04")             # Ctrl-D: execute
        self._read_until(b"OK")
        return self

    def readline(self):
        """One line, or b'' at end of execution. Buffered over raw reads."""
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._buf[:nl])
                del self._buf[:nl + 1]
                # The raw REPL emits 0x04 when the script finishes.
                if line.startswith(b"\x04"):
                    return b""
                return line + b"\n"
            chunk = self.ser.read(65536)
            if chunk:
                self._buf += chunk
            elif not self.ser.is_open:
                return b""

    def stop(self):
        try:
            self.ser.write(b"\r\x03\x03")   # interrupt the running script
            time.sleep(0.1)
            self.ser.write(b"\r\x02")       # Ctrl-B: back to the friendly REPL
            time.sleep(0.1)
        except self._serial.SerialException:
            pass
        finally:
            try:
                self.ser.close()
            except self._serial.SerialException:
                pass


class QuietServer(ThreadingHTTPServer):
    """A browser closing an MJPEG tab resets the connection, which is normal.

    socketserver's default dumps a full traceback for it, which buries the
    board's own messages -- the ones that matter -- in the log.
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default=None,
                    help="serial device (default: the sole /dev/cu.usbmodem*)")
    ap.add_argument("--http-port", type=int, default=8090)
    ap.add_argument("--framesize", default="VGA",
                    help="csi framesize NAME, e.g. QVGA VGA HD SXGAM (default VGA)")
    ap.add_argument("--quality", type=int, default=50)
    ap.add_argument("--max-seconds", type=float, default=3600)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--model", default="/rom/yolov8n_192.tflite")
    ap.add_argument("--threshold", type=float, default=0.4)
    ap.add_argument("--no-detect", action="store_true",
                    help="skip inference (isolates capture+encode cost)")
    ap.add_argument("--no-blobs", action="store_true",
                    help="skip the colour-blob overlay")
    ap.add_argument("--blob-thresh", default=None,
                    help="LAB threshold L_lo,L_hi,A_lo,A_hi,B_lo,B_hi")
    ap.add_argument("--blob-pixels", type=int, default=150)
    ap.add_argument("--blob-label", default="blob",
                    help="what to call a colour blob in the overlay "
                         "(e.g. --blob-label ball)")
    ap.add_argument("--tune", action="store_true",
                    help="draw a centre target and report its mean LAB, so you "
                         "can read a --blob-thresh off a real object")
    return ap.parse_args(argv)


def cfg_from_args(args):
    cfg = {
        "framesize": args.framesize,
        "quality": args.quality,
        "max_seconds": args.max_seconds,
        "max_frames": args.max_frames,
        "model": args.model,
        "threshold": args.threshold,
        "detect": not args.no_detect,
        "blobs": not args.no_blobs,
        "blob_pixels": args.blob_pixels,
        "blob_area": args.blob_pixels,
        "tune": args.tune,
        "blob_label": args.blob_label,
    }
    if args.blob_thresh:
        parts = [int(p) for p in args.blob_thresh.split(",")]
        if len(parts) != 6:
            raise SystemExit("--blob-thresh needs 6 comma-separated ints")
        cfg["blob_thresh"] = tuple(parts)
    return cfg


def find_port(explicit=None):
    """Resolve the N6's serial device, failing with something actionable."""
    if explicit:
        return explicit
    import glob
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        raise SystemExit("no /dev/cu.usbmodem* found -- is the N6 plugged in? "
                         "(name it explicitly with --port)")
    if len(ports) > 1:
        raise SystemExit("several boards present (%s) -- choose one with --port"
                         % ", ".join(ports))
    return ports[0]


def main(argv=None):
    args = parse_args(argv)
    cfg = cfg_from_args(args)
    port = find_port(args.port)
    script_text = build_board_script_text(cfg)

    print("connecting to %s" % port, flush=True)
    try:
        board = SerialBoard(port).start(script_text)
    except ImportError:
        raise SystemExit("pyserial missing -- pip3 install --user pyserial")
    except (OSError, TimeoutError) as exc:
        raise SystemExit(
            "could not start the board script on %s: %s\nNothing else may hold "
            "the port -- close any mpremote session or serial monitor, then "
            "retry." % (port, exc))

    latest, stats = Latest(), Stats()
    state = {"alive": True}
    threading.Thread(target=reader_loop, args=(board, latest, stats, state),
                     daemon=True).start()

    httpd = QuietServer(("127.0.0.1", args.http_port),
                        make_handler(latest, stats))
    url = "http://localhost:%d/" % args.http_port
    print("open %s  (Ctrl-C to stop)" % url, flush=True)

    # SIGTERM must unwind through the same path as Ctrl-C. A stop that skips
    # board.stop() leaves the board streaming into a closed endpoint from
    # inside the raw REPL, and it can drop off the USB bus entirely -- which
    # then needs a physical replug, because a Mac cannot power-cycle the port.
    # (Measured the hard way: a `kill -9` did exactly that. SIGKILL still
    # cannot be caught, so `kill -9` remains the one thing not to do.)
    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGHUP, _term)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...", flush=True)
    finally:
        httpd.server_close()
        board.stop()
        s = stats.snapshot()
        print("frames %d  mean fps %.1f  resyncs %d  inference %.1f ms  "
              "encode %.1f ms" % (s["frames"], s["fps"], s["resyncs"],
                                  s["inf_ms"], s["enc_ms"]), flush=True)
        if stats.junk:
            print("junk lines seen (last 5): %r" % (stats.junk[-5:],), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
