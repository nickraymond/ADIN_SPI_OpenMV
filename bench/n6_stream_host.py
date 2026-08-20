#!/usr/bin/env python3
"""n6_stream_host.py -- watch the N6's detection stream in a browser (S24 bite 1).

Headless replacement for the OpenMV IDE's framebuffer view: pushes
``bench/n6_stream_board.py`` into the N6's raw REPL over the serial port,
decodes the frames it prints, and serves them as multipart MJPEG so any
browser can watch live.

    python3 bench/n6_stream_host.py                 # then open http://localhost:8090/
    python3 bench/n6_stream_host.py --tune          # centre LAB readout
    python3 bench/n6_stream_host.py --blob-thresh pink:20,70,10,50,-20,25 \
                                    --blob-thresh purple:10,80,10,65,-75,-10
    python3 bench/n6_stream_host.py --blob-thresh pink:... --save-frames data/raw

Nothing is written to the board -- the script runs from RAM and ``/flash`` is
never touched. Needs ``mpremote`` (its transport does the raw-REPL attach) and
``pyserial``; stdlib otherwise.

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
import urllib.parse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOARD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "n6_stream_board.py")



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


def parse_blob_class(spec, default_label, index):
    """``"pink:20,70,10,50,-20,25"`` or a bare 6-tuple -> ``(label, tuple)``.

    Kept as pure text-in/tuple-out so every error message is unit-testable
    without a board attached.
    """
    label, _, numbers = spec.rpartition(":")
    if not label:
        # Bare form: keep the pre-bite-A spelling working, and name the box
        # after --blob-label so a single-colour run reads exactly as before.
        label = default_label if index == 0 else "%s%d" % (default_label, index + 1)
    parts = [p.strip() for p in numbers.split(",")]
    if len(parts) != 6:
        raise ValueError("--blob-thresh %r needs 6 comma-separated ints "
                         "(L_lo,L_hi,A_lo,A_hi,B_lo,B_hi), got %d"
                         % (spec, len(parts)))
    try:
        values = tuple(int(p) for p in parts)
    except ValueError:
        raise ValueError("--blob-thresh %r has a non-integer bound" % spec)
    return label, values


def blob_classes_from_args(args):
    """-> ``[(label, thresh), ...]``, or ``[]`` to leave the board default.

    Returning empty rather than materialising the default keeps ONE copy of
    the default threshold, on the board, where the script can also run alone.
    """
    if not args.blob_thresh:
        return []
    classes, seen = [], set()
    for i, spec in enumerate(args.blob_thresh):
        label, values = parse_blob_class(spec, args.blob_label, i)
        if label in seen:
            raise ValueError("--blob-thresh: duplicate colour name %r -- the "
                             "per-class counts would be unreadable" % label)
        seen.add(label)
        classes.append((label, values))
    return classes


def lab_box_volume(box):
    """Volume of a (L_lo,L_hi,A_lo,A_hi,B_lo,B_hi) box, edges counted inclusively."""
    v = 1
    for axis in range(3):
        v *= max(0, box[axis * 2 + 1] - box[axis * 2]) + 1
    return v


def lab_overlap_fraction(earlier, later):
    """How much of `later` sits inside `earlier`, as a fraction of `later`.

    A yes/no answer is not actionable -- a 2% corner and a fully nested box
    are different problems -- so the guard reports the share of the later box
    that the earlier one will claim.
    """
    inter = 1
    for axis in range(3):
        lo = max(earlier[axis * 2], later[axis * 2])
        hi = min(earlier[axis * 2 + 1], later[axis * 2 + 1])
        if lo > hi:
            return 0.0
        inter *= (hi - lo) + 1
    volume = lab_box_volume(later)
    return inter / volume if volume else 0.0


def shadowed_pairs(classes):
    """-> [(earlier_label, later_label), ...] for boxes that overlap in LAB.

    Measured on the N6 (S8 bite A, nibble 3): in ONE ``find_blobs`` call over
    a threshold list, each pixel is claimed by the FIRST matching threshold in
    list order, and ``merge=True`` ORs the codes of blobs it joins. So two
    boxes that overlap in LAB are not both counted -- the earlier one takes
    the shared pixels and the later one can report zero, with nothing to show
    it happened. Disjoint boxes (pink vs purple) are unaffected: first match
    is the only match.
    """
    pairs = []
    for i in range(len(classes)):
        for j in range(i + 1, len(classes)):
            frac = lab_overlap_fraction(classes[i][1], classes[j][1])
            if frac > 0:
                pairs.append((classes[i][0], classes[j][0], frac))
    return pairs


def parse_board_thresh(spec, index=0):
    """``"AE3:pink:32,75,14,32,-16,6"`` -> ``("AE3", ("pink", tuple))``."""
    label, _, rest = spec.partition(":")
    label = label.strip()
    # All THREE parts are required. Without this, "pink:1,2,3,4,5,6" parses as
    # a board called "pink" with an unnamed colour -- structurally identical
    # to a valid --blob-thresh, and wrong in a way nothing downstream notices.
    if not label or ":" not in rest:
        raise ValueError("--board-thresh wants LABEL:NAME:L,L,A,A,B,B "
                         "(all three parts), got %r" % spec)
    name, values = parse_blob_class(rest, "blob", index)
    return label, (name, values)


def board_thresh_map(args, known_labels):
    """-> ``{board_label: [(name, thresh), ...]}`` from --board-thresh.

    A typo in a board label is rejected rather than silently ignored: a
    threshold that quietly applies to nothing looks exactly like a threshold
    that does not work.
    """
    out = {}
    for spec in (args.board_thresh or []):
        label, entry = parse_board_thresh(spec, len(out.get(spec, ())))
        if label not in known_labels:
            raise ValueError("--board-thresh names board %r, which is not one "
                             "of %s" % (label, ", ".join(known_labels) or "(none)"))
        out.setdefault(label, [])
        if any(name == entry[0] for name, _ in out[label]):
            raise ValueError("--board-thresh: duplicate colour %r for board %r"
                             % (entry[0], label))
        out[label].append(entry)
    return out


def board_pixels_map(args, known_labels):
    """-> ``{board_label: min_area}`` from --board-pixels."""
    out = {}
    for spec in (args.board_pixels or []):
        label, _, value = spec.partition(":")
        label = label.strip()
        if not label or not value.strip().isdigit():
            raise ValueError("--board-pixels wants LABEL:N, got %r" % spec)
        if label not in known_labels:
            raise ValueError("--board-pixels names board %r, which is not one "
                             "of %s" % (label, ", ".join(known_labels) or "(none)"))
        out[label] = int(value)
    return out


def blob_classes_from_args_or_exit(args):
    try:
        return blob_classes_from_args(args)
    except ValueError as exc:
        raise SystemExit(str(exc))


def class_labels(args):
    """The names the HUD shows, whether or not thresholds were given."""
    return [lbl for lbl, _ in blob_classes_from_args(args)] or [args.blob_label]


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

    def __init__(self, clock=time.monotonic, labels=None):
        self._clock = clock
        self.labels = list(labels or [])
        self.blob_counts = [0] * len(self.labels)
        self.model_counts = []
        self.amb = 0
        self.saved = 0
        self._times = []
        self._win_bytes = []
        self._win_wire = []
        self.frames = 0
        self.bytes = 0
        self.det = 0
        self.blobs = 0
        self.info = ""
        self.board = ""    # e.g. "OpenMV N6 with STM32N657X0"
        self.info_fields = {}   # parsed #I banner: geometry, model, q
        self.junk = []
        self.lab = None
        self.resyncs = 0        # frames dropped to a framing/length fault
        self.reconnects = 0
        self.status = "starting"
        self.last_frame_at = None
        self._acc = {"cap_us": 0, "inf_us": 0, "blob_us": 0, "enc_us": 0,
                     "mdec_us": 0}

    def note(self, hdr, nbytes):
        now = self._clock()          # one timestamp per frame, read once
        self.last_frame_at = now
        self.frames += 1
        self.bytes += nbytes
        self.det = hdr.get("det", 0)
        self.blobs = hdr.get("blobs", 0)
        # Per-class counts are the board's, verbatim. Trusting the length from
        # the wire rather than from our own config means a board running an
        # older script shows short-but-honest counts instead of a crash.
        self.blob_counts = list(hdr.get("bc", []))
        self.model_counts = list(hdr.get("mc", []))
        self.amb = hdr.get("amb", 0)
        self.lab = hdr.get("lab") or None
        for k in self._acc:
            self._acc[k] += hdr.get(k, 0)
        self._times.append(now)
        # Bandwidth is measured over the SAME rolling window as fps, from the
        # JPEG bytes actually delivered -- never from the quality setting or a
        # nominal rate. A programmed q says what was asked for; only the bytes
        # say what came out, and on these boards that varies with the scene by
        # more than a factor of two.
        self._win_bytes.append(nbytes)
        # Wire cost is the base64 payload actually carried over USB, which the
        # board reports per frame -- ~4/3 of the JPEG. Reporting only the image
        # rate would understate what the link is really moving.
        self._win_wire.append(hdr.get("b64", 0))
        del self._times[:-self.WINDOW]
        del self._win_bytes[:-self.WINDOW]
        del self._win_wire[:-self.WINDOW]

    def fps(self):
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / span if span > 0 else 0.0

    def _window_span(self):
        return (self._times[-1] - self._times[0]) if len(self._times) >= 2 else 0.0

    def mbps(self):
        """Delivered image bandwidth, megabits/s, over the fps window."""
        span = self._window_span()
        if span <= 0:
            return 0.0
        # The first sample's bytes belong to a frame that arrived before the
        # window opened, so pair N-1 intervals with the last N-1 payloads.
        return sum(self._win_bytes[1:]) * 8.0 / span / 1e6

    def wire_mbps(self):
        """Bytes actually crossing USB (base64), megabits/s, same window."""
        span = self._window_span()
        if span <= 0:
            return 0.0
        return sum(self._win_wire[1:]) * 8.0 / span / 1e6

    def kb_per_frame(self):
        n = len(self._win_bytes)
        return (sum(self._win_bytes) / n / 1024.0) if n else 0.0

    def _mean_ms(self, key):
        return round(self._acc[key] / self.frames / 1000.0, 1) if self.frames else 0.0

    def snapshot(self):
        return {
            "frames": self.frames,
            "bytes": self.bytes,
            "fps": round(self.fps(), 1),
            "det": self.det,
            "blobs": self.blobs,
            "blob_labels": self.labels,
            "blob_counts": self.blob_counts,
            "model_counts": self.model_counts,
            "amb": self.amb,
            "saved": self.saved,
            "lab": self.lab,
            "suggest": suggest_threshold(self.lab) if self.lab else "",
            "resyncs": self.resyncs,
            "reconnects": self.reconnects,
            "status": self.status,
            "board": self.board,
            # Seconds since the last frame. The viewer keeps showing the last
            # good JPEG when the board goes away, so the page MUST be able to
            # tell a live stream from a frozen one -- a still scene and a dead
            # board look identical otherwise.
            "stale_s": (round(self._clock() - self.last_frame_at, 1)
                        if self.last_frame_at is not None else None),
            "mbps": round(self.mbps(), 2),
            "wire_mbps": round(self.wire_mbps(), 2),
            "kb_frame": round(self.kb_per_frame(), 1),
            # Everything the board reported about itself at startup: capture
            # geometry, JPEG q, and which model binary it actually loaded.
            "framesize": self.info_fields.get("framesize", ""),
            "w": self.info_fields.get("w", 0),
            "h": self.info_fields.get("h", 0),
            "pixfmt": self.info_fields.get("pixfmt", ""),
            "quality": self.info_fields.get("quality", None),
            "model": self.info_fields.get("model", ""),
            "model_bytes": self.info_fields.get("model_bytes", -1),
            "model_in": self.info_fields.get("model_in", ""),
            "model_out": self.info_fields.get("model_out", ""),
            "arena": self.info_fields.get("arena", -1),
            "labels": self.info_fields.get("labels", []),
            "cap_ms": self._mean_ms("cap_us"),
            "inf_ms": self._mean_ms("inf_us"),
            "blob_ms": self._mean_ms("blob_us"),
            "enc_ms": self._mean_ms("enc_us"),
            "mdec_ms": self._mean_ms("mdec_us"),
            "info": self.info,
        }


class BoardView:
    """One board's stream: its label, its port, and its live state.

    The comparison view is just a list of these. Everything that was
    per-process in the single-board design (Latest, Stats, the supervisor's
    state dict) is per-view here, so one board disconnecting or wedging
    cannot disturb the other -- which is the point of watching two at once.
    """

    def __init__(self, label, port=None, labels=None, saver=None,
                 classes=None, script_text=None):
        self.label = label
        self.port = port
        self.latest = Latest()
        self.stats = Stats(labels=labels)
        self.state = {"alive": True, "quit": False, "board": None}
        # Per-board capture: two boards writing frame_000001.jpg into one
        # directory would overwrite each other, and the dataset wants to know
        # which sensor produced which frame regardless -- one model deploys to
        # both, so "which camera shot this" is a training-relevant field.
        self.saver = saver
        # Each board gets its OWN threshold list and therefore its own board
        # script -- the two sensors render the same scene differently enough
        # that one config cannot serve both (measured: the N6's pink sits
        # ~10 LAB units lower in b than the AE3's).
        self.classes = list(classes or [])
        self.script_text = script_text
        self.overlay = True
        #: () -> script text for the CURRENT overlay setting. Set by main();
        #: the supervisor re-reads it on every attach, so flipping the toggle
        #: and dropping the board is all a live change needs.
        self.make_script = None

    def set_overlay(self, on):
        """Flip the board-side overlay and rebuild this board's script."""
        self.overlay = bool(on)
        if self.make_script is not None:
            self.script_text = self.make_script(self.overlay)
        return self.overlay

    def snapshot(self):
        s = self.stats.snapshot()
        s["label"] = self.label
        s["port"] = self.port or "(auto)"
        return s


