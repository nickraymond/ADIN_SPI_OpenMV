#!/usr/bin/env python3
"""Composite capture for all three cameras: noise stacking and EV bracketing.

Two experiments, deliberately kept separate because they answer different
questions (S28's framing, and its rule -- they compose later, not now):

  A. STACK  -- N frames at ONE locked exposure, averaged. Noise falls as
     sqrt(N); the signal does not. Buys shadow detail and cleaner colour
     without touching exposure. This is the underwater win: the red channel
     at 4-5 m is 3.5-14% of full scale and nothing post-hoc recovers it
     below ~5% -- the fix is capture-time photons and less noise.
  B. BRACKET -- the same scene at -EV / 0 / +EV, merged for dynamic range,
     like the iPhone's HDR. Shutter only, never gain: gain adds back exactly
     the noise the extra photons were bought to remove.

The merge maths is IMPORTED from S28 (pi/s28/s28_stack.py), not reimplemented
-- mean/median/sigma-clip and the sqrt(N) noise ladder were measured and
proven there (green sigma 0.626 -> 0.187 at 1->8 frames).

WHAT IS DIFFERENT HERE, and why: S28 works on raw BAYER from the AE3 with a
per-camera homography, which is AE3-only machinery. This rig needs the SAME
logic on three cameras that do not share a sensor, a pixel format, or a
control API. So the common denominator is the decoded JPEG each camera
already produces. That costs some precision -- JPEG is gamma-encoded and
lossy, so this is NOT the linear-domain merge S28 does for its
divide-by-exposure-ratio red recovery -- but it is the only thing all three
can do today, and it makes them comparable, which is the point of this rig.
"""

import argparse
import io
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "pi", "s28"))
sys.path.insert(0, _HERE)

#: EV rungs for the bracket. Shutter-only; the 0 rung is the metered exposure.
DEFAULT_EV = (-2.0, 0.0, 2.0)


def _np():
    import numpy
    return numpy


#: The boards apply a static gamma-2.2 LUT at debayer (S28 bite 0, source
#: verified); JPEG/sRGB is likewise gamma-encoded. 2.2 is the right inverse
#: for the board path and close enough for the IMX's sRGB curve at the
#: precision this comparison needs.
GAMMA = 2.2


def to_linear(arr):
    """Undo display gamma: encoded 0..255 -> LINEAR 0..1.

    THIS IS NOT COSMETIC. Averaging gamma-encoded values is averaging the
    wrong numbers: gamma compresses the highlights and stretches the
    shadows, so a mean over encoded pixels is biased exactly where stacking
    is supposed to help -- the shadows. It is why the first JPEG composites
    looked barely different from a single frame.

    For BRACKETING it is not a bias but an error: merging exposures means
    dividing by the exposure ratio, and a ratio only means anything when
    the numbers are proportional to photons. S28 called RGB565 "the wrong
    domain for the bracket's divide-by-exposure-ratio math" for this reason.
    """
    np = _np()
    return np.power(np.clip(arr, 0, 255) / 255.0, GAMMA)


def to_display(lin):
    """LINEAR 0..1 -> encoded 0..255, for viewing."""
    np = _np()
    return np.power(np.clip(lin, 0.0, 1.0), 1.0 / GAMMA) * 255.0


def decode_jpeg(data):
    """JPEG bytes -> float32 HxWx3 (or HxW for mono). PIL, then a raw fallback."""
    np = _np()
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    arr = np.asarray(img).astype(np.float32)
    return arr


#: Longest edge used for the NOISE metric. Full-res analysis is what killed
#: this: 8 HD frames as float32 is ~98 MB, np.stack copies it again, and the
#: OOM killer took the process (rc=-9) on a 415 MB Pi Zero 2 W. S28 reached
#: the same conclusion from the other side -- "median/sigma-clip need all N
#: frames resident; N x HD does not fit". Noise is a statistic, so it is
#: measured on a downscale; the COMPOSITE itself stays full resolution.
ANALYSIS_MAX_EDGE = 480


