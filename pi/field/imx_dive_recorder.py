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
import signal
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    import depth as depth_mod
except ImportError:
    depth_mod = None

#: Seconds of AWB/AE convergence before the lock is taken. The reference card
#: must be in frame for this whole window.
DEFAULT_CONVERGE_S = 8.0

#: Segment length. Nick: max 5 minutes, then close the file and open the next.
DEFAULT_SEGMENT_S = 300.0


def now_iso():
    """ISO-8601 UTC. Timestamps in files are always UTC on this rig."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _jsonable(v):
    """libcamera hands back tuples/enums; make them survive json.dump."""
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


class DiveRecorder:
    def __init__(self, args):
        self.args = args
        self.cam = None
        self.locked = {}
        self.segments = []
        self.stop = threading.Event()
        self.depth = None
        if depth_mod is not None:
            self.depth = depth_mod.open_sensor()

    # -- setup ------------------------------------------------------------
    def configure(self):
        from picamera2 import Picamera2
        a = self.args
        self.cam = Picamera2()
        cfg = self.cam.create_video_configuration(
            main={"size": tuple(a.science_size), "format": "YUV420"},
            lores={"size": tuple(a.proxy_size), "format": "YUV420"},
            controls={"FrameDurationLimits": (int(1e6 / a.fps),
                                              int(1e6 / a.fps))},
            buffer_count=a.buffers,
            raw=None,               # Bayer is ~50 MB/s; never a video candidate
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
            md = self.cam.capture_metadata()
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
            md = self.cam.capture_metadata()
        if self.stop.is_set() and not md:
            md = self.cam.capture_metadata()
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
        after = self.cam.capture_metadata()
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
    def _encoders(self, index):
        from picamera2.encoders import H264Encoder, JpegEncoder
        from picamera2.outputs import FileOutput
        a = self.args
        base = os.path.join(a.out_dir, "seg_%04d" % index)
        sci_path, pxy_path = base + "_science.mjpeg", base + "_proxy.h264"
        sci = JpegEncoder(q=a.jpeg_q, num_threads=a.jpeg_threads)
        pxy = H264Encoder(bitrate=a.proxy_bitrate)
        self.cam.start_encoder(sci, FileOutput(sci_path), name="main")
        self.cam.start_encoder(pxy, FileOutput(pxy_path), name="lores")
        return sci, pxy, sci_path, pxy_path

    def record_segment(self, index):
        a = self.args
        t_start = time.time()
        started_iso = now_iso()
        depth_start = dict(self.depth.read()) if self.depth else None
        # Re-read the LIVE gains at the top of every segment. Copying the
        # lock dict forward would make each manifest agree with itself and
        # prove nothing; this is the check that would actually catch AWB
        # having crept back on across a segment boundary (rule 1).
        gains_at_start = _jsonable(self.cam.capture_metadata().get("ColourGains"))
        sci, pxy, sci_path, pxy_path = self._encoders(index)

        end = t_start + a.segment_s
        while time.time() < end and not self.stop.is_set():
            time.sleep(0.25)

        # Rule 2: only the encoders stop. The camera keeps running, so the
        # WB lock and the sensor's state survive into the next segment.
        gains_at_end = _jsonable(self.cam.capture_metadata().get("ColourGains"))
        self.cam.stop_encoder([sci, pxy])
        elapsed = time.time() - t_start
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
        for label, path in (("science", sci_path), ("proxy", pxy_path)):
            size = os.path.getsize(path) if os.path.exists(path) else 0
            d = {"path": os.path.basename(path), "bytes": size,
                 "MB_s": round(size / elapsed / 1e6, 2) if elapsed else None}
            if label == "science":
                d["frames"] = _count_soi(path)
                d["fps"] = round(d["frames"] / elapsed, 2) if elapsed else None
                if not d["frames"]:
                    man["problems"].append("science segment has NO frames")
            man["delivered"][label] = d
        if not man["problems"] and man["delivered"]["proxy"]["bytes"] == 0:
            man["problems"].append("proxy segment is empty")

        # A drifting gain means the lock did not hold, which silently ruins the
        # colour dataset -- so it is a PROBLEM, not a footnote.
        locked_gains = (self.locked.get("observed_colour_gains")
                        if self.locked.get("mode") == "wb-locked" else None)
        for when, seen in (("start", gains_at_start), ("end", gains_at_end)):
            if locked_gains and seen and not _gains_match(locked_gains, seen):
                man["problems"].append(
                    "WB DRIFTED at segment %s: locked %s, observed %s"
                    % (when, locked_gains, seen))

        with open(os.path.join(a.out_dir, "seg_%04d.json" % index), "w") as f:
            json.dump(man, f, indent=2)
        os.sync() if hasattr(os, "sync") else None
        self.segments.append(man)
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

    def run(self):
        a = self.args
        self.install_signal_handlers()
        os.makedirs(a.out_dir, exist_ok=True)
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

        i = 0
        t0 = time.time()
        try:
            while not self.stop.is_set():
                if a.max_seconds and (time.time() - t0) >= a.max_seconds:
                    break
                if a.max_segments and i >= a.max_segments:
                    break
                man = self.record_segment(i)
                print("seg %04d: %.1fs  science %s fr %.2f MB/s  proxy %.2f MB/s  %s"
                      % (i, man["elapsed_s"],
                         man["delivered"]["science"].get("frames"),
                         man["delivered"]["science"].get("MB_s") or 0,
                         man["delivered"]["proxy"].get("MB_s") or 0,
                         man["problems"] or "ok"), flush=True)
                i += 1
        except KeyboardInterrupt:
            print("interrupted -- closing current segment", flush=True)
        finally:
            try:
                self.cam.stop()
                self.cam.close()
            except Exception:
                pass
        return self.segments


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
    p.add_argument("--out-dir", default=None,
                   help="default: ~/recordings/dive_<UTC timestamp>")
    p.add_argument("--recipe", default="imx-dive-default")
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
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    if not a.out_dir:
        a.out_dir = os.path.expanduser(
            "~/recordings/dive_%s" % time.strftime("%Y%m%dT%H%M%SZ",
                                                   time.gmtime()))
    rec = DiveRecorder(a)
    segs = rec.run()
    bad = [s for s in segs if s["problems"]]
    print(json.dumps({"out_dir": a.out_dir, "segments": len(segs),
                      "with_problems": len(bad)}, indent=2))
    return 1 if bad or not segs else 0


if __name__ == "__main__":
    sys.exit(main())
