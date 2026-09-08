# S31 clip grab, final. HD, MJPEG q90 vs H.264 at 8/16/32 Mbps.
#
# TIMESTAMPS ARE SYNTHETIC, at exactly 1/30 s. This is deliberate and it is
# the only way to get an honest 30 fps file size out of this board:
#
#   * mp4.py is pure-Python and cannot mux HD at 30 fps here -- measured
#     12-13 fps with the file writes removed entirely, so it is the muxer,
#     not the I/O.
#   * encode() rate control follows the timestamps it is given. Fed REAL
#     ticks at 12 fps it allocates ~2.5x the bits a 30 fps stream would get,
#     which inflates both the file AND the apparent picture quality.
#   * Feeding 33333 us steps makes the bit allocation exactly right for
#     30 fps. The frames themselves are still captured further apart in
#     time, which gives the encoder LESS inter-frame correlation than a true
#     30 fps capture -- so this is conservative, never flattering.
import gc, time, csi, codec, mp4

NH = 45
NJ = 6
STEP_US = 33333          # 1/30 s

c = csi.CSI(stream=False)
c.reset()
c.pixformat(csi.RGB565)
c.framesize(csi.HD)
for _ in range(12):
    c.snapshot()
W, H = c.width(), c.height()
print("frame %dx%d" % (W, H))

gc.collect()
n = 0
with open("/remote/mjpeg_q90.mjpeg", "wb") as f:
    for _ in range(NJ):
        j = c.snapshot().compress(quality=90)
        f.write(j.bytearray())
        n += j.size()
print("RESULT mjpeg_q90 %d frames %d bytes %d B/frame" % (NJ, n, n // NJ))
gc.collect()

for bps, name in ((8000000, "h264_08mbps"), (16000000, "h264_16mbps"),
                  (32000000, "h264_32mbps")):
    gc.collect()
    e = codec.H264Encoder(W, H, fps=30, bitrate=bps, keyframe_interval=30)
    n = 0
    try:
        with mp4.Mp4("/remote/%s.mp4" % name, W, H, fps=30, encoder=e,
                     buffer_size=2 * 1024 * 1024) as m:
            for i in range(NH):
                ts = i * STEP_US
                au = e.encode(c.snapshot(), timestamp_us=ts)
                n += len(au)
                m.write(au, timestamp_us=ts)
    finally:
        e.deinit()
    print("RESULT %s %d frames %d video bytes %d B/frame"
          % (name, NH, n, n // NH))
    gc.collect()

print("done")
