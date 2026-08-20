# n6_stream_board.py -- OpenMV N6 live detection stream, board side
# (S24 bite 1; multi-colour classes + labelled capture added in S8 bite A).
#
# Runs ON the N6 under `mpremote run`, driven by bench/n6_stream_host.py, which
# serves the result as MJPEG in a browser. No OpenMV IDE involved (the IDE has
# no macOS 14 build -- SPEC/S24), and NOTHING is written to the board: this file
# is executed from the host, /flash is never touched.
#
# Per frame: snapshot -> yolov8n_192 predict -> overlay -> JPEG -> stdout.
#
# WIRE FORMAT (chosen deliberately -- see DESIGN S24):
#   #F {"seq":N,"w":W,"h":H,"b64":LEN, ...}\n
#   <exactly LEN base64 ASCII chars>\n
# The payload is base64, NOT raw binary, because `mpremote run` streams the
# script's stdout back through the raw REPL, which terminates on byte 0x04 --
# and JPEG payloads contain 0x04 freely. Base64 costs ~33% and buys a stream
# that needs no board-side deployment at all. The explicit b64 length means the
# host never has to guess where a frame ends.
#
# Per-frame header fields added in S8 bite A:
#   "bc":[n, ...]   blob count PER CLASS, in BLOB_CLASSES order
#   "amb":N         blobs matching more than one class (counted once, see
#                   classify_blobs) -- the honest denominator for bite C
#   "bb":[[cls, x, y, w, h, pixels], ...]  boxes, capped at MAX_BOXES, so a
#                   saved frame carries the labels of the detector that ran
#
# Config is injected by the host as a `_CFG` dict prepended to this file, so the
# host owns every knob and there is one copy of the defaults (below).

import csi
import gc
import os
import time
import binascii

try:
    _CFG                      # injected by the host
except NameError:
    _CFG = {}

FRAMESIZE = _CFG.get("framesize", "VGA")
QUALITY = _CFG.get("quality", 50)
MAX_SECONDS = _CFG.get("max_seconds", 3600)
MAX_FRAMES = _CFG.get("max_frames", 0)        # 0 = bounded by MAX_SECONDS only
MODEL = _CFG.get("model", "/rom/yolov8n_192.tflite")
THRESHOLD = _CFG.get("threshold", 0.4)
DETECT = _CFG.get("detect", True)             # run the model at all
BLOBS = _CFG.get("blobs", True)               # colour-blob overlay
BLOB_PIXELS = _CFG.get("blob_pixels", 150)    # reject sensor noise
BLOB_AREA = _CFG.get("blob_area", 150)
TUNE = _CFG.get("tune", False)                # centre-patch LAB readout
BLOB_LABEL = _CFG.get("blob_label", "blob")   # name for the default class
# One LAB box per colour, (L_lo, L_hi, A_lo, A_hi, B_lo, B_hi), each with a
# name. The default is the broad purple/violet the S24 demo ran; the host
# replaces the whole list from repeated --blob-thresh. Pink and purple differ
# almost entirely in `b` (pink neutral-to-positive, purple negative), which is
# why one box cannot hold both -- S24 bite 1's finding, and this bite's reason.
BLOB_CLASSES = [(c[0], tuple(c[1])) for c in
                _CFG.get("blob_classes",
                         [(BLOB_LABEL, (10, 80, 10, 65, -75, -10))])]
# "codes"     -- ONE find_blobs pass over every threshold, attributed by the
#                blob's code bitfield. Cheap: one ~11 ms pass at VGA total.
# "per-class" -- one pass per threshold. Unambiguous by construction and
#                ~11 ms PER CLASS. The two are compared on a real scene in
#                this bite's manual test; whichever is right, its cost is
#                visible in blob_us.
BLOB_SCAN = _CFG.get("blob_scan", "codes")
# Overlays are OFF while capturing training frames: a JPEG with boxes burned
# into it is useless as a training image. The blob pass still runs and still
# reports boxes, so the labels survive -- only the drawing stops.
OVERLAY = _CFG.get("overlay", True)
MAX_BOXES = _CFG.get("max_boxes", 32)         # bound the header line length
# "auto" sniffs the filename; "yolo" and "fomo" force. The S8 B2 custom model
# is FOMO-style: per-cell class logits on a stride-8 grid, no NMS, no anchors.
MODEL_KIND = _CFG.get("model_kind", "auto")
# Labels for models that ship no .txt next to them (the N6 ROMFS carries just
# the .tflite); harmless for models that do -- the file wins when present.
MODEL_LABELS = _CFG.get("model_labels", [])
# Margin decode: a cell counts as class c when logit_c beats the best other
# logit by this much. 0.69 = ln(2) makes p(c) >= 0.5 guaranteed at 3 classes
# WITHOUT computing softmax -- 576 exp calls per frame is real time on the
# AE3, two float compares is not.
FOMO_MARGIN = _CFG.get("fomo_margin", 0.69)

