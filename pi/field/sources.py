#!/usr/bin/env python3
"""Frame sources for the field rig: OpenMV boards over serial, IMX708 over CSI.

The S8 viewer (``bench/n6_stream_host.py``) already solved the hard half of
this -- the raw-REPL transport, the reconnect supervisor, and the backoff that
keeps a viewer from wedging the very board it is waiting for. Those are
IMPORTED here, not copied, exactly as ``pi/hil/hil_harness.py`` imports
``SerialBoard``. What is new is that this rig has a third camera which is not
a serial board at all, so "a stream" has to stop meaning "a board".

Two producers, one contract -- both fill a ``Latest`` slot and a ``StreamStats``:

* ``SerialSource``  -- AE3 / N6, reusing the proven S8 machinery verbatim.
* ``CsiSource``     -- IMX708 via ``rpicam-vid --codec mjpeg`` on stdout.

Why rpicam-vid and not picamera2: picamera2 is not installed on this Pi Zero
2 W and is heavy for it, while rpicam-vid hands us JPEGs the ISP already
encoded. Measured on this rig 2026-09-06: 640x480 MJPEG at 15 fps cost ~1.2 s
of user CPU over 7.4 s wall on 4 cores (~5% of one core, load average 0.08)
and produced ~2.6 Mbps. All three cameras encode off-CPU -- the Pi is a byte
relay, not an encoder, which is the only reason three streams fit on a Zero.
"""

import os
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "bench"))

from n6_stream_host import Latest, RETRY_BACKOFF_S     # noqa: E402

#: JPEG framing markers. A concatenated MJPEG stream carries no length field,
#: so the splitter has to find these itself.
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


class StreamStats:
    """Rolling counters for one stream. Pure arithmetic, so it is testable.

    A deliberately leaner cousin of the S8 ``Stats``: this rig streams video
    and nothing else, so the blob/model/LAB columns that class carries would
    be permanently empty here, and an always-zero field on a page is worse
    than an absent one -- it invites someone to trust it.

    The attribute surface IS kept compatible with ``n6_stream_host.reader_loop``
    (``note``/``junk``/``resyncs``/``status``/``info_fields``/``board``/
    ``saved``) so that loop can be reused without a shim.
    """

    WINDOW = 30

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._times = []
        self._win_bytes = []
        self.frames = 0
        self.bytes = 0
        self.resyncs = 0
        self.reconnects = 0
        self.saved = 0
        self.status = "starting"
        self.info = ""
        self.info_fields = {}
        self.board = ""
        self.junk = []
        self.last_frame_at = None

    def note(self, hdr, nbytes):
        now = self._clock()
        self.last_frame_at = now
        self.frames += 1
        self.bytes += nbytes
        self._times.append(now)
        self._win_bytes.append(nbytes)
        del self._times[:-self.WINDOW]
        del self._win_bytes[:-self.WINDOW]

    def _span(self):
        return (self._times[-1] - self._times[0]) if len(self._times) >= 2 else 0.0

    def fps(self):
        span = self._span()
        return (len(self._times) - 1) / span if span > 0 else 0.0

    def mbps(self):
        # Pair N-1 intervals with the last N-1 payloads: the first sample's
        # bytes belong to a frame that arrived before the window opened.
        span = self._span()
        return sum(self._win_bytes[1:]) * 8.0 / span / 1e6 if span > 0 else 0.0

    def kb_per_frame(self):
        n = len(self._win_bytes)
        return (sum(self._win_bytes) / n / 1024.0) if n else 0.0

    def snapshot(self):
        return {
            "frames": self.frames,
            "bytes": self.bytes,
            "fps": round(self.fps(), 1),
            "mbps": round(self.mbps(), 2),
            "kb_frame": round(self.kb_per_frame(), 1),
            "resyncs": self.resyncs,
            "reconnects": self.reconnects,
            "status": self.status,
            "board": self.board,
            # Seconds since the last frame. The page keeps showing the last
            # good JPEG when a camera goes away, so liveness has to be
            # MEASURED and displayed -- a frozen stream and a motionless
            # scene are indistinguishable by eye. Three separate S24 bugs
            # all presented as "a plausible still image".
            "stale_s": (round(self._clock() - self.last_frame_at, 1)
                        if self.last_frame_at is not None else None),
            "info": self.info,
            "junk": [j.decode("utf-8", "replace") if isinstance(j, bytes) else str(j)
                     for j in self.junk[-3:]],
        }