class FrameSaver:
    """Write every Nth frame plus its blob boxes: a labelled capture set.

    The sidecar is JSONL, one object per SAVED frame, carrying the boxes the
    board actually produced. B0 turns that into YOLO ``.txt`` labels and
    Label Studio pre-annotations; nothing here re-derives the detection on the
    host, which would not agree with the board that made it.

    ``saved`` is counted and shown in the HUD on purpose: a capture run that
    "worked" while writing nothing is the exact failure CLAUDE.md rule 4 is
    about, and it is invisible unless the count is on screen.
    """

    def __init__(self, directory, every=15, labels=None, opener=open,
                 makedirs=os.makedirs):
        self.dir = directory
        self.every = max(1, int(every))
        self.labels = list(labels or [])
        self._opener = opener
        self._seen = 0
        self.saved = 0
        makedirs(directory, exist_ok=True)
        self._index = os.path.join(directory, "index.jsonl")

    def name_for(self, n):
        """Monotonic in SAVED frames, not in the board's seq.

        The board's seq restarts at 0 on every reconnect, and a nudged USB
        connector is enough to cause one -- naming by seq would overwrite the
        first frames of the session with the first frames after the replug.
        """
        return "frame_%06d.jpg" % n

    def put(self, hdr, jpg):
        """-> the filename written, or None when this frame is skipped."""
        self._seen += 1
        if (self._seen - 1) % self.every:
            return None
        name = self.name_for(self.saved)
        with self._opener(os.path.join(self.dir, name), "wb") as fh:
            fh.write(jpg)
        record = {
            "file": name,
            "seq": hdr.get("seq", -1),
            "w": hdr.get("w", 0),
            "h": hdr.get("h", 0),
            "classes": self.labels,
            "boxes": hdr.get("bb", []),
            "counts": hdr.get("bc", []),
            "amb": hdr.get("amb", 0),
        }
        with self._opener(self._index, "a") as fh:
            fh.write(json.dumps(record) + "\n")
        self.saved += 1
        return name


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


