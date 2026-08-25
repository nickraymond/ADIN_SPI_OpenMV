# hil_probe_n6.py -- S8 bite E HIL: answer the board-side unknowns BEFORE
# the harness is written (N6 first: cheap attaches, no bite-R budget).
#
#   mpremote connect /dev/serial/by-id/<N6> run pi/hil/hil_probe_n6.py
#
# Answers, each printed as a #P line:
#   rom      what stage-1 models are on /rom, exact names + sizes
#   model    input/output shapes + dtypes as ml.Model reports them
#   predict  output container type, per-level types/shapes, DEQUANT OR NOT
#            (int8 raw would need scale/zp; floats mean firmware dequants)
#   tobytes  whether ulab arrays serialize via .tobytes() / bytes(buffer)
#   crop     img.copy(roi=...) works + cost (the tiled mode's per-tile step)
#   letter   256x256 canvas + aspect-preserving draw_image (whole-frame
#            letterbox route) works + cost
#   timing   snapshot / whole-frame predict / crop predict, us
import gc
import os
import sys
import time

import csi
import image
import ml

print("#P start fw=%r" % (sys.version,))

rom = os.listdir("/rom")
print("#P rom n=%d %r" % (len(rom), rom))
cands = [f for f in rom if "stage1" in f or "urchin" in f or "yolox" in f]
print("#P rom_stage1 %r" % (cands,))
if not cands:
    raise SystemExit("FAIL: no stage-1 model on /rom -- names above")
sizes = {}
for f in cands:
    sizes[f] = os.stat("/rom/" + f)[6]
print("#P rom_sizes %r" % (sizes,))

# smallest candidate = nano
nano = sorted(cands, key=lambda f: sizes[f])[0]
t0 = time.ticks_us()
m = ml.Model("/rom/" + nano)
t_load = time.ticks_diff(time.ticks_us(), t0)
print("#P model %r load_us=%d input=%r output=%r" %
      (nano, t_load, m.input_shape, m.output_shape))
try:
    print("#P model_dtype in=%r out=%r" % (m.input_dtype, m.output_dtype))
except AttributeError as e:
    print("#P model_dtype unavailable: %r" % (e,))

csi0 = csi.CSI()
csi0.reset()
csi0.pixformat(csi.RGB565)
csi0.framesize(csi.VGA)
for _ in range(3):
    csi0.snapshot()                       # settle AE
t0 = time.ticks_us()
img = csi0.snapshot()
cap_us = time.ticks_diff(time.ticks_us(), t0)
print("#P snapshot us=%d w=%d h=%d" % (cap_us, img.width(), img.height()))

gc.collect()
t0 = time.ticks_us()
out = m.predict([img])
inf_us = time.ticks_diff(time.ticks_us(), t0)
print("#P predict_whole us=%d container=%r n=%d" %
      (inf_us, type(out).__name__, len(out) if isinstance(out, (list, tuple)) else -1))
outs = out if isinstance(out, (list, tuple)) else [out]
for i, o in enumerate(outs):
    shp = getattr(o, "shape", None)
    dt = getattr(o, "dtype", None)
    print("#P out[%d] type=%r shape=%r dtype=%r" %
          (i, type(o).__name__, shp, dt))
    try:
        print("#P out[%d] sample %r" % (i, [float(x) for x in o.flatten()[:4]]))
    except Exception as e:
        print("#P out[%d] sample-failed %r" % (i, e))

o0 = outs[0]
try:
    b = o0.tobytes()
    print("#P tobytes ok len=%d" % (len(b),))
except Exception as e:
    print("#P tobytes FAILED %r" % (e,))
    try:
        b = bytes(o0)
        print("#P bytes(buffer) ok len=%d" % (len(b),))
    except Exception as e2:
        print("#P bytes(buffer) FAILED %r" % (e2,))

import binascii
try:
    t0 = time.ticks_us()
    bb = b"".join(o.tobytes() for o in outs)
    e = binascii.b2a_base64(bb)
    print("#P b64 all-heads bytes=%d b64=%d us=%d" %
          (len(bb), len(e), time.ticks_diff(time.ticks_us(), t0)))
except Exception as ex:
    print("#P b64 FAILED %r" % (ex,))

gc.collect()
print("#P heap free=%d" % (gc.mem_free(),))

# crop route (tiles at native px)
try:
    t0 = time.ticks_us()
    tile = img.copy(roi=(192, 72, 256, 256))
    crop_us = time.ticks_diff(time.ticks_us(), t0)
    print("#P crop ok us=%d w=%d h=%d" % (crop_us, tile.width(), tile.height()))
    gc.collect()
    t0 = time.ticks_us()
    out_t = m.predict([tile])
    print("#P predict_tile us=%d" % (time.ticks_diff(time.ticks_us(), t0),))
except Exception as e:
    print("#P crop FAILED %r" % (e,))

# letterbox route (whole frame, aspect preserved, gray pad)
try:
    t0 = time.ticks_us()
    canvas = image.Image(256, 256, image.RGB565)
    canvas.draw_rectangle((0, 0, 256, 256), color=(114, 114, 114), fill=True)
    # VGA 640x400 * 0.4 = 256x160, top-left -- hil_stills.py's exact geometry
    canvas.draw_image(img, 0, 0, x_scale=0.4, y_scale=0.4)
    lb_us = time.ticks_diff(time.ticks_us(), t0)
    print("#P letterbox ok us=%d" % (lb_us,))
    gc.collect()
    t0 = time.ticks_us()
    out_l = m.predict([canvas])
    print("#P predict_letterbox us=%d" % (time.ticks_diff(time.ticks_us(), t0),))
except Exception as e:
    print("#P letterbox FAILED %r" % (e,))

# JPEG cost at VGA (the calib/eyeball artifact on the wire)
t0 = time.ticks_us()
jb = img.to_jpeg(quality=50, copy=True).bytearray()
print("#P jpeg us=%d bytes=%d" % (time.ticks_diff(time.ticks_us(), t0), len(jb)))

gc.collect()
print("#P done heap=%d" % (gc.mem_free(),))
