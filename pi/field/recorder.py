#!/usr/bin/env python3
"""S32 video recorder -- pull frames off the OpenMV boards and land them on disk.

The streaming app we already have, but recording. Three parts, kept separate
because they fail in completely different ways:

  FrameParser  -- turns the board's byte stream into frames, and RESYNCS rather
                  than trusting a length field it cannot corroborate.
  RingBuffer   -- decouples the USB reader from the disk writer. This is not a
                  nicety; see "why the ring exists" below.
  Session      -- one recording across N cameras: a directory, a manifest, and
                  one file per camera.

WHY THE RING EXISTS -- measured on nereus000, S32 bite 0:
  Paced at exactly the recorder's own rate (14.5 MB/s, 21% of what the card
  sustains), a 128 s buffered-append run held its throughput perfectly --
  median second 14.52 MB/s, write() p50 0.21 ms -- and STILL threw three
  write() stalls longer than one second, worst **3.86 s**. The card's average
  is irrelevant; its worst case is what drops frames. A recorder that writes on
  the reader thread loses ~115 HD frames to one such stall. So the reader never
  touches the filesystem: it parses and enqueues, and a writer thread drains.

WHY BYTES, NOT FRAMES, BOUND THE RING:
  Frame size varies 8x across the quality rungs (HD q70 118 KB, q90 423 KB), so
  a frame-count bound would be either wasteful or useless depending on settings.
  The ring is bounded in BYTES and sized from the measured worst stall.

HOST PORTABILITY -- nereus000 is a Pi 5, nereus002 is a Pi Zero 2 W (512 MB):
  A fixed ring size cannot serve both. It is derived from MemAvailable at run
  time and reported in the manifest, so a clip always records how much slack it
  actually had.
"""

import argparse
import glob
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from collections import deque

MAGIC = b"\xab\xcd\x12\x34"
HDR_LEN = 12
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"

#: A frame larger than this is a corrupt length field, not a frame. The largest
#: thing these sensors produce is HD q100 (~840 KB measured, S32 bite 0), so 4 MB
#: is ~5x headroom and still refuses a garbage 4 GB length.
MAX_FRAME_BYTES = 4 * 1024 * 1024

#: Measured worst-case SD write stall on nereus000 (3.86 s) with margin.
#: The ring must cover a stall of this long at the recording's byte rate.
STALL_COVER_S = 6.0
RING_MIN_BYTES = 24 * 1024 * 1024
RING_MAX_BYTES = 256 * 1024 * 1024


# --------------------------------------------------------------------------
# pure functions -- all testable with no board and no disk
# --------------------------------------------------------------------------

