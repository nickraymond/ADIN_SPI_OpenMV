# hil_probe3_n6.py -- discriminate the ~8-frame hard-fault: tobytes+b64
# WITHOUT the 43 KB wire write. 40 frames, prints only lengths.
import binascii, gc, time
import csi, image, ml
m = ml.Model("/rom/stage1_v2_256_int8.tflite")
csi0 = csi.CSI(); csi0.reset(); csi0.pixformat(csi.RGB565); csi0.framesize(csi.VGA)
for _ in range(3): csi0.snapshot()
canvas = image.Image(256, 256, image.RGB565)
print("#P3 start")
for i in range(40):
    img = csi0.snapshot()
    canvas.draw_rectangle((0,0,256,256), color=(114,114,114), fill=True)
    canvas.draw_image(img, 0, 0, x_scale=0.4, y_scale=0.4)
    out = m.predict([canvas])
    bb = b"".join(o.tobytes() for o in out)
    e = binascii.b2a_base64(bb)
    print("#P3 f%d len=%d" % (i, len(e)))
    gc.collect()
print("#P3 done")
