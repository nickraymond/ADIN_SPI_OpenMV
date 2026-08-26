#!/usr/bin/env python3
"""RF-DETR gate under the REAL training controller (train_ctl).

Nick's call, 2026-08-25: the gate gets the SAME cockpit as every other
run — Start / Pause / Stop buttons, the progress bar, the thermal
warning, the CPU/GPU time-series — not a bespoke read-only page. This
file is a thin adapter: train_ctl.Ctl supplies the page, the buttons,
the process primitives (SIGSTOP/SIGCONT/SIGTERM), the system sampler
and the plots; the subclass only rebinds what is rfdetr-specific
(spawn command, run dir, Lightning metrics.csv instead of loss.log,
mAP50 history parsed from the gate's own console log).

  ~/nereus_ml/venvs/rfdetr/bin/python ml/rfdetr_gate/gate_ctl.py \
      [--port 8898]

Semantics that differ from train.py runs, reflected in the page hint:
Pause/Resume are exact (SIGSTOP frees the GPU, nothing lost). STOP is
NOT checkpoint-safe mid-epoch — rfdetr checkpoints at epoch ends, so a
stop before the epoch completes discards that epoch's training.
Adopts an already-running detached gate process on launch.
"""
import argparse
import csv
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "yolox_urchin"))
import train_ctl                                     # noqa: E402

RUN_DIR = Path.home() / "nereus_ml" / "runs" / "rfdetr_gate"
LOG = Path.home() / "nereus_ml" / "runs" / "rfdetr_gate_console.log"
GATE = Path(__file__).resolve().parent / "run_gate.py"
VENV_PY = Path.home() / "nereus_ml" / "venvs" / "rfdetr" / "bin" / "python"
# batch 4 × grad-accum 4 = effective 16; 19,997 train imgs → 1,250
# optimizer steps/epoch (Lightning's global_step counts optimizer steps)
STEPS_PER_EPOCH = 1250

PAGE = (train_ctl.PAGE
        .replace("Training control — nereus ML",
                 "RF-DETR gate control — E3")
        .replace("Stop checkpoints within one iteration, then exits.",
                 "STOP mid-epoch discards the epoch (rfdetr checkpoints "
                 "at epoch ends).")
        .replace("(auto-resumes from last.pt when present)",
                 "(restarts the one-epoch gate from the pretrained "
                 "backbone)"))


