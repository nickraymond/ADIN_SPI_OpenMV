# S31 clip grab, attempt 4. Accumulate every access unit in RAM FIRST, then
# write once. encode() hands back a memoryview into the encoder's single
# reused output buffer, so doing slow filesystem I/O (here: a VFS mounted
# over the serial link) between encode() calls risks the buffer moving or
# being refilled underneath the write. Copy with bytes(au) and keep all I/O
# outside the encode loop.
import gc, time, csi, codec

NH = 30
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
jpegs = []
for _ in range(NJ):
    jpegs.append(bytes(c.snapshot().compress(quality=90).bytearray()))
with open("/remote/mjpeg_q90.mjpeg", "wb") as f:
    for j in jpegs:
        f.write(j)
print("mjpeg_q90.mjpeg %d frames %d bytes" % (NJ, sum(len(j) for j in jpegs)))
jpegs = None
gc.collect()

for bps, name in ((8000000, "h264_08mbps"), (16000000, "h264_16mbps"),
                  (32000000, "h264_32mbps")):
    gc.collect()
    e = codec.H264Encoder(W, H, fps=30, bitrate=bps, keyframe_interval=30)
    parts = [bytes(e.sps_pps())]
    try:
        for _ in range(NH):
            au = e.encode(c.snapshot(), timestamp_us=time.ticks_us())
            parts.append(bytes(au))          # copy out of the encoder's buffer
    finally:
        e.deinit()
    n = 0
    with open("/remote/%s.h264" % name, "wb") as f:
        for p in parts:
            f.write(p)
            n += len(p)
    print("%s.h264 %d frames %d bytes" % (name, NH, n))
    parts = None
    gc.collect()

print("done")
