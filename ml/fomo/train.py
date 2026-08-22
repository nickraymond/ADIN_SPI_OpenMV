# train.py -- S8 B2: the from-scratch two-colour FOMO-style detector.
#
#   ~/nereus_ml/venvs/fomo/bin/python ml/fomo/train.py
#
# Trains on the combined AE3+N6 1 m captures (one model, both colour casts --
# the point), exports a full-int8 uint8-io NHWC .tflite matching the boards'
# spec-by-example ((1,192,192,3) uint8, scale 1/255, zp 0), and reports count
# accuracy on a held-out split with the same grid decode the boards will run.
#
# ARCHITECTURE, deliberately boring: plain Conv/BN/ReLU stride-8 backbone +
# 1x1 head -> (24,24,3) per-cell class logits (bg/pink/purple). No depthwise,
# no residuals, no resize ops -- the smallest op set both vela 5.0.0 and
# ST Edge AI 4.0.0 are known to map fully to their NPUs. MobileNetV2-FOMO was
# considered and rejected: this is a colour task on 693 frames, and every op
# class added is compile risk on TWO compilers (ml/README.md's trap list).
#
# The training graph takes floats in [0,1]: the boards' runtime dequantizes
# uint8 with scale 1/255, zp 0, so [0,1] is what the deployed graph sees.
import json
import os
import sys

import numpy as np

ROOT = os.path.expanduser("~/nereus_ml/datasets/two_ball")
OUT = os.path.expanduser("~/nereus_ml/runs/fomo_two_ball")
IN_SIZE = 192
GRID = 24          # stride 8
NCLASS = 3         # bg, pink, purple
CLASSES = ["pink", "purple"]
OBJ_W = 40.0       # object-cell loss weight vs background
VAL_FRAC = 0.2
SEED = 8


def load_split():
    """-> (Xtr, Ytr, Xva, Yva, va_meta). Split = last 20% of each dir (time
    order), so near-duplicate neighbouring frames never straddle the split."""
    from PIL import Image
    Xtr, Ytr, Xva, Yva, meta = [], [], [], [], []
    for run in sorted(os.listdir(ROOT)):
        rdir = os.path.join(ROOT, run)
        if not os.path.isdir(rdir):
            continue
        for board in sorted(os.listdir(rdir)):
            lbl = os.path.join(rdir, board, "labels.jsonl")
            if not os.path.isfile(lbl):
                continue
            recs = [json.loads(l) for l in open(lbl)]
            n_val = max(1, int(len(recs) * VAL_FRAC))
            for i, rec in enumerate(recs):
                img = Image.open(os.path.join(rdir, board, rec["file"]))
                img = img.convert("RGB").resize((IN_SIZE, IN_SIZE),
                                                Image.BILINEAR)
                y = np.zeros((GRID, GRID), np.int32)
                for ci, x, yy, w, h, px in rec["boxes"]:
                    gx = min(GRID - 1, int((x + w / 2) / rec["w"] * GRID))
                    gy = min(GRID - 1, int((yy + h / 2) / rec["h"] * GRID))
                    y[gy, gx] = ci + 1
                if i >= len(recs) - n_val:
                    Xva.append(np.asarray(img)); Yva.append(y)
                    meta.append((run, board, rec["file"]))
                else:
                    Xtr.append(np.asarray(img)); Ytr.append(y)
    return (np.stack(Xtr), np.stack(Ytr), np.stack(Xva), np.stack(Yva), meta)


def build_model(tf):
    L = tf.keras.layers
    inp = L.Input((IN_SIZE, IN_SIZE, 3))
    x = inp
    for filters, stride in ((16, 2), (32, 1), (32, 2), (64, 1), (64, 2), (64, 1)):
        x = L.Conv2D(filters, 3, strides=stride, padding="same",
                     use_bias=False)(x)
        # momentum 0.9, NOT the 0.99 default: at this dataset size an epoch
        # is ~18 steps, and with 0.99 the inference-mode moving stats lag the
        # batch stats for hundreds of steps -- measured here as train-mode
        # loss 0.036 vs inference-mode 1.12 on the SAME data, which also
        # poisons val_loss and makes EarlyStopping restore garbage weights.
        x = L.BatchNormalization(momentum=0.9)(x)
        x = L.ReLU()(x)
    x = L.Conv2D(NCLASS, 1, padding="same")(x)
    return tf.keras.Model(inp, x)


def weighted_loss(tf):
    sce = tf.keras.losses.SparseCategoricalCrossentropy(
        from_logits=True, reduction="none")
    def loss(y_true, y_pred):
        per_cell = sce(y_true, y_pred)
        w = tf.where(y_true > 0, OBJ_W, 1.0)
        return tf.reduce_sum(per_cell * w) / tf.reduce_sum(w)
    return loss


def augment(tf, x, y):
    """Flips (image and grid together) + mild brightness/contrast on the
    image only. NO hue jitter: colour IS the class signal."""
    if tf.random.uniform(()) > 0.5:
        x = tf.image.flip_left_right(x)
        y = tf.reverse(y, axis=[1])
    if tf.random.uniform(()) > 0.5:
        x = tf.image.flip_up_down(x)
        y = tf.reverse(y, axis=[0])
    x = tf.image.random_brightness(x, 0.08)
    x = tf.image.random_contrast(x, 0.9, 1.1)
    return tf.clip_by_value(x, 0.0, 1.0), y


