#!/usr/bin/env python3
"""Turn a recorded .mjpeg into something a browser will actually play.

THE PROBLEM, measured (S32 bite 0): the boards produce concatenated MJPEG.
Remuxing it is instant and lossless -- `ffmpeg -f mjpeg -c:v copy` runs at
448 fps -- but the result is still an MJPEG elementary stream in a container,
`mjpeg / yuvj444p`, and **Chrome and Safari will not play it**. Something has
to actually re-encode to H.264.

THE ENCODER CHOICE IS PER HOST, and the two rigs differ in the opposite
direction from the obvious guess:

  nereus000, Pi 5 (BCM2712)      -- NO hardware H.264 encoder at all. Verified
      twice: /dev/video* carries only rpi-hevc-dec (a DECODER) and the PiSP
      back-end, and ffmpeg's h264_v4l2m2m wrapper has no M2M device to bind.
      But 4x Cortex-A76 at 2.4 GHz make software x264 faster than real time:
      ultrafast 68.7 fps, superfast 54.5, veryfast 41.4 on HD 1280x800.
      A 5 s clip transcodes in 2.2-3.6 s.

  nereus002, Pi Zero 2 W (BCM2710A1) -- DOES have a hardware H.264 encoder,
      and its 4x A53 at 1 GHz would be far too slow for software x264 at HD.
      There the hardware path is not a nicety, it is the only option.
      **Its speed is UNMEASURED** -- that rig was offline when this was
      written, so nothing here claims a number for it.

So the encoder is DETECTED, never assumed, and which one ran is recorded in
the manifest next to the clip. A clip whose provenance is unknown is a clip
whose numbers cannot be compared against another rig's.
"""

import glob
import json
import os
import subprocess
import time


def _v4l2_encoder_present():
    """True if a real V4L2 M2M H.264 ENCODER device exists on this host.

    Checked by device name, not by asking ffmpeg whether it has the wrapper --
    ffmpeg lists h264_v4l2m2m on every Linux build whether or not the kernel
    exposes anything for it to drive. That distinction is exactly what made the
    Pi 5 look like it had a hardware encoder.
    """
    for path in glob.glob("/sys/class/video4linux/*/name"):
        try:
            with open(path) as f:
                name = f.read().strip().lower()
        except OSError:
            continue
        # The Pi's hardware codec block enumerates as bcm2835-codec-encode.
        if "codec-encode" in name or ("h264" in name and "enc" in name):
            return "/dev/" + os.path.basename(os.path.dirname(path))
    return None


def pick_encoder(prefer=None):
    """Return (name, ffmpeg_args) for this host. Detected, not assumed."""
    if prefer == "x264":
        return "libx264", ["-c:v", "libx264", "-preset", "ultrafast",
                           "-crf", "23", "-pix_fmt", "yuv420p"]
    dev = _v4l2_encoder_present()
    if dev and prefer != "software":
        return "h264_v4l2m2m", ["-c:v", "h264_v4l2m2m", "-b:v", "12M",
                                "-pix_fmt", "yuv420p"]
    return "libx264", ["-c:v", "libx264", "-preset", "ultrafast",
                       "-crf", "23", "-pix_fmt", "yuv420p"]


def transcode(mjpeg_path, mp4_path, fps, prefer=None, timeout=900):
    """MJPEG -> browser-playable H.264 mp4. Returns a result dict, never raises.

    Verified as an ARTIFACT, not an exit code: ffmpeg can return 0 having
    written a zero-byte or undecodable file, so the output is probed and the
    probe's answers are what decide `ok`.
    """
    name, enc_args = pick_encoder(prefer)
    # Leave one core for the page and the next recording. Transcoding a 20 min
    # HD clip took 186 s with every core pegged and carried nereus000 to 77.9 C,
    # against a Pi 5 soft limit of 80 -- and the field rig has far less thermal
    # headroom than that. One spare core costs a little wall time and keeps the
    # rig answering while a long clip converts.
    threads = max(1, (os.cpu_count() or 2) - 1)
    cmd = (["ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "mjpeg", "-framerate", "%g" % fps, "-i", mjpeg_path]
           + enc_args + ["-threads", str(threads),
                         "-movflags", "+faststart", mp4_path])
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        rc, err = p.returncode, (p.stderr or "").strip()[-400:]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        rc, err = -1, str(e)
    wall = time.monotonic() - t0

    res = {"encoder": name, "cmd": " ".join(cmd), "rc": rc, "wall_s": round(wall, 2),
           "stderr": err, "ok": False, "bytes": 0, "probe": {}}
    if os.path.exists(mp4_path):
        res["bytes"] = os.path.getsize(mp4_path)
    if rc == 0 and res["bytes"] > 0:
        res["probe"] = probe(mp4_path)
        # A file only counts as playable if it really decodes as H.264 with a
        # browser-safe pixel format and a non-zero duration.
        pr = res["probe"]
        # yuvj420p is yuv420p with full-range (JPEG) colour. ffmpeg emits it
        # when the source is MJPEG, and browsers decode it fine -- rejecting it
        # failed a file that actually played. Both spellings are accepted; a
        # 4:2:2 or 4:4:4 output is NOT, because that genuinely will not play.
        res["ok"] = (pr.get("codec_name") == "h264"
                     and str(pr.get("pix_fmt", "")) in ("yuv420p", "yuvj420p")
                     and float(pr.get("duration") or 0) > 0)
        if not res["ok"]:
            res["stderr"] = ("output is not browser-playable: %s" % json.dumps(pr))
    return res


def probe(path):
    """ffprobe the produced file. Returns {} rather than lying on failure."""
    cmd = ["ffprobe", "-v", "error",
           "-show_entries", "stream=codec_name,width,height,pix_fmt,nb_frames",
           "-show_entries", "format=duration,size",
           "-of", "json", path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            return {}
        d = json.loads(p.stdout)
    except (subprocess.SubprocessError, ValueError, OSError):
        return {}
    out = {}
    if d.get("streams"):
        out.update(d["streams"][0])
    if d.get("format"):
        out["duration"] = d["format"].get("duration")
        out["size"] = d["format"].get("size")
    return out


if __name__ == "__main__":
    import sys
    src, dst = sys.argv[1], sys.argv[2]
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
    print(json.dumps(transcode(src, dst, fps), indent=1))
