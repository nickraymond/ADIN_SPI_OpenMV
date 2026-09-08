# n6_h264_probe.py -- runs ON the OpenMV N6, driven by n6_h264_run.py.
#
# Measures, on one scene, what MJPEG and hardware H.264 actually cost per
# frame. Answers S31's rung 1/3 legs: like-for-like bytes at matched quality,
# encode throughput, and the largest frame size the encoder will accept.
#
# Design constraints that are NOT negotiable here:
#
#   * NO csi.framerate(). Both boards carry the PAG7936, and set_framerate()
#     wedges the AE3 (SPEC sec Open questions, S28 bite 3: omv_csi_abort +
#     a full mode-register rewrite that does not reliably restart). Whether
#     the N6 shares that fault is UNMEASURED, and finding out on a board we
#     would have to physically replug is not worth it. The loop free-runs and
#     reports the fps it achieved.
#   * A ratio is only valid on TRUE-MOTION frames. Consecutive sensor frames
#     ~33 ms apart share far more than frames spaced by a USB link, so a
#     ratio taken from a streamed clip flatters H.264. Every cell records
#     achieved fps and a true_motion flag; the host refuses to compute a
#     ratio from cells whose loop could not keep up.
#   * Runs with or without the codec module, so the SAME probe measures the
#     MJPEG baseline on the CURRENT firmware before flashing and both codecs
#     after. That before/after on one scene is a far better control than
#     comparing against a table measured on another day.
#
# Output: one `RESULT <json>` line per cell on stdout, plus `INFO`/`ERROR`.
# Nothing else is printed, so the host can parse without guessing.

import gc
import json
import time

import csi

try:
    import codec

    HAVE_CODEC = True
except ImportError:
    codec = None
    HAVE_CODEC = False

# Frame sizes by name, so the host passes strings and the board resolves them.
SIZES = {
    "QVGA": csi.QVGA,
    "VGA": csi.VGA,
    "HD": csi.HD,
}

WARMUP_FRAMES = 8  # let AE/AWB settle and drop the first stale snapshot(s)


def emit(kind, payload):
    print("%s %s" % (kind, json.dumps(payload)))


def _configure(csi0, size):
    csi0.pixformat(csi.RGB565)
    csi0.framesize(SIZES[size])
    for _ in range(WARMUP_FRAMES):
        csi0.snapshot()
    return csi0.width(), csi0.height()


def cell_mjpeg(csi0, size, quality, frames):
    """Capture-and-encode loop, MJPEG. Returns a result dict."""
    w, h = _configure(csi0, size)
    gc.collect()
    total = 0
    enc_us = 0
    t0 = time.ticks_us()
    for _ in range(frames):
        img = csi0.snapshot()
        e0 = time.ticks_us()
        jpg = img.compress(quality=quality)
        enc_us += time.ticks_diff(time.ticks_us(), e0)
        total += jpg.size()
    wall_us = time.ticks_diff(time.ticks_us(), t0)
    return _result("mjpeg", size, w, h, quality, frames, total, enc_us, wall_us)


def cell_h264(csi0, size, quality, frames, bitrate, keyframe_interval):
    """Capture-and-encode loop, hardware H.264. Returns a result dict."""
    w, h = _configure(csi0, size)
    gc.collect()
    free_before = gc.mem_free()

    kwargs = {"fps": 30, "keyframe_interval": keyframe_interval}
    if quality is None:
        kwargs["bitrate"] = bitrate
    else:
        kwargs["quality"] = quality
    enc = codec.H264Encoder(w, h, **kwargs)

    # SPS/PPS is part of the deliverable's byte cost -- count it once.
    total = len(enc.sps_pps())
    enc_us = 0
    keyframes = 0
    t0 = time.ticks_us()
    try:
        for _ in range(frames):
            img = csi0.snapshot()
            ts = time.ticks_us()
            e0 = ts
            au = enc.encode(img, timestamp_us=ts)
            enc_us += time.ticks_diff(time.ticks_us(), e0)
            total += len(au)
            if enc.keyframe():
                keyframes += 1
        wall_us = time.ticks_diff(time.ticks_us(), t0)
    finally:
        enc.deinit()

    res = _result("h264", size, w, h, quality, frames, total, enc_us, wall_us)
    res["bitrate_target"] = None if quality is not None else bitrate
    res["keyframe_interval"] = keyframe_interval
    res["keyframes"] = keyframes
    res["heap_free_before"] = free_before
    return res


def _result(kind, size, w, h, quality, frames, total, enc_us, wall_us):
    achieved = frames * 1e6 / wall_us if wall_us else 0.0
    return {
        "codec": kind,
        "size": size,
        "w": w,
        "h": h,
        "quality": quality,
        "frames": frames,
        "bytes": total,
        "bytes_per_frame": total / frames,
        "encode_ms_per_frame": enc_us / frames / 1000.0,
        "loop_ms_per_frame": wall_us / frames / 1000.0,
        "achieved_fps": achieved,
        # The sensor free-runs; a loop that cannot keep up is sampling a
        # SLOWER scene, and its inter-frame correlation is not the one a real
        # 30 fps clip would have. The host will not take a ratio from these.
        "true_motion": achieved >= 25.0,
        "heap_free_after": gc.mem_free(),
    }


def probe_max_size(csi0):
    """Which frame sizes will the encoder actually accept?

    Functional, not register-level: constructing the encoder is the question
    we care about, and it cannot fault the board the way a guessed peripheral
    address can. Settles 'maxEncodedWidth' the safe way.
    """
    out = {}
    for name in ("QVGA", "VGA", "HD"):
        try:
            w, h = _configure(csi0, name)
        except Exception as e:  # sensor cannot do this size
            out[name] = "sensor: %s" % e
            continue
        if not HAVE_CODEC:
            out[name] = "no codec module"
            continue
        try:
            enc = codec.H264Encoder(w, h, fps=30, bitrate=1000000)
            enc.deinit()
            out[name] = "ok %dx%d" % (w, h)
        except Exception as e:
            out[name] = "encoder: %s" % e
        gc.collect()
    return out


def run(plan):
    import os

    csi0 = csi.CSI(stream=False)
    csi0.reset()

    gc.collect()
    emit(
        "INFO",
        {
            "uname": str(os.uname()),
            "codec_module": HAVE_CODEC,
            "heap_free": gc.mem_free(),
            "sensor": str(csi0),
        },
    )

    emit("INFO", {"max_size_probe": probe_max_size(csi0)})

    frames = plan.get("frames", 90)
    for size in plan.get("sizes", ["VGA"]):
        for q in plan.get("mjpeg_quality", [30]):
            try:
                emit("RESULT", cell_mjpeg(csi0, size, q, frames))
            except Exception as e:
                emit("ERROR", {"codec": "mjpeg", "size": size, "quality": q, "err": str(e)})
            gc.collect()

        if not HAVE_CODEC:
            continue

        for q in plan.get("h264_quality", [30]):
            try:
                emit(
                    "RESULT",
                    cell_h264(csi0, size, q, frames, None, plan.get("keyframe_interval", 30)),
                )
            except Exception as e:
                emit("ERROR", {"codec": "h264", "size": size, "quality": q, "err": str(e)})
            gc.collect()

        for br in plan.get("h264_bitrate", []):
            try:
                emit(
                    "RESULT",
                    cell_h264(csi0, size, None, frames, br, plan.get("keyframe_interval", 30)),
                )
            except Exception as e:
                emit("ERROR", {"codec": "h264", "size": size, "bitrate": br, "err": str(e)})
            gc.collect()

    emit("INFO", {"done": True})
