# hil_probe5_n6.py -- workaround ladder for the snapshot+stage1-predict
# hard-fault. Sections safe->risky; the dying section names itself.
import time
import csi, image, ml
m = ml.Model("/rom/stage1_v2_256_int8.tflite")
csi0 = csi.CSI(); csi0.reset(); csi0.pixformat(csi.RGB565); csi0.framesize(csi.VGA)
time.sleep_ms(400)
print("#P5 api", [n for n in dir(csi0) if "frame" in n or "buf" in n])
img = csi0.snapshot()
print("#P5 S1 static-VGA predict x30")
for i in range(30):
    m.predict([img])
print("#P5 S1 ok")
print("#P5 S2 QVGA snapshot+predict x30")
csi0.framesize(csi.QVGA)
time.sleep_ms(300)
for i in range(30):
    im2 = csi0.snapshot()
    m.predict([im2])
print("#P5 S2 ok")
print("#P5 S3 VGA fb-pin snapshot+predict x30")
csi0.framesize(csi.VGA)
time.sleep_ms(300)
try:
    csi0.framebuffers(1)
    print("#P5 S3 framebuffers(1) set")
except Exception as e:
    print("#P5 S3 no framebuffers API: %r" % e)
for i in range(30):
    im3 = csi0.snapshot()
    m.predict([im3])
print("#P5 S3 ok")
print("#P5 done")
