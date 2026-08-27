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
import os
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
        self.maint_every = 0          # score+recycle every N epochs

    def pstate(self):
        # during maintenance the training process is legitimately gone;
        # without this the page would flash STOPPED mid-recycle
        if self.scoring:
            return "SCORING", self.pid()
        return super().pstate()

    def _scored_epochs(self):
        p = self.rundir / "rung_a_scores.jsonl"
        if not p.exists():
            return set()
        out = set()
        for ln in open(p):
            try:
                out.add(int(__import__("json").loads(ln)["epoch"]))
            except (ValueError, KeyError):
                pass
        return out

    def _maint_loop(self):
        """Nick's cadence (2026-08-26): every maint_every-th epoch,
        STOP the run (full process exit — this IS the daily memory-creep
        recycle), score the checkpoint onto the plot, START fresh
        (auto-resume). Never touches a user PAUSED run. A swapped
        process can take minutes to honor SIGTERM — wait, don't kill."""
        while True:
            time.sleep(60)
            try:
                state, pid = super().pstate()
                if state != "RUNNING" or not self.maint_every:
                    continue
                mc = self.rundir / "metrics.csv"
                if not mc.exists():
                    continue
                last = [ln for ln in mc.read_text().splitlines()
                        if ln.strip()][-1]
                ep = int(float(last.split(",")[0]))
                if (ep < self.maint_every
                        or ep % self.maint_every
                        or ep in self._scored_epochs()):
                    continue
                print(f"[maint] epoch {ep}: stop → score → recycle",
                      flush=True)
                self.scoring = True
                os.kill(pid, signal.SIGTERM)
                for _ in range(90):            # ≤15 min for swapped exit
                    time.sleep(10)
                    if super().pstate()[0] == "STOPPED":
                        break
                else:
                    print("[maint] process would not exit — leaving it",
                          flush=True)
                    self.scoring = False
                    continue
                out = subprocess.run(
                    [str(VENV_PY), "-u", str(GATE), "--skip-train"],
                    capture_output=True, text=True, timeout=3600,
                    env={"PYTORCH_ENABLE_MPS_FALLBACK": "1",
                         "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                         "HOME": str(Path.home())})
                m = re.search(r"mAP50 = ([0-9.]+)", out.stdout)
                print(f"[maint] scored: "
                      f"{m.group(1) if m else 'NO SCORE — see log'}",
                      flush=True)
                (self.rundir / "ctl_plot.png").unlink(missing_ok=True)
                self.scoring = False
                self.start()                   # fresh process, resumes
                print("[maint] recycled + resumed", flush=True)
            except Exception as e:
                self.scoring = False
                print(f"[maint] error (will retry): {e}", flush=True)

    def _adopt(self):
        """Find the REAL trainer. pgrep -f matches any command line
        containing the text — including diagnostic shells that merely
        mention run_gate.py. That phantom ate a SIGTERM on 2026-08-26
        (the 'recycle' hit a shell; the 24 h trainer lived on). Verify
        each candidate's executable via ps before trusting it."""
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", r"run_gate\.py"], text=True).split()
        except subprocess.CalledProcessError:
            return None
        for pid in out:
            try:
                cmd = subprocess.check_output(
                    ["ps", "-o", "command=", "-p", pid], text=True)
            except subprocess.CalledProcessError:
                continue
            if cmd.strip().startswith(str(VENV_PY)):
                return int(pid)
        return None

    def start(self):
        # staged training (Nick 2026-08-25 night): Start spawns the
        # LEAN config (batch 2 — batch 4 swap-thrashed this Mac) toward
        # self.epochs, auto-resuming from checkpoint.pth via run_gate's
        # default. Stop → restart later loses only the partial epoch.
        with self.lock:
            if self.pid() is not None:
                raise RuntimeError("already running")
            logf = open(LOG, "a")
            self.proc = subprocess.Popen(
                [str(VENV_PY), "-u", str(GATE),
                 "--epochs", str(self.epochs),
                 "--batch", "2", "--grad-accum", "8"],
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
        """[(epoch, mAP50), ...] from the durable scores file (every
        scorecard run appends there; stdout/console parsing was the
        2026-08-26 gap — a manual score never reached the log)."""
        p = self.rundir / "rung_a_scores.jsonl"
        if not p.exists():
            return []
        pts = {}
        for ln in open(p):
            try:
                r = __import__("json").loads(ln)
                pts[int(r["epoch"])] = float(r["map50"])
            except (ValueError, KeyError):
                continue
        return sorted(pts.items())

    def status(self):
        state, _ = self.pstate()
        rows = self._metrics()
        cur = rows[-1] if rows else {}
        step = int(float(cur.get("step", 0) or 0))
        ep_now = cur.get("epoch", 0)
        total = STEPS_PER_EPOCH * self.epochs
        epoch = (f"{ep_now} / {self.epochs} "
                 f"(step {step % STEPS_PER_EPOCH}/{STEPS_PER_EPOCH})")
        pct = round(100 * min(1.0, step / total), 1)
        now = time.time()
        hist = getattr(self, "_pace_hist", [])
        if not hist or hist[-1][1] != step:
            hist.append((now, step))
            self._pace_hist = hist[-100:]
        pace = "measuring…"
        if len(hist) >= 2 and hist[-1][1] > hist[0][1]:
            spm = ((hist[-1][1] - hist[0][1])
                   / (hist[-1][0] - hist[0][0]) * 60)
            rem = STEPS_PER_EPOCH - (step % STEPS_PER_EPOCH)
            pace = (f"{spm:.1f} steps/min · this epoch ETA "
                    f"{rem / spm / 60:.1f} h" if spm else "measuring…")
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
            sx, sy = zip(*scores)
            axes[0].plot(sx, sy, color="#1d4", lw=1.4, marker="o", ms=4)
            for e, v in scores:
                axes[0].annotate(f"{v:.3f}", (e, v), xytext=(3, 4),
                                 textcoords="offset points", fontsize=7,
                                 color="#8fa3b3")
            axes[0].set_ylabel("rung-A mAP50", fontsize=8,
                               color="#8fa3b3")
            axes[0].set_xlabel("epoch", fontsize=8, color="#8fa3b3")
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
    ap.add_argument("--epochs", type=int, default=30,
                    help="target the Start button trains toward "
                         "(staged: Stop/Start resumes by checkpoint)")
    ap.add_argument("--score-every", type=int, default=5,
                    help="every N epochs: stop, score rung-A onto the "
                         "plot, recycle the process (the memory-creep "
                         "cleanup), resume; 0 = manual only")
    args = ap.parse_args()
    ctl = GateCtl()
    ctl.epochs = args.epochs
    ctl.maint_every = args.score_every
    threading.Thread(target=ctl._maint_loop, daemon=True).start()
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
