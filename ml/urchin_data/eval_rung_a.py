"""Eval rung A: score detectors on Urchinbot's OFFICIAL test split,
single-class "urchin" (all species collapsed), boxes from the CSV (the
authoritative label source — see manifests/urchinbot.json).

Builds a YOLO-layout eval dir under ~/nereus_ml/datasets/urchinbot/
rung_a_eval/ (symlinked images present on disk + generated label txts),
then runs ultralytics val() per weights file. Prints coverage — a run
below 100% of the split is PROVISIONAL and says so.

Usage:
  ~/nereus_ml/venvs/urchin/bin/python ml/urchin_data/eval_rung_a.py \
      <weights.pt> [<weights2.pt> ...]
"""
import ast
import csv
import os
import re
import sys

DS = os.path.expanduser("~/nereus_ml/datasets/urchinbot")
EVAL = f"{DS}/rung_a_eval"


def build():
    ids = [re.search(r"im(\d+)", l).group(1) for l in open(f"{DS}/archives/test.txt")]
    rows = {r["id"]: r for r in csv.DictReader(open(f"{DS}/archives/Complete_urchin_dataset.csv"))}
    os.makedirs(f"{EVAL}/images", exist_ok=True)
    os.makedirs(f"{EVAL}/labels", exist_ok=True)
    have = 0
    for i in ids:
        src = f"{DS}/images/im{i}.JPG"
        if not (os.path.exists(src) and os.path.getsize(src) > 0):
            continue
        have += 1
        dst = f"{EVAL}/images/im{i}.JPG"
        if not os.path.islink(dst):
            os.symlink(src, dst)
        with open(f"{EVAL}/labels/im{i}.txt", "w") as f:
            for t in ast.literal_eval(rows[i]["boxes"] or "[]"):
                f.write(f"0 {t[2]} {t[3]} {t[4]} {t[5]}\n")
    with open(f"{EVAL}/data.yaml", "w") as f:
        f.write(f"path: {EVAL}\ntrain: images\nval: images\nnames:\n  0: urchin\n")
    return len(ids), have


def main():
    n_split, n_have = build()
    tag = "FULL" if n_have == n_split else "PROVISIONAL"
    print(f"rung A [{tag}]: {n_have}/{n_split} test-split images on disk")
    from ultralytics import YOLO
    for wp in sys.argv[1:]:
        m = YOLO(wp)
        r = m.val(data=f"{EVAL}/data.yaml", verbose=False, plots=False)
        print(f"{os.path.basename(wp)} [{tag} n={n_have}]: "
              f"mAP50={r.box.map50:.3f} mAP50-95={r.box.map:.3f} "
              f"P={r.box.mp:.3f} R={r.box.mr:.3f}")


if __name__ == "__main__":
    main()