def mem_available_bytes(proc_meminfo="/proc/meminfo"):
    """MemAvailable, or None if it cannot be read (never guess a number)."""
    try:
        with open(proc_meminfo) as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def ring_size_bytes(byte_rate, mem_available=None):
    """How big the ring must be to survive the measured worst SD stall.

    Sized from the recording's own byte rate, then clamped by what the HOST can
    actually spare -- a Pi Zero 2 W has 512 MB total and cannot lend 256 MB.
    """
    want = int(byte_rate * STALL_COVER_S)
    want = max(RING_MIN_BYTES, min(RING_MAX_BYTES, want))
    if mem_available:
        # Never take more than a quarter of what is free; a recorder that
        # triggers the OOM killer has not recorded anything.
        want = min(want, max(RING_MIN_BYTES // 2, int(mem_available * 0.25)))
    return want


class FrameParser:
    """Byte stream -> frames. Resyncs on corruption instead of trusting length.

    A length field alone is not enough: one corrupt byte in the header yields a
    plausible-looking length and every subsequent frame is garbage. So a header
    is only accepted when the payload it describes actually STARTS WITH a JPEG
    SOI marker. That pairs an independent check against the length, which is
    exactly the "trust artifacts, not exit codes" rule applied to a wire.
    """

    def __init__(self, max_frame=MAX_FRAME_BYTES):
        self.buf = bytearray()
        self.max_frame = max_frame
        self.resyncs = 0
        self.dropped_bytes = 0

    def feed(self, data):
        """Add bytes; yield complete (seq, jpeg) frames in arrival order."""
        self.buf += data
        out = []
        while True:
            i = self.buf.find(MAGIC)
            if i < 0:
                # Keep only a possible partial magic at the tail.
                if len(self.buf) > len(MAGIC):
                    self.dropped_bytes += len(self.buf) - (len(MAGIC) - 1)
                    del self.buf[:-(len(MAGIC) - 1)]
                return out
            if i > 0:
                # Bytes before the magic are pre-banner noise or corruption.
                self.dropped_bytes += i
                del self.buf[:i]
            if len(self.buf) < HDR_LEN:
                return out
            seq, n = struct.unpack_from("<II", self.buf, 4)
            if n == 0 or n > self.max_frame:
                self.resyncs += 1
                del self.buf[:4]          # skip this magic, hunt the next
                continue
            if len(self.buf) < HDR_LEN + n:
                return out                # wait for the rest of the payload
            payload = bytes(self.buf[HDR_LEN:HDR_LEN + n])
            if not payload.startswith(SOI):
                self.resyncs += 1
                del self.buf[:4]
                continue
            del self.buf[:HDR_LEN + n]
            out.append((seq, payload))


class RingBuffer:
    """Bounded-by-bytes queue. A full ring DROPS and COUNTS; it never blocks.

    Blocking would push back on the reader, which would stall the USB read,
    which would overflow the board's own CDC buffer -- trading a counted drop
    for an uncounted one. Dropping here is visible and lands in the manifest.
    """

    def __init__(self, capacity_bytes):
        self.capacity = capacity_bytes
        self._q = deque()
        self._bytes = 0
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self.dropped_frames = 0
        self.dropped_bytes = 0
        self.high_water = 0
        self.closed = False

    def put(self, item):
        """Enqueue (seq, payload). Returns False if it had to be dropped."""
        n = len(item[1])
        with self._not_empty:
            if self._bytes + n > self.capacity:
                self.dropped_frames += 1
                self.dropped_bytes += n
                return False
            self._q.append(item)
            self._bytes += n
            if self._bytes > self.high_water:
                self.high_water = self._bytes
            self._not_empty.notify()
            return True

    def get(self, timeout=0.5):
        with self._not_empty:
            if not self._q and not self.closed:
                self._not_empty.wait(timeout)
            if not self._q:
                return None
            item = self._q.popleft()
            self._bytes -= len(item[1])
            return item

    def close(self):
        with self._not_empty:
            self.closed = True
            self._not_empty.notify_all()

    @property
    def pending_bytes(self):
        with self._lock:
            return self._bytes


def read_frame(mjpeg_path, n, max_scan_bytes=64 * 1024 * 1024):
    """Return the nth JPEG from a .mjpeg, BYTE-EXACT, or None.

    Scanned in 1 MB chunks: a 45 minute recording is 7 GB and must never be
    read into RAM to fetch one frame. `max_scan_bytes` bounds the walk so a
    huge file cannot turn a page load into a disk crawl -- frame indexes used
    here are small, near the head of the file.
    """
    soi, eoi = b"\xff\xd8", b"\xff\xd9"
    buf = bytearray()
    idx = 0
    scanned = 0
    try:
        with open(mjpeg_path, "rb") as f:
            while scanned < max_scan_bytes:
                chunk = f.read(1 << 20)
                if not chunk:
                    return None
                scanned += len(chunk)
                buf += chunk
                while True:
                    s = buf.find(soi)
                    if s < 0:
                        del buf[:max(0, len(buf) - 1)]
                        break
                    e = buf.find(eoi, s + 2)
                    if e < 0:
                        del buf[:s]
                        break
                    if idx == n:
                        return bytes(buf[s:e + 2])
                    del buf[:e + 2]
                    idx += 1
    except OSError:
        return None
    return None


#: Which frame to lift as a recording's thumbnail. Not frame 0: the board runs
#: 8 warm-up frames but AE/AWB can still be settling, and S31 measured that a
#: not-yet-converged frame comes back BLACK while remaining a valid JPEG -- a
#: thumbnail that looks like a failure when the clip is fine.
THUMB_FRAME = 20


def write_thumbnail(mjpeg_path, out_path, n=THUMB_FRAME):
    """Copy one frame out as the recording's thumbnail. No decode, no re-encode.

    Deliberately verbatim: Nick's rule for this rig is that energy is only spent
    on conversion when he asks for it, so a thumbnail must cost a read and a
    write, not a transcode. Falls back to earlier frames for a very short clip.
    """
    for idx in (n, 5, 0):
        data = read_frame(mjpeg_path, idx)
        if data:
            try:
                with open(out_path, "wb") as f:
                    f.write(data)
                return len(data)
            except OSError:
                return 0
    return 0


def build_board_script(cfg, board_src=None):
    """record_board.py with a _CFG literal prepended -- the host owns the knobs."""
    if board_src is None:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "record_board.py")) as f:
            board_src = f.read()
    return "_CFG = %r\n%s" % (cfg, board_src)