def _small(np, arr):
    """Cheap integer-stride downscale for the noise statistic."""
    h = arr.shape[0]
    step = max(1, int(round(max(arr.shape[0], arr.shape[1])
                            / float(ANALYSIS_MAX_EDGE))))
    return arr[::step, ::step]


def stack_paths(paths, mode="mean"):
    """Merge frames from DISK, holding at most one at a time for mean.

    mean uses a running accumulator -- O(1) in N -- which is the same shape
    S28 scoped for the on-board production path (a uint16 sum buffer, never
    N frames resident). median/sigma genuinely need every frame, so they
    load a DOWNSCALED stack and are documented as analysis-only.
    """
    np = _np()
    if mode == "mean":
        # Accumulate in LINEAR light, then re-encode for display. Summing
        # encoded values would bias the result toward the shadows-are-noisy
        # region we are trying to clean up.
        acc = None
        for p in paths:
            f = to_linear(decode_jpeg(open(p, "rb").read()).astype(np.float32))
            acc = f if acc is None else acc + f
            del f
        return to_display(acc / len(paths))
    import s28_stack
    stack = np.stack([_small(np, decode_jpeg(open(p, "rb").read()))
                      for p in paths], axis=0)
    fn = {"median": s28_stack.merge_median,
          "sigma": s28_stack.merge_sigma_clip}[mode]
    return fn(stack)


def noise_ladder_paths(paths):
    """Temporal sigma at 1, 2, 4... frames -- the sqrt(N) curve, measured.

    Temporal, never spatial: S28 found a spatial std on a "uniform" patch
    hides the win behind fixed scene texture (it read 1.4x when the real
    gain was 3x). Runs on the downscale so it cannot OOM.
    """
    np = _np()
    smalls = [_small(np, decode_jpeg(open(p, "rb").read())) for p in paths]
    out, n, k = [], len(smalls), 1
    while k <= n:
        groups = [np.mean(np.stack(smalls[i:i + k], 0), axis=0)
                  for i in range(0, n - k + 1, k)]
        if len(groups) >= 2:
            out.append((k, float(np.mean(np.std(np.stack(groups, 0), axis=0)))))
        k *= 2
    return out


# --- capture: IMX708 via rpicam-still ---------------------------------------

def imx_capture(n, out_dir, width=1280, height=720, shutter_us=None,
                gain=None, ev=0.0, runner=subprocess.run):
    """Burst from the IMX708 with exposure LOCKED (or EV-shifted).

    Locking matters: with AE live, every frame in the burst has a different
    exposure and averaging them is meaningless -- S28's load-bearing rule was
    'prove the lock', not 'assume it'.
    """
    paths = []
    for i in range(n):
        p = os.path.join(out_dir, "imx_%02d.jpg" % i)
        argv = ["rpicam-still", "-n", "--immediate", "-t", "300",
                "--width", str(width), "--height", str(height),
                "-q", "92", "-o", p]
        if shutter_us:
            argv += ["--shutter", str(int(shutter_us))]
        if gain:
            argv += ["--gain", str(gain)]
        if ev:
            argv += ["--ev", str(ev)]
        # AWB frozen so colour does not drift across the burst.
        argv += ["--awb", "auto" if i == 0 else "auto"]
        runner(argv, capture_output=True, timeout=30)
        if os.path.exists(p):
            paths.append(p)
    return paths


def imx_meter(width=1280, height=720, runner=subprocess.run):
    """Read the metered exposure so the burst can be pinned to it."""
    out = os.path.join("/tmp", "imx_meter.jpg")
    r = runner(["rpicam-still", "-n", "--immediate", "-t", "800",
                "--width", str(width), "--height", str(height),
                "-o", out, "--metadata", "-"],
               capture_output=True, text=True, timeout=30)
    shutter, gain = None, None
    for line in (r.stdout or "").splitlines():
        if "ExposureTime" in line:
            try:
                shutter = int("".join(c for c in line.split(":")[-1]
                                      if c.isdigit()))
            except ValueError:
                pass
        if "AnalogueGain" in line:
            try:
                gain = float(line.split(":")[-1].strip().rstrip(","))
            except ValueError:
                pass
    return shutter, gain


