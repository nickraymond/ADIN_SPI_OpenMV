#!/usr/bin/env python3
"""S30 video DOE -- what can each camera actually deliver, and at what cost.

Nick's question is a LINK BUDGET question: "what will it take to transmit a
5 second video per hour from any of the boards." So the headline number here
is **bytes as produced by the camera** -- the sum of the JPEG payloads the
board encodes -- not the size of anything this Pi re-encodes afterwards. A
5 s clip once per hour is a sustained bandwidth: bytes / 3600 s.

Two stages, because they answer different questions at very different cost:

  STAGE 1 -- PROBE (cheap, COMPLETE).  For every (camera, resolution,
      quality) run one unpaced burst and measure two things: how fast the
      camera can capture+encode, and how many bytes a frame costs. That is
      the whole size matrix, because MJPEG has no inter-frame coding:
          clip_bytes  ==  fps * seconds * bytes_per_frame
      54 cells in a few minutes, and it prices every fps target at once --
      including the ones the camera cannot actually sustain, which is how a
      cell earns "NA" instead of a guess.

  STAGE 2 -- CLIPS (expensive, CURATED).  Record real 5 s clips so Nick can
      LOOK at them, because "where is the quality floor" is not a number
      anyone should take from a spreadsheet. Customer preference is high
      frame rate over high quality, so the quality axis is weighted low --
      the interesting region is q10-q45, not the top end.

The MJPEG bytes are the deliverable. The H.264/MP4 copy exists so the clips
are watchable in a browser AND to price the other architecture: if a Pi sits
in the loop and can transcode before transmit, the budget changes by ~4x
(measured 26.43 -> 6.60 Mbps at 720p15). Both numbers are reported; neither
is allowed to stand in for the other.

Serve-first (DESIGN D48): the HTTP server binds and the page renders BEFORE
any camera is touched, because the workbench health-gates LIVE on this page
answering within 60 s and this run takes far longer than that.
"""

import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

CAMERAS = ("IMX708", "N6", "AE3")

#: The three sensors do NOT frame the same rectangle and this tool must not
#: pretend otherwise (field_stream.py carries the same warning). The boards
#: letterbox to 16:10; the IMX708 picks freely from a 4608x2592 sensor and is
#: matched to the conventional rectangle for each class. Every result records
#: the width/height actually observed, so the report never relies on this map.
RESOLUTIONS = {
    "QVGA": {"board": "QVGA", "imx": (320, 240)},
    "VGA":  {"board": "VGA",  "imx": (640, 480)},
    "HD":   {"board": "HD",   "imx": (1280, 720)},
}
RES_ORDER = ("QVGA", "VGA", "HD")

#: The OPERATING BAND, per Nick 2026-09-07 after looking at real frames:
#: "q30 is not impressive, anything around q50-90 is where we want to
#: operate." An earlier version of this file swept q10-q45 on the reasoning
#: that customers prefer frame rate over quality and the interesting region
#: is therefore the floor. That reasoning was sound and the conclusion was
#: wrong: preferring frame rate does not mean the pictures may be ugly, and
#: the floor turned out to sit well above where the sweep was looking.
#: q40 is kept as the one rung BELOW the band, so the report still shows
#: what falling out of it costs rather than just asserting the band.
QUALITIES = (40, 50, 60, 70, 80, 90)

#: Nick's targets. 30 is the YouTube goal; the rest map the fall-off.
FPS_TARGETS = (30, 25, 20, 15, 10, 5)

CLIP_SECONDS = 5
PROBE_MS = 3000            # unpaced burst length for the ceiling measurement
CLIP_PER_HOUR = 3600.0     # one clip per hour -> the sustained-bandwidth figure

#: A cell counts as reaching an fps target only if it got within this much of
#: it. 0.9 rather than 1.0 because a 30 fps target measured at 29.4 is a pass
#: in every sense Nick cares about, and a hard equality would report noise.
FPS_TOLERANCE = 0.9


# --------------------------------------------------------------------------
# Pure logic -- no hardware, no IO. This half is what the tests exercise.
# --------------------------------------------------------------------------

def clip_bytes(bytes_per_frame, fps, seconds=CLIP_SECONDS):
    """Bytes for one clip. MJPEG has no inter-frame coding, so this is exact."""
    return int(round(bytes_per_frame * fps * seconds))


def link_bitrate_bps(total_bytes, period_s=CLIP_PER_HOUR):
    """Sustained bandwidth to move `total_bytes` once per `period_s`.

    This is THE number the DOE exists to produce: a 5 s clip per hour is not
    a 5 s problem, it is a 3600 s one.
    """
    if period_s <= 0:
        raise ValueError("period_s must be > 0")
    return (total_bytes * 8.0) / period_s