class SourceView:
    """One camera's panel: its label, its kind, and its live state.

    Per-source isolation is the whole point -- one camera wedging must not
    disturb the other two, which is why every counter and every thread state
    lives here rather than in the process.
    """

    def __init__(self, label, kind, target=""):
        self.label = label
        self.kind = kind              # "csi" | "serial"
        self.target = target          # port path, or camera index
        self.latest = Latest()
        self.stats = StreamStats()
        self.state = {"alive": True, "quit": False, "board": None}
        #: What was ASKED of this camera (framesize, fps, and for the CSI its
        #: pixel size). Reported beside what it actually delivers, because
        #: "15 fps" as a setting and 3.6 fps as a measurement are the whole
        #: point of a camera-comparison tool -- showing only one of them
        #: turns a hardware limit into a mystery.
        self.want = {}

    def snapshot(self):
        s = self.stats.snapshot()
        s["label"] = self.label
        s["kind"] = self.kind
        s["target"] = self.target or "(auto)"
        want = self.want or {}
        s["set_fps"] = want.get("fps")
        s["framesize"] = want.get("framesize")
        # Resolution SET: for a board the sensor decides the letterbox, so the
        # authoritative number is the one it reported in its #I banner; only
        # fall back to what we asked for when it has not answered yet.
        info = self.stats.info_fields or {}
        w = info.get("w") or want.get("w")
        h = info.get("h") or want.get("h")
        s["res"] = ("%dx%d" % (w, h)) if w and h else None
        return s


def split_mjpeg(buf):
    """Pull whole JPEGs out of a byte buffer. Returns (frames, remainder).

    Pure function so the framing is unit-testable without a camera. Leading
    bytes before the first SOI are discarded: a stream joined mid-frame must
    resynchronise rather than emit a truncated image that decodes to garbage.
    """
    frames = []
    while True:
        start = buf.find(SOI)
        if start < 0:
            # No SOI at all: keep only a trailing byte, in case it is half of
            # a marker split across reads.
            return frames, buf[-1:] if buf else buf
        end = buf.find(EOI, start + 2)
        if end < 0:
            return frames, buf[start:]
        frames.append(bytes(buf[start:end + 2]))
        buf = buf[end + 2:]


def csi_reader_loop(stream, latest, stats, state, chunk=4096):
    """Feed frames from an MJPEG byte stream into one view's slot."""
    buf = b""
    seq = 0
    while not state.get("quit"):
        data = stream.read(chunk)
        if not data:
            break
        buf += data
        frames, buf = split_mjpeg(buf)
        for jpg in frames:
            seq += 1
            latest.put(seq, jpg)
            stats.note({"seq": seq}, len(jpg))
    state["alive"] = False


def rpicam_argv(width, height, fps, quality, camera=0, extra=None):
    """The rpicam-vid command line. Separated so tests can assert on it."""
    argv = [
        "rpicam-vid", "-n", "--codec", "mjpeg",
        "--camera", str(camera),
        "--width", str(width), "--height", str(height),
        "--framerate", str(fps),
        "--quality", str(quality),
        "-t", "0",              # run until we kill it
        "-o", "-",              # MJPEG to stdout
    ]
    return argv + list(extra or [])


def supervise_csi(view, width, height, fps, quality, camera=0,
                  backoff=RETRY_BACKOFF_S, spawn=None, sleep=time.sleep):
    """Keep the CSI stream running, restarting it if rpicam-vid exits.

    Same shape as the S8 serial supervisor, and for the same reason: a camera
    that drops out must recover without a human at a terminal. The backoff is
    shared with the serial path rather than re-tuned -- a viewer that hammers
    a busy resource is the fault, not the fix.
    """
    spawn = spawn or (lambda argv: subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
    argv = rpicam_argv(width, height, fps, quality, camera)
    fails = 0
    first = True
    while not view.state.get("quit"):
        try:
            proc = spawn(argv)
        except OSError as exc:
            fails += 1
            wait = backoff[min(fails - 1, len(backoff) - 1)]
            view.stats.status = "rpicam-vid will not start: %s (retry in %gs)" % (
                exc, wait)
            sleep(wait)
            continue
        fails = 0
        if not first:
            view.stats.reconnects += 1
        first = False
        view.state["board"] = proc
        view.stats.status = "streaming %dx%d @%g fps" % (width, height, fps)
        try:
            csi_reader_loop(proc.stdout, view.latest, view.stats, view.state)
        finally:
            # SIGTERM, never SIGKILL. The hard kill is banned on this bench
            # (it took the N6 off the USB bus and needed a physical replug);
            # rpicam holds the CSI device and the ISP, and orphaning those
            # makes the NEXT start fail for reasons that look like hardware.
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:               # noqa: BLE001
                pass
        if view.state.get("quit"):
            break
        view.stats.status = "camera stream ended -- restarting"
        sleep(1.0)
