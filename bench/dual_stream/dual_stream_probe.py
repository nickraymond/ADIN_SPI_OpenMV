#!/usr/bin/env python3
"""Can a Pi Zero 2 W encode a SCIENCE stream and an iPad PROXY at the same time?

The question (Nick, 2026-09-09 spec): record a high-quality per-frame record
for post-hoc colour work AND an H.264 proxy the iPad can play natively, both
live off the one IMX708, so a dive is reviewable on the boat without a
transcode step.

WHY picamera2 AND NOT rpicam-vid: `rpicam-vid --codec` takes exactly ONE
codec, so the CLI structurally cannot emit two encodings of one camera.
picamera2 can attach a separate encoder to the `main` and `lores` streams of
a single configuration, which is the only route on this hardware.

WHY NOT BAYER RAW: derived from the sensor's own mode list, 1536x864 10-bit
packed at 30 fps is ~50 MB/s and YUV420 1280x800 is ~46 MB/s, against the
~10.4 MB/s this rig's card sustained at HD q90 in S32. Raw video is not a
candidate for dive-length capture; "science stream" here means MJPEG, which
is per-frame independent and so carries no inter-frame colour smearing.

Everything is MEASURED from the artifacts, never from an exit code (CLAUDE.md
rule 4): JPEG frames are counted by scanning for SOI markers, H.264 frames by
decoding the file, and a run that produced an unreadable file FAILS.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

SOI = b"\xff\xd8"


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def health():
    """Thermal / throttle state -- a Zero 2 W that throttles invalidates fps."""
    out = {"temp_c": None, "throttled": None, "load1": None}
    t = _read("/sys/class/thermal/thermal_zone0/temp")
    if t.isdigit():
        out["temp_c"] = round(int(t) / 1000.0, 1)
    try:
        r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0:
            out["throttled"] = r.stdout.strip().split("=")[-1]
    except (OSError, subprocess.SubprocessError):
        pass
    la = _read("/proc/loadavg").split()
    if la:
        out["load1"] = la[0]
    return out


def count_jpeg_frames(path):
    """Count SOI markers. An MJPEG file with 0 frames is a FAILED run."""
    n, tail = 0, b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            buf = tail + chunk
            n += buf.count(SOI)
            tail = buf[-1:]
    return n


def count_h264_frames(path):
    """Decode the H.264 and count what actually comes out.

    Uses ffprobe's *decoded* frame count, not a container header: a file that
    reports a duration but will not decode is exactly the plausible-but-wrong
    artifact this repo keeps paying for.
    """
    if not shutil.which("ffprobe"):
        return None, "ffprobe not installed -- H.264 NOT verified"
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of",
         "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        return None, "ffprobe failed: %s" % r.stderr.strip()[:200]
    txt = r.stdout.strip()
    if not txt.isdigit():
        return None, "ffprobe returned %r" % txt
    return int(txt), None


def run(args):
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder, JpegEncoder, MJPEGEncoder
    from picamera2.outputs import FileOutput

    want_main = args.mode in ("both", "science")
    want_lores = args.mode in ("both", "proxy")

    cam = Picamera2()
    cfg_kw = {
        "main": {"size": tuple(args.main_size), "format": "YUV420"},
        "controls": {"FrameDurationLimits": (int(1e6 / args.fps),
                                             int(1e6 / args.fps))},
        "buffer_count": args.buffers,
    }
    if not args.keep_raw:
        # picamera2 requests a Bayer stream by default. It is unusable for
        # dive-length video (~50 MB/s) and it costs buffers on a 415 MB board,
        # so drop it unless a run is explicitly studying it.
        cfg_kw["raw"] = None
    if want_lores:
        cfg_kw["lores"] = {"size": tuple(args.lores_size), "format": "YUV420"}
    cam.configure(cam.create_video_configuration(**cfg_kw))

    os.makedirs(args.out_dir, exist_ok=True)
    tag = "%s_%dx%d" % (args.mode, args.main_size[0], args.main_size[1])
    sci_path = os.path.join(args.out_dir, "science_%s.mjpeg" % tag)
    pxy_path = os.path.join(args.out_dir, "proxy_%s.h264" % tag)

    started = []
    if want_main:
        # HW MJPEG (bcm2835-codec) is RATE-controlled; SW JpegEncoder is
        # QUALITY-controlled. Colour science wants constant quality, but the
        # software path may not hold 30 fps on this board -- so both are
        # measurable and the run records which one produced the numbers.
        if args.science_encoder == "hw":
            sci_enc = MJPEGEncoder(bitrate=args.mjpeg_bitrate)
        else:
            sci_enc = JpegEncoder(q=args.jpeg_q, num_threads=args.jpeg_threads)
        cam.start_encoder(sci_enc, FileOutput(sci_path), name="main")
        started.append("main/%s" % type(sci_enc).__name__)
    if want_lores:
        cam.start_encoder(H264Encoder(bitrate=args.h264_bitrate),
                          FileOutput(pxy_path), name="lores")
        started.append("lores/H264")
    print("encoders started: %s" % ", ".join(started), flush=True)

    before = health()
    cam.start()
    t0 = time.time()
    time.sleep(args.seconds)
    elapsed = time.time() - t0
    during = health()
    cam.stop()
    cam.stop_encoder()
    cam.close()

    res = {"mode": args.mode, "seconds": round(elapsed, 2),
           "requested_fps": args.fps,
           "science_encoder": args.science_encoder,
           "mjpeg_bitrate": args.mjpeg_bitrate,
           "raw_stream": bool(args.keep_raw),
           "main_size": args.main_size, "lores_size": args.lores_size,
           "jpeg_q": args.jpeg_q, "h264_bitrate": args.h264_bitrate,
           "health_before": before, "health_during": during,
           "streams": {}, "problems": []}

    if want_main:
        size = os.path.getsize(sci_path)
        n = count_jpeg_frames(sci_path)
        res["streams"]["science_mjpeg"] = {
            "path": sci_path, "bytes": size, "frames": n,
            "fps": round(n / elapsed, 2) if elapsed else None,
            "MB_s": round(size / elapsed / 1e6, 2) if elapsed else None}
        if n == 0:
            res["problems"].append("science stream produced NO JPEG frames")
    if want_lores:
        size = os.path.getsize(pxy_path)
        n, err = count_h264_frames(pxy_path)
        res["streams"]["proxy_h264"] = {
            "path": pxy_path, "bytes": size, "frames": n,
            "fps": round(n / elapsed, 2) if (n and elapsed) else None,
            "MB_s": round(size / elapsed / 1e6, 2) if elapsed else None,
            "verify_error": err}
        if err:
            res["problems"].append("proxy NOT verified: %s" % err)
        elif not n:
            res["problems"].append("proxy decoded to 0 frames")
    return res


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=("both", "science", "proxy"), default="both",
                   help="both = the real question; science/proxy = the baselines it must be judged against")
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--main-size", type=int, nargs=2, default=[1280, 800])
    p.add_argument("--lores-size", type=int, nargs=2, default=[640, 400])
    p.add_argument("--science-encoder", choices=("hw", "sw"), default="hw",
                   help="hw = bcm2835 MJPEG (rate-controlled); sw = JpegEncoder (quality-controlled)")
    p.add_argument("--mjpeg-bitrate", type=int, default=80000000,
                   help="hw science stream; 80 Mbps ~ the 10.4 MB/s S32 measured at HD q90")
    p.add_argument("--jpeg-q", type=int, default=90)
    p.add_argument("--jpeg-threads", type=int, default=4)
    p.add_argument("--keep-raw", action="store_true",
                   help="also request the Bayer stream (costs buffers; not a video candidate)")
    p.add_argument("--h264-bitrate", type=int, default=4000000)
    p.add_argument("--buffers", type=int, default=4)
    p.add_argument("--out-dir", default="/home/pi/dual_stream_out")
    p.add_argument("--json", help="also write the result here")
    args = p.parse_args()

    try:
        res = run(args)
    except Exception as exc:                      # loud, and name the fix
        print("PROBE FAILED (%s): %s" % (type(exc).__name__, exc), file=sys.stderr)
        raise

    print(json.dumps(res, indent=2))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=2)
    if res["problems"]:
        print("\nPROBLEMS:", file=sys.stderr)
        for x in res["problems"]:
            print("  - %s" % x, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
