#!/usr/bin/env python3
"""IMX708 dive recorder: a science stream and an iPad proxy, in fixed segments.

WHAT THIS IS FOR (Nick's Channel Islands spec, 2026-09-09): record a whole
dive continuously off the IMX708 so frames can be harvested in post to build a
depth-and-distance colour-correction model, while also producing something an
iPad can play natively between dives. Both come off ONE camera at once --
measured on nereus002: science alone 29.70 fps, science + proxy 29.70 fps.

THREE DESIGN RULES, each of which exists because breaking it ruins the dataset
rather than merely annoying someone:

1. WHITE BALANCE IS LOCKED ONCE PER DIVE, AND THE LOCK OUTLIVES EVERY SEGMENT.
   The whole point of the trip is that colour drifts with depth. If AWB were
   allowed to re-converge, the camera would partially CANCEL that drift -- it
   would quietly correct away the signal being measured. So AWB converges once
   against the reference card, the gains are read back and frozen, and nothing
   afterwards is allowed to touch them.

2. SEGMENTING ROLLS THE ENCODERS, NEVER THE CAMERA. Restarting the camera
   would re-run AWB and AF and undo rule 1, and would drop frames while the
   sensor reconfigures. `Picamera2.stop_encoder([enc])` takes specific
   encoders, so the camera free-runs across a segment boundary and only the
   files change. A fresh H264Encoder per segment is deliberate too: it emits
   its own SPS/PPS and opening IDR, so every proxy file decodes standalone
   rather than depending on a header written 20 minutes earlier.

3. A SEGMENT IS NOT FINISHED UNTIL IT IS ON THE CARD AND DESCRIBED. Each
   segment closes with its own sidecar manifest carrying what was asked for,
   what was delivered, the frozen WB/exposure/focus state, and depth if a
   sensor is fitted. A clip whose settings are unknown is not science data.

MEASURED ON THIS RIG (nereus002, 2026-09-09) -- do not re-derive:
  * science sw JPEG q90 @1280x800 = 4.94 MB/s; proxy H.264 @640x400 = 0.35 MB/s
  * N6 HD q70 = 2.86 MB/s; AE3 VGA q50 = 0.15 MB/s; all four = 8.30 MB/s
  * the SD bus sustains 21.31 MB/s (1% spread over 2 GB, no stalls), so the
    card is at 39% -- headroom is real, capacity is the constraint.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    import depth as depth_mod
except ImportError:
    depth_mod = None

# The board recorder's byte-exact thumbnail (one JPEG lifted out of the
# MJPEG, no decode). The science stream is the same container, so the IMX
# gets its index-page thumbnail the same way and at the same cost: a read
# and a write, never a transcode (Nick's rule for this rig).
try:
    from recorder import write_thumbnail as _write_thumbnail
    from recorder import write_json_durable as _write_json
except ImportError:
    _write_thumbnail = None
    _write_json = None


def _write_json_fallback(path, obj, indent=None):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


if _write_json is None:
    _write_json = _write_json_fallback

#: Seconds of AWB/AE convergence before the lock is taken. The reference card
#: must be in frame for this whole window.
DEFAULT_CONVERGE_S = 8.0

#: Segment length. Nick: max 5 minutes, then close the file and open the next.
DEFAULT_SEGMENT_S = 300.0


def now_iso():
    """ISO-8601 UTC. Timestamps in files are always UTC on this rig."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_iso_at(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _jsonable(v):
    """libcamera hands back tuples/enums; make them survive json.dump."""
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


#: Exit status when the sensor stopped delivering frames. The launcher
#: (run_channel_islands.sh) relaunches on exactly this code, at the next
#: segment, so a dead frontend costs seconds of footage rather than the dive.
EXIT_STALLED = 3


def call_with_timeout(fn, timeout, default=None):
    """Run fn() on a helper thread; give up after `timeout` s.

    Measured 2026-09-10 on nereus002: 55 s after boot libcamera reported
    "Camera frontend has timed out!", the sensor stopped, and the recorder
    then sat forever inside capture_metadata() at the top of the next
    segment -- boards recording, manifests saying nothing, IMX silently
    absent. Every call that waits on the camera is bounded now.
    """
    box = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as exc:                  # noqa: BLE001
            box["e"] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive() or "e" in box:
        return default
    return box.get("v", default)


class StallWatch:
    """Frames stopped arriving? Pure bookkeeping, tested without a camera."""

    def __init__(self, stall_s, grace_s=None):
        self.stall_s = float(stall_s)
        self.grace_s = float(grace_s if grace_s is not None else stall_s)
        self.frames = 0
        self.t_change = None
        self.t_first = None

    def update(self, frames, now):
        if self.t_first is None:
            self.t_first, self.t_change = now, now
        if frames != self.frames:
            self.frames, self.t_change = frames, now
            return None
        if now - self.t_first < self.grace_s:
            return None                     # still starting up
        if now - self.t_change >= self.stall_s:
            return "no IMX frame for %.0f s after %d frames" % (now - self.t_change, frames)
        return None