def reader_loop(out, latest, stats, state, saver=None):
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
            if saver is not None:
                try:
                    if saver.put(hdr, jpg) is not None:
                        stats.saved = saver.saved
                except OSError as exc:
                    # A full disk must not look like a healthy capture run.
                    stats.junk.append(b"save failed: %s"
                                      % str(exc).encode("utf-8", "replace"))
        elif line.startswith(b"#I "):
            stats.info = line[3:].decode("utf-8", "replace")
            try:
                fields = json.loads(stats.info)
                stats.info_fields = fields
                stats.board = fields.get("board", "")
            except ValueError:
                stats.board = ""
            print("board: %s" % stats.info, flush=True)
        elif line.startswith(b"#D "):
            # A CLEAN completion, not a disconnect: the board hit --max-frames
            # or --max-seconds. Distinguishing the two is what lets a bounded
            # run end by itself instead of being restarted by the supervisor.
            state["done"] = True
            print("board: stream done %s" % line[3:].decode("utf-8", "replace"),
                  flush=True)
        else:
            text = line.decode("utf-8", "replace")
            stats.junk.append(line[:120])
            print("board: %s" % text, flush=True)
    state["alive"] = False
    print("board: stream ended", flush=True)


def make_multi_handler(views):
    """Routes for N boards: /s/<i>/stream, /s/<i>/stats.json, /s/<i>/frame.jpg.

    Everything is served same-origin from one process so the page can fetch
    each board's stats without CORS, and so one browser tab shows both.
    """

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

        def _view(self, idx):
            return views[idx] if 0 <= idx < len(views) else None

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/" or path.startswith("/index"):
                self._body(multi_page(views).encode(), "text/html; charset=utf-8")
                return
            if path == "/api/overlay":
                # Flip the BOARD-side overlay. The picture is drawn on the
                # board before the JPEG is made, so the host cannot strip it
                # after the fact -- the board script is rebuilt and the stream
                # re-attached. Counts keep flowing either way.
                q = urllib.parse.parse_qs(self.path.partition("?")[2])
                on = q.get("on", ["1"])[0] not in ("0", "false", "off")
                for v in views:
                    v.set_overlay(on)
                    v.state["restart"] = True
                    b = v.state.get("board")
                    if b is not None:
                        try:
                            b.stop()          # reader ends -> supervisor
                        except Exception:     # re-attaches with the new script
                            pass
                self._body(json.dumps({"overlay": on}).encode(),
                           "application/json")
                return
            if path == "/api/boards":
                rows = []
                for v in views:
                    r = v.snapshot()
                    r["overlay"] = v.overlay
                    rows.append(r)
                body = json.dumps(rows).encode()
                self._body(body, "application/json")
                return
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "s" and parts[1].isdigit():
                view = self._view(int(parts[1]))
                if view is None:
                    self.send_error(404)
                    return
                if parts[2] == "stats.json":
                    self._body(json.dumps(view.snapshot()).encode(),
                               "application/json")
                elif parts[2] == "frame.jpg":
                    frame, _ = view.latest.get()
                    if frame:
                        self._body(frame, "image/jpeg")
                    else:
                        self.send_error(503, "no frame yet")
                elif parts[2] == "stream":
                    self._mjpeg(view)
                else:
                    self.send_error(404)
                return
            self.send_error(404)

        def _mjpeg(self, view):
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = -1
            try:
                while True:
                    frame, seq = view.latest.get()
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


