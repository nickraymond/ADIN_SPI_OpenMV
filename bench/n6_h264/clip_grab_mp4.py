# S31 clip grab via mp4.py -- PR #3247's OWN supported route.
#
# The raw Annex-B attempt produced a structurally perfect byte stream whose
# IDR would not decode. mp4.py muxes to AVCC with SPS/PPS out-of-band in the
# avcC box (it pulls them from the encoder), which is the path the PR author
# validated with ffprobe and a strict -c copy remux. Try that before blaming
# the encoder.
#
# Still NO csi.framerate(): both boards carry the PAG7936 and set_framerate()
# wedges the AE3 (SPEC, S28 bite 3). The loop free-runs; mp4.py is handed real
# microsecond timestamps so the container carries true frame times either way.
import gc, time, csi, codec, mp4

NH = 45   # 1.5 s at 30 fps
NJ = 6

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
print("mjpeg_q90.mjpeg %d frames %d bytes" % (NJ, n))
gc.collect()

for bps, name in ((8000000, "h264_08mbps"), (16000000, "h264_16mbps"),
                  (32000000, "h264_32mbps")):
    gc.collect()
    e = codec.H264Encoder(W, H, fps=30, bitrate=bps, keyframe_interval=30)
    try:
        # mp4.py defaults to buffer_size=262144, but an HD IDR here is
        # ~293 KB, so the default raises "access unit larger than
        # buffer_size". Worth knowing before anyone records HD with it.
        with mp4.Mp4("/remote/%s.mp4" % name, W, H, fps=30, encoder=e,
                     buffer_size=2 * 1024 * 1024) as m:
            for _ in range(NH):
                img = c.snapshot()
                ts = time.ticks_us()
                au = e.encode(img, timestamp_us=ts)
                m.write(au, timestamp_us=ts)
    finally:
        e.deinit()
    print("%s.mp4 written" % name)
    gc.collect()

print("done")