class DiveRecorder:
    def __init__(self, args):
        self.args = args
        self.cam = None
        self.locked = {}
        self.segments = []
        self.stop = threading.Event()
        self.stalled = None
        self.depth = None
        if depth_mod is not None:
            self.depth = depth_mod.open_sensor()

    def _meta(self, timeout=5.0):
        """capture_metadata() that cannot hang the dive (see call_with_timeout)."""
        md = call_with_timeout(self.cam.capture_metadata, timeout, None)
        if md is None:
            print("WARNING: no camera metadata within %.0fs" % timeout,
                  file=sys.stderr, flush=True)
            return {}
        return md

    # -- setup ------------------------------------------------------------
    def configure(self):
        from picamera2 import Picamera2
        a = self.args
        self.cam = Picamera2()
        streams = {"main": {"size": tuple(a.science_size), "format": "YUV420"}}
        if a.science == "jpeg":
            streams["lores"] = {"size": tuple(a.proxy_size), "format": "YUV420"}
        cfg = self.cam.create_video_configuration(
            controls={"FrameDurationLimits": (int(1e6 / a.fps),
                                              int(1e6 / a.fps))},
            buffer_count=a.buffers,
            raw=None,               # Bayer is ~50 MB/s; never a video candidate
            **streams
        )
        self.cam.configure(cfg)

    def start_camera(self):
        """Start with AWB/AE free so they can converge on the card."""
        a = self.args
        self.cam.start()
        # Exposure biased down: highlights that clip are gone for good, while
        # shadows survive in the raw-ish JPEG and can be lifted in post.
        controls = {"AwbEnable": True, "ExposureValue": a.exposure_value}
        try:
            from libcamera import controls as lc
            if a.focus == "manual":
                # Focus is pinned, not hunted. Value carried over from
                # bm_cam_legacy's bmcam000 profile (camera_schedule.yaml:
                # focus.mode "manual", lens_position 1.82), so the two rigs
                # agree. Units are dioptres (1/m) in BOTH rpicam's
                # --lens-position and picamera2's LensPosition, so the number
                # transfers unchanged; 1.82 is ~55 cm.
                #
                # CARRIED FORWARD FROM THAT REPO, NOT RE-DERIVED: its own note
                # says these are IN-AIR values and that water shifts the
                # effective focus behind a flat port (n~1.33). It is the right
                # default because it is what the other rig runs -- it is not a
                # measured underwater focus, and nothing here claims it is.
                controls["AfMode"] = lc.AfModeEnum.Manual
                controls["LensPosition"] = float(a.lens_position)
            else:
                controls["AfMode"] = lc.AfModeEnum.Continuous
        except Exception:
            pass
        self.cam.set_controls(controls)

    def converge_and_lock(self):
        """Let AWB/AE settle on the reference card, then FREEZE everything.

        Rule 1. The gains are read back from live metadata and written back as
        fixed values -- reading them is what makes the lock auditable later,
        and every segment manifest carries them.
        """
        a = self.args
        if a.wb == "auto":
            # AWB deliberately left running. This is the "just record" recipe:
            # focus is pinned but colour is allowed to track, which is what a
            # normal capture wants. It is NOT the depth-study mode -- an AWB
            # that tracks will partially cancel the depth colour shift, so a
            # dive recorded this way cannot serve the colour dataset.
            time.sleep(min(a.converge_s, 3.0))
            md = self._meta()
            self.locked = {
                "mode": "awb-auto",
                "locked_at_utc": None,
                "note": ("AWB left ENABLED; colour tracks the scene. Not "
                         "usable as depth-vs-colour reference data."),
                "focus_mode": a.focus,
                "lens_position": (float(a.lens_position)
                                  if a.focus == "manual" else None),
                "observed_colour_gains": _jsonable(md.get("ColourGains")),
                "observed_lens_position": _jsonable(md.get("LensPosition")),
                "exposure_value_bias": a.exposure_value,
            }
            return self.locked
        deadline = time.time() + a.converge_s
        md = {}
        while time.time() < deadline and not self.stop.is_set():
            md = self._meta()
        if self.stop.is_set() and not md:
            md = self._meta()
        gains = md.get("ColourGains")
        lock = {"AwbEnable": False}
        if gains:
            lock["ColourGains"] = (float(gains[0]), float(gains[1]))
        try:
            from libcamera import controls as lc
            lens = md.get("LensPosition")
            if lens is not None:
                lock["AfMode"] = lc.AfModeEnum.Manual
                lock["LensPosition"] = float(lens)
        except Exception:
            pass
        self.cam.set_controls(lock)
        # Read back what actually took effect. A control that was set is not a
        # control that is in force (CLAUDE.md rule 4).
        time.sleep(0.5)
        after = self._meta()
        self.locked = {
            "mode": "wb-locked",
            "focus_mode": a.focus,
            "lens_position": (float(a.lens_position)
                              if a.focus == "manual" else None),
            "locked_at_utc": now_iso(),
            "converge_s": a.converge_s,
            "requested": {k: _jsonable(v) for k, v in lock.items()},
            "observed_colour_gains": _jsonable(after.get("ColourGains")),
            "observed_lens_position": _jsonable(after.get("LensPosition")),
            "observed_exposure_time": _jsonable(after.get("ExposureTime")),
            "observed_analogue_gain": _jsonable(after.get("AnalogueGain")),
            "exposure_value_bias": a.exposure_value,
        }
        if not gains:
            self.locked["warning"] = (
                "no ColourGains in metadata -- WB may NOT be locked; "
                "treat this dive's colour as unreferenced")
        return self.locked

    # -- segments ---------------------------------------------------------
    def segment_dir(self, index):
        """<root>/<prefix>_s0000 -- the name the boards are given too."""
        a = self.args
        return os.path.join(a.root, "%s_s%04d" % (a.session_prefix, index))

    def _counting_output(self, path):
        """FileOutput that counts frames as they pass, not afterwards.

        WHY: closing a segment used to re-read the whole science file to count
        JPEG SOI markers -- 1.5 GB per 5-minute segment. That added tens of
        seconds to every IMX segment while the board recorder marched on at a
        flat 300 s, so the two drifted: measured over 2.5 h, 26 board segments
        against 22 IMX ones, which silently paired cameras from different
        moments inside one session directory. The encoder hands us exactly one
        buffer per frame, so counting costs nothing.
        """
        from picamera2.outputs import FileOutput

        class _Counting(FileOutput):
            frames = 0

            def outputframe(self, frame, keyframe=True, timestamp=None,
                            packet=None, audio=False):
                self.frames += 1
                try:
                    return super().outputframe(frame, keyframe, timestamp,
                                               packet, audio)
                except TypeError:
                    # Older picamera2 signature; never lose a frame over it.
                    return super().outputframe(frame, keyframe, timestamp)

        return _Counting(path)

    def _encoders(self, index):
        from picamera2.encoders import H264Encoder, JpegEncoder
        from picamera2.outputs import FileOutput
        a = self.args
        # ONE SESSION DIRECTORY PER SEGMENT, SHARED WITH THE BOARDS.
        # Nick reads the library as timestamped events, and a dive is one
        # event with three cameras in it -- not an IMX tree beside a separate
        # board tree. The launcher hands both recorders the same session name
        # per segment, so N6.mjpeg, AE3.mjpeg and IMX.mjpeg land together and
        # the page shows them side by side.
        #
        # The camera is called IMX for the same reason: the label the library
        # displays IS the file stem, and "seg_0000_science" told Nick nothing
        # about which camera he was looking at.
        seg_dir = self.segment_dir(index)
        os.makedirs(seg_dir, exist_ok=True)
        if a.science == "none":
            # NO SCIENCE JPEG (Nick, 2026-09-10 night): the software JPEG at
            # q90 1280x800 ran all four cores and was the single biggest
            # load on a rig whose battery could not carry it. The IMX is
            # recorded ONCE, as hardware H.264 of the full main stream, and
            # that file is both the record and what the iPad plays.
            sci, sci_path = None, None
            pxy_path = os.path.join(seg_dir, "IMX.h264")
            pxy = H264Encoder(bitrate=a.proxy_bitrate)
            self._sci_out = self._counting_output(pxy_path)
            self._counted_path = pxy_path
            self.cam.start_encoder(pxy, self._sci_out, name="main")
            return sci, pxy, sci_path, pxy_path
        sci_path = os.path.join(seg_dir, "IMX.mjpeg")
        pxy_path = os.path.join(seg_dir, "IMX_proxy.h264")
        sci = JpegEncoder(q=a.jpeg_q, num_threads=a.jpeg_threads)
        pxy = H264Encoder(bitrate=a.proxy_bitrate)
        self._sci_out = self._counting_output(sci_path)
        self._counted_path = sci_path
        self.cam.start_encoder(sci, self._sci_out, name="main")
        self.cam.start_encoder(pxy, FileOutput(pxy_path), name="lores")
        return sci, pxy, sci_path, pxy_path

    def write_status(self, index, t_start, elapsed=None, closing=False):
        """Publish current-segment progress so the card can show a countdown.

        Written at segment start and rewritten at close. It is deliberately a
        tiny separate file rather than part of the manifest: the manifest
        describes a FINISHED segment and must not be half-written while one is
        in flight, and a reader must never have to distinguish the two.
        """
        a = self.args
        st = {
            "session": os.path.basename(self.segment_dir(index)),
            "segment": index,
            "segment_s": a.segment_s,
            "started_unix": t_start,
            "ends_unix": t_start + a.segment_s,
            "seconds_left": (0.0 if closing
                             else max(0.0, (t_start + a.segment_s) - time.time())),
            "closing": bool(closing),
            "elapsed_s": elapsed,
            "recipe": a.recipe,
            "wb_mode": a.wb,
            "updated_utc": now_iso(),
        }
        try:
            d = self.segment_dir(index)
            os.makedirs(d, exist_ok=True)
            _write_json(os.path.join(d, "status.json"), st)
            # ONE CLOCK FOR BOTH RECORDERS. The board loop reads this to learn
            # which segment is open and when it ends, instead of counting its
            # own. Two independent 300 s timers drifted four segments apart
            # over 2.5 h; a follower cannot drift.
            cur = os.path.join(a.root, "%s_current.json" % a.session_prefix)
            _write_json(cur, {"segment": index, "session": st["session"],
                              "ends_unix": st["ends_unix"],
                              "closing": st["closing"]})
        except OSError:
            pass          # a countdown is a convenience; never fail a dive for it

    def record_segment(self, index):
        a = self.args
        t_start = time.time()
        started_iso = now_iso()
        seg_dir = self.segment_dir(index)
        os.makedirs(seg_dir, exist_ok=True)
        self.write_status(index, t_start)
        depth_start = dict(self.depth.read()) if self.depth else None
        # Re-read the LIVE gains at the top of every segment. Copying the
        # lock dict forward would make each manifest agree with itself and
        # prove nothing; this is the check that would actually catch AWB
        # having crept back on across a segment boundary (rule 1).
        gains_at_start = _jsonable(self._meta().get("ColourGains"))
        sci, pxy, sci_path, pxy_path = self._encoders(index)

        end = t_start + a.segment_s
        watch = StallWatch(a.stall_s)
        while time.time() < end and not self.stop.is_set():
            time.sleep(0.25)
            counted = getattr(self, "_sci_out", None)
            why = watch.update(counted.frames if counted is not None else 0, time.time())
            if why:
                self.stalled = why
                print("IMX STALL: %s -- closing this segment and exiting %d "
                      "for relaunch" % (why, EXIT_STALLED), file=sys.stderr, flush=True)
                break

        # Rule 2: only the encoders stop. The camera keeps running, so the
        # WB lock and the sensor's state survive into the next segment.
        gains_at_end = _jsonable(self._meta().get("ColourGains"))
        live = [e for e in (sci, pxy) if e is not None]
        if call_with_timeout(lambda: self.cam.stop_encoder(live), 15, "ok") is None:
            print("WARNING: stop_encoder did not return in 15 s", file=sys.stderr, flush=True)
        elapsed = time.time() - t_start
        self.write_status(index, t_start, elapsed=elapsed, closing=True)
        depth_end = dict(self.depth.read()) if self.depth else None

        # Rule 3: measure the artifacts, then describe them.
        man = {
            "segment": index,
            "started_utc": started_iso,
            "ended_utc": now_iso(),
            "elapsed_s": round(elapsed, 2),
            "requested": {
                "science_size": a.science_size, "jpeg_q": a.jpeg_q,
                "proxy_size": a.proxy_size, "proxy_bitrate": a.proxy_bitrate,
                "fps": a.fps, "segment_s": a.segment_s,
                "recipe": a.recipe, "wb_mode": a.wb,
                "focus_mode": a.focus, "lens_position": a.lens_position,
            },
            "white_balance": self.locked,
            "wb_observed_at_segment_start": gains_at_start,
            "wb_observed_at_segment_end": gains_at_end,
            "depth_start": depth_start,
            "depth_end": depth_end,
            "delivered": {},
            "problems": [],
        }
        counted_path = getattr(self, "_counted_path", sci_path)
        for label, path in (("science", sci_path), ("proxy", pxy_path)):
            if path is None:
                man["delivered"][label] = {"disabled": True, "bytes": 0,
                                           "frames": None, "MB_s": None}
                continue
            size = os.path.getsize(path) if os.path.exists(path) else 0
            d = {"path": os.path.basename(path), "bytes": size,
                 "MB_s": round(size / elapsed / 1e6, 2) if elapsed else None}
            if path == counted_path:
                # Counted in flight; the file scan stays only as a fallback
                # for an output that never reported, and the manifest records
                # which of the two produced the number.
                counted = getattr(self, "_sci_out", None)
                in_flight = counted.frames if counted is not None else 0
                d["frames"] = in_flight or (_count_soi(path) if label == "science" else 0)
                d["frames_counted_in_flight"] = bool(in_flight)
                d["fps"] = round(d["frames"] / elapsed, 2) if elapsed else None
                if not d["frames"]:
                    man["problems"].append("%s segment has NO frames"
                                           % ("science" if label == "science" else "IMX"))
            man["delivered"][label] = d
        if not man["problems"] and man["delivered"]["proxy"]["bytes"] == 0:
            man["problems"].append("proxy segment is empty")
        if self.stalled:
            man["problems"].append("IMX STALLED: %s (camera frontend stopped; "
                                   "recorder relaunched)" % self.stalled)
            man["imx_stalled"] = True

        # A drifting gain means the lock did not hold, which silently ruins the
        # colour dataset -- so it is a PROBLEM, not a footnote.
        locked_gains = (self.locked.get("observed_colour_gains")
                        if self.locked.get("mode") == "wb-locked" else None)
        for when, seen in (("start", gains_at_start), ("end", gains_at_end)):
            if locked_gains and seen and not _gains_match(locked_gains, seen):
                man["problems"].append(
                    "WB DRIFTED at segment %s: locked %s, observed %s"
                    % (when, locked_gains, seen))

        # Make the proxy playable before anything claims the segment is done.
        pxy = man["delivered"].get("proxy") or {}
        if pxy.get("bytes"):
            mp4_path = pxy_path.rsplit(".", 1)[0] + ".mp4"
            ok, why = mux_h264_to_mp4(
                pxy_path, mp4_path,
                pxy.get("fps")
                or (man["delivered"].get("science") or {}).get("fps") or a.fps)
            if ok:
                pxy["mp4"] = os.path.basename(mp4_path)
                pxy["mp4_bytes"] = os.path.getsize(mp4_path)
                if a.science == "none":
                    # No JPEG stream to lift a frame from: decode ONE frame
                    # of the mp4 (bounded), so the index still shows a picture.
                    th_path = os.path.join(seg_dir, "IMX_thumb.jpg")
                    if thumb_from_mp4(mp4_path, th_path):
                        pxy["thumb"] = os.path.basename(th_path)
                    else:
                        man["problems"].append("IMX thumbnail not written")
            else:
                man["problems"].append("proxy not muxed to mp4: %s" % why)

        # One frame of the science stream as the thumbnail the review index
        # shows beside the N6's and AE3's. Without it the IMX -- Nick's
        # reference camera -- was the only tile on the index with no picture.
        sci = man["delivered"].get("science") or {}
        if sci.get("frames") and _write_thumbnail is not None:
            th_path = os.path.join(seg_dir, "IMX_thumb.jpg")
            try:
                if _write_thumbnail(sci_path, th_path) > 0:
                    sci["thumb"] = os.path.basename(th_path)
                else:
                    man["problems"].append("IMX thumbnail not written")
            except OSError as exc:
                man["problems"].append("IMX thumbnail not written: %s" % exc)

        with open(os.path.join(seg_dir, "imx_segment.json"), "w") as f:
            json.dump(man, f, indent=2)
        self.segments.append(man)
        # Rewritten after EVERY segment, so a dive interrupted at any point is
        # still a complete, listable session rather than one that only becomes
        # reviewable if the run ends tidily.
        try:
            merge_session_manifest(seg_dir, man, self.locked, a)
        except OSError as exc:
            man["problems"].append("session manifest not written: %s" % exc)
        if hasattr(os, "sync"):
            os.sync()
        return man

    # -- run --------------------------------------------------------------
    def install_signal_handlers(self):
        """Turn Stop into a clean segment close, not an unwind.

        MEASURED FAILURE this replaces: with the default handler, SIGINT from
        the workbench's Stop raised KeyboardInterrupt inside the segment's
        wait loop, which unwound straight past the code that closes the
        segment. The run left a 576 MB .mjpeg and a 27.8 MB .h264 on the card
        with NO manifest -- footage whose white balance, focus and settings
        were unrecorded, which for this trip is data that cannot be used.
        Setting the flag instead lets the loop exit normally, so the segment
        is measured, described and synced exactly as a full-length one is.
        """
        def _stop(signum, _frame):
            print("signal %d -- finishing the current segment" % signum,
                  file=sys.stderr, flush=True)
            self.stop.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass            # not the main thread; caller handles it

    def run_h264_only(self):
        """--science none: ONE hardware H.264 encoder, started once, whose
        output file is SWITCHED at every segment boundary (SplittableOutput,
        at the next keyframe, <= 1 s with iperiod = fps). The encoder is
        never stopped between segments, so there is no stop_encoder wait and
        no mux gap; a closed segment is finalized on a worker thread.
        """
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import SplittableOutput
        a = self.args
        self.install_signal_handlers()
        os.makedirs(a.root, exist_ok=True)
        self.configure()
        self.start_camera()
        print("AWB left AUTO -- ordinary recording, not reference data"
              if a.wb == "auto" else
              "converging %.1fs -- KEEP THE REFERENCE CARD IN FRAME" % a.converge_s,
              flush=True)
        lock = self.converge_and_lock()
        print("wb=%s focus=%s lens=%s gains=%s"
              % (lock.get("mode"), lock.get("focus_mode"),
                 lock.get("observed_lens_position"),
                 json.dumps(lock.get("observed_colour_gains"))), flush=True)
        if "warning" in lock:
            print("WARNING: %s" % lock["warning"], file=sys.stderr, flush=True)

        enc = H264Encoder(bitrate=a.proxy_bitrate, repeat=True, iperiod=int(a.fps))
        split = SplittableOutput()
        self.cam.start_encoder(enc, split, name="main")
        workers = []
        pending = None            # (seg_dir, man, h264_path) awaiting finalize
        i = int(a.first_segment)
        t0 = time.time()
        try:
            while not self.stop.is_set():
                if a.max_seconds and (time.time() - t0) >= a.max_seconds:
                    break
                if a.max_segments and (i - a.first_segment) >= a.max_segments:
                    break
                seg_dir = self.segment_dir(i)
                os.makedirs(seg_dir, exist_ok=True)
                h264_path = os.path.join(seg_dir, "IMX.h264")
                out = self._counting_output(h264_path)
                # Switch the file at the next keyframe. Bounded: a dead
                # frontend never produces one, and that is a stall, not a hang.
                if call_with_timeout(lambda: split.split_output(out), 10, "ok") is None:
                    self.stalled = "no keyframe within 10 s to open segment %d" % i
                    print("IMX STALL: %s" % self.stalled, file=sys.stderr, flush=True)
                    break
                t_start = time.time()
                self._sci_out = out
                self.write_status(i, t_start)
                if pending is not None:
                    # The previous file closed at this keyframe; describe it
                    # off the recording path.
                    pend_dir, pend_man, pend_path = pending
                    pend_man["ended_utc"] = now_iso()
                    pend_man["elapsed_s"] = round(t_start - pend_man["_t_start"], 2)
                    pend_man.pop("_t_start", None)
                    w = threading.Thread(target=finalize_h264_segment,
                                         args=(pend_dir, pend_man, pend_path, a, self.locked),
                                         name="finalize-%04d" % pend_man["segment"], daemon=True)
                    w.start(); workers.append(w)
                    pending = None
                depth_start = dict(self.depth.read()) if self.depth else None
                gains_at_start = _jsonable(self._meta().get("ColourGains"))
                end = t_start + a.segment_s
                watch = StallWatch(a.stall_s)
                while time.time() < end and not self.stop.is_set():
                    time.sleep(0.25)
                    why = watch.update(out.frames, time.time())
                    if why:
                        self.stalled = why
                        print("IMX STALL: %s -- exiting %d for relaunch"
                              % (why, EXIT_STALLED), file=sys.stderr, flush=True)
                        break
                gains_at_end = _jsonable(self._meta().get("ColourGains"))
                man = {
                    "segment": i, "started_utc": now_iso_at(t_start), "_t_start": t_start,
                    "requested": {"science": "none", "science_size": a.science_size,
                                  "proxy_bitrate": a.proxy_bitrate, "fps": a.fps,
                                  "segment_s": a.segment_s, "recipe": a.recipe,
                                  "wb_mode": a.wb, "focus_mode": a.focus,
                                  "lens_position": a.lens_position},
                    "white_balance": self.locked,
                    "wb_observed_at_segment_start": gains_at_start,
                    "wb_observed_at_segment_end": gains_at_end,
                    "depth_start": depth_start,
                    "depth_end": dict(self.depth.read()) if self.depth else None,
                    "delivered": {"science": {"disabled": True, "bytes": 0,
                                              "frames": None, "MB_s": None},
                                  "proxy": {"path": "IMX.h264", "frames": out.frames,
                                            "frames_counted_in_flight": True,
                                            "fps": None}},
                    "problems": [],
                }
                self.write_status(i, t_start, elapsed=time.time() - t_start, closing=True)
                if self.stalled:
                    man["problems"].append("IMX STALLED: %s (camera frontend "
                                           "stopped; recorder relaunched)" % self.stalled)
                    man["imx_stalled"] = True
                pending = (seg_dir, man, h264_path)
                self.segments.append(man)
                i += 1
                if self.stalled:
                    break
        except KeyboardInterrupt:
            print("interrupted -- closing current segment", flush=True)
        finally:
            # Stop the one encoder (bounded), which closes the last file, then
            # finalize it here rather than on a worker so a Stop returns only
            # once the last segment is described.
            call_with_timeout(lambda: self.cam.stop_encoder([enc]), 30)
            if pending is not None:
                pend_dir, pend_man, pend_path = pending
                pend_man["ended_utc"] = now_iso()
                pend_man["elapsed_s"] = round(time.time() - pend_man["_t_start"], 2)
                pend_man.pop("_t_start", None)
                pend_man["delivered"]["proxy"]["frames"] = self._sci_out.frames
                pend_man["delivered"]["proxy"]["fps"] = (
                    round(self._sci_out.frames / pend_man["elapsed_s"], 2)
                    if pend_man["elapsed_s"] else None)
                call_with_timeout(lambda: finalize_h264_segment(
                    pend_dir, pend_man, pend_path, a, self.locked), 240)
            for w in workers:
                w.join(120)
            call_with_timeout(self.cam.stop, 5)
            call_with_timeout(self.cam.close, 5)
        if self.stalled:
            sys.stdout.flush(); sys.stderr.flush()
            os._exit(EXIT_STALLED)
        return self.segments

    def run(self):
        a = self.args
        if a.science == "none":
            # The H.264-only loop opens and starts the camera itself; it
            # must be chosen BEFORE this method does (a second Picamera2()
            # on an open camera is "Device or resource busy" -- measured
            # 2026-09-10 22:46, twice).
            return self.run_h264_only()
        self.install_signal_handlers()
        os.makedirs(a.root, exist_ok=True)
        self.configure()
        self.start_camera()
        if a.wb == "lock":
            print("converging %.1fs -- KEEP THE REFERENCE CARD IN FRAME"
                  % a.converge_s, flush=True)
        else:
            print("AWB left AUTO -- ordinary recording, not reference data",
                  flush=True)
        lock = self.converge_and_lock()
        print("wb=%s focus=%s lens=%s gains=%s"
              % (lock.get("mode"), lock.get("focus_mode"),
                 lock.get("observed_lens_position"),
                 json.dumps(lock.get("observed_colour_gains"))), flush=True)
        if "warning" in lock:
            print("WARNING: %s" % lock["warning"], file=sys.stderr, flush=True)

        i = int(a.first_segment)
        t0 = time.time()
        try:
            while not self.stop.is_set():
                if a.max_seconds and (time.time() - t0) >= a.max_seconds:
                    break
                if a.max_segments and (i - a.first_segment) >= a.max_segments:
                    break
                man = self.record_segment(i)
                print("seg %04d: %.1fs  science %s fr %.2f MB/s  proxy %.2f MB/s  %s"
                      % (i, man["elapsed_s"],
                         man["delivered"]["science"].get("frames"),
                         man["delivered"]["science"].get("MB_s") or 0,
                         man["delivered"]["proxy"].get("MB_s") or 0,
                         man["problems"] or "ok"), flush=True)
                i += 1
                if self.stalled:
                    break
        except KeyboardInterrupt:
            print("interrupted -- closing current segment", flush=True)
        finally:
            # A dead frontend can hang stop()/close() too; bound them, and on
            # a stall exit hard so a stuck libcamera thread cannot keep the
            # process alive and block the relaunch.
            call_with_timeout(self.cam.stop, 5)
            call_with_timeout(self.cam.close, 5)
        if self.stalled:
            sys.stdout.flush(); sys.stderr.flush()
            os._exit(EXIT_STALLED)
        return self.segments


