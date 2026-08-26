# probe_e11_lens_corr.py -- E11 measure-only probe (ONE mpremote run, N6).
#
# Question (cost fact for Nick's layer-cake call): what does
# img.lens_corr() cost per frame at HD on the N6 with the tiny model
# resident? lens_corr would fix BOTH layers (the measurement AND the
# model's distorted input -- edge urchins are genuinely harder through
# a barrel lens) but charges every frame and changes the model's input
# mid-experiment. E11 ships the measurement fix only; this number
# prices the on-board option so a later call is made on a measurement,
# not a guess.
#
# Bounded; fixed counts only; ends at the REPL.
import gc
import time

import csi
import image  # noqa: F401  (yes: importing registers the image methods)
import ml

csi0 = csi.CSI()
csi0.reset()
csi0.pixformat(csi.RGB565)
csi0.framesize(csi.HD)
for _ in range(3):
    csi0.snapshot()

m = ml.Model("/rom/stage1_tiny_blurft_256_int8.tflite")
gc.collect()
print("PROBE HD ready, tiny resident, heap %d" % gc.mem_free())

for strength in (1.2, 1.8):
    us = []
    for _i in range(5):
        img = csi0.snapshot()
        t0 = time.ticks_us()
        img.lens_corr(strength)
        us.append(time.ticks_diff(time.ticks_us(), t0))
    us.sort()
    print("VERDICT lens_corr strength=%.1f at HD: median %d us "
          "(min %d max %d, 5 frames)"
          % (strength, us[2], us[0], us[4]))

gc.collect()
print("PROBE heap after %d" % gc.mem_free())
print("PROBE done")