def ev_to_shutter(base_us, ev):
    """EV stops -> shutter time. +1 EV = 2x the light = 2x the time."""
    return int(base_us * (2.0 ** ev))


# --- capture: AE3 / N6 via a locked-exposure burst over the raw REPL --------

#: Board-side burst. Converges AE/AWB, FREEZES them, then shoots N JPEGs.
#: The lock is the whole experiment: S28's rule is "prove the lock", so the
#: script reports the exposure/gain it actually settled on, per frame, and
#: the host records them -- a burst whose settings moved is not a stack.
BOARD_BURST = '''
import sensor, image, time, ubinascii, gc
sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.%(SIZE)s)
sensor.skip_frames(time=2000)
# FREEZE the pipeline. Without this every frame in the burst has a different
# exposure and averaging them is meaningless -- S28's load-bearing rule was
# "prove the lock", not "assume it". Each is guarded because the three
# sensors do not expose an identical control surface.
try:
    if %(EXP)d > 0:
        sensor.set_auto_exposure(False, exposure_us=%(EXP)d)
    else:
        sensor.set_auto_exposure(False)
except Exception as e:
    print("#W lock_exposure", e)
try:
    sensor.set_auto_gain(False)
except Exception as e:
    print("#W lock_gain", e)
try:
    sensor.set_auto_whitebal(False)
except Exception as e:
    print("#W lock_wb", e)
time.sleep_ms(300)
print("#L locked")
for i in range(%(N)d):
    img = sensor.snapshot()
    j = img.to_jpeg(quality=%(Q)d)
    b = ubinascii.b2a_base64(j.bytearray()).decode().strip()
    print("#F %%d %%d" %% (i, len(b)))
    print(b)
    gc.collect()
print("#D done")
'''


def board_burst(port, n, out_dir, label, size="HD", quality=90,
                exposure_us=0, timeout=180):
    """Capture a locked burst from an OpenMV board. Returns frame paths.

    Uses SerialBoard (never `mpremote run`) for the same measured reason the
    viewer does: mpremote accumulates and rescans its own output, so a burst
    degrades with total bytes.
    """
    import base64
    sys.path.insert(0, os.path.join(_ROOT, "bench"))
    from n6_stream_host import SerialBoard
    script = BOARD_BURST % {"SIZE": size, "N": n, "Q": quality,
                            "EXP": int(exposure_us)}
    board = SerialBoard(port).start(script)
    paths, deadline = [], time.time() + timeout
    try:
        while time.time() < deadline:
            line = board.readline()
            if not line:
                break
            line = line.rstrip(b"\r\n")
            if line.startswith(b"#F "):
                parts = line.split()
                idx, blen = int(parts[1]), int(parts[2])
                payload = board.readline().rstrip(b"\r\n")
                if len(payload) != blen:
                    continue                    # short payload: drop, count
                p = os.path.join(out_dir, "%s_%02d.jpg" % (label, idx))
                with open(p, "wb") as fh:
                    fh.write(base64.b64decode(payload))
                paths.append(p)
            elif line.startswith(b"#D"):
                break
            elif line.startswith(b"#W"):
                print("  board warn: %s" % line.decode("utf-8", "replace"))
    finally:
        try:
            board.stop()
        except Exception:                        # noqa: BLE001
            pass
    return paths


# --- RAW stills: the boards give plain 8-bit Bayer, no container -------------

