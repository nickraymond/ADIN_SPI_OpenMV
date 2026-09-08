# S31 diagnostic: WHY are H.264 P-frames so expensive on a static scene?
import gc, json, time, csi, codec

Q = 75
N = 60
KI = 300   # max allowed; with N=60 that means ONE IDR, rest are P-frames

c = csi.CSI(stream=False)
c.reset()
c.pixformat(csi.RGB565)
c.framesize(csi.HD)
for _ in range(10):
    c.snapshot()
W, H = c.width(), c.height()
print("INFO %s" % json.dumps({"w": W, "h": H}))


def encode_seq(label, get_frame, n=N):
    gc.collect()
    e = codec.H264Encoder(W, H, fps=30, quality=Q, keyframe_interval=KI)
    intra = 0
    inter = []
    try:
        for i in range(n):
            img = get_frame()
            au = e.encode(img, timestamp_us=time.ticks_us())
            if e.keyframe():
                intra = len(au)
            else:
                inter.append(len(au))
    finally:
        e.deinit()
    mean = sum(inter) / len(inter) if inter else 0
    print("RESULT %s" % json.dumps({
        "case": label, "intra": intra, "inter_mean": mean,
        "inter_min": min(inter) if inter else 0,
        "inter_max": max(inter) if inter else 0,
        "inter_first3": inter[:3], "inter_last3": inter[-3:],
        "pct_of_intra": 100.0 * mean / intra if intra else 0,
    }))


# A -- live capture, ISP free-running (this is what the ladder measured)
encode_seq("A live, AE/AWB auto", c.snapshot)

# B -- live capture, exposure and white balance LOCKED
c.auto_exposure(False)
c.auto_whitebal(False)
for _ in range(5):
    c.snapshot()
encode_seq("B live, AE/AWB locked", c.snapshot)

# C -- ONE frame, re-encoded. Zero scene change, zero sensor noise.
#      This is the codec's theoretical floor. If P-frames are big HERE,
#      the fault is in how the encoder is driven, not in the scene.
frozen = c.snapshot().copy()
encode_seq("C same frame x%d" % N, lambda: frozen)

c.auto_exposure(True)
c.auto_whitebal(True)
print("INFO {\"done\": true}")