def pace_ms_for(target_fps):
    """Minimum ms per frame for a target fps. 0 means free-run."""
    if not target_fps or target_fps <= 0:
        return 0
    return int(1000.0 / float(target_fps))


# --------------------------------------------------------------------------
# board I/O
# --------------------------------------------------------------------------

class BoardRecorder:
    """Own one board's port, run the pump, write frames to a file.

    The script is pushed through PASTE MODE in the friendly REPL, not the raw
    REPL. That is the whole reason this can carry raw binary: the raw REPL
    frames output with 0x04 and JPEG payloads contain 0x04 freely, which is why
    the existing stream pays a 33% base64 tax. Paste mode has no output framing.
    """

    def __init__(self, label, port, cfg, out_path, log=print):
        self.label = label
        self.port = port
        self.cfg = cfg
        self.out_path = out_path
        self.log = log
        self.parser = FrameParser()
        self.ring = None
        self.frames = 0
        self.bytes = 0
        self.first_seq = None
        self.last_seq = None
        self.banner = {}
        self.trailer = {}
        self.error = ""
        self.started_at = None
        self.finished_at = None
        #: When frames actually arrived. The delivered rate is measured across
        #: THESE, not across the pump's wall time -- the pump also spends time
        #: waiting for a trailer, and dividing by that reported 2.38 fps for a
        #: recording that really ran at 23.8.
        self.first_frame_at = None
        self.last_frame_at = None
        self._ser = None

    # -- lifecycle ---------------------------------------------------------

    def open(self):
        import serial
        self._ser = serial.Serial(self.port, timeout=1.0)
        s = self._ser
        s.write(b"\r\x03\x03")          # interrupt anything running
        time.sleep(0.4)
        s.reset_input_buffer()
        s.write(b"\x02")                # ensure FRIENDLY repl (not raw)
        time.sleep(0.3)
        s.reset_input_buffer()
        return self

    def start(self, script_text):
        """Push the script in paste mode and wait for the board's banner.

        THE ECHO MUST BE DRAINED WHILE WRITING. Paste mode echoes every byte it
        receives. Pushing ~7 KB without reading fills the host's receive buffer,
        so the board blocks writing its echo, so it stops consuming our input,
        so our write blocks -- a deadlock with both ends waiting on the other.
        Measured on nereus000: this hung every single time until the drain below
        was added, and it hung *silently*, which is the worst kind.
        """
        s = self._ser
        s.write(b"\x05")                # Ctrl-E: paste mode
        time.sleep(0.3)
        s.read(s.in_waiting or 1)       # swallow the paste-mode prompt

        data = script_text.replace("\n", "\r\n").encode("utf-8")
        deadline = time.time() + 60
        for i in range(0, len(data), 256):
            if time.time() > deadline:
                self.error = "timed out pushing the script (echo not draining)"
                return False
            s.write(data[i:i + 256])
            s.flush()
            if s.in_waiting:
                s.read(s.in_waiting)    # discard the echo -- see the docstring
        s.write(b"\x04")                # Ctrl-D: execute
        s.flush()

        deadline = time.time() + 60
        pre = bytearray()
        while time.time() < deadline:
            chunk = s.read(4096)
            if chunk:
                pre += chunk
                i = pre.find(b"#REC-START ")
                if i >= 0:
                    j = pre.find(b"\n", i)
                    if j >= 0:
                        try:
                            self.banner = json.loads(pre[i + 11:j].decode("utf-8", "replace"))
                        except ValueError:
                            self.banner = {}
                        # Anything after the banner line is already frame data.
                        self.parser.feed(bytes(pre[j + 1:]))
                        self.started_at = time.time()
                        return True
        self.error = ("board never sent #REC-START; last bytes: %r"
                      % bytes(pre[-200:]))
        return False

    def pump(self, ring, stop_event):
        """Read big chunks and enqueue frames until the board says it is done.

        The trailer is scanned in the RAW chunk, not in the parser's buffer.
        The parser deliberately discards bytes that are not frames (that is how
        it resyncs), so "#REC-END {...}" was being thrown away before it could
        be seen -- which made every recording sit until its timeout instead of
        finishing when the board actually stopped.
        """
        s = self._ser
        self.ring = ring
        done = False
        tail = bytearray()          # trailing NON-frame text, for the trailer
        deadline = time.time() + float(self.cfg.get("duration_s", 5)) + 45
        while not done and not stop_event.is_set() and time.time() < deadline:
            chunk = s.read(65536)
            if not chunk:
                continue
            now = time.time()
            for seq, jpg in self.parser.feed(chunk):
                if self.first_seq is None:
                    self.first_seq = seq
                    self.first_frame_at = now
                self.last_seq = seq
                self.last_frame_at = now
                self.frames += 1
                self.bytes += len(jpg)
                ring.put((seq, jpg))

            tail += chunk
            i = tail.find(b"#REC-END ")
            if i >= 0:
                j = tail.find(b"\n", i)
                if j >= 0 or len(tail) - i > 400:
                    blob = tail[i + 9:j if j >= 0 else len(tail)]
                    try:
                        self.trailer = json.loads(blob.decode("utf-8", "replace"))
                    except ValueError:
                        self.trailer = {}
                    done = True
            # Keep only enough tail to span a trailer split across two reads.
            if len(tail) > 4096:
                del tail[:-1024]
        self.finished_at = time.time()
        return done

    def close(self):
        """Leave the board at a usable REPL. Best effort -- a gone board is not
        an error worth masking the real event with."""
        try:
            if self._ser is not None:
                self._ser.write(b"\r\x03\x03")
                time.sleep(0.1)
                self._ser.reset_input_buffer()
        except Exception:
            pass
        finally:
            try:
                if self._ser is not None:
                    self._ser.close()
            except Exception:
                pass

    # -- reporting ---------------------------------------------------------

    def stats(self):
        # The recording's real span is first frame -> last frame. The pump's own
        # wall time includes waiting for the trailer and is NOT the frame rate.
        if (self.first_frame_at is not None and self.last_frame_at is not None
                and self.last_frame_at > self.first_frame_at and self.frames > 1):
            wall = self.last_frame_at - self.first_frame_at
            fps = (self.frames - 1) / wall
        else:
            wall = ((self.finished_at or time.time())
                    - (self.started_at or time.time()))
            fps = (self.frames / wall) if wall > 0 else 0.0
        expected = None
        if self.first_seq is not None and self.last_seq is not None:
            expected = self.last_seq - self.first_seq + 1
        return {
            "label": self.label,
            "port": self.port,
            "frames": self.frames,
            "bytes": self.bytes,
            "wall_s": round(wall, 2),
            "delivered_fps": round(fps, 2),
            "mb_per_s": round(self.bytes / wall / 1e6, 2) if wall > 0 else 0.0,
            # What the BOARD thinks it did, kept separate from what arrived.
            "board_fps": (self.trailer or {}).get("fps"),
            "board_frames": (self.trailer or {}).get("frames"),
            # A gap in the board's own sequence numbers is a frame that was
            # encoded and then lost on the way here. It is NOT the same as a
            # frame the board never made, and the two must not be conflated.
            "seq_gaps": (expected - self.frames) if expected is not None else None,
            "resyncs": self.parser.resyncs,
            "banner": self.banner,
            "trailer": self.trailer,
            "error": self.error,
        }


