#!/usr/bin/env python3
"""One HD still per board, auto-exposure LEFT ON.

NOT composite.board_burst: that freezes AE/AWB/gain because stacking needs a
frozen pipeline, and in a dim room the freeze lands before AE has converged
and returns a BLACK frame that is nonetheless a valid 28 kB JPEG. For "what
does this camera see", the ISP must be allowed to do its job.
"""
import os, sys, time, base64, argparse
ROOT = os.path.expanduser("~/ADIN_SPI_OpenMV")
sys.path.insert(0, os.path.join(ROOT, "pi", "field"))
sys.path.insert(0, os.path.join(ROOT, "bench"))
import discover
from n6_stream_host import SerialBoard

SRC = '''
import csi, image, time, gc, ubinascii
c = csi.CSI(); c.reset()
c.pixformat(csi.RGB565)
c.framesize(csi.%(SIZE)s)
time.sleep_ms(2000)
for _ in range(%(SKIP)d):
    c.snapshot()
img = c.snapshot()
st = img.get_statistics()
try:
    e = c.exposure_us()
except Exception:
    e = -1
try:
    g = c.gain_db()
except Exception:
    g = -1
print('#A mean=%%d min=%%d max=%%d exp_us=%%d gain=%%d w=%%d h=%%d'
      %% (st.mean(), st.min(), st.max(), e, g, img.width(), img.height()))
j = img.to_jpeg(quality=%(Q)d)
b = ubinascii.b2a_base64(j.bytearray()).decode().strip()
print('#F %%d' %% len(b))
print(b)
print('#D')
'''

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.expanduser("~/optics"))
ap.add_argument("--tag", required=True)
ap.add_argument("--size", default="HD")
ap.add_argument("--quality", type=int, default=95)
ap.add_argument("--skip", type=int, default=40)
ap.add_argument("--wait", type=float, default=40.0)
ap.add_argument("--roles", default="AE3,N6")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

found, problems = discover.discover()
for p in problems:
    print("PROBLEM:", p)
for role in [r.strip() for r in a.roles.split(",") if r.strip()]:
    info = found.get(role)
    if not info:
        print("MISS %s" % role); continue
    time.sleep(a.wait)
    b = SerialBoard(info["port"]).start(
        SRC % {"SIZE": a.size, "Q": a.quality, "SKIP": a.skip})
    t, meta = time.time(), ""
    try:
        while time.time() - t < 90:
            L = b.readline()
            if not L: break
            L = L.rstrip(b"\r\n")
            if L.startswith(b"#A"):
                meta = L.decode("utf-8", "replace")
                print(role, meta, flush=True)
            elif L.startswith(b"#F "):
                n = int(L.split()[1])
                payload = b.readline().rstrip(b"\r\n")
                if len(payload) != n:
                    print("SHORT %s" % role); break
                p = os.path.join(a.out, "%s_%s.jpg" % (a.tag, role))
                open(p, "wb").write(base64.b64decode(payload))
                print("OK %s -> %s (%d bytes)" % (role, p, os.path.getsize(p)))
            elif L.startswith(b"#D"):
                break
    finally:
        b.stop()