class GateCtl(train_ctl.Ctl):
    def __init__(self):
        super().__init__(["--run-name", "rfdetr_gate", "--epochs", "1"])
        self.rundir = RUN_DIR

    def _adopt(self):
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", r"run_gate\.py"], text=True).split()
            return int(out[0]) if out else None
        except subprocess.CalledProcessError:
            return None

    def start(self):
        with self.lock:
            if self.pid() is not None:
                raise RuntimeError("already running")
            logf = open(LOG, "a")
            self.proc = subprocess.Popen(
                [str(VENV_PY), "-u", str(GATE)],
                stdout=logf, stderr=logf, start_new_session=True,
                env={"PYTORCH_ENABLE_MPS_FALLBACK": "1",
                     "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                     "HOME": str(Path.home())})
            self.stopping = False

    def _metrics(self):
        p = self.rundir / "metrics.csv"
        if not p.exists():
            return []
        with open(p) as fh:
            return list(csv.DictReader(fh))

    def _cfg(self):
        return {"model": "RF-DETR-Base · DINOv2 backbone",
                "init": "rf-detr-base.pth (pretrained, Apache-2.0)",
                "corpus": "corpus_v2 → COCO (19,997 imgs)"}

    def _gate_scores(self):
        """[(step_or_label, mAP50), ...] from the gate's console log —
        the baseline val, any epoch vals, and the final GATE line."""
        if not LOG.exists():
            return []
        txt = re.sub(r"\x1b\[[0-9;]*m", "", LOG.read_text(errors="replace"))
        out = []
        for m in re.finditer(r"mAP50\s*=\s*([0-9.]+)", txt):
            out.append(float(m.group(1)))
        return out

    def status(self):
        state, _ = self.pstate()
        rows = self._metrics()
        cur = rows[-1] if rows else {}
        step = int(float(cur.get("step", 0) or 0))
        epoch = f"{cur.get('epoch', 0)} / 1 (step {step}/{STEPS_PER_EPOCH})"
        pct = round(100 * min(1.0, step / STEPS_PER_EPOCH), 1)
        now = time.time()
        hist = getattr(self, "_pace_hist", [])
        if not hist or hist[-1][1] != step:
            hist.append((now, step))
            self._pace_hist = hist[-100:]
        pace = "measuring…"
        if len(hist) >= 2 and hist[-1][1] > hist[0][1]:
            spm = ((hist[-1][1] - hist[0][1])
                   / (hist[-1][0] - hist[0][0]) * 60)
            eta_h = (STEPS_PER_EPOCH - step) / spm / 60 if spm else 0
            pace = f"{spm:.1f} steps/min · epoch ETA {eta_h:.1f} h"
        lines = []
        if LOG.exists():
            raw = LOG.read_bytes()[-6000:].decode("utf-8", "replace")
            raw = re.sub(r"\x1b\[[0-9;]*m", "", raw).replace("\r", "\n")
            lines = [ln for ln in raw.split("\n") if ln.strip()][-3:]
        ck = self.rundir / "checkpoint_best_total.pth"
        ckpt = (time.strftime("%H:%M:%S",
                              time.localtime(ck.stat().st_mtime))
                if ck.exists() else "none yet (epoch-end)")
        if self.sysmetrics:
            _, cpu, gpu, tok = self.sysmetrics[-1]
            sysline = (f"CPU {cpu:.0f}% · GPU "
                       f"{gpu if gpu is not None else '?'}% · thermal "
                       f"{'nominal' if tok else 'WARNING (throttling)'}")
        else:
            sysline = "sampling…"
        return {"state": state, "run": "rfdetr_gate (E3 one-epoch)",
                "arch": "rf-detr-base", "epoch": epoch, "pace": pace,
                "pct": pct, "ckpt": ckpt, "log": lines, "sched": "n/a",
                "sys": sysline, **self._cfg()}

    def plot_png(self):
        """Loss from metrics.csv + mAP50 history from the console log,
        same two-panel look as the train.py plot."""
        rows = self._metrics()
        losses = [(int(float(r["step"])), float(v))
                  for r in rows
                  for k, v in r.items()
                  if "loss" in k.lower() and v not in ("", None)
                  and k.endswith("loss")]
        scores = self._gate_scores()
        out = self.rundir / "ctl_plot.png"
        if (out.exists()
                and out.stat().st_mtime > time.time() - 60):
            return out.read_bytes()
        if len(losses) < 2 and not scores:
            return None
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        n_panels = (1 if not scores else 2) if losses else 1
        fig, axes = plt.subplots(n_panels, 1,
                                 figsize=(6.4, 2.0 * n_panels), dpi=110)
        axes = [axes] if n_panels == 1 else list(axes)
        fig.patch.set_facecolor("#1d2126")
        for ax in axes:
            ax.set_facecolor("#1d2126")
            ax.grid(True, color="#2c333a", lw=0.6)
            for s in ax.spines.values():
                s.set_color("#3a434c")
            ax.tick_params(colors="#8fa3b3", labelsize=7)
        if scores:
            axes[0].plot(range(len(scores)), scores, color="#1d4",
                         lw=1.4, marker="o", ms=4)
            for i, v in enumerate(scores):
                axes[0].annotate(f"{v:.3f}", (i, v), xytext=(3, 4),
                                 textcoords="offset points", fontsize=7,
                                 color="#8fa3b3")
            axes[0].set_ylabel("rung-A mAP50", fontsize=8,
                               color="#8fa3b3")
            axes[0].set_xlabel("eval # (baseline → epoch → gate)",
                               fontsize=8, color="#8fa3b3")
            axes[0].set_ylim(0, 1)
            axes[0].axhline(0.658, color="#ca3", lw=1,
                            ls="--", alpha=0.8)
            axes[0].annotate("YOLOX-S e1 bar 0.658", (0, 0.658),
                             xytext=(3, 4), textcoords="offset points",
                             fontsize=7, color="#ca3")
        if losses:
            xs, ys = zip(*losses)
            axes[-1].plot(xs, ys, color="#6cf", lw=1.2)
            axes[-1].set_xlabel("optimizer step", fontsize=8,
                                color="#8fa3b3")
            axes[-1].set_ylabel("loss", fontsize=8, color="#8fa3b3")
        fig.tight_layout(pad=0.5)
        fig.savefig(out, facecolor=fig.get_facecolor())
        plt.close(fig)
        return out.read_bytes()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8898)
    args = ap.parse_args()
    ctl = GateCtl()
    train_ctl.PAGE = PAGE            # the handler serves module PAGE
    threading.Thread(target=ctl._sampler_loop, daemon=True).start()
    # NO scorer loop: the gate scores rung A itself at the end
    srv = ThreadingHTTPServer((args.bind, args.port),
                              train_ctl.make_handler(ctl))
    srv.daemon_threads = True
    for _sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(_sig, lambda *_: __import__("os")._exit(0))
    print(f"gate control on http://localhost:{args.port}/ "
          f"(adopts a running run_gate.py; Ctrl-C stops the PAGE only)",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
