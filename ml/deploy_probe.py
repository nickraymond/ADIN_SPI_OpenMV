# B1 acceptance probe: does OUR compiled model run like the vendor's?
# Same board, same session, same frame -- the A/B discipline this project
# learned the hard way today.
import csi, gc, time, ml, sys

ROM = "/rom/fomo_face_detection.tflite"
OURS = "/flash/fomo_ours.tflite"

print("fw:", sys.version)

# 1. Are the bytes identical? (chunked -- 64 KB will not fit comfortably)
a = open(ROM, "rb"); b = open(OURS, "rb")
same, n = True, 0
while True:
    x = a.read(1024); y = b.read(1024)
    if not x and not y:
        break
    if x != y:
        same = False
        break
    n += len(x)
a.close(); b.close()
print("BYTE-IDENTICAL:", same, "(compared %d bytes)" % n)

csi0 = csi.CSI(); csi0.reset(); csi0.pixformat(csi.RGB565)
csi0.framesize(csi.QVGA); time.sleep_ms(400)
img = csi0.snapshot()          # ONE frame, both models see it

for name, path in (("ROM ", ROM), ("OURS", OURS)):
    gc.collect()
    m = ml.Model(path)
    print("%s %-34s in=%s out=%s" % (name, path.split("/")[-1],
                                     m.input_shape, m.output_shape))
    m.predict([img])                                   # warm-up
    t0 = time.ticks_us()
    for _ in range(20):
        m.predict([img])
    us = time.ticks_diff(time.ticks_us(), t0) / 20.0
    print("%s inference %.2f ms  (%.1f/s)" % (name, us / 1000.0, 1e6 / us))
    del m
print("DONE")
