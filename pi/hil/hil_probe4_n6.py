# hil_probe4_n6.py -- WHICH loop kills stock N6 fw with a resident model?
# A: predict on the snapshot img directly (B2's proven pattern)
# B: canvas letterbox + predict(canvas)   (hil whole-mode, crash suspect)
# C: img.copy(roi) + predict(copy)        (tile route candidate)
import gc, time
import csi, image, ml
m = ml.Model("/rom/stage1_v2_256_int8.tflite")
csi0 = csi.CSI(); csi0.reset(); csi0.pixformat(csi.RGB565); csi0.framesize(csi.VGA)
for _ in range(3): csi0.snapshot()
canvas = image.Image(256, 256, image.RGB565)

print("#P4 A start (predict on snapshot, 60x)")
for i in range(60):
    img = csi0.snapshot()
    out = m.predict([img])
    gc.collect()
print("#P4 A ok")

print("#P4 B start (canvas letterbox predict, 60x)")
for i in range(60):
    img = csi0.snapshot()
    canvas.draw_rectangle((0,0,256,256), color=(114,114,114), fill=True)
    canvas.draw_image(img, 0, 0, x_scale=0.4, y_scale=0.4)
    out = m.predict([canvas])
    gc.collect()
    if i % 20 == 0: print("#P4 B i=%d" % i)
print("#P4 B ok")

print("#P4 C start (copy-roi predict, 60x)")
for i in range(60):
    img = csi0.snapshot()
    t = img.copy(roi=(192, 72, 256, 256))
    out = m.predict([t])
    gc.collect()
    if i % 20 == 0: print("#P4 C i=%d" % i)
print("#P4 C ok")
print("#P4 done")
