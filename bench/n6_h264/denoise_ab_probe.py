# S31: does the VC8000's in-loop Wiener denoiser recover inter-frame coding
# at high quality? HD, quality-matched against MJPEG q90 by intra size.
import gc, json, time, csi, codec

N = 120
KI = 30
QUAL = [70, 75, 80, 85]
DEN = [0, 5, 10, 20, 30]

c = csi.CSI(stream=False)
c.reset()
c.pixformat(csi.RGB565)
c.framesize(csi.HD)
for _ in range(10):
    c.snapshot()
W, H = c.width(), c.height()
print("INFO %s" % json.dumps({"w": W, "h": H, "frames": N}))

# MJPEG reference on the same scene, same run.
for q in (90, 100):
    gc.collect()
    tot = 0
    t0 = time.ticks_us()
    for _ in range(N):
        tot += c.snapshot().compress(quality=q).size()
    wall = time.ticks_diff(time.ticks_us(), t0)
    print("RESULT %s" % json.dumps({
        "codec": "mjpeg", "quality": q, "denoise": None,
        "bytes_per_frame": tot / N, "fps": N * 1e6 / wall}))

for d in DEN:
    for q in QUAL:
        gc.collect()
        try:
            e = codec.H264Encoder(W, H, fps=30, quality=q,
                                  keyframe_interval=KI, denoise=d)
        except Exception as ex:
            print("ERROR %s" % json.dumps({"denoise": d, "quality": q, "err": str(ex)}))
            break
        intra = []
        inter = []
        t0 = time.ticks_us()
        try:
            for _ in range(N):
                img = c.snapshot()
                au = e.encode(img, timestamp_us=time.ticks_us())
                (intra if e.keyframe() else inter).append(len(au))
            wall = time.ticks_diff(time.ticks_us(), t0)
        finally:
            e.deinit()
        tot = sum(intra) + sum(inter)
        print("RESULT %s" % json.dumps({
            "codec": "h264", "quality": q, "denoise": d,
            "bytes_per_frame": tot / N,
            "intra_mean": sum(intra) / len(intra) if intra else 0,
            "inter_mean": sum(inter) / len(inter) if inter else 0,
            "pct_of_intra": (100.0 * (sum(inter) / len(inter)) / (sum(intra) / len(intra)))
                            if intra and inter else 0,
            "fps": N * 1e6 / wall}))
print("INFO {\"done\": true}")