def decode_grid(prob, thr=0.5):
    """(GRID,GRID,NCLASS) softmax -> {class: [(gy,gx), ...]} via 4-connected
    grouping. THE reference for the board-side decode -- keep them matched."""
    out = {}
    for ci in range(1, NCLASS):
        mask = prob[:, :, ci] > thr
        seen = np.zeros_like(mask, bool)
        cents = []
        for gy in range(GRID):
            for gx in range(GRID):
                if not mask[gy, gx] or seen[gy, gx]:
                    continue
                stack, cells = [(gy, gx)], []
                seen[gy, gx] = True
                while stack:
                    cy, cx = stack.pop()
                    cells.append((cy, cx))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if (0 <= ny < GRID and 0 <= nx < GRID
                                and mask[ny, nx] and not seen[ny, nx]):
                            seen[ny, nx] = True
                            stack.append((ny, nx))
                ws = [prob[cy, cx, ci] for cy, cx in cells]
                cy = sum(c[0] * w for c, w in zip(cells, ws)) / sum(ws)
                cx = sum(c[1] * w for c, w in zip(cells, ws)) / sum(ws)
                cents.append((cy, cx))
        out[ci] = cents
    return out


def count_eval(probs, Y, thr=0.5, tag=""):
    """Count accuracy + centroid PR (match within 1.5 cells)."""
    stats = {ci: dict(tp=0, fp=0, fn=0) for ci in range(1, NCLASS)}
    exact = 0
    mae = np.zeros(NCLASS - 1)
    for p, y in zip(probs, Y):
        got = decode_grid(p, thr)
        ok = True
        for ci in range(1, NCLASS):
            truth = [(gy, gx) for gy in range(GRID) for gx in range(GRID)
                     if y[gy, gx] == ci]
            pred = list(got[ci])
            mae[ci - 1] += abs(len(pred) - len(truth))
            if len(pred) != len(truth):
                ok = False
            used = set()
            for t in truth:
                best, bd = None, 1.5
                for i, c in enumerate(pred):
                    d = ((c[0] - t[0]) ** 2 + (c[1] - t[1]) ** 2) ** 0.5
                    if i not in used and d <= bd:
                        best, bd = i, d
                if best is None:
                    stats[ci]["fn"] += 1
                else:
                    used.add(best)
                    stats[ci]["tp"] += 1
            stats[ci]["fp"] += len(pred) - len(used)
        exact += ok
    n = len(Y)
    print("%s exact-count frames: %d/%d (%.0f%%)" % (tag, exact, n, 100 * exact / n))
    for ci in range(1, NCLASS):
        s = stats[ci]
        prec = s["tp"] / max(1, s["tp"] + s["fp"])
        rec = s["tp"] / max(1, s["tp"] + s["fn"])
        print("%s %-6s precision %.3f recall %.3f  count-MAE %.2f"
              % (tag, CLASSES[ci - 1], prec, rec, mae[ci - 1] / n))


def main():
    import tensorflow as tf
    tf.keras.utils.set_random_seed(SEED)
    Xtr, Ytr, Xva, Yva, meta = load_split()
    print("train %d frames, val %d" % (len(Xtr), len(Xva)),
          "obj cells/frame %.1f" % (Ytr > 0).sum(axis=(1, 2)).mean())

    ds = tf.data.Dataset.from_tensor_slices(
        (Xtr.astype(np.float32) / 255.0, Ytr))
    ds = ds.shuffle(len(Xtr), seed=SEED)
    ds = ds.map(lambda x, y: augment(tf, x, y)).batch(32).prefetch(2)
    vds = tf.data.Dataset.from_tensor_slices(
        (Xva.astype(np.float32) / 255.0, Yva)).batch(32)

    model = build_model(tf)
    model.compile(tf.keras.optimizers.Adam(1e-3), loss=weighted_loss(tf))
    model.summary(line_length=80)
    model.fit(ds, validation_data=vds, epochs=60, verbose=2,
              callbacks=[tf.keras.callbacks.EarlyStopping(
                  patience=8, restore_best_weights=True),
                  tf.keras.callbacks.ReduceLROnPlateau(patience=4)])

    os.makedirs(OUT, exist_ok=True)
    model.save(os.path.join(OUT, "fomo_two_ball.keras"))

    probs = tf.nn.softmax(model.predict(
        Xva.astype(np.float32) / 255.0, verbose=0)).numpy()
    count_eval(probs, Yva, tag="[float]")

    # --- full-int8 export, uint8 io (the boards' spec-by-example) ---
    def rep():
        idx = np.random.RandomState(SEED).permutation(len(Xtr))[:128]
        for i in idx:
            yield [Xtr[i:i + 1].astype(np.float32) / 255.0]
    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = rep
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.uint8
    conv.inference_output_type = tf.uint8
    blob = conv.convert()
    path = os.path.join(OUT, "nereus_two_ball.tflite")
    open(path, "wb").write(blob)

    # verify the exported artifact, not the exporter's exit code
    it = tf.lite.Interpreter(model_content=blob)
    it.allocate_tensors()
    inp, outp = it.get_input_details()[0], it.get_output_details()[0]
    print("tflite: %d bytes  in %s %s scale=%s zp=%s  out %s %s"
          % (len(blob), inp["shape"].tolist(), inp["dtype"].__name__,
             inp["quantization"][0], inp["quantization"][1],
             outp["shape"].tolist(), outp["dtype"].__name__))

    qprobs = []
    for i in range(len(Xva)):
        it.set_tensor(inp["index"], Xva[i:i + 1])
        it.invoke()
        q = it.get_tensor(outp["index"])[0].astype(np.float32)
        s, zp = outp["quantization"]
        e = np.exp((q - zp) * s - ((q - zp) * s).max(-1, keepdims=True))
        qprobs.append(e / e.sum(-1, keepdims=True))
    count_eval(np.stack(qprobs), Yva, tag="[int8 ]")
    print("saved:", path)


if __name__ == "__main__":
    sys.exit(main())
