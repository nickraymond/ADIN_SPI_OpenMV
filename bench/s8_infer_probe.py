# s8_infer_probe.py -- S8 bite C: the inference-ONLY ceiling, per board.
#
#   mpremote connect <by-id> run bench/s8_infer_probe.py
#
# The streaming demo's fps includes JPEG encode + USB -- costs a deployed
# counter never pays. This measures what the application is actually limited
# by: capture + predict, and predict alone. Bounded output, so mpremote run
# is the right transport (the decay trap only bites continuous streams).
# AE3 CONTACT RULES APPLY: workbench idle, settle elapsed, ONE attempt.
import sys
import time

import csi
import ml

N = 50
PATHS = ("/flash/nereus_two_ball.tflite", "/rom/nereus_two_ball.tflite")

model = None
path = None
for path in PATHS:
    try:
        model = ml.Model(path)
        break
    except Exception:
        model = None
if model is None:
    print('#P {"error":"nereus_two_ball.tflite not found on /flash or /rom"}')
    raise SystemExit

csi0 = csi.CSI()
csi0.reset()
csi0.pixformat(csi.RGB565)
csi0.framesize(csi.VGA)
time.sleep_ms(400)
img = csi0.snapshot()

model.predict([img])                      # warm-up
t0 = time.ticks_us()
for _ in range(N):
    model.predict([img])
infer_us = time.ticks_diff(time.ticks_us(), t0) / N

t0 = time.ticks_us()
for _ in range(N):
    img = csi0.snapshot()
    model.predict([img])
loop_us = time.ticks_diff(time.ticks_us(), t0) / N

print('#P {"fw":"%s","model":"%s","infer_ms":%.2f,"infer_per_s":%.1f,'
      '"cap_plus_infer_ms":%.2f,"cap_plus_infer_per_s":%.1f}'
      % (sys.version.split(";")[1].strip() if ";" in sys.version
         else sys.version, path, infer_us / 1000.0, 1e6 / infer_us,
         loop_us / 1000.0, 1e6 / loop_us))