# One colour per class, cycled. Distinct at a glance on a JPEG at 2 m.
PALETTE = ((0, 255, 255), (255, 0, 255), (255, 255, 0),
           (0, 255, 0), (255, 128, 0), (255, 255, 255))


def class_colour(i):
    return PALETTE[i % len(PALETTE)]


def draw_label(img, xy, text, colour):
    """Text with a 1 px black offset behind it, so it reads on any background.

    Deliberately built from draw_string alone: every drawing call in this file
    is one measured to exist on this firmware. A filled backing rectangle
    would be prettier and is one more unverified kwarg (`fill=`) than this
    bite has earned.
    """
    x, y = xy
    img.draw_string((x + 1, y + 1), text, color=(0, 0, 0), scale=2)
    img.draw_string((x, y), text, color=colour, scale=2)


def centre_roi(w, h, frac=8):
    """A centred box ~1/frac of each dimension, as (x, y, w, h)."""
    bw, bh = w // frac, h // frac
    return ((w - bw) // 2, (h - bh) // 2, bw, bh)


def tune_readout(img, colour=(255, 255, 0)):
    """Draw a centre target and return its mean LAB, for picking a threshold.

    Point the object at the box and read the numbers off the stream HUD: that
    is how you get a real threshold for a real object under real light, rather
    than guessing LAB ranges from a colour name.
    """
    roi = centre_roi(img.width(), img.height())
    st = img.get_statistics(roi=roi)
    img.draw_rectangle(roi, color=colour, thickness=2)
    # get_statistics() returns a namedtuple here: means are ATTRIBUTES.
    return st.l_mean, st.a_mean, st.b_mean

# yolov8n_192 and yolo_lc_192 both ship a ONE-line label file: "person".
# Read it live rather than hard-coding a COCO list we do not have (rule 3).
def load_labels(model_path):
    try:
        f = open(model_path.rsplit(".", 1)[0] + ".txt")
    except OSError:
        return []
    try:
        return [ln.strip() for ln in f.read().split("\n") if ln.strip()]
    finally:
        f.close()


def model_kind(path):
    if MODEL_KIND != "auto":
        return MODEL_KIND
    if "yolov8" in path:
        return "yolo"
    if "fomo" in path or "nereus" in path:
        return "fomo"
    return "raw"


def make_model(path, threshold):
    """Load the model with a postprocessor when one is identifiable by name."""
    import ml
    if model_kind(path) == "yolo":
        from ml.postprocessing.ultralytics import YoloV8
        return ml.Model(path, postprocess=YoloV8(threshold=threshold)), "yolo"
    return ml.Model(path), model_kind(path)


def _out_grid(out):
    """predict() output -> the (gh, gw, nclass) tensor, tolerating a leading
    batch axis and ndarray-vs-nested-list. Nothing here is assumed about the
    array type beyond indexing and len() -- the ulab surface on this firmware
    has not been exercised by this project before."""
    o = out[0] if isinstance(out, (list, tuple)) else out
    while True:
        try:
            shape = o.shape
        except AttributeError:
            shape = None
        n = len(shape) if shape else None
        if n == 4 or (n is None and len(o) == 1):
            o = o[0]
            continue
        return o


def fomo_decode(o, nclass, margin):
    """(gh,gw,nclass) logits -> per-class cell groups via margin decode +
    4-connected BFS. Returns (counts, boxes) with boxes in GRID cell units:
    [class, gx, gy, gw, gh]. Mirrors ml/fomo/train.py decode_grid -- keep
    them matched."""
    gh, gw = len(o), len(o[0])
    hits = []                       # (gy, gx, class)
    grid = [[0] * gw for _ in range(gh)]     # 0 = bg, else class idx
    for gy in range(gh):
        row = o[gy]
        for gx in range(gw):
            cell = row[gx]
            best, second, bi = -1e9, -1e9, 0
            for ci in range(nclass):
                v = cell[ci]
                if v > best:
                    second, best, bi = best, v, ci
                elif v > second:
                    second = v
            if bi != 0 and best - second > margin:
                grid[gy][gx] = bi
                hits.append((gy, gx, bi))
    counts = [0] * (nclass - 1)
    boxes = []
    seen = set()
    for gy, gx, ci in hits:
        if (gy, gx) in seen:
            continue
        stack = [(gy, gx)]
        seen.add((gy, gx))
        x0, x1, y0, y1 = gx, gx, gy, gy
        while stack:
            cy, cx = stack.pop()
            x0 = min(x0, cx); x1 = max(x1, cx)
            y0 = min(y0, cy); y1 = max(y1, cy)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = cy + dy, cx + dx
                if (0 <= ny < gh and 0 <= nx < gw and (ny, nx) not in seen
                        and grid[ny][nx] == ci):
                    seen.add((ny, nx))
                    stack.append((ny, nx))
        counts[ci - 1] += 1
        boxes.append((ci - 1, x0, y0, x1 - x0 + 1, y1 - y0 + 1))
    return counts, boxes


def draw_fomo(img, boxes, labels, counts, gw, gh):
    """One rect per cell group, scaled from grid to pixels, class-coloured."""
    w, h = img.width(), img.height()
    for ci, gx, gy, gws, ghs in boxes:
        colour = class_colour(ci)
        x = gx * w // gw
        y = gy * h // gh
        img.draw_rectangle((x, y, gws * w // gw, ghs * h // gh),
                           color=colour, thickness=2)
        name = labels[ci] if ci < len(labels) else str(ci)
        draw_label(img, (x + 2, max(0, y - 18)), name, colour)


def draw_detections(img, out, labels, colour=(255, 0, 0), draw=True):
    """Count postprocessor output, drawing it unless the overlay is off.

    Output shape is per-class lists of ((x, y, w, h), score); a model with no
    detections yields empty lists (or an empty tuple), which is not an error.

    Counting is deliberately NOT fused with drawing: a capture run turns the
    overlay off to keep training frames clean, and a detection that happened
    still has to be reported. Fusing them made `det` read 0 during capture.
    """
    n = 0
    for cls_idx, dets in enumerate(out):
        name = labels[cls_idx] if cls_idx < len(labels) else str(cls_idx)
        for box, score in dets:
            n += 1
            if not draw:
                continue
            x, y, w, h = box
            # NOTE: OpenMV v5 firmware takes a TUPLE first argument on every
            # draw_* call -- the older x, y, w, h spelling raises TypeError
            # ("object 'int' isn't a tuple or list"). Measured on this board.
            img.draw_rectangle((x, y, w, h), color=colour, thickness=2)
            draw_label(img, (x + 2, max(0, y - 18)),
                       "%s %.2f" % (name, score), colour)
    return n


def blob_code(b):
    """The blob's threshold bitfield, tolerating attribute-or-method.

    Blob fields are attributes on this firmware (b.rect, not b.rect()), but
    `code` has NOT been verified live and the attribute-vs-method split has
    already bitten this project twice (draw_*, get_statistics). Three lines
    here beat a crash on the first multi-colour frame.
    """
    c = b.code
    return c if isinstance(c, int) else c()


def scan_blobs(img, threshes, pixels, area, scan):
    """Find blobs. Returns [(blob, bits)], bits = which thresholds matched."""
    if scan == "per-class":
        out = []
        for i in range(len(threshes)):
            for b in img.find_blobs([threshes[i]], pixels_threshold=pixels,
                                    area_threshold=area, merge=True):
                out.append((b, 1 << i))
        return out
    return [(b, blob_code(b))
            for b in img.find_blobs(threshes, pixels_threshold=pixels,
                                    area_threshold=area, merge=True)]


def classify_blobs(records, nclasses):
    """-> (rows, counts, ambiguous).

    A blob whose code carries more than one bit (merge joined two colours, or
    the boxes overlap in LAB) is counted ONCE, against its lowest-index class,
    and tallied in `ambiguous`. Counting it into every class it touches would
    inflate exactly the number bite C compares against ground truth; dropping
    it would hide a real detection. Counted once, and said out loud.
    """
    rows, counts, ambiguous = [], [0] * nclasses, 0
    for b, bits in records:
        idx = -1
        extra = False
        for i in range(nclasses):
            if bits & (1 << i):
                if idx < 0:
                    idx = i
                else:
                    extra = True
        if idx < 0:
            continue            # matched nothing nameable -- do not mislabel it
        counts[idx] += 1
        if extra:
            ambiguous += 1
        rows.append((idx, b, extra, counts[idx]))
    return rows, counts, ambiguous


def draw_blob_rows(img, rows, classes):
    """Draw one rectangle per blob in its own class colour."""
    for idx, b, extra, n in rows:
        colour = class_colour(idx)
        # OpenMV v5 draw_* takes a TUPLE first argument (measured, DESIGN S24).
        img.draw_rectangle(b.rect, color=colour, thickness=2)
        img.draw_cross((b.cx, b.cy), color=colour, size=8)
        draw_label(img, (b.x, max(0, b.y - 18)),
                   "%s%s %d  %dpx" % (classes[idx][0], "?" if extra else "",
                                      n, b.pixels), colour)


def box_list(rows, limit):
    """Blob boxes for the wire: [[class, x, y, w, h, pixels], ...].

    This is what turns --save-frames into a labelled capture rather than a pile
    of JPEGs: the boxes ride along with the frame that produced them, so B0's
    auto-labels come from the detector that actually ran, not from a host-side
    reimplementation of it that would not agree.
    """
    out = []
    for idx, b, extra, n in rows[:limit]:
        out.append((idx, b.x, b.y, b.w, b.h, b.pixels))
    return out


def framesize_const(name):
    """Resolve a framesize NAME to its csi constant, failing loudly and usefully."""
    try:
        return getattr(csi, name)
    except AttributeError:
        raise ValueError("framesize %r not exported by csi on this firmware" % name)


def main():
    import sys

    csi0 = csi.CSI()
    csi0.reset()
    csi0.pixformat(csi.RGB565)
    csi0.framesize(framesize_const(FRAMESIZE))

    model = None
    labels = []
    kind = "raw"
    if DETECT:
        model, kind = make_model(MODEL, THRESHOLD)
        labels = load_labels(MODEL) or list(MODEL_LABELS)

    # Model provenance, so "is this apples to apples?" is answerable from the
    # page instead of from memory. The two boards ship DIFFERENT yolov8n
    # binaries under the same filename (S8 correction), so the FILE SIZE is
    # the field that actually settles it -- read from os.stat, which cannot
    # fail, rather than a model attribute that might not exist on a given
    # firmware. Everything else is best-effort.
    minfo = {"path": MODEL if DETECT else "", "bytes": -1,
             "in": "", "out": "", "arena": -1}
    if DETECT:
        try:
            minfo["bytes"] = os.stat(MODEL)[6]
        except OSError:
            pass
        for key, attr in (("in", "input_shape"), ("out", "output_shape")):
            try:
                minfo[key] = str(getattr(model, attr))
            except Exception:
                pass
        for attr in ("ram_size", "ram"):
            try:
                minfo["arena"] = int(getattr(model, attr))
                break
            except Exception:
                pass

    # One banner line the host echoes verbatim -- provenance for the results
    # table. `board` is NOT decoration: with an N6 and an AE3 both on USB, a
    # table attributed to the wrong board is the exact failure this project
    # has already shipped once (DESIGN "S8 detail CORRECTION"). Every row
    # should carry the board that produced it.
    img = csi0.snapshot()
    # Union of both sessions' banner fields: model provenance (which binary,
    # how big, what shape) from the head-to-head work, blob-class provenance
    # from bite A. Both are answers to "apples to apples?" -- the model half
    # for the boards, the blob half for the control method.
    print("#I {\"board\":%s,\"fw\":%s,\"framesize\":\"%s\",\"w\":%d,\"h\":%d,"
          "\"pixfmt\":\"RGB565\",\"model\":\"%s\",\"model_bytes\":%d,"
          "\"model_in\":%s,\"model_out\":%s,\"arena\":%d,"
          "\"labels\":%s,\"quality\":%d,\"heap\":%d,\"blob_classes\":%s,"
          "\"blob_scan\":\"%s\",\"overlay\":%d}"
          % (_json_str(os.uname().machine), _json_str(sys.version),
             FRAMESIZE, img.width(), img.height(),
             minfo["path"], minfo["bytes"],
             _json_str(minfo["in"]), _json_str(minfo["out"]), minfo["arena"],
             _json_list(labels), QUALITY, gc.mem_free(),
             _json_list([c[0] for c in BLOB_CLASSES]), BLOB_SCAN, OVERLAY))

    threshes = [c[1] for c in BLOB_CLASSES]
    seq = 0
    t_end = time.ticks_add(time.ticks_ms(), int(MAX_SECONDS * 1000))
    while True:
        if time.ticks_diff(t_end, time.ticks_ms()) <= 0:
            break
        if MAX_FRAMES and seq >= MAX_FRAMES:
            break

        t_cap0 = time.ticks_us()
        img = csi0.snapshot()
        cap_us = time.ticks_diff(time.ticks_us(), t_cap0)

        ndet = 0
        inf_us = 0
        mdec_us = 0
        mcounts = []
        mboxes_px = []
        if model is not None:
            t_inf0 = time.ticks_us()
            out = model.predict([img])
            inf_us = time.ticks_diff(time.ticks_us(), t_inf0)
            if kind == "yolo":
                ndet = draw_detections(img, out, labels, draw=OVERLAY)
            elif kind == "fomo":
                t_dec0 = time.ticks_us()
                o = _out_grid(out)
                nclass = len(o[0][0])
                mcounts, mboxes = fomo_decode(o, nclass, FOMO_MARGIN)
                mdec_us = time.ticks_diff(time.ticks_us(), t_dec0)
                ndet = sum(mcounts)
                gh, gw = len(o), len(o[0])
                for ci, gx, gy, gws, ghs in mboxes[:MAX_BOXES]:
                    mboxes_px.append((ci, gx * img.width() // gw,
                                      gy * img.height() // gh,
                                      gws * img.width() // gw,
                                      ghs * img.height() // gh))
                if OVERLAY:
                    draw_fomo(img, mboxes, labels, mcounts, gw, gh)

        nblob = 0
        counts = [0] * len(BLOB_CLASSES)
        amb = 0
        boxes = []
        t_blob0 = time.ticks_us()
        if BLOBS:
            recs = scan_blobs(img, threshes, BLOB_PIXELS, BLOB_AREA, BLOB_SCAN)
            rows, counts, amb = classify_blobs(recs, len(BLOB_CLASSES))
            nblob = len(rows)
            boxes = box_list(rows, MAX_BOXES)
            if OVERLAY:
                draw_blob_rows(img, rows, BLOB_CLASSES)
        blob_us = time.ticks_diff(time.ticks_us(), t_blob0)

        lab = (0, 0, 0)
        if TUNE:
            lab = tune_readout(img)

        t_enc0 = time.ticks_us()
        jpg = img.to_jpeg(quality=QUALITY)
        enc_us = time.ticks_diff(time.ticks_us(), t_enc0)

        payload = binascii.b2a_base64(jpg)
        if payload.endswith(b"\n"):
            payload = payload[:-1]

        print("#F {\"seq\":%d,\"w\":%d,\"h\":%d,\"b64\":%d,\"jpeg\":%d,"
              "\"cap_us\":%d,\"inf_us\":%d,\"blob_us\":%d,\"enc_us\":%d,"
              "\"mdec_us\":%d,"
              "\"det\":%d,\"blobs\":%d,\"bc\":%s,\"amb\":%d,\"bb\":%s,"
              "\"mc\":%s,\"mb\":%s,"
              "\"lab\":[%d,%d,%d]}"
              % (seq, img.width(), img.height(), len(payload), len(jpg),
                 cap_us, inf_us, blob_us, enc_us, mdec_us, ndet, nblob,
                 _json_ints(counts), amb, _json_boxes(boxes),
                 _json_ints(mcounts), _json_boxes(mboxes_px),
                 int(lab[0]), int(lab[1]), int(lab[2])))
        sys.stdout.write(payload)
        sys.stdout.write("\n")

        seq += 1
        del jpg, payload
        if (seq & 0x0F) == 0:
            gc.collect()

    print("#D {\"frames\":%d}" % seq)


def _json_str(s):
    """Minimal JSON string escaping -- ujson is not worth the import here."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') \
                       .replace("\n", " ").replace("\r", " ") + '"'


def _json_list(items):
    return "[" + ",".join(_json_str(i) for i in items) + "]"


def _json_ints(values):
    return "[" + ",".join("%d" % v for v in values) + "]"


def _json_boxes(boxes):
    return "[" + ",".join(_json_ints(b) for b in boxes) + "]"


main()