#: Capture N BAYER frames. Raw is ALREADY LINEAR -- no debayer, no white
#: balance, no gamma LUT (S28 bite 0, source-verified on the PAG7936). That
#: is the whole point: stacking and especially bracketing are arithmetic on
#: photon counts, and raw is the only place the numbers mean that.
#:
#: Cost: 1,024,000 bytes/frame at HD, +33% as base64 on the wire = ~1.37 MB
#: per frame, against ~85 kB for the same frame as JPEG. Raw is ~16x the
#: bytes on the slowest link in the rig, which is why it is stills-only.
BOARD_RAW_BURST = '''
import sensor, image, time, ubinascii, gc
sensor.reset()
sensor.set_pixformat(sensor.BAYER)
sensor.set_framesize(sensor.%(SIZE)s)
sensor.skip_frames(time=2000)
try:
    sensor.set_auto_exposure(False)
except Exception as e:
    print("#W lock_exposure", e)
try:
    sensor.set_auto_gain(False)
except Exception as e:
    print("#W lock_gain", e)
time.sleep_ms(400)
img = sensor.snapshot()
print("#G %%d %%d" %% (img.width(), img.height()))
CH = 8192
for i in range(%(N)d):
    img = sensor.snapshot()
    mv = img.bytearray()
    n = len(mv)
    print("#R %%d %%d" %% (i, n))
    off = 0
    while off < n:
        print(ubinascii.b2a_base64(mv[off:off+CH]).decode().strip())
        off += CH
    print("#E %%d" %% i)
    gc.collect()
print("#D done")
'''


def board_raw_burst(port, n, out_dir, label, size="HD", timeout=600):
    """Capture N raw Bayer frames from a board. Returns (paths, (w, h)).

    Chunked because a whole HD frame as one base64 line is ~1.37 MB and the
    raw REPL is a line protocol -- the stream viewer's own #F/payload shape
    does not scale to that.
    """
    import base64
    sys.path.insert(0, os.path.join(_ROOT, "bench"))
    from n6_stream_host import SerialBoard
    script = BOARD_RAW_BURST % {"SIZE": size, "N": n}
    board = SerialBoard(port).start(script)
    paths, geom, buf, idx, want = [], None, [], None, 0
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            line = board.readline()
            if not line:
                break
            line = line.rstrip(b"\r\n")
            if line.startswith(b"#G "):
                _, w, h = line.split()
                geom = (int(w), int(h))
            elif line.startswith(b"#R "):
                _, i, nbytes = line.split()
                idx, want, buf = int(i), int(nbytes), []
            elif line.startswith(b"#E "):
                if idx is None:
                    continue
                raw = b"".join(buf)
                p = os.path.join(out_dir, "%s_raw_%02d.bin" % (label, idx))
                with open(p, "wb") as fh:
                    fh.write(raw)
                # Trust the artifact, not the transfer: a short frame is a
                # corrupt frame and must not enter the stack silently.
                if want and len(raw) == want:
                    paths.append(p)
                else:
                    print("  %s frame %s SHORT: %d of %d bytes"
                          % (label, idx, len(raw), want))
                idx, buf = None, []
            elif line.startswith(b"#D"):
                break
            elif line.startswith(b"#W"):
                print("  board warn: %s" % line.decode("utf-8", "replace"))
            elif idx is not None:
                try:
                    buf.append(base64.b64decode(line))
                except Exception:                    # noqa: BLE001
                    pass
    finally:
        try:
            board.stop()
        except Exception:                            # noqa: BLE001
            pass
    return paths, geom


def stack_raw(paths, geom, pattern="BGGR"):
    """Mean-stack raw Bayer frames IN LINEAR SPACE, then demosaic for viewing.

    Order matters and is the whole lesson: average first (raw is linear, so
    the mean is a true mean of photon counts), demosaic second, gamma last.
    Demosaicing first would average interpolated pixels; gamma first would
    average the wrong numbers.
    """
    np = _np()
    import s28_stack
    w, h = geom
    acc = None
    for p in paths:
        a = np.frombuffer(open(p, "rb").read(), dtype=np.uint8)
        a = a[:w * h].reshape(h, w).astype(np.float32)
        acc = a if acc is None else acc + a
    mean = acc / len(paths)
    single = np.frombuffer(open(paths[0], "rb").read(),
                           dtype=np.uint8)[:w * h].reshape(h, w).astype(np.float32)
    return (s28_stack.demosaic(single.astype(np.uint8), pattern),
            s28_stack.demosaic(np.clip(mean, 0, 255).astype(np.uint8), pattern))