def finalize_h264_segment(seg_dir, man, h264_path, args, locked):
    """Everything that happens to a CLOSED segment: mux, thumbnail, the
    segment record and the shared manifest. Runs on a worker thread so the
    next segment records while this one is described.

    Measured 2026-09-10 23:38 on nereus002: done inline, the mux of a
    300 MB H.264 (+faststart = two passes over the SD bus) plus the
    thumbnail took ~70 s per segment, during which nothing was recorded --
    a 23% hole in every 5-minute segment.
    """
    pxy = man["delivered"]["proxy"]
    try:
        pxy["bytes"] = os.path.getsize(h264_path)
    except OSError:
        pxy["bytes"] = 0
    el = man.get("elapsed_s") or 0
    if not pxy.get("fps") and pxy.get("frames") and el:
        pxy["fps"] = round(pxy["frames"] / el, 2)
    pxy["MB_s"] = round(pxy["bytes"] / el / 1e6, 2) if el else None
    if not pxy.get("frames"):
        man["problems"].append("IMX segment has NO frames")
    if pxy["bytes"]:
        mp4_path = h264_path.rsplit(".", 1)[0] + ".mp4"
        ok, why = mux_h264_to_mp4(h264_path, mp4_path, pxy.get("fps") or args.fps)
        if ok:
            pxy["mp4"] = os.path.basename(mp4_path)
            pxy["mp4_bytes"] = os.path.getsize(mp4_path)
            th_path = os.path.join(seg_dir, "IMX_thumb.jpg")
            if thumb_from_mp4(mp4_path, th_path):
                pxy["thumb"] = os.path.basename(th_path)
            else:
                man["problems"].append("IMX thumbnail not written")
        else:
            man["problems"].append("proxy not muxed to mp4: %s" % why)
    else:
        man["problems"].append("proxy segment is empty")
    try:
        _write_json(os.path.join(seg_dir, "imx_segment.json"), man, indent=2)
        merge_session_manifest(seg_dir, man, locked, args)
    except OSError as exc:
        man["problems"].append("session manifest not written: %s" % exc)
        print("WARNING: %s" % man["problems"][-1], file=sys.stderr, flush=True)
    if hasattr(os, "sync"):
        os.sync()
    print("seg %04d: %.1fs  IMX %s fr %.2f MB/s  %s"
          % (man["segment"], el, pxy.get("frames"), pxy.get("MB_s") or 0,
             man["problems"] or "ok"), flush=True)
    return man