class JpegSplitter:
    """Split a concatenated-MJPEG byte stream into frames.

    The boards send length-prefixed frames; rpicam-vid does not -- it emits raw
    concatenated JPEGs, so the boundaries have to be found. Kept separate from
    FrameParser rather than bolted onto it: that class's whole value is that a
    length field is corroborated by an independent SOI check, and there is no
    length here to corroborate.
    """

    def __init__(self):
        self.buf = bytearray()
        self.frames = 0

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            s = self.buf.find(SOI)
            if s < 0:
                del self.buf[:max(0, len(self.buf) - 1)]
                return out
            e = self.buf.find(EOI, s + 2)
            if e < 0:
                del self.buf[:s]          # keep the partial frame
                return out
            out.append((self.frames, bytes(self.buf[s:e + 2])))
            self.frames += 1
            del self.buf[:e + 2]


#: What each framesize means for the IMX708. It is NOT the boards' rectangle:
#: they letterbox 16:10 (VGA 640x400, HD 1280x800) off a 4608x2592 sensor that
#: is free to pick anything. Matched to 4:3 / 16:9 standards here and reported
#: per camera, so the manifest never implies the three cameras framed the same
#: scene when they did not.
CSI_SIZES = {"QVGA": (320, 240), "VGA": (640, 480), "HD": (1280, 720)}


