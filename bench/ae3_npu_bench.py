# ae3_npu_bench.py -- OpenMV AE3 NPU inference benchmark (Sprint S8, bite 1)
#
# S0-style measured table: detector inference latency vs input size on the
# AE3, plus HD-coverage arithmetic against the T2 gate (3-5 fps on-device,
# fish >= ~24-32 px). Feeds board selection for the BM-native arc.
#
# No sensor use (avoids the D15 crash class; scene-independent numbers).
# Benches against the stored reef reference scene, mounted from the host:
#
#   mpremote connect <dev> mount bench/assets/ref_scene run bench/ae3_npu_bench.py
#
# Models are discovered live from /rom (nothing hard-coded): the installed
# firmware ships e.g. yolov8n_192.tflite + blazeface_front_128.tflite
# (docs.openmv.io v5.0.0 AE3 quickref). Wall-clock is what's measured;
# whether a given op ran on the NPU vs CPU is not queryable from Python.
#
# Copy the printed tables into DESIGN.md §Bench results.

import time
import gc

try:
    import ml
    import image
    import os
    ON_TARGET = True
except ImportError:
    ml = image = os = None      # host CPython: helpers importable for tests
    ON_TARGET = False

ROM = "/rom"
BASE = "/remote"
REF_IMAGES = (                  # (label, file, w, h) -- from make_ref_scene.py
    ("qvga-ish", "ref_color_320x200.bmp", 320, 200),
    ("HD", "ref_color_1280x800.bmp", 1280, 800),
)
HD_W, HD_H = 1280, 800          # T2 frame (matches the stored HD ref scene)
TILE_OVERLAP_PX = 32            # >= detector floor (SPEC: fish >= 24-32 px)
FISH_PX = (100, 150)            # fish size at P7071008 range (SPEC T2)
FISH_FLOOR_PX = 24              # lower detection floor (SPEC T2)
T2_GATE_FPS = 3.0               # lower edge of the 3-5 fps target
WARMUP = 2
REPS = 10


# ---------- pure helpers (host-testable) ----------

def parse_hw(shape):
    """Model input tensor shape -> (h, w). NHWC 4-tuple or HWC 3-tuple."""
    if len(shape) == 4:
        return shape[1], shape[2]
    if len(shape) == 3:
        return shape[0], shape[1]
    raise ValueError("unsupported input shape %r" % (shape,))


def tile_count(frame_w, frame_h, tile_w, tile_h, overlap):
    """Tiles of tile_w x tile_h covering the frame, stride = tile - overlap."""
    def n(frame, tile):
        if tile >= frame:
            return 1
        stride = tile - overlap
        if stride <= 0:
            raise ValueError("overlap %d >= tile %d" % (overlap, tile))
        return (frame - tile + stride - 1) // stride + 1
    return n(frame_w, tile_w) * n(frame_h, tile_h)


def eff_fps(ms_per_inf, tiles=1):
    """Full-frame detection fps if every tile costs ms_per_inf."""
    total = ms_per_inf * tiles
    return 1000.0 / total if total > 0 else 0.0


def scaled_fish_px(fish_px, in_w, frame_w):
    """Apparent fish size after downscaling the frame to the model input."""
    return fish_px * in_w / frame_w


def gate_line(name, fps, gate=T2_GATE_FPS):
    verdict = "MEETS" if fps >= gate else "BELOW"
    return "%s: %.1f fps -> %s the T2 gate (>= %.0f fps)" % (
        name, fps, verdict, gate)


# ---------- on-target stages ----------

def list_rom_models():
    try:
        names = sorted(os.listdir(ROM))
    except OSError as e:
        print("FAIL: cannot list %s: %r" % (ROM, e))
        return []
    out = []
    for n in names:
        try:
            size = os.stat(ROM + "/" + n)[6]
        except OSError:
            size = -1
        print("  %-36s %8d B" % (n, size))
        if n.endswith(".tflite"):
            out.append(ROM + "/" + n)
    return out


def make_postprocess(path):
    """Detector postprocessor when one is identifiable from the filename."""
    if "yolov8" in path:
        try:
            from ml.postprocessing.ultralytics import YoloV8
            return YoloV8(threshold=0.4), "YoloV8(0.4)"
        except ImportError as e:
            print("  note: no YoloV8 postprocess (%r) -- raw predict only" % e)
    return None, "-"