def reachable_fps(ceiling_fps, targets=FPS_TARGETS, tol=FPS_TOLERANCE):
    """Which fps targets this cell can actually sustain. Everything else is NA."""
    if not ceiling_fps or ceiling_fps <= 0:
        return []
    return [f for f in targets if ceiling_fps >= f * tol]


def human_bytes(n):
    if n is None:
        return "--"
    for unit, div in (("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return "%.2f %s" % (n / float(div), unit)
    return "%d B" % n


def human_rate(bps):
    if bps is None:
        return "--"
    if bps >= 1e6:
        return "%.2f Mbps" % (bps / 1e6)
    if bps >= 1e3:
        return "%.1f kbps" % (bps / 1e3)
    return "%.0f bps" % bps


def parse_list(spec):
    """Split a param list on "," or "-".

    The workbench recipe schema rejects commas in param choices (they break
    the rendered card), so recipes spell a list with dashes -- "10-20-30".
    Both are accepted so a human typing the CLI is never surprised.
    """
    if not spec:
        return []
    sep = "," if "," in spec else "-"
    return [x.strip() for x in spec.split(sep) if x.strip()]


def build_grid(cameras=CAMERAS, resolutions=RES_ORDER, qualities=QUALITIES):
    """Probe cells, ordered so a camera comparison is not confounded with time.

    The scene drifts over a run (sun moves, someone walks past) and JPEG size
    is a function of scene detail, so the ORDER is part of the experiment: all
    three cameras are measured back-to-back within a (resolution, quality)
    block. That keeps the camera-vs-camera delta -- the comparison this exists
    for -- inside seconds of each other rather than tens of minutes.
    """
    return [{"resolution": r, "quality": q, "camera": c}
            for r in resolutions for q in qualities for c in cameras]


# --------------------------------------------------------------------------
# Board capture (AE3 / N6)
# --------------------------------------------------------------------------

#: Unpaced burst: how fast can this board capture+encode, and what does a
#: frame cost? Nothing is transferred -- the frame is measured and dropped.
#:
#: That is the whole point. On this rig transfer is 99% of a burst's wall
#: time (measured S29: the AE3 encodes raw at 54 fps but a burst appeared to
#: take 8-10 s), so a probe that shipped its frames would measure the USB
#: link and report it as the camera's frame rate.
BOARD_PROBE = '''
import csi, image, time, gc
c = csi.CSI()
c.reset()
c.pixformat(csi.RGB565)
c.framesize(csi.%(SIZE)s)
time.sleep_ms(1500)
for _ in range(3):
    c.snapshot()
gc.collect()
n = 0
tot = 0
mn = 1 << 30
mx = 0
w = 0
h = 0
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < %(MS)d:
    img = c.snapshot()
    w = img.width()
    h = img.height()
    j = img.to_jpeg(quality=%(Q)d)
    L = len(j.bytearray())
    tot += L
    n += 1
    if L < mn:
        mn = L
    if L > mx:
        mx = L
    gc.collect()
el = time.ticks_diff(time.ticks_ms(), t0)
print('#P {"n":%%d,"ms":%%d,"tot":%%d,"min":%%d,"max":%%d,"w":%%d,"h":%%d,"free":%%d}'
      %% (n, el, tot, mn if n else 0, mx, w, h, gc.mem_free()))
print("#D done")
'''

#: Record a clip. Two modes, chosen HOST-side from the probe's numbers:
#:
#:   BUFFER -- capture the whole clip into RAM at the target pace, then ship
#:       it. The only way to get TRUE motion at a target the USB link cannot
#:       stream. Needs fps*seconds*bytes_per_frame to fit in free heap.
#:   STREAM -- encode and ship each frame as it comes. Unbounded memory, but
#:       the pace collapses to whatever the link allows, so the clip covers
#:       more scene time than it claims.
#:
#: Every frame carries its own capture timestamp so the host can report the
#: rate that ACTUALLY happened rather than the one that was requested.
BOARD_CLIP = '''
import csi, image, time, gc, ubinascii
c = csi.CSI()
c.reset()
c.pixformat(csi.RGB565)
c.framesize(csi.%(SIZE)s)
time.sleep_ms(1500)
for _ in range(3):
    c.snapshot()
gc.collect()
N = %(N)d
PERIOD = %(PERIOD)d
BUFFER = %(BUFFER)d
buf = []
stamps = []
t0 = time.ticks_ms()
t_next = t0
for i in range(N):
    d = time.ticks_diff(t_next, time.ticks_ms())
    if d > 0:
        time.sleep_ms(d)
    img = c.snapshot()
    j = img.to_jpeg(quality=%(Q)d)
    stamps.append(time.ticks_diff(time.ticks_ms(), t0))
    if BUFFER:
        buf.append(bytes(j.bytearray()))
    else:
        b = ubinascii.b2a_base64(j.bytearray()).decode().strip()
        print("#F %%d %%d %%d" %% (i, len(b), stamps[-1]))
        print(b)
        gc.collect()
    t_next = time.ticks_add(t_next, PERIOD)
if BUFFER:
    for i in range(len(buf)):
        b = ubinascii.b2a_base64(buf[i]).decode().strip()
        print("#F %%d %%d %%d" %% (i, len(b), stamps[i]))
        print(b)
        buf[i] = None
        gc.collect()
print('#S {"span_ms":%%d,"n":%%d}' %% (stamps[-1] if stamps else 0, len(stamps)))
print("#D done")
'''


def _serial_board(port):
    sys.path.insert(0, os.path.join(_ROOT, "bench"))
    from n6_stream_host import SerialBoard
    return SerialBoard(port)


def board_probe(port, size, quality, probe_ms=PROBE_MS, timeout=90):
    """Unpaced burst -> {fps, bytes_per_frame, w, h}. Nothing transferred."""
    script = BOARD_PROBE % {"SIZE": size, "Q": quality, "MS": probe_ms}
    board = _serial_board(port).start(script)
    out, deadline = None, time.time() + timeout
    try:
        while time.time() < deadline:
            line = board.readline()
            if not line:
                break
            line = line.rstrip(b"\r\n")
            if line.startswith(b"#P "):
                out = json.loads(line[3:].decode("utf-8", "replace"))
            elif line.startswith(b"#D"):
                break
    finally:
        try:
            board.stop()
        except Exception:                            # noqa: BLE001
            pass
    if not out or not out.get("n"):
        return None
    ms = max(1, out["ms"])
    return {"fps": out["n"] * 1000.0 / ms,
            "bytes_per_frame": out["tot"] / float(out["n"]),
            "min_bytes": out["min"], "max_bytes": out["max"],
            "frames": out["n"], "w": out["w"], "h": out["h"],
            "free_heap": out.get("free")}


#: Fraction of free heap a buffered clip may claim. Deliberately conservative:
#: the encoder needs working room on top of the stored frames, and an OOM here
#: does not raise -- it wedges a board that then costs a settle window.
HEAP_MARGIN = 0.45


def buffer_fits(n_frames, bytes_per_frame, free_heap, margin=HEAP_MARGIN):
    """Can the whole clip sit in RAM? Base64 happens per frame, so this is raw."""
    if not free_heap or not bytes_per_frame:
        return False
    return (n_frames * bytes_per_frame) < (free_heap * margin)


def board_clip(port, size, quality, fps, out_dir, label,
               seconds=CLIP_SECONDS, buffered=False, timeout=600):
    """Record a clip. Returns (frame_paths, stamps_ms)."""
    import base64
    n = max(1, int(round(fps * seconds)))
    script = BOARD_CLIP % {"SIZE": size, "Q": quality, "N": n,
                           "PERIOD": max(1, int(round(1000.0 / fps))),
                           "BUFFER": 1 if buffered else 0}
    board = _serial_board(port).start(script)
    paths, stamps, deadline = [], [], time.time() + timeout
    try:
        while time.time() < deadline:
            line = board.readline()
            if not line:
                break
            line = line.rstrip(b"\r\n")
            if line.startswith(b"#F "):
                parts = line.split()
                idx, blen, ms = int(parts[1]), int(parts[2]), int(parts[3])
                payload = board.readline().rstrip(b"\r\n")
                if len(payload) != blen:
                    continue                        # short payload: drop, count
                p = os.path.join(out_dir, "%s_%04d.jpg" % (label, idx))
                with open(p, "wb") as fh:
                    fh.write(base64.b64decode(payload))
                paths.append(p)
                stamps.append(ms)
            elif line.startswith(b"#D"):
                break
    finally:
        try:
            board.stop()
        except Exception:                            # noqa: BLE001
            pass
    return paths, stamps


# --------------------------------------------------------------------------
# IMX708 capture (CSI, via rpicam-vid)
# --------------------------------------------------------------------------

def imx_argv(width, height, fps, quality, out_path, ms, camera=0):
    """rpicam-vid command line for one DOE clip. Separated so tests assert it."""
    return ["rpicam-vid", "-n", "--codec", "mjpeg",
            "--camera", str(camera),
            "--width", str(width), "--height", str(height),
            "--framerate", str(fps),
            "--quality", str(quality),
            "-t", str(int(ms)),
            "-o", out_path]


def _split_mjpeg_sizes(path):
    """Per-frame JPEG sizes in a concatenated MJPEG file."""
    sys.path.insert(0, _HERE)
    from sources import SOI, EOI
    data = open(path, "rb").read()
    sizes, pos = [], 0
    while True:
        s = data.find(SOI, pos)
        if s < 0:
            break
        e = data.find(EOI, s + 2)
        if e < 0:
            break
        sizes.append(e + 2 - s)
        pos = e + 2
    return sizes


#: What the IMX708 is ASKED for during a probe.
#:
#: MEASURED 2026-09-07, and it was a real defect: this used to be 30, and the
#: probe then reported 25.6 fps for EVERY IMX cell -- which failed the 30 fps
#: target and would have told Nick the IMX708 cannot do 30 fps. It can. Asked
#: for 60 it delivers 55.6 at both VGA and HD.
#:
#:     requested   delivered   deficit
#:        30         25.6      ~0.37 s of window
#:        40         36.4      ~0.22 s
#:        60         55.6      ~0.18 s
#:
#: The cause is that rpicam-vid's -t window INCLUDES sensor start-up, so a
#: fixed ~0.2-0.4 s of the window produces no frames. The board probe does not
#: have this problem: it discards 3 frames and starts its clock afterwards.
#: Asking high finds the ceiling and shrinks the deficit to a few percent;
#: what remains makes the IMX figure CONSERVATIVE, never optimistic.
IMX_PROBE_FPS = 60


def imx_probe(width, height, quality, probe_ms=PROBE_MS, tmp_dir="/tmp",
              runner=subprocess.run):
    """Same contract as board_probe, for the CSI camera."""
    path = os.path.join(tmp_dir, "doe_probe.mjpeg")
    argv = imx_argv(width, height, IMX_PROBE_FPS, quality, path, probe_ms)
    t0 = time.time()
    try:
        runner(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               timeout=probe_ms / 1000.0 + 30)
    except Exception:                                # noqa: BLE001
        return None
    el = max(0.001, time.time() - t0)
    if not os.path.exists(path):
        return None
    sizes = _split_mjpeg_sizes(path)
    try:
        os.unlink(path)
    except OSError:
        pass
    if not sizes:
        return None
    # rpicam-vid's -t is the CAPTURE window; startup is outside it. Rate is
    # taken from that window, not from our wall clock, which includes the
    # ~1-2 s of sensor configuration before the first frame.
    return {"fps": len(sizes) * 1000.0 / probe_ms,
            "bytes_per_frame": sum(sizes) / float(len(sizes)),
            "min_bytes": min(sizes), "max_bytes": max(sizes),
            "frames": len(sizes), "w": width, "h": height,
            "free_heap": None, "wall_s": el}


def imx_clip(width, height, fps, quality, out_path, seconds=CLIP_SECONDS,
             runner=subprocess.run):
    """Record one MJPEG clip. Returns (frame_sizes, path) or (None, None)."""
    argv = imx_argv(width, height, fps, quality, out_path, seconds * 1000)
    try:
        runner(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               timeout=seconds + 40)
    except Exception:                                # noqa: BLE001
        return None, None
    if not os.path.exists(out_path):
        return None, None
    return _split_mjpeg_sizes(out_path), out_path


# --------------------------------------------------------------------------
# Assembly + transcode
# --------------------------------------------------------------------------

#: The Pi Zero 2 W's VideoCore has a real H.264 encoder on /dev/video11
#: (bcm2835-codec-encode). Measured 2026-09-07 on a 720p30 5 s clip:
#: hardware 7 s, libx264 ultrafast 12 s, libx264 veryfast 33 s -- and the
#: hardware file was SMALLER (277 kB vs 494 kB). Software is the fallback
#: only, because a DOE that spends 33 s per cell transcoding is a DOE nobody
#: runs twice.
H264_ENCODER = "h264_v4l2m2m"


def concat_jpegs(paths, out_path):
    """Board frames -> one MJPEG file, the same container the IMX writes."""
    with open(out_path, "wb") as out:
        for p in paths:
            with open(p, "rb") as fh:
                out.write(fh.read())
    return out_path


def transcode_argv(src, dst, fps, encoder=H264_ENCODER, bitrate=None):
    """MJPEG -> H.264/MP4 for viewing in a browser.

    -r on the INPUT is what sets the clip's timebase: an MJPEG file carries no
    frame timing of its own, so without this ffmpeg assumes 25 and every clip
    plays at the wrong speed.
    """
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-r", "%g" % fps, "-f", "mjpeg", "-i", src,
            "-c:v", encoder]
    if bitrate:
        argv += ["-b:v", str(bitrate)]
    argv += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", dst]
    return argv


def transcode(src, dst, fps, encoder=H264_ENCODER, bitrate="4M",
              runner=subprocess.run):
    """Returns the H.264 size in bytes, or None. Never raises on a bad clip."""
    try:
        runner(transcode_argv(src, dst, fps, encoder, bitrate),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    except Exception:                                # noqa: BLE001
        return None
    return os.path.getsize(dst) if os.path.exists(dst) else None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def probe_row(cell, ports, imx_camera=0, probe_ms=PROBE_MS):
    """Measure one (camera, resolution, quality) cell. Never raises.

    One refused board must not take the run down -- S29 shipped that bug once
    already (a single AE3 refusal killed a whole three-camera card).
    """
    cam, res, q = cell["camera"], cell["resolution"], cell["quality"]
    row = dict(cell, ok=False, error=None,
               ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    try:
        if cam == "IMX708":
            w, h = RESOLUTIONS[res]["imx"]
            got = imx_probe(w, h, q, probe_ms)
        else:
            port = ports.get(cam)
            if not port:
                row["error"] = "no board reported role %s" % cam
                return row
            got = board_probe(port, RESOLUTIONS[res]["board"], q, probe_ms)
    except Exception as exc:                          # noqa: BLE001
        row["error"] = "%s: %s" % (type(exc).__name__, exc)
        return row
    if not got:
        row["error"] = "probe returned nothing"
        return row
    row.update(got)
    row["ok"] = True
    row["reachable"] = reachable_fps(got["fps"])
    # The size matrix: every fps target priced from this one measurement.
    row["sizes"] = {
        str(f): {
            "clip_bytes": clip_bytes(got["bytes_per_frame"], f),
            "link_bps": link_bitrate_bps(clip_bytes(got["bytes_per_frame"], f)),
            "reachable": f in row["reachable"],
        } for f in FPS_TARGETS}
    return row


def clip_row(cam, res, quality, fps, ports, out_dir, imx_camera=0,
             probe=None, seconds=CLIP_SECONDS, do_transcode=True):
    """Record one viewable clip and price it. Never raises."""
    label = "%s_%s_q%02d_f%02d" % (cam, res, quality, fps)
    row = {"camera": cam, "resolution": res, "quality": quality,
           "fps_target": fps, "label": label, "ok": False, "error": None,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    mjpeg = os.path.join(out_dir, label + ".mjpeg")
    try:
        if cam == "IMX708":
            w, h = RESOLUTIONS[res]["imx"]
            sizes, path = imx_clip(w, h, fps, quality, mjpeg, seconds)
            if not sizes:
                row["error"] = "rpicam-vid produced no frames"
                return row
            span_ms = seconds * 1000.0
            mode = "rpicam"
        else:
            port = ports.get(cam)
            if not port:
                row["error"] = "no board reported role %s" % cam
                return row
            buffered = bool(probe) and buffer_fits(
                int(round(fps * seconds)), probe.get("bytes_per_frame"),
                probe.get("free_heap"))
            frames_dir = os.path.join(out_dir, "_frames_" + label)
            os.makedirs(frames_dir, exist_ok=True)
            paths, stamps = board_clip(port, RESOLUTIONS[res]["board"], quality,
                                       fps, frames_dir, label, seconds, buffered)
            if not paths:
                row["error"] = "board produced no frames"
                return row
            sizes = [os.path.getsize(p) for p in paths]
            concat_jpegs(paths, mjpeg)
            for p in paths:                           # frames were only a means
                try:
                    os.unlink(p)
                except OSError:
                    pass
            try:
                os.rmdir(frames_dir)
            except OSError:
                pass
            span_ms = (stamps[-1] - stamps[0]) if len(stamps) > 1 else 0
            mode = "buffered" if buffered else "streamed"
    except Exception as exc:                          # noqa: BLE001
        row["error"] = "%s: %s" % (type(exc).__name__, exc)
        return row

    total = sum(sizes)
    # The rate that ACTUALLY happened, from the frames' own timestamps -- never
    # the rate we asked for. A streamed clip at a target the USB link cannot
    # carry covers more scene time than it claims, and the report must say so.
    actual = (len(sizes) - 1) * 1000.0 / span_ms if span_ms > 0 else float(fps)
    row.update({
        "ok": True, "mode": mode, "frames": len(sizes),
        "mjpeg_bytes": total, "bytes_per_frame": total / float(len(sizes)),
        "fps_actual": actual, "span_ms": span_ms,
        "link_bps": link_bitrate_bps(total),
        "mjpeg": os.path.basename(mjpeg),
    })
    if do_transcode:
        mp4 = os.path.join(out_dir, label + ".mp4")
        h264 = transcode(mjpeg, mp4, fps)
        row["h264_bytes"] = h264
        row["h264_link_bps"] = link_bitrate_bps(h264) if h264 else None
        row["mp4"] = os.path.basename(mp4) if h264 else None
    return row


class DoeState(object):
    """Everything the page renders. Written incrementally so a run that is
    stopped half way is still worth reading -- this takes far longer than
    anyone wants to watch, and partial results are the normal case."""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.phase = "starting"
        self.note = ""
        self.probes = []
        self.clips = []
        self.done = 0
        self.total = 0
        self.started = time.time()
        self.finished = None
        self.ports = {}

    def snapshot(self):
        return {"phase": self.phase, "note": self.note, "done": self.done,
                "total": self.total, "elapsed_s": round(time.time() - self.started, 1),
                "finished": self.finished, "ports": self.ports,
                "probes": self.probes, "clips": self.clips,
                "qualities": list(QUALITIES), "fps_targets": list(FPS_TARGETS),
                "clip_seconds": CLIP_SECONDS, "cameras": list(CAMERAS),
                "resolutions": list(RES_ORDER)}

    def save(self):
        path = os.path.join(self.out_dir, "results.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.snapshot(), fh, indent=1)
        os.replace(tmp, path)                 # atomic: the page never reads half


def clip_plan(probes, qualities, fps_targets, cameras=CAMERAS):
    """Which clips to actually record, given what the probes proved possible.

    The probe already priced every cell, so clips exist ONLY to be watched.
    That makes the selection a judgement, not a sweep: take the FASTEST
    sustainable rate at each (camera, resolution, quality), because Nick's
    customers want frame rate and the question is how low the quality can go
    before it stops being worth transmitting. Recording all six rates per cell
    would quadruple a run whose extra clips differ only in smoothness.
    """
    plan = []
    for p in probes:
        if not p.get("ok") or not p.get("reachable"):
            continue
        if p["quality"] not in qualities:
            continue
        best = max(f for f in p["reachable"] if f in fps_targets) \
            if any(f in fps_targets for f in p["reachable"]) else None
        if best:
            plan.append({"camera": p["camera"], "resolution": p["resolution"],
                         "quality": p["quality"], "fps": best, "probe": p})
    return plan


def run_doe(state, ports, qualities=QUALITIES, resolutions=RES_ORDER,
            cameras=CAMERAS, fps_targets=FPS_TARGETS, do_clips=True,
            probe_ms=PROBE_MS, settle_s=1.0):
    """Walk the grid. Writes results after EVERY cell -- see DoeState.save."""
    grid = build_grid(cameras, resolutions, qualities)
    state.total = len(grid)
    state.phase = "probing"
    state.note = "measuring encode rate and bytes/frame"
    state.save()
    for cell in grid:
        state.note = "probe %s %s q%d" % (cell["camera"], cell["resolution"],
                                          cell["quality"])
        row = probe_row(cell, ports, probe_ms=probe_ms)
        state.probes.append(row)
        state.done += 1
        state.save()
        time.sleep(settle_s)                # let the port go quiet between cells

    if not do_clips:
        state.phase = "done"
        state.finished = time.time()
        state.note = "probe-only run"
        state.save()
        return state

    plan = clip_plan(state.probes, qualities, fps_targets, cameras)
    state.phase = "clips"
    state.total = len(grid) + len(plan)
    for item in plan:
        state.note = "clip %s %s q%d @%d fps" % (
            item["camera"], item["resolution"], item["quality"], item["fps"])
        row = clip_row(item["camera"], item["resolution"], item["quality"],
                       item["fps"], ports, state.out_dir, probe=item["probe"])
        state.clips.append(row)
        state.done += 1
        state.save()
        time.sleep(settle_s)
    state.phase = "done"
    state.finished = time.time()
    state.note = "%d probes, %d clips" % (len(state.probes), len(state.clips))
    state.save()
    return state


# --------------------------------------------------------------------------
# HTTP: the page, served BEFORE anything is measured (D48)
# --------------------------------------------------------------------------

PAGE = """<!doctype html><meta charset="utf-8">
<title>Video DOE __HOST__</title>
<style>
 body{background:#11151a;color:#e6edf3;font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;padding:18px}
 h1{font-size:19px;margin:0 0 2px} .sub{color:#8b98a5;font-size:12.5px;margin-bottom:14px}
 .bar{background:#1c2430;border:1px solid #2b3644;border-radius:6px;padding:10px 12px;margin-bottom:16px}
 .prog{height:6px;background:#2b3644;border-radius:3px;overflow:hidden;margin-top:8px}
 .prog i{display:block;height:100%;background:#3b82f6;width:0}
 table{border-collapse:collapse;font-size:12.5px;margin-bottom:22px;width:100%}
 th,td{border:1px solid #2b3644;padding:4px 7px;text-align:right;white-space:nowrap}
 th{background:#1c2430;color:#8b98a5;font-weight:600} td.l,th.l{text-align:left}
 .na{color:#5b6673} .hit{color:#4ade80;font-weight:600} .miss{color:#f0883e}
 h2{font-size:15px;margin:22px 0 8px;color:#cbd5e1}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
 .clip{background:#1c2430;border:1px solid #2b3644;border-radius:6px;padding:9px}
 .clip video{width:100%;border-radius:4px;background:#000}
 .clip .cap{font-size:12px;color:#8b98a5;margin-top:6px}
 .clip b{color:#e6edf3}
 code{color:#7dd3fc}
</style>
<h1>Video DOE — what each camera can deliver, and what it costs to send</h1>
<div class="sub">Headline is <b>bytes as produced by the camera</b> (MJPEG).
A 5&nbsp;s clip once per hour is a sustained link budget: bytes&nbsp;&divide;&nbsp;3600&nbsp;s.
The H.264 column is <b>the Pi's</b> encoder, not the board's — the boards cannot make H.264.</div>
<div class="bar"><span id="ph">starting…</span> <span id="nt" style="color:#8b98a5"></span>
<div class="prog"><i id="pb"></i></div></div>
<div id="body"></div>
<script>
const B=n=>n==null?'--':n>=1048576?(n/1048576).toFixed(2)+' MB':n>=1024?(n/1024).toFixed(1)+' KB':n+' B';
const R=b=>b==null?'--':b>=1e6?(b/1e6).toFixed(2)+' Mbps':b>=1e3?(b/1e3).toFixed(1)+' kbps':b.toFixed(0)+' bps';
function tick(){
 fetch('/api/state').then(r=>r.json()).then(s=>{
  document.getElementById('ph').textContent=s.phase.toUpperCase()+'  '+s.done+'/'+s.total;
  document.getElementById('nt').textContent=' — '+s.note+'  ('+s.elapsed_s+'s)';
  document.getElementById('pb').style.width=(s.total?100*s.done/s.total:0)+'%';
  let h='';
  // ---- the size matrix, one table per resolution
  for(const res of s.resolutions){
   const rows=s.probes.filter(p=>p.resolution===res);
   if(!rows.length) continue;
   h+='<h2>'+res+' — bytes per '+s.clip_seconds+'s clip (camera-produced MJPEG)</h2><table>';
   h+='<tr><th class="l">camera</th><th class="l">px</th><th>q</th><th>max fps</th><th>B/frame</th>';
   for(const f of s.fps_targets) h+='<th>'+f+' fps</th>';
   h+='<th>hourly link @max</th></tr>';
   for(const p of rows){
    if(!p.ok){h+='<tr><td class="l">'+p.camera+'</td><td class="l na" colspan="4">'+(p.error||'failed')+'</td>'
      +'<td class="na" colspan="'+(s.fps_targets.length+1)+'"></td></tr>';continue;}
    h+='<tr><td class="l">'+p.camera+'</td><td class="l">'+p.w+'×'+p.h+'</td><td>'+p.quality+'</td>'
      +'<td>'+p.fps.toFixed(1)+'</td><td>'+B(Math.round(p.bytes_per_frame))+'</td>';
    // The headline is the FASTEST sustainable rate, not the last one the loop
    // happened to see. fps_targets is descending, so a naive `best=c` inside
    // the loop lands on the SLOWEST reachable rate and understates the budget
    // by up to 6x -- which is the wrong direction for a link budget to be
    // wrong in. Take the first reachable, and pin it with a test.
    let best=null;
    for(const f of s.fps_targets){const c=p.sizes[f];
      if(!c){h+='<td class="na">--</td>';continue;}
      if(c.reachable){if(!best)best=c;h+='<td class="hit">'+B(c.clip_bytes)+'</td>';}
      else h+='<td class="na">NA</td>';}
    h+='<td>'+(best?R(best.link_bps):'<span class="na">NA</span>')+'</td></tr>';
   }
   h+='</table>';
  }
  // ---- the clips
  if(s.clips.length){
   h+='<h2>Clips — look at these, the floor is not a number</h2><div class="grid">';
   for(const c of s.clips){
    if(!c.ok){h+='<div class="clip"><div class="cap"><b>'+c.label+'</b><br>'+(c.error||'failed')+'</div></div>';continue;}
    h+='<div class="clip">'+(c.mp4?'<video controls loop muted playsinline src="/v/'+c.mp4+'"></video>':'')
     +'<div class="cap"><b>'+c.camera+'  '+c.resolution+'  q'+c.quality+'  '+c.fps_target+' fps</b>'
     +'<br>camera MJPEG <b>'+B(c.mjpeg_bytes)+'</b> → <b>'+R(c.link_bps)+'</b> hourly'
     +'<br>Pi H.264 '+B(c.h264_bytes)+' → '+R(c.h264_link_bps)+' hourly'
     +'<br>'+c.frames+' frames, actual '+c.fps_actual.toFixed(1)+' fps ('+c.mode+')'
     +' · <a href="/v/'+c.mjpeg+'" style="color:#7dd3fc">mjpeg</a></div></div>';
   }
   h+='</div>';
  }
  document.getElementById('body').innerHTML=h;
  if(s.phase!=='done') setTimeout(tick,2500); else setTimeout(tick,10000);
 }).catch(()=>setTimeout(tick,4000));
}
tick();
</script>"""


def make_handler(state):
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):                    # quiet: the run is the log
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/" or path.startswith("/index"):
                host = self.headers.get("Host", "").split(":")[0] or "field rig"
                return self._send(200, "text/html; charset=utf-8",
                                  PAGE.replace("__HOST__", host).encode())
            if path == "/healthz":
                return self._send(200, "text/plain", b"ok")
            if path == "/api/state":
                return self._send(200, "application/json",
                                  json.dumps(state.snapshot()).encode())
            if path.startswith("/v/"):
                name = os.path.basename(path[3:])
                full = os.path.join(state.out_dir, name)
                if not os.path.isfile(full):
                    return self._send(404, "text/plain", b"no such clip")
                ctype = "video/mp4" if name.endswith(".mp4") else "video/x-motion-jpeg"
                with open(full, "rb") as fh:
                    return self._send(200, ctype, fh.read())
            self._send(404, "text/plain", b"not found")

    return H


def discover_ports(settle_s=0.0):
    """Role -> port. Boards are addressed by ROLE, never by USB serial."""
    sys.path.insert(0, _HERE)
    from discover import discover
    if settle_s:
        time.sleep(settle_s)
    found, problems = discover()
    return {k: v["port"] for k, v in found.items()}, problems


def main(argv=None):
    import argparse
    import socketserver
    import threading

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=8094)
    ap.add_argument("--out-dir", default=os.path.expanduser("~/video_doe"))
    ap.add_argument("--qualities", default=",".join(str(q) for q in QUALITIES),
                    help="JPEG quality levels to sweep")
    ap.add_argument("--resolutions", default=",".join(RES_ORDER))
    ap.add_argument("--cameras", default=",".join(CAMERAS))
    ap.add_argument("--probe-ms", type=int, default=PROBE_MS)
    ap.add_argument("--no-clips", action="store_true",
                    help="probe only -- the full size matrix, no video")
    args = ap.parse_args(argv)

    run_dir = os.path.join(args.out_dir,
                           time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    os.makedirs(run_dir, exist_ok=True)
    state = DoeState(run_dir)
    state.save()

    class Quiet(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = Quiet((args.bind, args.http_port), make_handler(state))

    def work():
        # Discovery touches the boards, so it happens HERE and not before the
        # socket is bound: the workbench health-gates LIVE on this page
        # answering within 60 s, and a refused AE3 costs a 60 s silence wait.
        # Two 60 s timeouts on one path is how S29 killed a whole card (D48).
        state.phase = "discovering"
        state.note = "asking each board its role"
        state.save()
        try:
            ports, problems = discover_ports()
        except Exception as exc:                      # noqa: BLE001
            ports, problems = {}, ["discovery failed: %s" % exc]
        state.ports = ports
        if problems:
            state.note = "; ".join(str(p) for p in problems)
        state.save()
        try:
            run_doe(state,
                    ports,
                    qualities=tuple(int(q) for q in parse_list(args.qualities)),
                    resolutions=tuple(parse_list(args.resolutions)),
                    cameras=tuple(parse_list(args.cameras)),
                    do_clips=not args.no_clips,
                    probe_ms=args.probe_ms)
        except Exception as exc:                      # noqa: BLE001
            state.phase = "error"
            state.note = "%s: %s" % (type(exc).__name__, exc)
            state.finished = time.time()
            state.save()

    threading.Thread(target=work, daemon=True).start()
    print("video DOE: http://%s:%d/  results -> %s"
          % (args.bind, args.http_port, run_dir), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