def thumb_from_mp4(mp4_path, thumb_path, at_s=1.0):
    """One decoded frame as JPEG. ~1 s of one core; bounded at 30 s."""
    if not shutil.which("ffmpeg"):
        return False
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.1f" % at_s,
             "-i", mp4_path, "-frames:v", "1", "-q:v", "4", thumb_path],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0


def mux_h264_to_mp4(h264_path, mp4_path, fps):
    """Wrap the raw H.264 in MP4 so it will actually PLAY.

    The proxy is written as an Annex-B elementary stream, which neither an
    iPad nor a browser will open -- "H.264" is not the same as "playable".
    This is a CONTAINER change only (-c copy): no re-encode, so it costs
    seconds per 5-minute segment and changes not one pixel of the video.

    Returns (ok, detail). A failure is reported and never fatal: the .h264 is
    still on the card and can be muxed later on the boat or at home.
    """
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not installed -- proxy left as raw .h264"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "h264", "-framerate", "%.3f" % max(fps, 1.0),
             "-i", h264_path, "-c", "copy",
             "-movflags", "+faststart", mp4_path],
            capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    if r.returncode != 0:
        return False, (r.stderr or "").strip()[:200]
    if not os.path.exists(mp4_path) or os.path.getsize(mp4_path) == 0:
        return False, "ffmpeg exited 0 but produced no mp4"
    return True, None