def rpicam_record_argv(width, height, fps, quality, duration_s, camera=0):
    """rpicam-vid writing MJPEG to stdout for a bounded time.

    Separated so a test can assert on it without a camera. Output goes to
    stdout rather than straight to a file so the frames pass through the same
    ring and writer as the boards' -- which is what gives the IMX708 the same
    frame counts, the same drop accounting and the same stall protection
    instead of a second, differently-behaved path.
    """
    return ["rpicam-vid", "-n", "--codec", "mjpeg",
            "--camera", str(camera),
            "--width", str(width), "--height", str(height),
            "--framerate", str(fps),
            "--quality", str(quality),
            "-t", str(int(duration_s * 1000)),
            "-o", "-"]


def csi_present(camera=0, timeout=15):
    """Is there a CSI camera at this index? Returns (ok, why_not).

    Asked of the stack rather than assumed from a /dev node: nereus000 has no
    CSI camera at all, and a recorder that silently produced an empty clip
    there would be exactly the plausible-but-wrong artifact this repo keeps
    paying for. rpicam-hello is the same tool the rig's other cards use.
    """
    try:
        p = subprocess.run(["rpicam-hello", "--list-cameras"],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "rpicam-hello unavailable (%s)" % e
    text = (p.stdout or "") + (p.stderr or "")
    if "no cameras available" in text.lower():
        return False, "no CSI camera on this rig"
    for line in text.splitlines():
        if line.strip().startswith("%d :" % camera):
            return True, line.strip()
    return False, "no CSI camera at index %d" % camera


class CsiRecorder:
    """Record the IMX708 (or any CSI camera) via rpicam-vid.

    Presents the SAME interface as BoardRecorder -- open/start/pump/close/stats
    -- so record_run drives all three cameras through one code path and the
    manifest has one shape. What differs is real and is reported rather than
    papered over: there are no board sequence numbers here, so `seq_gaps` is
    None (unknown) instead of 0 (verified none). Claiming zero losses on a
    channel that cannot detect them would be exactly the wrong lie.
    """

    def __init__(self, label, cfg, out_path, log=print, camera=0):
        self.label = label
        self.port = "csi:%d" % camera
        self.cfg = cfg
        self.out_path = out_path
        self.log = log
        self.camera = camera
        self.splitter = JpegSplitter()
        self.frames = 0
        self.bytes = 0
        self.banner = {}
        self.trailer = {}
        self.error = ""
        self.started_at = None
        self.finished_at = None
        self.first_frame_at = None
        self.last_frame_at = None
        self._proc = None
        self._stderr = b""

    def open(self):
        return self                      # nothing to open; rpicam owns the ISP

    def start(self, _script_text=None):
        w, h = CSI_SIZES.get(self.cfg.get("framesize", "VGA"), (640, 480))
        argv = rpicam_record_argv(w, h, self.cfg.get("fps", 30),
                                  self.cfg.get("quality", 70),
                                  self.cfg.get("duration_s", 5), self.camera)
        try:
            self._proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE)
        except (OSError, ValueError) as e:
            self.error = "rpicam-vid will not start: %s" % e
            return False
        self.started_at = time.time()
        self.banner = {"board": self.label, "w": w, "h": h,
                       "framesize": self.cfg.get("framesize"),
                       "quality": self.cfg.get("quality"),
                       "fw": "rpicam-vid", "camera": self.camera}
        return True

    def pump(self, ring, stop_event):
        deadline = time.time() + float(self.cfg.get("duration_s", 5)) + 45
        out = self._proc.stdout
        while not stop_event.is_set() and time.time() < deadline:
            chunk = out.read(65536)
            if not chunk:
                break                    # rpicam exited: its -t bound elapsed
            now = time.time()
            for seq, jpg in self.splitter.feed(chunk):
                if self.first_frame_at is None:
                    self.first_frame_at = now
                self.last_frame_at = now
                self.frames += 1
                self.bytes += len(jpg)
                ring.put((seq, jpg))
        self.finished_at = time.time()
        try:
            self._stderr = self._proc.stderr.read() or b""
        except Exception:                                   # noqa: BLE001
            pass
        return True

    def close(self):
        p = self._proc
        if p is None:
            return
        try:
            if p.poll() is None:
                # rpicam holds the CSI device and the ISP; orphaning those
                # leaves the next recording unable to open the camera at all.
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)
            for s in (p.stdout, p.stderr):
                try:
                    s.close()
                except Exception:                           # noqa: BLE001
                    pass
        except Exception:                                   # noqa: BLE001
            pass

    def stats(self):
        if (self.first_frame_at is not None and self.last_frame_at is not None
                and self.last_frame_at > self.first_frame_at and self.frames > 1):
            wall = self.last_frame_at - self.first_frame_at
            fps = (self.frames - 1) / wall
        else:
            wall = ((self.finished_at or time.time())
                    - (self.started_at or time.time()))
            fps = (self.frames / wall) if wall > 0 else 0.0
        err = self.error
        if not self.frames and not err:
            tail = self._stderr.decode("utf-8", "replace").strip()[-300:]
            err = "rpicam-vid produced no frames: %s" % (tail or "no output")
        return {
            "label": self.label, "port": self.port,
            "frames": self.frames, "bytes": self.bytes,
            "wall_s": round(wall, 2), "delivered_fps": round(fps, 2),
            "mb_per_s": round(self.bytes / wall / 1e6, 2) if wall > 0 else 0.0,
            "board_fps": None, "board_frames": None,
            # rpicam-vid gives no per-frame sequence numbers, so a frame lost
            # between the ISP and here is UNDETECTABLE. None means unknown --
            # never 0, which would claim a check that was not performed.
            "seq_gaps": None,
            "resyncs": 0, "banner": self.banner, "trailer": {}, "error": err,
        }


