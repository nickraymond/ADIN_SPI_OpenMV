# hil_probe6_n6.py -- the workaround matrix. Each section bounded; the
# dying section names itself. Theory under test: VGA-sourced predict is
# the poison (bench's QVGA static predicts ran thousands of times).
import time
import csi, image, ml
m = ml.Model("/rom/stage1_v2_256_int8.tflite")
csi0 = csi.CSI(); csi0.reset(); csi0.pixformat(csi.RGB565)
canvas = image.Image(256, 256, image.RGB565)

print("#P6 S2 QVGA snapshot+predict x40")
csi0.framesize(csi.QVGA); time.sleep_ms(300)
for i in range(40):
    im = csi0.snapshot()
    m.predict([im])
    if i % 10 == 0: print("#P6 S2 i=%d" % i)
print("#P6 S2 ok")

print("#P6 S4 QVGA->canvas256 letterbox predict x40")
for i in range(40):
    im = csi0.snapshot()
    canvas.draw_rectangle((0,0,256,256), color=(114,114,114), fill=True)
    canvas.draw_image(im, 0, 0, x_scale=0.8, y_scale=0.8)  # 320x200*0.8=256x160
    m.predict([canvas])
    if i % 10 == 0: print("#P6 S4 i=%d" % i)
print("#P6 S4 ok")

print("#P6 S5 canvas predict + cell indexing x40")
for i in range(40):
    im = csi0.snapshot()
    canvas.draw_rectangle((0,0,256,256), color=(114,114,114), fill=True)
    canvas.draw_image(im, 0, 0, x_scale=0.8, y_scale=0.8)
    out = m.predict([canvas])
    n = 0
    for o in out:
        g = o[0]
        for y in range(len(g)):
            row = g[y]
            for x in range(len(row)):
                if float(row[x][4]) >= 0.10:
                    n += 1
    if i % 10 == 0: print("#P6 S5 i=%d cells=%d" % (i, n))
print("#P6 S5 ok")

print("#P6 S6 VGA capture, tile draw-roi, predict x40")
csi0.framesize(csi.VGA); time.sleep_ms(300)
for i in range(40):
    im = csi0.snapshot()
    canvas.draw_image(im, 0, 0, roi=(192, 72, 256, 256))
    m.predict([canvas])
    if i % 10 == 0: print("#P6 S6 i=%d" % i)
print("#P6 S6 ok")
print("#P6 done")
