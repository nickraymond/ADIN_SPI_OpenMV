# ratio_probe.py -- runs ON the N6. Does H.264 clear the >=2x bar against MJPEG?
#
# S33 threshold 4: ">= 2x smaller than MJPEG on a MOVING scene". S31 measured
# 1.5x quality-matched and bigger ratios only under a bitrate cap on a static
# scene, and Nick's instruction is explicit: do not quote 4x at him.
#
# TWO THINGS THIS DOES THAT THE EXISTING PROBE DOES NOT:
#
# 1. IT COUNTS UNIQUE FRAMES. csi.snapshot() hands back the SAME buffer when
#    the loop outruns the sensor -- measured on this board 2026-09-08: 200
#    iterations produced 118 unique frames while reporting 117.4 fps against a
#    true 69.2. A duplicated frame costs H.264 almost nothing to inter-code
#    (S31: an identical frame re-encoded is 43 bytes vs 257,704 for a live
#    static scene), so duplicates inflate the compression ratio -- the exact
#    number this threshold turns on. MJPEG is unaffected by them, so the bias
#    runs one way only, in H.264's favour.
#
# 2. BOTH CODECS SEE THE SAME FRAMES, in the same loop iteration. Comparing
#    two separate runs compares two different scenes.
#
# Quality matching, per the S31 method: nominal "q70" means nothing across
# codecs, so match on the INTRA frame -- an H.264 IDR is an intra-coded still
# at this resolution, the closest thing it has to a JPEG. The H.264 quality
# whose IDR is about the size of the JPEG is the quality-matched one.

import codec
import csi
import gc
import time


def sig(buf):
    """Cheap frame fingerprint. Sampled, because hashing a whole HD frame in
    MicroPython costs more than the encode being measured."""
    n = len(buf)
    step = max(1, n // 256)
    # An explicit loop, not buf[::step]: MicroPython raises
    # "only slices with step=1 (aka None) are supported".
    acc = 0
    i = 0
    while i < n:
        acc += buf[i]
        i += step
    return (n, acc)


def run(quality, frames, size, jpeg_q):
    csi0 = csi.CSI()
    csi0.reset()
    csi0.pixformat(csi.RGB565)
    csi0.framesize(size)
    # The csi module has no skip_frames(); burn frames by hand so AE/AWB have
    # converged before anything is measured. S31 shipped BLACK composites by
    # freezing exposure before convergence -- do not repeat it.
    t_settle = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t_settle) < 2500:
        csi0.snapshot()
    w, h = csi0.width(), csi0.height()

    gc.collect()
    enc = codec.H264Encoder(w, h, fps=30, quality=quality, keyframe_interval=30)
    h264 = len(enc.sps_pps())
    jpeg = 0
    intra = inter = n_intra = 0
    sigs = []
    t0 = time.ticks_us()
    try:
        for _ in range(frames):
            img = csi0.snapshot()
            # H.264 FIRST. img.to_jpeg() compresses the image IN PLACE, and
            # feeding the result to the encoder fails with "Expected an
            # uncompressed image" -- so the order here is load-bearing, not
            # stylistic. Both codecs still see the same captured frame.
            ts = time.ticks_us()
            au = enc.encode(img, timestamp_us=ts)
            n = len(au)
            j = img.to_jpeg(quality=jpeg_q).bytearray()
            jpeg += len(j)
            sigs.append(sig(j))                  # fingerprint the SOURCE frame
            h264 += n
            if enc.keyframe():
                intra += n
                n_intra += 1
            else:
                inter += n
    finally:
        enc.deinit()
    wall = time.ticks_diff(time.ticks_us(), t0) / 1e6

    uniq = len(set(sigs))
    return {
        "quality": quality, "w": w, "h": h, "frames": frames,
        "unique": uniq,
        "dup_pct": round(100.0 * (frames - uniq) / frames, 1),
        "loop_fps": round(frames / wall, 1),
        "true_fps": round(uniq / wall, 1),
        "jpeg_total": jpeg, "jpeg_per_frame": jpeg // frames,
        "h264_total": h264, "h264_per_frame": h264 // frames,
        "h264_intra_mean": intra // n_intra if n_intra else 0,
        "h264_inter_mean": inter // (frames - n_intra) if frames > n_intra else 0,
        "intra_frames": n_intra,
        "ratio": round(float(jpeg) / h264, 2) if h264 else 0.0,
    }


def main(qualities, frames=90, size=None, jpeg_q=70):
    size = size or csi.HD
    for q in qualities:
        r = run(q, frames, size, jpeg_q)
        print("RESULT " + repr(r))
