#!/usr/bin/env python3
"""Build corpus_v1 -- the stage-1 merged training view (S8 bite E step 2).

Manifest-based, NO copies: the output jsonls carry absolute image paths
into the per-source trees under ~/nereus_ml/datasets/. Box records keep
the agreed labels.jsonl convention [ci, x0, y0, w, h, pixels], ci=0
("urchin") everywhere.

Composition (docs/urchin_corpus_plan.md, stage 1):
  train = Urchinbot official train.txt + DUO train/ + RF100 train/
  val   = Urchinbot official val.txt
Fenced, enforced by assertion, never written to train/val:
  - Urchinbot official test split (983 imgs -- rung A; test.txt has NO
    trailing newline, so line-count is 982 but entries are 983)
  - DUO test/ AND val/ (val is a byte-copy of test -- S26 finding)
  - RF100 valid/ + test/
  - the 74-img Roboflow set (hard-case eval, purple-only)

Usage: python3 ml/urchin_data/build_corpus_v1.py [--out ~/nereus_ml/datasets/corpus_v1]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

DS = Path.home() / "nereus_ml" / "datasets"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_split(path: Path) -> set:
    # basenames; robust to the missing trailing newline in test.txt
    return {Path(line.strip()).name for line in open(path) if line.strip()}


def load_jsonl(path: Path):
    return [json.loads(line) for line in open(path) if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DS / "corpus_v1"))
    ap.add_argument("--include-gbif-reviewed", action="store_true",
                    help="corpus_v2: add Nick's hand-reviewed GBIF frames "
                         "(classes collapsed to urchin; rung-B source "
                         "frames fenced)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sources = {}
    train, val = [], []
    fence_report = {}

    # ---- Urchinbot: official splits by basename -------------------------
    ub = DS / "urchinbot"
    ub_labels = ub / "labels.jsonl"
    splits = {name: read_split(ub / "archives" / f"{name}.txt")
              for name in ("train", "val", "test")}
    assert len(splits["test"]) == 983, f"urchinbot test != 983: {len(splits['test'])}"
    assert not (splits["train"] & splits["test"]), "urchinbot train/test overlap"
    # Measured 2026-08-22: the official split files themselves collide --
    # im5348179.JPG is listed in BOTH val.txt and test.txt (and the true
    # entry counts are 7913/977/983, not the published 7912/976/982; more
    # trailing-newline arithmetic). Test is rung A and inviolable, so the
    # collision resolves to test: it is REMOVED from val here.
    val_test_collisions = sorted(splits["val"] & splits["test"])
    splits["val"] -= splits["test"]
    ub_counts = {"train": 0, "val": 0, "test_fenced": 0, "unassigned": 0}
    for rec in load_jsonl(ub_labels):
        name = Path(rec["file"]).name
        rec_abs = {**rec, "src": "urchinbot", "file": str(ub / rec["file"])}
        if name in splits["train"]:
            train.append(rec_abs); ub_counts["train"] += 1
        elif name in splits["val"]:
            val.append(rec_abs); ub_counts["val"] += 1
        elif name in splits["test"]:
            ub_counts["test_fenced"] += 1
        else:
            ub_counts["unassigned"] += 1  # recorded, not trained
    sources["urchinbot"] = {
        "labels_sha256": sha256(ub_labels), "counts": ub_counts,
        "val_test_collisions_resolved_to_test": val_test_collisions,
        "split_files_sha256": {n: sha256(ub / "archives" / f"{n}.txt")
                               for n in ("train", "val", "test")}}
    fence_report["urchinbot_test"] = ub_counts["test_fenced"]

    # ---- DUO: split embedded in path; train/ only -----------------------
    duo = DS / "duo"
    duo_labels = duo / "labels.jsonl"
    duo_counts = {"train": 0, "test_or_val_fenced": 0}
    for rec in load_jsonl(duo_labels):
        rec_abs = {**rec, "src": "duo", "file": str(duo / rec["file"])}
        if "/images/train/" in rec["file"]:
            train.append(rec_abs); duo_counts["train"] += 1
        else:
            duo_counts["test_or_val_fenced"] += 1
    sources["duo"] = {"labels_sha256": sha256(duo_labels), "counts": duo_counts}
    fence_report["duo_test_val"] = duo_counts["test_or_val_fenced"]

    # ---- RF100 underwater: train/ only ----------------------------------
    rf = DS / "roboflow" / "rf100_underwater"
    rf_labels = rf / "labels.jsonl"
    rf_counts = {"train": 0, "valid_test_fenced": 0}
    for rec in load_jsonl(rf_labels):
        rec_abs = {**rec, "src": "rf100", "file": str(rf / rec["file"])}
        if rec["file"].startswith("train/"):
            train.append(rec_abs); rf_counts["train"] += 1
        else:
            rf_counts["valid_test_fenced"] += 1
    sources["rf100_underwater"] = {"labels_sha256": sha256(rf_labels),
                                   "counts": rf_counts}
    fence_report["rf100_valid_test"] = rf_counts["valid_test_fenced"]

    # ---- GBIF reviewed (corpus_v2): Nick's hand-verified boxes ----------
    if args.include_gbif_reviewed:
        gbif = DS / "gbif_inat"
        gbif_labels = gbif / "labels.jsonl"
        # Fence = source images of the species head's ACTUAL rung-B split
        # (train_species.py's deterministic group split -- NOT the stale
        # pre-review autobox rung_b_candidates.json, which over-fences).
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                               / "yolox_urchin"))
        from train_species import split_sets, group_key
        rb_fence = {group_key(p) + ".jpg"
                    for p, _ in split_sets()["rung_b"]}
        gb_counts = {"train": 0, "rung_b_fenced": 0, "unreviewed_or_empty": 0,
                     "boxes": 0}
        for rec in load_jsonl(gbif_labels):
            if not rec.get("reviewed") or not rec["boxes"]:
                gb_counts["unreviewed_or_empty"] += 1
                continue
            if Path(rec["file"]).name in rb_fence:
                gb_counts["rung_b_fenced"] += 1
                continue
            boxes = [[0, *b[1:]] for b in rec["boxes"]]  # collapse to urchin
            train.append({"file": str(gbif / rec["file"]), "w": rec["w"],
                          "h": rec["h"], "classes": ["urchin"],
                          "boxes": boxes, "src": "gbif_reviewed"})
            gb_counts["train"] += 1
            gb_counts["boxes"] += len(boxes)
        sources["gbif_reviewed"] = {"labels_sha256": sha256(gbif_labels),
                                    "counts": gb_counts}
        fence_report["gbif_rung_b_sources"] = gb_counts["rung_b_fenced"]

    # ---- Fence enforcement + artifact checks ----------------------------
    test_names = splits["test"]
    for split_name, records in (("train", train), ("val", val)):
        for rec in records:
            base = Path(rec["file"]).name
            assert not (rec["src"] == "urchinbot" and base in test_names), \
                f"FENCE BREACH: urchinbot test img {base} in {split_name}"
            assert "/images/test/" not in rec["file"] and "/images/val/" not in rec["file"], \
                f"FENCE BREACH: DUO non-train img in {split_name}: {rec['file']}"
    missing = [r["file"] for r in train + val if not os.path.isfile(r["file"])]
    assert not missing, f"{len(missing)} image paths missing, first: {missing[:3]}"

    # ---- Write outputs ---------------------------------------------------
    def write_jsonl(path, records):
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    write_jsonl(out / "train.jsonl", train)
    write_jsonl(out / "val.jsonl", val)

    def n_boxes(records):
        return sum(len(r["boxes"]) for r in records)

    try:
        git_sha = subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
            text=True).strip()
    except Exception:
        git_sha = "unknown"

    manifest = {
        "built": "2026-08-22",
        "repo_git_sha": git_sha,
        "plan": "docs/urchin_corpus_plan.md stage 1 (approved 2026-08-22)",
        "convention": "boxes [ci,x0,y0,w,h,pixels] absolute px, ci=0 urchin",
        "train": {"images": len(train), "boxes": n_boxes(train)},
        "val": {"images": len(val), "boxes": n_boxes(val),
                "note": "urchinbot official val.txt only"},
        "fenced_never_trained": {
            **fence_report,
            "roboflow_74img": "entire set (hard-case eval)",
            "note": "urchinbot test = rung A (983); DUO val is byte-copy of test"},
        "sources": sources,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"corpus_v1 -> {out}")
    print(f"  train: {len(train)} imgs / {n_boxes(train)} boxes")
    print(f"  val:   {len(val)} imgs / {n_boxes(val)} boxes")
    for k, v in fence_report.items():
        print(f"  fenced {k}: {v}")
    for s, info in sources.items():
        print(f"  {s}: {info['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
