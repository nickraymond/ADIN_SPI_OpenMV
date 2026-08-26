#!/usr/bin/env python3
"""Merge frozen-stills sets into one deployable dir (S8 bite E10).

    hil_stills_merge.py OUT SRC1 SRC2 ... [--against DEPLOYED]

Each SRC is a stills dir (frames/ + labels.jsonl + stills_manifest.json,
the shape hil_stills.py writes). Clips are concatenated in SRC order —
put the older set first so the prior canonical ordering stays a prefix.
Frames are copied into OUT/frames; labels rows are concatenated.

Every rule fails LOUD and nothing is written on failure (OUT must not
pre-exist):
  - a frame filename appearing in two sources is a collision, not a merge
  - every manifest sampled index must have its frame on disk
  - every frame must have exactly one labels row (and vice versa)
  - --against DEPLOYED enforces append-only against the currently
    deployed set: every deployed frame must be byte-identical in the
    merge, and every deployed REVIEWED row's boxes must be unchanged —
    cross-run comparability breaks silently otherwise (a changed still
    is a DECISION, recorded, never a merge side-effect).

The merged manifest keeps each source's metadata under "merged_from"
and concatenates "clips" (all the playback server reads). Prints
per-clip reviewed counts and totals; exit 0 only on a verified merge.

Stdlib only — runs on the Mac or the Pi.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time


def fail(msg):
    sys.exit("FAIL: %s" % msg)


def load_src(d):
    d = os.path.expanduser(d)
    man_p = os.path.join(d, "stills_manifest.json")
    lab_p = os.path.join(d, "labels.jsonl")
    if not os.path.isfile(man_p):
        fail("%s has no stills_manifest.json" % d)
    if not os.path.isfile(lab_p):
        fail("%s has no labels.jsonl" % d)
    with open(man_p) as fh:
        man = json.load(fh)
    rows = [json.loads(ln) for ln in open(lab_p)]
    return d, man, rows


def manifest_frames(man):
    names = []
    for c in man["clips"]:
        for idx in c["sampled_indices"]:
            names.append("%s_f%04d.jpg" % (c["still_prefix"], idx))
    return names


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def merge(out, srcs, against=None):
    out = os.path.expanduser(out)
    if os.path.exists(out):
        fail("output %s already exists — refusing to overwrite" % out)
    loaded = [load_src(s) for s in srcs]

    # ---- validate before any write --------------------------------
    frame_src = {}                    # frame name -> source dir
    all_rows = []
    for d, man, rows in loaded:
        names = manifest_frames(man)
        by_file = {}
        for r in rows:
            name = r["file"].split("/")[-1]
            if name in by_file:
                fail("%s: duplicate labels row for %s" % (d, name))
            by_file[name] = r
        for name in names:
            if name in frame_src:
                fail("frame name collision: %s is in both %s and %s"
                     % (name, frame_src[name], d))
            p = os.path.join(d, "frames", name)
            if not os.path.isfile(p):
                fail("%s: manifest names %s but the frame is not on disk"
                     % (d, name))
            if name not in by_file:
                fail("%s: frame %s has no labels row" % (d, name))
            frame_src[name] = d
        extra = set(by_file) - set(names)
        if extra:
            fail("%s: labels rows for stills not in the manifest, "
                 "first: %s" % (d, sorted(extra)[0]))
        all_rows += [by_file[n] for n in names]

    if against:
        against = os.path.expanduser(against)
        _d, dman, drows = load_src(against)
        merged_rows = {r["file"].split("/")[-1]: r for r in all_rows}
        for name in manifest_frames(dman):
            if name not in frame_src:
                fail("append-only violated: deployed still %s is absent "
                     "from the merge — prior run rows would dangle" % name)
            a = sha256(os.path.join(against, "frames", name))
            b = sha256(os.path.join(frame_src[name], "frames", name))
            if a != b:
                fail("append-only violated: deployed frame %s differs "
                     "byte-wise in the merge" % name)
        for r in drows:
            name = r["file"].split("/")[-1]
            if r.get("reviewed") and r["boxes"] != merged_rows[name]["boxes"]:
                fail("append-only violated: reviewed boxes changed for "
                     "%s — a changed still is a DECISION, not a merge"
                     % name)

    # ---- write ----------------------------------------------------
    os.makedirs(os.path.join(out, "frames"))
    for name, d in frame_src.items():
        shutil.copy2(os.path.join(d, "frames", name),
                     os.path.join(out, "frames", name))
    man_out = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "merged_from": [
                   {"dir": d,
                    "meta": {k: v for k, v in man.items() if k != "clips"}}
                   for d, man, _ in loaded],
               "clips": [c for _, man, _ in loaded for c in man["clips"]]}
    with open(os.path.join(out, "stills_manifest.json"), "w") as fh:
        json.dump(man_out, fh, indent=1)
    with open(os.path.join(out, "labels.jsonl"), "w") as fh:
        for r in all_rows:
            fh.write(json.dumps(r) + "\n")

    # ---- report ---------------------------------------------------
    print("merged %d sources -> %s" % (len(loaded), out))
    for d, man, rows in loaded:
        for c in man["clips"]:
            names = set("%s_f%04d.jpg" % (c["still_prefix"], i)
                        for i in c["sampled_indices"])
            rev = sum(1 for r in rows
                      if r["file"].split("/")[-1] in names
                      and r.get("reviewed"))
            print("  %-14s %3d stills, %3d reviewed"
                  % (c["still_prefix"], len(names), rev))
    total_rev = sum(1 for r in all_rows if r.get("reviewed"))
    print("TOTAL %d stills, %d reviewed%s"
          % (len(all_rows), total_rev,
             ", append-only VERIFIED against %s" % against
             if against else ""))
    return len(all_rows), total_rev


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("srcs", nargs="+")
    ap.add_argument("--against", default=None,
                    help="currently deployed stills dir: enforce "
                         "append-only against it before writing")
    args = ap.parse_args()
    merge(args.out, args.srcs, args.against)


if __name__ == "__main__":
    main()