def writer_thread(ring, path, state):
    """Drain the ring to one .mjpeg file. The ONLY thing that touches the disk."""
    written = 0
    frames = 0
    max_stall = 0.0
    with open(path, "wb", buffering=1024 * 1024) as f:
        while True:
            item = ring.get(timeout=0.5)
            if item is None:
                if state.get("done") and ring.pending_bytes == 0:
                    break
                continue
            t0 = time.monotonic()
            f.write(item[1])
            dt = time.monotonic() - t0
            if dt > max_stall:
                max_stall = dt
            written += len(item[1])
            frames += 1
        f.flush()
        os.fsync(f.fileno())
    state["written_bytes"] = written
    state["written_frames"] = frames
    state["max_write_stall_s"] = round(max_stall, 3)


# --------------------------------------------------------------------------
# discovery + session
# --------------------------------------------------------------------------

#: Last known role -> port map. A HINT, never a source of truth: every hinted
#: port is re-probed and the board must still claim the expected role, so a
#: swapped board is detected rather than trusted. That keeps discover.py's
#: no-stale-map property while letting an N6-only recording avoid opening the
#: AE3's port at all.
HINT_PATH = os.path.expanduser("~/.cache/nereus_roles.json")

#: Seconds one board gets to answer "what are you". A wedged board must yield an
#: ERROR, never an unbounded hang: an AE3 that had just timed out held a
#: recording in discovery indefinitely, which is how the demo path can die on a
#: camera it was not even using.
PROBE_TIMEOUT_S = 25


