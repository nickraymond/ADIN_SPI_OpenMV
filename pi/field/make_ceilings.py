#!/usr/bin/env python3
"""Build camera_ceilings.json from n6_h264_run.py result files.

The recorder card shows each camera's ceiling and refuses or warns on an
impossible request. Those numbers must come from a MEASUREMENT ARTIFACT, never
from a table someone typed, because a mistyped ceiling turns the guard into a
liar in whichever direction the typo went.

    python3 make_ceilings.py --out camera_ceilings.json \
        N6=~/s32_bite0/n6_matrix_v501.json N6=~/s32_bite0/n6_rungs_v501.json \
        AE3=~/s32_bite0/ae3_matrix_v501.json

Later files win on a repeated cell, so a focused re-measure can refine a matrix.
Cells whose loop could not sustain the frame rate are still recorded -- for a
CEILING that is the point, the number IS what the camera managed.
"""

import argparse
import json
import os
import sys
import time


def load(path):
    with open(os.path.expanduser(path)) as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="*", metavar="ROLE=results.json")
    ap.add_argument("--recordings", default="",
                    help="a recordings root; every manifest there contributes a "
                         "DELIVERED rate, which is what the card must guard on")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "camera_ceilings.json"))
    a = ap.parse_args(argv)

    doc = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "host": os.uname().nodename,
           "how": "measured by pi/field/n6_h264_run.py -> make_ceilings.py; "
                  "encoder ceiling = capture+encode loop fps on the board, "
                  "with no USB payload transfer in the path",
           "cameras": {}}

    for pair in a.pairs:
        if "=" not in pair:
            sys.exit("expected ROLE=path, got %r" % pair)
        role, path = pair.split("=", 1)
        try:
            d = load(path)
        except (OSError, ValueError) as e:
            print("skip %s: %s" % (path, e), file=sys.stderr)
            continue
        cam = doc["cameras"].setdefault(
            role, {"cells": {}, "bytes": {}, "sources": [], "firmware": "",
                   "note": ""})
        cam["sources"].append(os.path.basename(path))
        for info in d.get("info", []):
            if "uname" in info:
                cam["firmware"] = info.get("uname", "")
        n = 0
        for r in d.get("results", []):
            if r.get("codec") != "mjpeg" or r.get("quality") is None:
                continue
            key = "%s_q%d" % (r["size"], r["quality"])
            cam["cells"][key] = round(r["achieved_fps"], 2)
            cam["bytes"][key] = int(r["bytes_per_frame"])
            n += 1
        print("%-4s %-28s %d cells" % (role, os.path.basename(path), n))

    # DELIVERED rates, harvested from real recordings.
    #
    # These matter more than the encoder ceiling and they are LOWER: the board
    # writes each frame over USB inside the same single-threaded loop that
    # encodes, so a big frame costs encode time AND link time. Measured on
    # nereus000: HD q85 encodes at 34.9 fps but DELIVERS 23.8, because 195 KB
    # per frame is ~11 ms of USB on top of 27 ms of encode. A card that guards
    # on the encoder number would cheerfully promise 30 fps and hand back 24.
    if a.recordings:
        root = os.path.expanduser(a.recordings)
        for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            mp = os.path.join(root, name, "manifest.json")
            if not os.path.isfile(mp):
                continue
            try:
                with open(mp) as f:
                    man = json.load(f)
            except (OSError, ValueError):
                continue
            s = man.get("settings") or {}
            fs, q = s.get("framesize"), s.get("quality")
            if not fs or q is None:
                continue
            for c in man.get("cameras", []):
                role, cf = c.get("label"), c.get("capture_fps")
                if not role or not cf or c.get("written_frames", 0) < 10:
                    continue
                cam = doc["cameras"].setdefault(
                    role, {"cells": {}, "bytes": {}, "sources": [],
                           "firmware": "", "note": ""})
                d = cam.setdefault("delivered", {})
                key = "%s_q%d" % (fs, int(q))
                # Keep the BEST observed delivery for a cell: a run that was
                # throttled by something transient must not permanently lower
                # the camera's stated capability.
                if cf > d.get(key, 0):
                    d[key] = round(float(cf), 2)
                    cam.setdefault("delivered_bytes", {})[key] = int(
                        c.get("written_bytes", 0) / max(1, c.get("written_frames", 1)))

    for role, cam in doc["cameras"].items():
        cells, deliv = cam.get("cells") or {}, cam.get("delivered") or {}
        bits = []
        if cells:
            best = max(cells.items(), key=lambda kv: kv[1])
            bits.append("%d encoder cells, fastest %s at %.1f fps"
                        % (len(cells), best[0], best[1]))
        if deliv:
            bits.append("%d delivered cells measured end to end" % len(deliv))
        cam["note"] = "; ".join(bits) or "nothing measured"

    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