def time_predict(model, img):
    for _ in range(WARMUP):
        out = model.predict([img])
    gc.collect()
    t0 = time.ticks_us()
    for _ in range(REPS):
        out = model.predict([img])
    us = time.ticks_diff(time.ticks_us(), t0)
    return (us / REPS) / 1000.0, out


def describe_output(out, has_pp):
    if has_pp:
        try:                     # per-class lists of ((x,y,w,h), score)
            return "%d det" % sum(len(c) for c in out)
        except TypeError:
            pass
    try:
        return "shapes %s" % ([tuple(o.shape) for o in out],)
    except AttributeError:
        return type(out).__name__


def bench_model(path, rows):
    gc.collect()
    pp, pp_name = make_postprocess(path)
    try:
        model = ml.Model(path, postprocess=pp) if pp else ml.Model(path)
    except Exception as e:
        print("SKIP %s: %r" % (path, e))
        return
    try:
        in_h, in_w = parse_hw(model.input_shape[0])
    except (ValueError, IndexError) as e:
        print("SKIP %s: input shape %r (%r)" % (path, model.input_shape, e))
        return
    name = path.rsplit("/", 1)[-1]
    print("%s: input %dx%d, arena %s B, pp=%s" % (
        name, in_w, in_h, getattr(model, "ram", "?"), pp_name))
    for label, fname, w, h in REF_IMAGES:
        try:
            img = image.Image(BASE + "/" + fname, copy_to_fb=True)
        except Exception as e:
            print("  SKIP %s: %r" % (fname, e))
            continue
        try:
            ms, out = time_predict(model, img)
        except Exception as e:
            print("  FAIL predict on %s: %r" % (fname, e))
            continue
        desc = describe_output(out, pp is not None)
        print("  %-8s %4dx%-4d  %8.1f ms  %6.1f fps  %s" % (
            label, w, h, ms, 1000.0 / ms, desc))
        rows.append((name, in_w, in_h, label, ms))
        del img
        gc.collect()
    del model
    gc.collect()


def coverage_table(rows):
    hdr = ("%-28s %-9s %6s %9s %8s %11s"
           % ("model", "in", "tiles", "ms/frame", "eff fps", "1-pass fps"))
    print(hdr)
    print("-" * len(hdr))
    verdicts = []
    for name, in_w, in_h, label, ms in rows:
        if label != "qvga-ish":     # tile cost ~= near-input-size latency
            continue
        single = next((r[4] for r in rows
                       if r[0] == name and r[3] == "HD"), None)
        tiles = tile_count(HD_W, HD_H, in_w, in_h, TILE_OVERLAP_PX)
        tiled_fps = eff_fps(ms, tiles)
        one = "%9.1f" % eff_fps(single) if single else "        -"
        print("%-28s %3dx%-4d %6d %9.1f %8.2f %s" % (
            name, in_w, in_h, tiles, ms * tiles, tiled_fps, one))
        lo, hi = (scaled_fish_px(p, in_w, HD_W) for p in FISH_PX)
        print("   1-pass fish apparent size: %.0f-%.0f px (floor %d px)%s"
              % (lo, hi, FISH_FLOOR_PX,
                 " -- BELOW FLOOR, tiling required" if hi < FISH_FLOOR_PX
                 else ""))
        verdicts.append(gate_line("%s tiled@HD" % name, tiled_fps))
    print("-" * len(hdr))
    for v in verdicts:
        print(v)


def main():
    import sys
    print("=" * 70)
    print("AE3 NPU inference benchmark (no sensor; reef ref scene)")
    print(sys.version)
    gc.collect()
    print("free heap at start: %d bytes" % gc.mem_free())
    print("=" * 70)
    print("%s contents:" % ROM)
    models = list_rom_models()
    if not models:
        print("FAIL: no .tflite models found in %s -- nothing to bench" % ROM)
        return
    print("-" * 70)
    rows = []
    for path in models:
        bench_model(path, rows)
    print("-" * 70)
    print("HD (%dx%d) coverage, tile overlap %d px:" % (
        HD_W, HD_H, TILE_OVERLAP_PX))
    coverage_table(rows)
    print("NOTE: ms/inf includes image->tensor preprocessing (predict path).")
    print("Copy these tables into DESIGN.md §Bench results.")


if __name__ == "__main__" and ON_TARGET:
    main()