def _load_hints(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json_durable(path, obj, indent=None):
    """Write JSON so that a power cut leaves either the old file or the new
    one -- never an empty one.

    Measured 2026-09-10 on nereus002: a hard reset seconds after a segment
    closed left `manifest.json` at ZERO bytes (and did the same to two
    NetworkManager profiles). tmp + rename alone is not enough on ext4:
    the rename is journalled before the data reaches the card, so a cut in
    between publishes an empty file under the real name. fsync the data
    first, then rename, then fsync the directory so the rename itself is
    on disk too.
    """
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dfd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass          # the data is safe; a directory fsync is belt and braces


def _save_hints(path, mapping):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(mapping, f)
        os.replace(tmp, path)
    except OSError:
        pass                      # a hint that cannot be saved is not an error


def _probe_bounded(port, timeout=PROBE_TIMEOUT_S):
    """Ask one board its role, bounded in time. Returns (info, problem)."""
    import discover as discovery
    box = {}

    def run():
        try:
            box["info"] = discovery.probe_port(port)
        except Exception as e:                          # noqa: BLE001
            box["err"] = "%s: %s" % (type(e).__name__, e)

    t = threading.Thread(target=run)
    t.daemon = True
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, ("%s did not answer within %d s -- the board may be "
                      "wedged. Give the port total silence, then power-cycle "
                      "it; do NOT retry immediately." % (port, timeout))
    if "err" in box:
        return None, "%s: %s" % (port, box["err"])
    return box.get("info"), None


def find_boards(roles=("N6", "AE3"), hint_path=HINT_PATH,
                probe_timeout=PROBE_TIMEOUT_S):
    """Role -> port, by ASKING each board. Never by USB serial.

    The S32 flash proved exactly why by-id cannot be pinned: updating the N6
    from v4.8.1 to v5.0.1 CHANGED its path (HS_Mode_0065345D3643-if01 ->
    FS_Mode_10003500025043364d343000-if00) because the newer firmware reports
    the full 96-bit chip UID. Every by_id-pinned config on this rig broke that
    morning; role lookup did not notice.

    Only the boards actually ASKED FOR are opened. Probing a camera the caller
    does not want is not free -- it costs a raw-REPL attach on a board that may
    be unwell, and repeated attaches are themselves how the AE3 gets wedged.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import discover as discovery

    want = list(roles)
    found, problems = {}, []
    hints = _load_hints(hint_path)

    # 1. Try the hinted port for each wanted role -- one probe, verified.
    for role in want:
        port = hints.get(role)
        if not port or not os.path.exists(port):
            continue
        info, err = _probe_bounded(port, probe_timeout)
        if err:
            problems.append(err)
            continue
        if info and info.get("role") == role:
            found[role] = info
        else:
            problems.append("%s no longer answers as %s (now %r) -- rescanning"
                            % (port, role, (info or {}).get("role")))

    # 2. Anything still missing: scan the remaining ports, stopping early.
    missing = [r for r in want if r not in found]
    if missing:
        used = {i["port"] for i in found.values()}
        for port in discovery.list_ports():
            if not missing:
                break
            if port in used:
                continue
            info, err = _probe_bounded(port, probe_timeout)
            if err:
                problems.append(err)
                continue
            role = (info or {}).get("role")
            if role in missing:
                found[role] = info
                missing.remove(role)

    if found:
        hints.update({r: i["port"] for r, i in found.items()})
        _save_hints(hint_path, hints)
    return {r: found[r]["port"] for r in want if r in found}, problems, found


class Session:
    """One recording: a directory, one file per camera, and a manifest."""

    def __init__(self, root, name=None):
        self.name = name or time.strftime("rec_%Y%m%dT%H%M%S")
        self.dir = os.path.join(root, self.name)
        os.makedirs(self.dir, exist_ok=True)
        self.manifest = {
            "name": self.name,
            "created": time.time(),
            "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "host": os.uname().nodename,
            "settings": {},
            "cameras": [],
            "notes": [],
        }

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def save(self):
        """Write the manifest, PRESERVING cameras written by another recorder.

        A dive session is written by TWO processes: this one pumps the USB
        boards, and imx_dive_recorder.py drives the CSI camera through
        picamera2 (they cannot share an interpreter -- one needs the mpremote
        venv, the other the system picamera2). Both land in the same
        timestamped directory so the library shows one event with all three
        cameras, which is how Nick reads it.

        A plain overwrite therefore loses whichever camera finished first, and
        which one that is depends on timing -- the worst kind of bug: the page
        looks fine and one camera's footage is simply absent from the manifest
        while its file sits on disk. So the on-disk manifest is merged in, and
        entries whose label we do not own are kept.
        """
        merged = dict(self.manifest)
        ours = {c.get("label") for c in merged.get("cameras", [])}
        try:
            with open(self.path("manifest.json")) as f:
                existing = json.load(f)
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            foreign = [c for c in existing.get("cameras", [])
                       if c.get("label") not in ours]
            if foreign:
                merged["cameras"] = list(merged.get("cameras", [])) + foreign
            # Keep any section this writer does not produce (the dive's white
            # balance block, for one) rather than dropping it on the floor.
            for key in ("white_balance", "segments", "dive"):
                if key in existing and key not in merged:
                    merged[key] = existing[key]
        write_json_durable(self.path("manifest.json"), merged, indent=1)


def load_sessions(root):
    """Every recording on disk, newest first. A broken manifest is REPORTED."""
    out = []
    if not os.path.isdir(root):
        return out
    # NEWEST FIRST BY TIME, not by name. Sorting names in reverse put every
    # "rec_" session above every "dive_" one for all eternity, so the IMX
    # footage sat below twenty-odd board recordings and read as missing --
    # Nick could not find his own dives (2026-09-09). Directory mtime is also
    # what the eviction ring orders by, so the library and the ring now agree
    # about which recording is oldest.
    def _mtime(n):
        try:
            return os.path.getmtime(os.path.join(root, n))
        except OSError:
            return 0.0
    for name in sorted(os.listdir(root), key=_mtime, reverse=True):
        mpath = os.path.join(root, name, "manifest.json")
        if not os.path.isfile(mpath):
            continue
        try:
            with open(mpath) as f:
                m = json.load(f)
        except (ValueError, OSError) as e:
            out.append({"name": name, "broken": str(e), "cameras": [],
                        "settings": {}, "created_iso": "?"})
            continue
        for cam in m.get("cameras", []):
            for key in ("mjpeg", "mp4"):
                rel = cam.get(key)
                if rel:
                    p = os.path.join(root, name, rel)
                    cam[key + "_bytes"] = (os.path.getsize(p)
                                           if os.path.exists(p) else 0)
            # The mp4 is now made ON REQUEST, so the manifest may name one that
            # does not exist yet, or a file may exist that the manifest predates.
            # Trust the filesystem for "is it playable", not the manifest.
            mp4p = os.path.join(root, name, "%s.mp4" % cam.get("label", ""))
            if os.path.isfile(mp4p):
                cam["mp4"] = os.path.basename(mp4p)
                cam["mp4_bytes"] = os.path.getsize(mp4p)
            else:
                cam.pop("mp4", None)
                cam["mp4_bytes"] = 0
            thumb = os.path.join(root, name, "%s_thumb.jpg" % cam.get("label", ""))
            cam["thumb"] = (os.path.basename(thumb) if os.path.isfile(thumb)
                            else None)
        out.append(m)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.expanduser("~/recordings"))
    ap.add_argument("--framesize", default="HD")
    ap.add_argument("--quality", type=int, default=85)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--duration", type=float, default=5.0)
    ap.add_argument("--cameras", default="N6,AE3")
    ap.add_argument("--session", default=None,
                    help="write into this session directory instead of a "
                         "generated rec_<time> one, so a dive's boards and "
                         "its IMX land in the SAME timestamped event")
    ap.add_argument("--no-transcode", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from record_run import run_recording          # noqa: E402

    # Stop must END the recording, not unwind out of it.
    #
    # MEASURED FAILURE this fixes (2026-09-09): interrupting a CLI recording
    # left rec_*/N6.mjpeg and AE3.mjpeg on the card with NO manifest.json and
    # no thumbnails -- footage with no record of the framesize, quality, fps
    # or delivered rate that produced it. run_recording already takes a
    # stop_event and recorder_web has always passed one, which is why the
    # page's Stop button never had this bug; the CLI simply never wired it up.
    stop = threading.Event()

    def _stop(signum, _frame):
        print("recorder: signal %d -- finishing and writing the manifest"
              % signum, file=sys.stderr, flush=True)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    res = run_recording(root=a.root, framesize=a.framesize, quality=a.quality,
                        fps=a.fps, duration_s=a.duration,
                        cameras=[c for c in a.cameras.split(",") if c],
                        transcode=not a.no_transcode, stop_event=stop,
                        session_name=a.session)
    print(json.dumps(res, indent=1) if a.json else res["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
