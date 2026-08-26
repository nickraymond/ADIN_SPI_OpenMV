# probe_e7_inplace_jpeg.py -- E7 nibble-1 probe (ONE mpremote run, AE3).
#
# Question (hardware fact, verify-not-assume): after the last tile
# inference, can the HD frame be JPEG-encoded IN PLACE in the frame
# buffer (to_jpeg(copy=False)) with the tiny model resident, avoiding
# the heap allocations that killed the board script (E7 symptom 1)?
# Also measures the chunked-b64 walk and proves the NEXT capture +
# inference still work after the in-place encode. Finally reproduces
# the kill (to_jpeg(copy=True)) under a catch, to convict the mechanism.
#
# Prints one VERDICT line per question. Bounded; no loops; ends at REPL.
import binascii
import gc
import time

import csi
import image
import ml

csi0 = csi.CSI()
csi0.reset()
csi0.pixformat(csi.RGB565)
csi0.framesize(csi.HD)
for _ in range(3):
    csi0.snapshot()

img = csi0.snapshot()
print("PROBE frame %dx%d" % (img.width(), img.height()))
gc.collect()
print("PROBE heap_start", gc.mem_free())

m = ml.Model("/rom/stage1_tiny_blurft_256_int8.tflite")
canvas = image.Image(256, 256, image.RGB565)
canvas.draw_image(img, 0, 0, roi=(0, 0, 256, 256))
t0 = time.ticks_us()
out = m.predict([canvas])
inf_us = time.ticks_diff(time.ticks_us(), t0)
gc.collect()
print("PROBE tiny loaded, tile inference %d us, heap %d"
      % (inf_us, gc.mem_free()))

# ---- Q1: in-place encode with the model resident --------------------
try:
    gc.collect()
    h0 = gc.mem_free()
    t0 = time.ticks_us()
    j = img.to_jpeg(quality=50, copy=False)
    enc_us = time.ticks_diff(time.ticks_us(), t0)
    gc.collect()
    h1 = gc.mem_free()
    print("VERDICT inplace_encode OK us=%d jpeg_B=%d heap_delta=%d"
          % (enc_us, j.size(), h0 - h1))
except Exception as e:
    print("VERDICT inplace_encode FAIL %r" % e)
    raise SystemExit

# ---- Q2: chunked b64 walk (3072 B = b64-aligned), no big alloc ------
try:
    buf = j.bytearray()
    mv = memoryview(buf)
    gc.collect()
    h0 = gc.mem_free()
    t0 = time.ticks_us()
    total = 0
    for i in range(0, len(buf), 3072):
        total += len(binascii.b2a_base64(mv[i:i + 3072]))
    b64_us = time.ticks_diff(time.ticks_us(), t0)
    gc.collect()
    print("VERDICT chunked_b64 OK us=%d out_B=%d heap_delta=%d"
          % (b64_us, total, h0 - gc.mem_free()))
except Exception as e:
    print("VERDICT chunked_b64 FAIL %r" % e)

# ---- Q3: frame buffer usable after in-place encode ------------------
try:
    img2 = csi0.snapshot()
    canvas.draw_image(img2, 0, 0, roi=(0, 0, 256, 256))
    t0 = time.ticks_us()
    m.predict([canvas])
    print("VERDICT post_encode_capture OK next_frame=%dx%d inf_us=%d"
          % (img2.width(), img2.height(),
         time.ticks_diff(time.ticks_us(), t0)))
except Exception as e:
    print("VERDICT post_encode_capture FAIL %r" % e)

# ---- Q4: convict the old path (copy=True) under a catch -------------
try:
    gc.collect()
    jc = img2.to_jpeg(quality=50, copy=True)
    print("VERDICT copytrue SURVIVED jpeg_B=%d heap=%d"
          % (jc.size(), gc.mem_free()))
except MemoryError as e:
    print("VERDICT copytrue MEMORYERROR (the E7 kill, reproduced) %r" % e)
except Exception as e:
    print("VERDICT copytrue OTHER %r" % e)
print("PROBE done, heap", gc.mem_free())