def merge_session_manifest(seg_dir, man, locked, args):
    """Fold this segment's IMX cameras into the SHARED session manifest.

    The board recorder writes the same manifest.json in the same directory,
    and either process may finish first. So this is a read-modify-write that
    keeps every camera it does not own -- exactly the mirror of Session.save()
    on the board side. A plain overwrite would silently drop whichever camera
    finished first, leaving its file on disk and absent from the page.
    """
    path = os.path.join(seg_dir, "manifest.json")
    try:
        with open(path) as f:
            existing = json.load(f)
    except (OSError, ValueError):
        existing = None
    if not isinstance(existing, dict):
        existing = {}

    sci = man["delivered"].get("science") or {}
    pxy = man["delivered"].get("proxy") or {}
    fps = sci.get("fps") or args.fps
    mine = []
    if sci.get("bytes"):
        mine.append({
            "label": "IMX", "mjpeg": os.path.basename(sci["path"]),
            "thumb": sci.get("thumb"),
            "capture_fps": fps, "delivered_fps": fps,
            "frames": sci.get("frames"), "bytes": sci.get("bytes"),
            "mb_per_s": sci.get("MB_s"),
            "w": args.science_size[0], "h": args.science_size[1],
            "kind": "science (JPEG q%d)" % args.jpeg_q,
        })
    if pxy.get("mp4") and getattr(args, "science", "jpeg") == "none":
        # No science stream: the H.264 of the main stream IS the IMX record.
        pfps = pxy.get("fps") or args.fps
        mine.append({
            "label": "IMX", "mp4": pxy["mp4"], "thumb": pxy.get("thumb"),
            "capture_fps": pfps, "delivered_fps": pfps,
            "frames": pxy.get("frames"),
            "bytes": pxy.get("mp4_bytes") or pxy.get("bytes"),
            "mb_per_s": pxy.get("MB_s"),
            "w": args.science_size[0], "h": args.science_size[1],
            "kind": "H.264 %d kbps (hardware), plays as-is" % (args.proxy_bitrate // 1000),
        })
    elif pxy.get("mp4"):
        mine.append({
            "label": "IMX_proxy", "mp4": pxy["mp4"], "capture_fps": fps,
            "bytes": pxy.get("mp4_bytes") or pxy.get("bytes"),
            "mb_per_s": pxy.get("MB_s"),
            "w": args.proxy_size[0], "h": args.proxy_size[1],
            "kind": "iPad proxy (H.264, plays as-is)",
        })

    ours = {c["label"] for c in mine}
    keep = [c for c in existing.get("cameras", [])
            if c.get("label") not in ours]
    name = os.path.basename(seg_dir.rstrip("/"))
    out = dict(existing)
    out.setdefault("name", name)
    out.setdefault("session", name)
    out.setdefault("created_iso", man["started_utc"])
    out["cameras"] = keep + mine
    out["white_balance"] = locked
    out["dive"] = {
        "recipe": args.recipe, "segment": man["segment"],
        "segment_s": args.segment_s, "wb_mode": args.wb,
        "focus_mode": args.focus, "lens_position": args.lens_position,
        "started_utc": man["started_utc"], "ended_utc": man["ended_utc"],
        "elapsed_s": man["elapsed_s"], "problems": man["problems"],
    }
    out.setdefault("settings", {}).update({
        "science": getattr(args, "science", "jpeg"),
        "science_size": args.science_size, "jpeg_q": args.jpeg_q,
        "proxy_size": args.proxy_size, "proxy_bitrate": args.proxy_bitrate,
        "fps": args.fps,
    })
    # fsync before the rename: a hard reset on 2026-09-10 left a segment's
    # manifest.json at zero bytes with the plain tmp+rename this replaced.
    _write_json(path, out, indent=1)
    return out


def _gains_match(a, b, tol=1e-3):
    """Colour gains equal within tolerance; shape mismatch is never a match."""
    try:
        if len(a) != len(b):
            return False
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
    except (TypeError, ValueError):
        return False


def _count_soi(path):
    n, tail = 0, b""
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                buf = tail + chunk
                n += buf.count(b"\xff\xd8")
                tail = buf[-1:]
    except OSError:
        return 0
    return n


def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=os.path.expanduser("~/recordings"),
                   help="recordings root; each segment becomes its own "
                        "timestamped session directory inside it")
    p.add_argument("--session-prefix", default=None,
                   help="session dirs are <prefix>_s0000, _s0001 ... The "
                        "launcher passes the SAME prefix to the board "
                        "recorder so a dive is one event with all cameras.")
    p.add_argument("--recipe", default="imx-dive-default")
    p.add_argument("--science", choices=("jpeg", "none"), default="jpeg",
                   help="jpeg = software JPEG science stream + H.264 proxy of "
                        "the lores stream (2 files); none = ONE hardware H.264 "
                        "of the main stream at --science-size, no JPEG "
                        "(about 1 W less on a Zero 2 W, Nick 2026-09-10)")
    p.add_argument("--science-size", type=int, nargs=2, default=[1280, 800])
    p.add_argument("--jpeg-q", type=int, default=90,
                   help="Nick 2026-09-09: q90 at 1280x800 is sufficient")
    p.add_argument("--jpeg-threads", type=int, default=4)
    p.add_argument("--proxy-size", type=int, nargs=2, default=[640, 400])
    p.add_argument("--proxy-bitrate", type=int, default=4000000)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--segment-s", type=float, default=DEFAULT_SEGMENT_S)
    p.add_argument("--converge-s", type=float, default=DEFAULT_CONVERGE_S)
    p.add_argument("--wb", choices=("lock", "auto"), default="lock",
                   help="lock = freeze WB once per dive against the reference "
                        "card (the depth-colour dataset); auto = leave AWB "
                        "tracking (ordinary recording, NOT reference data)")
    p.add_argument("--focus", choices=("manual", "auto"), default="manual")
    p.add_argument("--lens-position", type=float, default=1.82,
                   help="dioptres (1/m); 1.82 ~ 55 cm -- bm_cam_legacy "
                        "bmcam000's profile value, in-air")
    p.add_argument("--exposure-value", type=float, default=-0.5,
                   help="negative biases exposure down to protect highlights")
    p.add_argument("--buffers", type=int, default=4)
    p.add_argument("--max-seconds", type=float, default=0.0)
    p.add_argument("--max-segments", type=int, default=0)
    p.add_argument("--first-segment", type=int, default=0,
                   help="segment index to start at (the launcher passes the "
                        "next index when relaunching after a stall)")
    p.add_argument("--stall-s", type=float, default=20.0,
                   help="no science frame for this long = the frontend is "
                        "dead: close the segment, record the problem, exit %d"
                        % EXIT_STALLED)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    if not a.session_prefix:
        a.session_prefix = "dive_%s" % time.strftime("%Y%m%dT%H%M%SZ",
                                                     time.gmtime())
    rec = DiveRecorder(a)
    segs = rec.run()
    bad = [s for s in segs if s["problems"]]
    print(json.dumps({"root": a.root, "session_prefix": a.session_prefix,
                      "segments": len(segs),
                      "with_problems": len(bad)}, indent=2))
    return 1 if bad or not segs else 0


if __name__ == "__main__":
    sys.exit(main())