def multi_page(views):
    """Side-by-side comparison page, one panel per board, in argument order."""
    panels = "\n".join(
        '<div class="p"><h2 id="t%d">%s</h2><div class="ban" id="b%d"></div>'
        '<img src="/s/%d/stream"/><pre id="s%d">connecting&hellip;</pre></div>'
        % (i, v.label, i, i, i) for i, v in enumerate(views))
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenMV &mdash; side by side</title><style>
body{margin:0;background:#111;color:#ddd;font-family:ui-monospace,monospace}
h1{font-size:15px;margin:10px;text-align:center;color:#9ab;font-weight:600}
.row{display:flex;gap:10px;padding:0 10px 12px;align-items:flex-start}
.p{flex:1 1 0;min-width:0;text-align:center}
.p h2{font-size:14px;margin:4px 0;color:#fff}
img{width:100%%;height:auto;image-rendering:pixelated;background:#000;
    border:1px solid #333;border-radius:4px}
pre{color:#8c8;white-space:pre-wrap;font-size:12px;text-align:left;margin:6px 0}
.ban{font-weight:700;border-radius:3px}
@media(max-width:760px){.row{flex-direction:column}}
</style></head><body>
<h1>OpenMV side-by-side &mdash; same script, same scene, different silicon</h1>
<div style="text-align:center;margin:0 0 8px">
<label style="color:#9ab;font-size:13px;cursor:pointer">
<input type="checkbox" id="ov" checked onchange="setOverlay(this.checked)">
 draw boxes on the image (off = see what the camera sees; counts keep working)
</label> <span id="ovs" style="color:#777;font-size:12px"></span></div>
<div class="row">%s</div>
<script>
const N=%d;
async function setOverlay(on){
 const s=document.getElementById('ovs'); s.textContent=' restarting streams\u2026';
 try{ await fetch('/api/overlay?on='+(on?1:0)); }catch(e){}
 setTimeout(()=>{ s.textContent=''; }, 4000);
}
setInterval(async()=>{
 let js;
 try{ js = await (await fetch('/api/boards')).json(); }catch(e){ return; }
 js.forEach((j,i)=>{
  // Board identity comes from the board itself, never from the label we
  // typed -- with two boards attached, a mislabelled panel is how a wrong
  // number gets published (DESIGN "S8 detail CORRECTION").
  if(i===0){ const c=document.getElementById('ov');
             if(c && j.overlay!==undefined) c.checked=!!j.overlay; }
  const t=document.getElementById('t'+i);
  if(t) t.textContent = j.label + (j.board ? '  \\u2014  '+j.board : '');
  const dead = j.stale_s === null || j.stale_s > 3;
  const b=document.getElementById('b'+i);
  if(b){
   b.textContent = dead ? ('\\u25CF NOT LIVE \\u2014 '+j.status) : '';
   b.style.cssText = dead
     ? 'background:#a11;color:#fff;padding:5px;font-weight:700' : '';
  }
  const s=document.getElementById('s'+i);
  const mb = n => (n<0||n===undefined) ? '?' : (n/1048576).toFixed(2)+' MB';
  // Model identity is spelled out per panel so "apples to apples?" is
  // answerable on screen. The two boards ship DIFFERENT binaries under the
  // same filename, so the byte count is the field that settles it.
  const model = (j.model||'(none)').replace(/^.*\\//,'') +
      '  ' + mb(j.model_bytes) +
      (j.model_in ? '  in '+j.model_in : '') +
      (j.labels && j.labels.length ? '  ['+j.labels.join(',')+']' : '');
  // Per-class counts, from bite A: "5 blobs" cannot be checked against a
  // scene holding 3 pink and 2 purple balls, and bite C's baseline is exactly
  // that check. `amb` is blobs that matched more than one colour box.
  const per = (j.blob_labels||[]).map((n,i)=>n+' '+((j.blob_counts||[])[i]||0))
      .join('   ');
  // Model per-class counts (FOMO mode) next to the blob baseline: the whole
  // point of the page is that the two methods are comparable at a glance.
  const mlab = (j.labels && j.labels.length) ? j.labels : (j.blob_labels||[]);
  const mper = (j.model_counts||[]).map((c,i)=>(mlab[i]||('c'+i))+' '+c)
      .join('   ');
  if(s) s.textContent =
   'capture   '+(j.framesize||'?')+'  '+j.w+'\\u00d7'+j.h+'  '+(j.pixfmt||'')+'\\n'+
   'encode    JPEG q'+(j.quality===null?'?':j.quality)+
       '   '+j.kb_frame+' KB/frame  (measured)\\n'+
   'bandwidth '+j.mbps+' Mbps image   '+j.wire_mbps+' Mbps on USB\\n'+
   'model     '+model+'\\n'+
   'rate      '+j.fps+' fps   inference '+j.inf_ms+' ms   encode '+j.enc_ms+' ms\\n'+
   '          capture '+j.cap_ms+' ms   blobs '+j.blob_ms+' ms\\n'+
   'found     '+j.blobs+' blobs   '+j.det+' detections\\n'+
   (per ? '          blobs: '+per+(j.amb ? '   ambiguous '+j.amb : '')+'\\n' : '')+
   (mper ? '          model: '+mper+(j.mdec_ms ? '   decode '+j.mdec_ms+' ms' : '')+'\\n' : '')+
   (j.saved ? 'captured  '+j.saved+' frames saved\\n' : '')+
   'health    resyncs '+j.resyncs+'   reconnects '+j.reconnects+'\\n'+
   j.port;
 });
},500)</script></body></html>""" % (panels, len(views))


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

    def __init__(self, port, baudrate=115200):
        # The ATTACH uses mpremote's own transport rather than a hand-rolled
        # handshake -- reuse before rewriting, and the hand-rolled one was
        # measurably worse: it timed out on an AE3 that mpremote had just
        # talked to happily (5 s vs mpremote's 10 s, and no raw-paste
        # support). Only the attach comes from mpremote; the READ loop stays
        # ours, which is the half that has to avoid mpremote's
        # accumulate-and-rescan behaviour (see the module docstring).
        from mpremote.transport_serial import SerialTransport
        import serial
        self._serial = serial
        self.port = port
        self._transport = SerialTransport(port, baudrate=baudrate)
        self.ser = self._transport.serial
        self.ser.timeout = 0.1              # bound our own read loop
        self._buf = bytearray()
        self.last_error = ""   # board traceback captured at end-of-execution
        self.end_reason = ""   # "eot" (script ended) vs "usb" (link dropped)

    def start(self, script_text):
        """Enter the raw REPL and start the script running from RAM."""
        # soft_reset=True gives the script a clean heap, which matters much
        # more on the AE3 (3.9 MB free, and the model arena alone is 791 KB)
        # than on the N6's 25.6 MB.
        self._transport.enter_raw_repl(soft_reset=True)
        self._transport.exec_raw_no_follow(script_text)
        return self

    def readline(self):
        """One line, or b'' at end of execution. Buffered over raw reads."""
        while True:
            # The raw REPL's end-of-execution 0x04 arrives with NO trailing
            # newline, so it cannot be found by scanning for line ends alone --
            # a returning script would otherwise leave this blocked forever on
            # a newline that never comes. But only position 0 counts: that is
            # where the REPL puts it, once the preceding line has been
            # consumed. A 0x04 deeper in the buffer is corruption (base64 and
            # JSON headers contain no 0x04), and treating THAT as end-of-stream
            # tore down a healthy stream over one bad byte.
            nl = self._buf.find(b"\n")
            if self._buf[:1] == b"\x04":
                # The raw REPL frames a run as: OK <stdout> 0x04 <stderr> 0x04 >
                # so the board's traceback lives AFTER this first 0x04.
                # Returning here without reading it discards the only
                # explanation of why a stream ended -- which left "stream
                # ended / reconnect" cycling with no visible cause.
                self.end_reason = "eot"
                self.last_error = self._read_error_tail()
                return b""
            if nl >= 0:
                line = bytes(self._buf[:nl])
                del self._buf[:nl + 1]
                return line + b"\n"
            try:
                chunk = self.ser.read(65536)
            except (self._serial.SerialException, OSError) as exc:
                # The board went away mid-read -- a nudged USB connector is
                # enough ("[Errno 6] Device not configured"). Ending the
                # stream cleanly lets the supervisor reconnect; letting the
                # exception escape would kill the reader thread and leave the
                # viewer serving a stale frame forever.
                # Recorded distinctly from "eot": a script that ended and a
                # USB link that dropped need completely different fixes, and
                # they were previously indistinguishable in the log.
                self.end_reason = "usb: %s" % exc
                return b""
            if chunk:
                self._buf += chunk
            elif not self.ser.is_open:
                return b""

    def _read_error_tail(self, limit=2048, timeout_s=1.0):
        """Text the board printed after end-of-execution: its traceback.

        Bounded in both bytes and time -- this runs on the teardown path and
        must never be what hangs a reconnect.
        """
        del self._buf[:1]                       # consume the first 0x04
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and len(self._buf) < limit:
            if b"\x04" in self._buf:
                break
            try:
                chunk = self.ser.read(512)
            except Exception:
                break
            if chunk:
                self._buf += chunk
        end = self._buf.find(b"\x04")
        tail = bytes(self._buf[:end if end >= 0 else len(self._buf)])
        return tail.decode("utf-8", "replace").strip()

    def stop(self):
        """Interrupt the script and leave the board at a usable REPL.

        Best-effort throughout: if the board has already gone (a yanked
        cable), there is nothing to tidy and failing here would mask the
        real event.
        """
        try:
            self.ser.write(b"\r\x03\x03")   # interrupt the running script
            time.sleep(0.1)
            self._transport.exit_raw_repl()  # Ctrl-B: friendly REPL
            time.sleep(0.1)
        except Exception:
            pass
        finally:
            try:
                self._transport.close()
            except Exception:
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
                    help="serial device for single-board mode (default: the "
                         "only OpenMV board present)")
    ap.add_argument("--board", action="append", default=None,
                    metavar="LABEL=PORT",
                    help="add a board to the side-by-side view; repeatable, "
                         "and panels appear left-to-right in the order given. "
                         "e.g. --board AE3=/dev/serial/by-id/usb-OpenMV_... "
                         "--board N6=/dev/serial/by-id/usb-MicroPython_...")
    ap.add_argument("--http-port", type=int, default=8090)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="HTTP bind address. Defaults to localhost only. Pass "
                         "0.0.0.0 to watch from another device on the same "
                         "network -- that PUBLISHES THE CAMERA FEED to every "
                         "host on that network, unauthenticated.")
    ap.add_argument("--framesize", default="VGA",
                    help="csi framesize NAME, e.g. QVGA VGA HD SXGAM (default VGA)")
    ap.add_argument("--quality", type=int, default=50)
    ap.add_argument("--max-seconds", type=float, default=3600)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--model", default="/rom/yolov8n_192.tflite")
    ap.add_argument("--board-model", action="append", default=None,
                    metavar="LABEL:PATH",
                    help="per-board model path, e.g. AE3:/flash/nereus.tflite "
                         "N6:/rom/nereus_two_ball.tflite -- the S8 B2 custom "
                         "model deploys to a DIFFERENT place per board "
                         "(AE3 /flash, N6 ROMFS)")
    ap.add_argument("--model-kind", default="auto",
                    choices=("auto", "yolo", "fomo", "raw"),
                    help="postprocessing: auto sniffs the filename "
                         "(yolov8* -> yolo, *fomo*/*nereus* -> fomo)")
    ap.add_argument("--model-labels", default=None,
                    help="comma-separated class names for models that ship "
                         "no .txt beside them (the N6 ROMFS carries only "
                         "the .tflite)")
    ap.add_argument("--threshold", type=float, default=0.4)
    ap.add_argument("--no-detect", action="store_true",
                    help="skip inference (isolates capture+encode cost)")
    ap.add_argument("--no-blobs", action="store_true",
                    help="skip the colour-blob overlay")
    ap.add_argument("--blob-thresh", action="append", default=None,
                    metavar="[NAME:]L_lo,L_hi,A_lo,A_hi,B_lo,B_hi",
                    help="one LAB box, repeatable -- one colour per box, each "
                         "drawn in its own colour. e.g. "
                         "--blob-thresh pink:20,70,10,50,-20,25 "
                         "--blob-thresh purple:10,80,10,65,-75,-10")
    ap.add_argument("--board-thresh", action="append", default=None,
                    metavar="LABEL:NAME:L_lo,L_hi,A_lo,A_hi,B_lo,B_hi",
                    help="a LAB box for ONE board, repeatable. Two sensors "
                         "do not share a threshold: the N6's blue cast puts "
                         "its pink balls ~10 LAB units lower in b than the "
                         "AE3's, so one box cannot fit both (measured, "
                         "DESIGN S8 bite A). Boards with no override fall "
                         "back to --blob-thresh")
    ap.add_argument("--board-pixels", action="append", default=None,
                    metavar="LABEL:N",
                    help="minimum blob area for ONE board, repeatable. The "
                         "two sensors need different floors: measured, the "
                         "AE3 resolves a distant ball at ~73 px while the N6 "
                         "needs ~150 px to reject the shadowed rims of pink "
                         "balls that its wider purple box would otherwise "
                         "count. Boards with no override use --blob-pixels")
    ap.add_argument("--blob-scan", choices=("codes", "per-class"),
                    default="codes",
                    help="codes: one find_blobs pass, attributed by the blob's "
                         "code bitfield (~11 ms total at VGA). per-class: one "
                         "pass per colour, unambiguous, ~11 ms EACH")
    ap.add_argument("--blob-pixels", type=int, default=150)
    ap.add_argument("--blob-label", default="blob",
                    help="what to call a colour blob in the overlay "
                         "(e.g. --blob-label ball)")
    ap.add_argument("--tune", action="store_true",
                    help="draw a centre target and report its mean LAB, so you "
                         "can read a --blob-thresh off a real object")
    ap.add_argument("--save-frames", default=None, metavar="DIR",
                    help="save frames + their blob boxes to DIR as a training "
                         "set. Turns the OVERLAY OFF -- a JPEG with boxes "
                         "burned into it is not a training image")
    ap.add_argument("--save-every", type=int, default=15,
                    help="save one frame in N (default 15 -- at ~22 fps that "
                         "is ~1.5/s; consecutive frames are near-duplicates "
                         "and inflate a dataset without informing it)")
    return ap.parse_args(argv)


def cfg_from_args(args, classes=None, overlay=None, pixels=None, model=None):
    cfg = {
        "framesize": args.framesize,
        "quality": args.quality,
        "max_seconds": args.max_seconds,
        "max_frames": args.max_frames,
        "model": args.model if model is None else model,
        "model_kind": args.model_kind,
        "threshold": args.threshold,
        "detect": not args.no_detect,
        "blobs": not args.no_blobs,
        "blob_pixels": args.blob_pixels if pixels is None else int(pixels),
        "blob_area": args.blob_pixels if pixels is None else int(pixels),
        "tune": args.tune,
        "blob_label": args.blob_label,
        "blob_scan": args.blob_scan,
        # Training frames must be pixel-clean; the blob pass still runs and
        # still reports its boxes, so the labels survive -- only drawing stops.
        # The same flag backs the live UI toggle: counts keep coming while the
        # picture goes clean, which is what "what is the camera actually
        # seeing?" needs.
        "overlay": (not args.save_frames) if overlay is None else bool(overlay),
    }
    if getattr(args, "model_labels", None):
        cfg["model_labels"] = [s.strip() for s in args.model_labels.split(",")
                               if s.strip()]
    if classes is None:
        try:
            classes = blob_classes_from_args(args)
        except ValueError as exc:
            raise SystemExit(str(exc))
    if classes:
        cfg["blob_classes"] = classes
    return cfg


def board_model_map(args, known_labels):
    """``--board-model LABEL:PATH`` (repeatable) -> {label: path}."""
    out = {}
    for spec in (args.board_model or []):
        if ":" not in spec:
            raise ValueError("--board-model wants LABEL:PATH, got %r" % spec)
        label, path = spec.split(":", 1)
        label, path = label.strip(), path.strip()
        if label not in known_labels:
            raise ValueError("--board-model %r names no --board label "
                             "of %s" % (label, ", ".join(known_labels) or "(none)"))
        if not path:
            raise ValueError("--board-model %r has an empty path" % spec)
        out[label] = path
    return out


class PortError(Exception):
    """No usable serial device right now (may simply be mid-reconnect)."""


def lan_addresses():
    """This host's non-loopback IPv4 addresses, for printing a reachable URL."""
    import socket
    addrs = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except socket.gaierror:
        pass
    if not addrs:
        # getaddrinfo can miss the Wi-Fi address; ask the routing table.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 80))    # TEST-NET-1, never actually sent
            addrs.append(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
    return addrs


#: Where a board turns up, per platform. On Linux ALWAYS prefer
#: /dev/serial/by-id -- ttyACM numbering is assignment-order and swaps between
#: boots, and this project has already published a table attributed to the
#: wrong board because bare mpremote grabbed the first CDC device
#: (DESIGN "S8 detail CORRECTION"). by-id encodes the USB serial, so it names
#: one specific board forever.
PORT_GLOBS = {
    "darwin": ("/dev/cu.usbmodem*",),
    "linux": ("/dev/serial/by-id/usb-OpenMV*",
              "/dev/serial/by-id/usb-MicroPython*"),
}


def port_candidates():
    """Serial devices that look like an OpenMV board, on this platform."""
    import glob
    key = "darwin" if sys.platform == "darwin" else "linux"
    found = []
    for pattern in PORT_GLOBS[key]:
        for p in sorted(glob.glob(pattern)):
            if p not in found:
                found.append(p)
    return found


def find_port(explicit=None):
    """Resolve a board's serial device, failing with something actionable.

    The device node is NOT stable -- on macOS this board came back as
    usbmodem1201 after having been usbmodem1101, and on Linux ttyACM numbering
    is assignment-order -- so the reconnect path re-resolves every attempt
    rather than caching, and by-id is preferred where it exists.
    """
    if explicit:
        return explicit
    ports = port_candidates()
    if not ports:
        raise PortError("no OpenMV board found (looked for %s) -- plugged in?"
                        % ", ".join(PORT_GLOBS[
                            "darwin" if sys.platform == "darwin" else "linux"]))
    if len(ports) > 1:
        raise PortError("several boards present -- name one with --port:\n  %s"
                        % "\n  ".join(ports))
    return ports[0]


#: Backoff for reconnect attempts, in seconds, then the last value repeats.
#: NOT a flat retry: on the AE3, repeated raw-REPL attaches are themselves a
#: known way to wedge the board -- roughly 4-6 attaches after a teardown and
#: it starts refusing below the Python level, curable only by a power cycle
#: (TRACKER S23 bite R). A viewer that hammers a quiet port is not resilient,
#: it is the fault. Backing off also gives a board that is merely slow to
#: boot (the AE3 takes seconds on csi.reset) time to arrive.
RETRY_BACKOFF_S = (2, 5, 10, 20, 30)


def supervise(port_hint, script_text, latest, stats, state,
              retry_s=None, settle_s=1.0, backoff=RETRY_BACKOFF_S,
              saver=None, on_done=None):
    """Keep a board stream running, reconnecting when the board comes back.

    Bumping the USB connector while aiming the camera ends the stream. That is
    a normal thing to do with a camera, so recovery should not need a human at
    a terminal -- and it cannot be done on the board, which does not autostart
    this script (``/flash/main.py`` is the stock LED blinker and stays that
    way; nothing here writes to the board).
    """
    first = True
    fails = 0

    def _script():
        return script_text() if callable(script_text) else script_text

    def _backoff():
        """Seconds to wait after `fails` consecutive failures."""
        if retry_s is not None:            # tests pin this
            return retry_s
        return backoff[min(fails - 1, len(backoff) - 1)]

    while not state.get("quit"):
        try:
            port = find_port(port_hint)
            board = SerialBoard(port).start(_script())
        except PortError as exc:
            fails += 1
            stats.status = "waiting for board: %s (retry in %gs)" % (
                exc, _backoff())
            time.sleep(_backoff())
            continue
        except ImportError:
            stats.status = "pyserial or mpremote missing"
            state["fatal"] = ("pyserial/mpremote missing -- "
                              "pip3 install --user pyserial mpremote")
            return
        # Deliberately broad. mpremote raises TransportError, which is NOT an
        # OSError, so an OSError-only clause let it escape and KILL this
        # supervisor thread -- the board then never reconnected while its
        # panel kept showing the last frame. Any attach failure must back off
        # and retry, never take the thread down; a supervisor that can die is
        # not a supervisor.
        except Exception as exc:
            fails += 1
            wait = _backoff()
            stats.status = ("board not answering on %s after %d attempt(s): %s "
                            "(retry in %gs)" % (port, fails, exc, wait))
            print("board: attach failed (%d): %s -- waiting %gs"
                  % (fails, exc, wait), flush=True)
            time.sleep(wait)
            continue

        fails = 0
        if not first and not state.pop("restart", False):
            stats.reconnects += 1
        first = False
        state["board"] = board
        stats.status = "streaming from %s" % port
        print("board: streaming from %s" % port, flush=True)

        reader_loop(board, latest, stats, state, saver)

        if state.get("done"):
            stats.status = "board finished its bounded run"
            state["quit"] = True
            try:
                board.stop()
            except Exception:            # never let teardown strand serve_forever
                pass
            state["board"] = None
            if on_done is not None:
                on_done()
            return

        reason = getattr(board, "end_reason", "") or "unknown"
        why = getattr(board, "last_error", "") or ""
        print("board: stream ended (%s)" % reason, flush=True)
        if why:
            stats.status = "board stopped: %s" % why.splitlines()[-1][:90]
            stats.junk.append(why.encode("utf-8", "replace")[:400])
            print("board: STOPPED WITH AN ERROR ->\n%s" % why, flush=True)
        else:
            stats.status = "board disconnected -- reconnecting"
        print("board: disconnected -- will reconnect", flush=True)
        try:
            board.stop()
        except Exception:
            pass
        state["board"] = None
        if state.get("quit"):
            return
        time.sleep(settle_s)


def parse_board_specs(specs, labels=None):
    """``["AE3=/dev/x", "N6=/dev/y"]`` -> ``[BoardView, ...]`` in order."""
    views = []
    for spec in specs:
        if "=" not in spec:
            raise SystemExit("--board wants LABEL=PORT, got %r" % spec)
        label, port = spec.split("=", 1)
        label, port = label.strip(), port.strip()
        if not label or not port:
            raise SystemExit("--board wants a non-empty LABEL and PORT: %r"
                             % spec)
        views.append(BoardView(label, port, labels=labels))
    dupes = {v.port for v in views}
    if len(dupes) != len(views):
        raise SystemExit("the same port was given twice -- each board needs "
                         "its own device")
    return views


def main(argv=None):
    args = parse_args(argv)
    global_classes = blob_classes_from_args_or_exit(args)
    global_labels = class_labels(args)

    if args.board:
        views = parse_board_specs(args.board, global_labels)
    else:
        views = [BoardView("OpenMV", args.port, labels=global_labels)]

    try:
        labels_present = [v.label for v in views]
        overrides = board_thresh_map(args, labels_present)
        pixel_overrides = board_pixels_map(args, labels_present)
        model_overrides = board_model_map(args, labels_present)
    except ValueError as exc:
        raise SystemExit(str(exc))

    for view in views:
        view.classes = overrides.get(view.label, global_classes)
        view.labels = [n for n, _ in view.classes] or [args.blob_label]
        view.stats = Stats(labels=view.labels)
        px = pixel_overrides.get(view.label)
        mdl = model_overrides.get(view.label)
        view.make_script = (lambda classes, px, mdl: lambda on:
                            build_board_script_text(cfg_from_args(
                                args, classes, overlay=on, pixels=px,
                                model=mdl)))(
            view.classes, px, mdl)
        view.overlay = not args.save_frames
        view.script_text = view.make_script(view.overlay)
        if view.label in overrides:
            print("board %-6s thresholds: %s"
                  % (view.label, ", ".join(n for n, _ in view.classes)),
                  flush=True)
        # Loud, not fatal: overlapping boxes are legitimate with --blob-scan
        # per-class and are a silent under-count with the default single pass.
        if args.blob_scan == "codes":
            for earlier, later, frac in shadowed_pairs(view.classes):
                print("WARNING [%s]: %.0f%% of threshold %r lies inside %r. "
                      "Under --blob-scan codes the EARLIER box claims the "
                      "shared pixels, so %r under-counts silently. Use "
                      "--blob-scan per-class, or tighten the boxes."
                      % (view.label, 100 * frac, later, earlier, later),
                      flush=True)

    if args.save_frames:
        for view in views:
            view.saver = FrameSaver(os.path.join(args.save_frames, view.label),
                                    args.save_every, view.labels)
            print("saving 1 frame in %d to %s -- overlay OFF (training frames "
                  "must be clean); boxes go to index.jsonl"
                  % (view.saver.every, view.saver.dir), flush=True)

    httpd = QuietServer((args.bind, args.http_port),
                        make_multi_handler(views))
    if args.bind not in ("127.0.0.1", "localhost"):
        print("WARNING: bound to %s -- the camera feed is reachable by any "
              "host on this network, with no authentication." % args.bind,
              flush=True)
        for addr in lan_addresses():
            print("  http://%s:%d/" % (addr, args.http_port), flush=True)
    url = "http://localhost:%d/" % args.http_port
    bounded = bool(args.max_frames) or args.max_seconds < 3600
    print("open %s  (%s)"
          % (url, "ends by itself after the bounded run" if bounded
             else "Ctrl-C to stop"), flush=True)

    # A bounded run (--max-frames) ends when EVERY board has finished, not
    # when the first one does -- otherwise the faster board tears down the
    # viewer while the slower one is still mid-run, which on a two-board
    # comparison silently truncates exactly the row being measured.
    finished = set()
    finished_lock = threading.Lock()

    def _finished(label):
        with finished_lock:
            finished.add(label)
            if len(finished) == len(views):
                httpd.shutdown()

    for view in views:
        # One supervisor thread per board: an independent attach, reader and
        # reconnect loop, so a board that drops out cannot stall the other.
        threading.Thread(
            target=supervise,
            args=(view.port, (lambda v: lambda: v.script_text)(view),
                  view.latest, view.stats, view.state),
            kwargs={"saver": view.saver,
                    "on_done": (lambda lbl=view.label: _finished(lbl))},
            daemon=True).start()
        print("board %-6s -> %s" % (view.label, view.port or "(auto)"),
              flush=True)

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
        # Every board gets its teardown even if one of them raises: leaving a
        # board streaming into a closed endpoint is what takes it off the USB
        # bus, and on a two-board bench that would strand the other one too.
        for view in views:
            view.state["quit"] = True
            board = view.state.get("board")
            if board is not None:
                try:
                    board.stop()
                except Exception as exc:
                    print("board %s: teardown failed: %s" % (view.label, exc),
                          flush=True)
        for view in views:
            s = view.stats.snapshot()
            print("%-6s frames %d  mean fps %.1f  resyncs %d  reconnects %d  "
                  "inference %.1f ms  blobs %.1f ms  encode %.1f ms"
                  % (view.label, s["frames"], s["fps"], s["resyncs"],
                     s["reconnects"], s["inf_ms"], s["blob_ms"], s["enc_ms"]),
                  flush=True)
            if view.saver is not None:
                print("  saved %d frames to %s (index.jsonl)"
                      % (view.saver.saved, view.saver.dir), flush=True)
            if view.stats.junk:
                print("  junk (last 3): %r" % (view.stats.junk[-3:],),
                      flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
