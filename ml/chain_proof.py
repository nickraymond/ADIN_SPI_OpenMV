#!/usr/bin/env python3
"""Prove the train -> export -> load chain end to end, on throwaway data.

    ~/nereus_ml/venvs/train/bin/python ml/chain_proof.py

Run this after ANY version bump in requirements-train.txt. It trains a few
epochs on ultralytics' 8-image coco8 set, exports int8, and INSPECTS THE
RESULTING FILE -- shape and dtype, not the exporter's success message. The
export step is the version-fragile half of this stack, and a green "export
complete" next to an undeployable tensor layout is exactly the failure this
script exists to catch (S8 B0, 2026-08-20).

Exit status is 0 only if a .tflite was produced AND could be inspected. It
deliberately does NOT assert NHWC/int8, because as of ultralytics 8.4.124 the
only available path produces NCHW/float32 -- see ml/README.md. Change those
expectations when B1 settles the toolchain, and this becomes a real gate.
"""
import os
import sys

RUNS = os.path.expanduser("~/nereus_ml/runs")


def main():
    from ultralytics import YOLO

    model = YOLO("yolo11n.pt")
    result = model.train(data="coco8.yaml", epochs=3, imgsz=192, batch=4,
                         device="mps", project=RUNS, name="chain_proof",
                         exist_ok=True, verbose=False, plots=False)
    best = os.path.join(str(result.save_dir), "weights", "best.pt")
    print("trained:", best)

    path = YOLO(best).export(format="litert", quantize=8, imgsz=192,
                             data="coco8.yaml")
    print("exported:", path)

    # The artifact, not the exit code (CLAUDE.md rule 4).
    from ai_edge_litert.interpreter import Interpreter
    interp = Interpreter(model_path=str(path))
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]
    print("size    %.2f MB" % (os.path.getsize(path) / 1048576))
    print("input   %s %s" % (tuple(int(x) for x in inp["shape"]),
                             inp["dtype"].__name__))
    print("output  %s %s" % (tuple(int(x) for x in out["shape"]),
                             out["dtype"].__name__))

    nhwc = len(inp["shape"]) == 4 and int(inp["shape"][3]) in (1, 3)
    print("\nDEPLOYABLE TO THE BOARDS? %s" % (
        "yes" if nhwc and inp["dtype"].__name__ == "int8" else
        "NO -- the boards' ROM models are NHWC int8; see ml/README.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
