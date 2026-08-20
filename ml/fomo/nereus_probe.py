# S8 B2 acceptance probe: is OUR two-colour model on the NPU?
# One mpremote run; prints sha256 (deploy verify), shapes, and measured
# per-inference ms. The NUMBER is the acceptance: FOMO-class models run
# 1.66/2.75 ms (96 px) on these boards; a CPU fallback is 10-100x that
# and fails the bite (TRACKER S8 B2 acceptance).
#
# Model path comes from _P if a driver prepends one (the AE3 loads from
# /flash, the N6 from /rom -- the B1 deployment asymmetry).
import csi, gc, time, ml, sys, binascii
try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

try:
    _P
except NameError:
    _P = "/flash/nereus_two_ball.tflite"

print("fw:", sys.version)
h = hashlib.sha256()
n = 0
f = open(_P, "rb")
while True:
    b = f.read(4096)
    if not b:
        break
    h.update(b)
    n += len(b)
f.close()
print("model %s  %d bytes  sha256 %s"
      % (_P, n, binascii.hexlify(h.digest()).decode()))

csi0 = csi.CSI()
csi0.reset()
csi0.pixformat(csi.RGB565)
csi0.framesize(csi.VGA)
time.sleep_ms(400)
img = csi0.snapshot()
gc.collect()
m = ml.Model(_P)
print("in", m.input_shape, "out", m.output_shape)
m.predict([img])                     # warm-up
t0 = time.ticks_us()
for _ in range(20):
    m.predict([img])
us = time.ticks_diff(time.ticks_us(), t0) / 20.0
print("inference %.2f ms  (%.1f/s)" % (us / 1000.0, 1e6 / us))
print("DONE")
