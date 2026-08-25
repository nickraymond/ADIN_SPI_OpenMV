# hil_probe2_n6.py -- isolate the probe-1 crash (board reset after the b64
# step) and qualify the SAFE tile route. Every action prints its name BEFORE
# running, so the last line on the wire names the killer.
#
#   mpremote connect /dev/serial/by-id/<N6> run pi/hil/hil_probe2_n6.py
import gc
import time

import csi
import image
import ml

print("#P2 load-model")
m = ml.Model("/rom/stage1_v2_256_int8.tflite")
print("#P2 cam-init")
csi0 = csi.CSI()
csi0.reset()
csi0.pixformat(csi.RGB565)
csi0.framesize(csi.VGA)
for _ in range(3):
    csi0.snapshot()
img = csi0.snapshot()
print("#P2 gc")
gc.collect()
print("#P2 heap=%d" % gc.mem_free())

# route A: preallocated 256x256 canvas, draw_image from a source roi --
# ONE allocation for the whole run, reused per tile
print("#P2 canvas-alloc")
canvas = image.Image(256, 256, image.RGB565)
print("#P2 draw-roi")
t0 = time.ticks_us()
canvas.draw_image(img, 0, 0, roi=(192, 72, 256, 256))
print("#P2 draw-roi ok us=%d" % time.ticks_diff(time.ticks_us(), t0))
print("#P2 predict-tile")
t0 = time.ticks_us()
out = m.predict([canvas])
print("#P2 predict-tile ok us=%d shape0=%r" %
      (time.ticks_diff(time.ticks_us(), t0), out[0].shape))

# route B: letterbox whole frame into the same canvas (fill then scale-draw)
print("#P2 letterbox")
t0 = time.ticks_us()
canvas.draw_rectangle((0, 0, 256, 256), color=(114, 114, 114), fill=True)
canvas.draw_image(img, 0, 0, x_scale=0.4, y_scale=0.4)
print("#P2 letterbox ok us=%d" % time.ticks_diff(time.ticks_us(), t0))
print("#P2 predict-letterbox")
t0 = time.ticks_us()
out = m.predict([canvas])
print("#P2 predict-letterbox ok us=%d" % time.ticks_diff(time.ticks_us(), t0))

# jpeg for the calib/eyeball wire artifact
print("#P2 jpeg")
t0 = time.ticks_us()
jb = img.to_jpeg(quality=50, copy=True).bytearray()
print("#P2 jpeg ok us=%d bytes=%d" %
      (time.ticks_diff(time.ticks_us(), t0), len(jb)))
print("#P2 gc2")
gc.collect()
print("#P2 heap2=%d" % gc.mem_free())

# THE SUSPECT, last: img.copy(roi) allocates a new image per call
print("#P2 copy-roi (probe-1 suspect)")
t0 = time.ticks_us()
tile = img.copy(roi=(192, 72, 256, 256))
print("#P2 copy-roi ok us=%d" % time.ticks_diff(time.ticks_us(), t0))
print("#P2 done")
