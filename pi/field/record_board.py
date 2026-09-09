# record_board.py -- runs ON an OpenMV board (N6 or AE3), driven by recorder.py.
#
# S32's frame pump. The job is to get JPEG frames off the board as fast as the
# link allows, and NOTHING else: no muxing, no storage, no per-frame handshake.
#
# WHY THIS EXISTS, measured (S31 + S32 bite 0):
#   The S8/S29 stream path does a per-frame request/response in Python on both
#   ends and delivers 3.2 fps at HD q90, against an encoder that does 30.5 and a
#   USB link that does 19.5 MB/s. The wire and the encoder were never the
#   problem; the protocol was. So this script writes length-prefixed frames
#   CONTINUOUSLY and never waits for the host.
#
# WIRE FORMAT -- deliberately raw binary, not base64:
#   banner   "#REC-START {json}\n"
#   frame    MAGIC(4) | seq(4, LE u32) | length(4, LE u32) | jpeg[length]
#   trailer  "#REC-END {json}\n"
#
#   The existing stream pays base64's 33% tax because `mpremote run` streams
#   stdout back through the RAW repl, which terminates on byte 0x04 -- and JPEG
#   payloads contain 0x04 freely. This script is pushed through PASTE MODE in
#   the FRIENDLY repl instead (see recorder.py), where no 0x04 framing exists,
#   so raw binary is safe and the 33% is not spent. At HD q90 that tax alone is
#   ~4.8 MB/s of a 9-19 MB/s link.
#
#   MAGIC is checked TOGETHER with a JPEG SOI test on the host, so a resync
#   after any corruption cannot silently accept a bogus frame length.
#
# CONSTRAINTS THAT ARE NOT NEGOTIABLE HERE:
#   * No csi.framerate() / set_framerate(). It wedges the AE3 (SPEC, S28 bite 3)
#     and whether the N6 shares that fault is UNMEASURED. Pacing is done by
#     sleeping between frames, which cannot wedge anything.
#   * Bounded allocations. The header buffer is allocated ONCE and packed into
#     per frame; `to_jpeg` returns an object supporting the buffer protocol, so
#     the payload is written with no intermediate copy.
#   * The board never buffers a clip. The N6 has no SD card and /flash has 3 MB
#     against a 5 s HD q90 clip of ~70 MB (S31). Frames leave immediately or
#     they are lost, and a lost frame is COUNTED, never hidden.

import csi
import gc
import sys
import time
import struct

try:
    _CFG  # injected by the host as a literal dict
except NameError:
    _CFG = {}

FRAMESIZE = _CFG.get("framesize", "VGA")
QUALITY = _CFG.get("quality", 90)
PIXFMT = _CFG.get("pixfmt", "RGB565")
DURATION_MS = int(_CFG.get("duration_s", 5) * 1000)
#: 0 = free-run (report the true ceiling). Otherwise the minimum ms per frame.
PACE_MS = _CFG.get("pace_ms", 0)
#: Hard cap so a wedged host can never make the board stream forever.
MAX_FRAMES = _CFG.get("max_frames", 100000)

MAGIC = b"\xab\xcd\x12\x34"


def framesize_const(name):
    """Resolve a framesize NAME to its csi constant, failing loudly."""
    try:
        return getattr(csi, name)
    except AttributeError:
        raise ValueError("framesize %r not exported by csi on this firmware" % name)


def main():
    csi0 = csi.CSI()
    csi0.reset()
    csi0.pixformat(csi.GRAYSCALE if PIXFMT == "GRAYSCALE" else csi.RGB565)
    csi0.framesize(framesize_const(FRAMESIZE))

    # Let AE/AWB converge. S31 measured that freezing exposure before it settles
    # returns BLACK frames that are still valid JPEGs -- an artifact that looks
    # like success. Warming up is cheaper than explaining a black clip.
    for _ in range(8):
        csi0.snapshot()

    gc.collect()
    img = csi0.snapshot()
    w, h = img.width(), img.height()

    try:
        board = __import__("omv").board_type()
    except Exception:
        board = "?"

    print('#REC-START {"board":"%s","w":%d,"h":%d,"framesize":"%s","quality":%d,'
          '"pixfmt":"%s","duration_s":%d,"pace_ms":%d,"fw":"%s","heap":%d}'
          % (board, w, h, FRAMESIZE, QUALITY, PIXFMT, DURATION_MS // 1000,
             PACE_MS, sys.version, gc.mem_free()))

    write = sys.stdout.buffer.write
    hdr = bytearray(12)
    hdr[0:4] = MAGIC
    pack_into = struct.pack_into

    seq = 0
    total = 0
    enc_us = 0
    t_start = time.ticks_ms()
    t_last = t_start

    while True:
        if time.ticks_diff(time.ticks_ms(), t_start) >= DURATION_MS:
            break
        if seq >= MAX_FRAMES:
            break

        # Pace BEFORE the encode timer, so a paced run reports the same
        # per-frame encode cost as a free-running one.
        if PACE_MS:
            dt = time.ticks_diff(time.ticks_ms(), t_last)
            if dt < PACE_MS:
                time.sleep_ms(PACE_MS - dt)
        t_last = time.ticks_ms()

        img = csi0.snapshot()
        e0 = time.ticks_us()
        jpg = img.to_jpeg(quality=QUALITY)
        enc_us += time.ticks_diff(time.ticks_us(), e0)

        n = len(jpg)
        pack_into("<II", hdr, 4, seq, n)
        write(hdr)
        write(jpg)

        seq += 1
        total += n
        del jpg
        if (seq & 0x1F) == 0:
            gc.collect()

    wall_ms = time.ticks_diff(time.ticks_ms(), t_start)
    print('\n#REC-END {"frames":%d,"bytes":%d,"wall_ms":%d,"enc_ms_per_frame":%d,'
          '"fps":%d,"heap":%d}'
          % (seq, total, wall_ms,
             (enc_us // 1000 // seq) if seq else 0,
             (seq * 1000 // wall_ms) if wall_ms else 0,
             gc.mem_free()))


main()
