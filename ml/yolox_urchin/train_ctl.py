#!/usr/bin/env python3
"""train_ctl.py -- one-switch browser control for a training run (S8).

Config stays in the TERMINAL (Nick's call): everything after `--` is the
train.py invocation this page controls. The page is a light switch, not
a cockpit -- Start / Pause / Resume / Stop + the night-schedule toggle.

  ~/nereus_ml/venvs/gate/bin/python ml/yolox_urchin/train_ctl.py \
      -- --arch yolox-s --epochs 120 --batch 24 --mosaic 0.75 \
         --run-name stage1_s_labeler --stop-after-hours 8

Controls map to primitives, nothing exotic:
  Start  = spawn train.py (auto-appends --resume <run>/last.pt if it exists)
  Pause  = SIGSTOP (instant freeze, GPU freed, nothing lost)
  Resume = SIGCONT
  Stop   = SIGTERM -> train.py checkpoints at the next iteration and exits
  Night  = launchctl load/unload of com.nereus.train-night (if installed)

Trusted-LAN posture (S25): binds 0.0.0.0 by default so the workbench
guide card's badge can probe it. Stdlib only.
"""
import argparse
import collections
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUNS = Path.home() / "nereus_ml" / "runs" / "stage1_yolox"
TRAIN = Path(__file__).resolve().parent / "train.py"
PLIST = Path.home() / "Library/LaunchAgents/com.nereus.train-night.plist"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>nereus training control</title><style>
 body { background:#14171a; color:#dde3e8; font:14px system-ui;
        max-width:34rem; margin:0 auto; padding:1.2rem; }
 .card { background:#1d2126; border:1px solid #2c333a; border-radius:8px;
         padding:1rem 1.2rem; }
 .row { display:flex; justify-content:space-between; padding:.15rem 0;
        font-size:13px; } .k { color:#8fa3b3; }
 .badge { padding:.2rem .7rem; border-radius:5px; font-weight:700; }
 .RUNNING { background:#1d4; color:#000; } .PAUSED { background:#ca3; color:#000; }
 .STOPPED { background:#555; color:#ddd; } .STOPPING { background:#ca3; color:#000; }
 .SCORING { background:#6cf; color:#000; }
 button { background:#262c33; color:#dde3e8; border:1px solid #3a434c;
          border-radius:5px; padding:.45rem 0; font:inherit; flex:1;
          cursor:pointer; } button:disabled { opacity:.4; cursor:default; }
 #btns { display:flex; gap:.5rem; margin:.9rem 0 .5rem; }
 #bar { height:8px; background:#262c33; border-radius:4px; overflow:hidden;
        margin-top:.8rem; }
 #fill { height:100%; width:0; background:#1d4; transition:width .5s; }
 pre { background:#14171a; border:1px solid #2c333a; border-radius:5px;
       padding:.5rem .7rem; font-size:11px; overflow-x:auto; color:#8fa3b3; }
 .hint { font-size:12px; color:#8fa3b3; margin:.3rem 0 .8rem; }
 .schedrow { display:flex; justify-content:space-between; align-items:center;
             border-top:1px solid #2c333a; padding-top:.8rem; margin-top:.4rem; }
</style></head><body>
<div class="card">
 <div style="display:flex; justify-content:space-between; align-items:center;">
  <div><b>Training control — nereus ML</b><br>
   <span class="k" style="font-size:12px">Mac · MPS · one run at a time</span></div>
  <span id="state" class="badge STOPPED">…</span>
 </div>
 <div style="border-top:1px solid #2c333a; margin-top:.7rem; padding-top:.6rem;">
  <div class="row"><span class="k">Run</span><span id="run">—</span></div>
  <div class="row"><span class="k">Model</span><span id="model">—</span></div>
  <div class="row"><span class="k">Init</span><span id="init">—</span></div>
  <div class="row"><span class="k">Corpus</span><span id="corpus">—</span></div>
  <div class="row"><span class="k">Epoch</span><span id="epoch">—</span></div>
  <div class="row"><span class="k">Pace</span><span id="pace">—</span></div>
  <div class="row"><span class="k">Last checkpoint</span><span id="ckpt">—</span></div>
  <div class="row"><span class="k">System</span><span id="sys">—</span></div>
 </div>
 <div id="bar"><div id="fill"></div></div>
 <div id="btns">
  <button id="b-start" onclick="act('start')">&#9654; Start</button>
  <button id="b-pause" onclick="act('pause')">&#10074;&#10074; Pause</button>
  <button id="b-stop" onclick="act('stop')">&#9632; Stop</button>
 </div>
 <div class="hint" id="hint">Pause freezes instantly (GPU freed, nothing
 lost). Stop checkpoints within one iteration, then exits.</div>
 <div class="schedrow">
  <div>Night schedule<br><span class="k" style="font-size:12px">
   auto-start 23:00, stops after the configured hours</span></div>
  <button id="b-sched" style="flex:0 0 5.5rem" onclick="act('sched')">…</button>
 </div>
 <div style="margin-top:.8rem"><span class="k" style="font-size:12px">System load (CPU · GPU · thermal)</span>
  <img id="sysplot" src="sys.png" style="width:100%; border:1px solid #2c333a;
   border-radius:5px; margin-top:.3rem; display:none;"></div>
 <div style="margin-top:.8rem"><span class="k" style="font-size:12px">Training loss</span>
  <img id="plot" src="plot.png" style="width:100%; border:1px solid #2c333a;
   border-radius:5px; margin-top:.3rem; display:none;"></div>
 <div style="margin-top:.8rem"><span class="k" style="font-size:12px">Log tail</span>
  <pre id="log">—</pre></div>
</div>
<script>
const $=id=>document.getElementById(id);
async function refresh(){
  const s = await (await fetch('api/status')).json();
  $('state').textContent = s.state; $('state').className = 'badge '+s.state;
  $('run').textContent = s.run;
  $('model').textContent = s.model;
  $('init').textContent = s.init;
  $('corpus').textContent = s.corpus;
  $('epoch').textContent = s.epoch;
  $('pace').textContent = s.pace;
  $('ckpt').textContent = s.ckpt;
  $('sys').textContent = s.sys;
  $('fill').style.width = s.pct + '%';
  $('log').innerHTML = s.log.join('<br>');
  $('b-start').disabled = (s.state !== 'STOPPED');
  $('b-pause').disabled = (s.state === 'STOPPED' || s.state === 'STOPPING' || s.state === 'SCORING');
  $('b-pause').innerHTML = (s.state === 'PAUSED') ? '&#9654; Resume' : '&#10074;&#10074; Pause';
  $('b-stop').disabled = (s.state === 'STOPPED' || s.state === 'STOPPING' || s.state === 'SCORING');
  $('b-sched').textContent = s.sched;
  if(s.state==='PAUSED') $('hint').textContent =
    'Frozen — laptop fully yours. Resume continues mid-instruction.';
  else if(s.state==='STOPPING') $('hint').textContent =
    'Stop requested — checkpointing, exits within one iteration.';
  else if(s.state==='SCORING') $('hint').textContent =
    'Periodic rung-A scoring — training frozen for ~10 min, resumes by itself. The mAP panel updates when it finishes.';
  else if(s.state==='RUNNING') $('hint').textContent =
    'Pause freezes instantly (GPU freed, nothing lost). Stop checkpoints within one iteration, then exits.';
  else $('hint').textContent =
    'Start launches the configured run (auto-resumes from last.pt when present).';
}
async function act(a){
  const target = (a==='pause' && $('b-pause').textContent.includes('Resume'))
      ? 'resume' : a;
  const r = await fetch('api/'+target, {method:'POST'});
  if(!r.ok) $('hint').textContent = 'FAILED: ' + await r.text();
  await refresh();
}
refresh(); setInterval(refresh, 2000);
function replot(){ const p=$('plot');
  p.onload = ()=>{ p.style.display='block'; };
  p.src = 'plot.png?t=' + Date.now();
  const q=$('sysplot');
  q.onload = ()=>{ q.style.display='block'; };
  q.src = 'sys.png?t=' + Date.now(); }
replot(); setInterval(replot, 30000);
</script></body></html>"""


class Ctl:
    def __init__(self, train_args):
        self.train_args = train_args
        if "--run-name" not in train_args:
            sys.exit("train args must include --run-name")
        self.run = train_args[train_args.index("--run-name") + 1]
        self.arch = (train_args[train_args.index("--arch") + 1]
                     if "--arch" in train_args else "yolox-nano")
        self.epochs = int(train_args[train_args.index("--epochs") + 1]
                          if "--epochs" in train_args else 40)
        self.rundir = RUNS / self.run
        self.proc = None
        self.stopping = False
        self.scoring = False
        self.score_every = 0
        self.lock = threading.Lock()
        # (ts, cpu%, gpu%, thermal_ok) ring: 2 h at 10 s -- Nick's "is my
        # laptop cooking" panel. Real degC needs root on macOS; the pmset
        # thermal-warning flag is the signal that matters (macOS throttles
        # long before hardware risk).
        self.sysmetrics = collections.deque(maxlen=720)

    def _sampler_loop(self):
        import psutil
        psutil.cpu_percent(None)  # prime the counter
        while True:
            time.sleep(10)
            try:
                cpu = psutil.cpu_percent(None)
                gpu = None
                out = subprocess.run(
                    ["ioreg", "-r", "-d", "1", "-w", "0", "-c",
                     "IOAccelerator"], capture_output=True, text=True)
                m = re.search(r'"Device Utilization %"=(\d+)', out.stdout)
                if m:
                    gpu = int(m.group(1))
                therm = subprocess.run(["pmset", "-g", "therm"],
                                       capture_output=True, text=True).stdout
                therm_ok = "No thermal warning" in therm
                self.sysmetrics.append(
                    (time.time(), cpu, gpu, therm_ok))
            except Exception:
                pass

    def _adopt(self):
        """Find an externally started process for this run."""
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", rf"train\.py .*--run-name {self.run}"],
                text=True).split()
            return int(out[0]) if out else None
        except subprocess.CalledProcessError:
            return None

    def pid(self):
        if self.proc and self.proc.poll() is None:
            return self.proc.pid
        return self._adopt()

    def pstate(self):
        pid = self.pid()
        if pid is None:
            self.stopping = False
            return "STOPPED", None
        if self.scoring:
            return "SCORING", pid
        if self.stopping:
            return "STOPPING", pid
        try:
            st = subprocess.check_output(
                ["ps", "-o", "state=", "-p", str(pid)], text=True).strip()
        except subprocess.CalledProcessError:
            return "STOPPED", None
        return ("PAUSED" if st.startswith("T") else "RUNNING"), pid

    def start(self):
        with self.lock:
            if self.pid() is not None:
                raise RuntimeError("already running")
            cmd = [sys.executable, str(TRAIN)] + self.train_args
            last = self.rundir / "last.pt"
            if last.exists() and "--resume" not in self.train_args:
                cmd += ["--resume", str(last)]
            self.rundir.mkdir(parents=True, exist_ok=True)
            logf = open(self.rundir / "console.log", "a")
            self.proc = subprocess.Popen(cmd, stdout=logf, stderr=logf,
                                         start_new_session=True)
            self.stopping = False

    def signal_run(self, sig):
        pid = self.pid()
        if pid is None:
            raise RuntimeError("no run")
        os.kill(pid, sig)
        if sig == signal.SIGTERM:
            self.stopping = True

    def _cfg(self):
        """What this run actually is: prefer the trainer's own recorded
        config.json (written at launch, cannot drift from reality);
        fall back to the server's configured args before first start."""
        cj = self.rundir / "config.json"
        if cj.exists():
            c = json.loads(cj.read_text())
        else:
            a = self.train_args

            def get(flag, default):
                return a[a.index(flag) + 1] if flag in a else default
            c = {"arch": get("--arch", "yolox-nano"),
                 "stem": get("--stem", "conv"),
                 "pretrained": get("--pretrained", None),
                 "corpus": get("--corpus", "corpus_v1")}
        pre = c.get("pretrained")
        return {"model": f"{c.get('arch')} · stem={c.get('stem', 'conv')}",
                "init": Path(pre).name if pre else "scratch (random)",
                "corpus": c.get("corpus", "corpus_v1")}

    def _cur_epoch(self):
        loss = self.rundir / "loss.log"
        if not loss.exists():
            return None
        lines = loss.read_text().splitlines()
        if not lines:
            return None
        m = re.match(r"e(\d+)", lines[-1])
        return int(m.group(1)) if m else None

    def _score_ckpt(self, ep):
        """Score last.pt on rung A, append [ep, mAP50] to the shared
        scores file. Returns True on success."""
        last = self.rundir / "last.pt"
        cfg = self._cfg()
        arch = cfg["model"].split(" ")[0]
        stem = cfg["model"].split("stem=")[1]
        print(f"[scorer] scoring {last} ({arch}/{stem}) as epoch {ep}")
        out = subprocess.run(
            [sys.executable,
             str(Path(__file__).resolve().parent / "eval_rung_a.py"),
             str(last), "--arch", arch, "--stem", stem],
            capture_output=True, text=True, timeout=3600)
        m = re.search(r"mAP50=([0-9.]+)", out.stdout)
        if not m:
            print("[scorer] no mAP in eval output")
            return False
        map50 = float(m.group(1))
        sj = RUNS / "rung_a_scores.json"
        d = json.loads(sj.read_text()) if sj.exists() else {}
        pts = [p for p in d.get(self.run, []) if p[0] != ep]
        d[self.run] = sorted(pts + [[ep, map50]])
        sj.write_text(json.dumps(d, indent=2))
        (self.rundir / "ctl_plot.png").unlink(missing_ok=True)
        print(f"[scorer] {self.run} e{ep}: mAP50={map50}")
        return True

    def _last_scored(self):
        sj = RUNS / "rung_a_scores.json"
        if not sj.exists():
            return None
        pts = json.loads(sj.read_text()).get(self.run, [])
        return max(p[0] for p in pts) if pts else None

    def _scorer_loop(self):
        """Two triggers, one thread. (1) Session end: STOPPED + a
        checkpoint newer than last scored -> score on the freed GPU.
        (2) Periodic (--score-every N): every N completed epochs, freeze
        the run (SIGSTOP), score on the freed GPU (~10 min), resume --
        the epoch-cadence view Nick asked for, at a known training-time
        cost. Never hijacks a user-initiated PAUSED."""
        marker = self.rundir / ".scored_mtime"
        while True:
            time.sleep(30)
            try:
                state, pid = self.pstate()
                last = self.rundir / "last.pt"
                if not last.exists():
                    continue
                if state == "STOPPED":
                    mt = str(int(last.stat().st_mtime))
                    if marker.exists() and marker.read_text() == mt:
                        continue
                    ep = self._cur_epoch() or 0
                    if self._score_ckpt(ep):
                        marker.write_text(mt)
                elif state == "RUNNING" and self.score_every:
                    cur = self._cur_epoch()
                    if cur is None or cur < 1:
                        continue
                    done = cur - 1  # last completed epoch = the checkpoint
                    prev = self._last_scored()
                    nxt = (self.score_every if prev is None else
                           self.score_every * (prev // self.score_every + 1))
                    if done < nxt:
                        continue
                    self.scoring = True
                    os.kill(pid, signal.SIGSTOP)
                    try:
                        self._score_ckpt(done)
                    finally:
                        os.kill(pid, signal.SIGCONT)
                        self.scoring = False
            except Exception as e:
                self.scoring = False
                print(f"[scorer] error (will retry): {e}")

    def plot_png(self):
        """Loss-vs-iteration PNG for THIS run; regenerated at most every
        60 s and only when loss.log has grown."""
        loss = self.rundir / "loss.log"
        if not loss.exists():
            return None
        out = self.rundir / "ctl_plot.png"
        if (out.exists() and out.stat().st_mtime > time.time() - 60
                and out.stat().st_mtime > loss.stat().st_mtime - 1):
            return out.read_bytes()
        import re
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # x = log order, not the iteration counter: the counter restarts
        # on resume, so a multi-session log would double back on itself
        ys = []
        for line in open(loss):
            m = re.search(r"loss=([0-9.]+)", line)
            if m:
                ys.append(float(m.group(1)))
        if len(ys) < 2:
            return None
        step = max(1, len(ys) // 500)
        ys = ys[::step]
        xs = [i * step * 20 for i in range(len(ys))]  # ~20 iters per line

        sj = RUNS / "rung_a_scores.json"
        scores = (json.loads(sj.read_text()).get(self.run, [])
                  if sj.exists() else [])

        n_panels = 2 if scores else 1
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
            axes[0].set_ylabel("rung-A mAP50", fontsize=8, color="#8fa3b3")
            axes[0].set_xlabel("epoch", fontsize=8, color="#8fa3b3")
            axes[0].set_ylim(0, 1)
        axes[-1].plot(xs, ys, color="#6cf", lw=1.2)
        axes[-1].set_xlabel("iterations trained (all sessions)", fontsize=8,
                            color="#8fa3b3")
        axes[-1].set_ylabel("loss", fontsize=8, color="#8fa3b3")
        axes[-1].set_ylim(bottom=0)
        fig.tight_layout(pad=0.5)
        fig.savefig(out, facecolor=fig.get_facecolor())
        plt.close(fig)
        return out.read_bytes()

    def sys_png(self):
        """CPU/GPU load + thermal state over the last ~2 h."""
        if len(self.sysmetrics) < 2:
            return None
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        now = time.time()
        pts = list(self.sysmetrics)
        mins = [(t - now) / 60 for t, *_ in pts]
        cpu = [p[1] for p in pts]
        gpu = [p[2] if p[2] is not None else float("nan") for p in pts]
        warn = [(m, 1) for m, p in zip(mins, pts) if not p[3]]
        fig, ax = plt.subplots(figsize=(6.4, 2.0), dpi=110)
        fig.patch.set_facecolor("#1d2126")
        ax.set_facecolor("#1d2126")
        ax.grid(True, color="#2c333a", lw=0.6)
        for s in ax.spines.values():
            s.set_color("#3a434c")
        ax.tick_params(colors="#8fa3b3", labelsize=7)
        ax.plot(mins, cpu, color="#6cf", lw=1.1, label="CPU %")
        ax.plot(mins, gpu, color="#1d4", lw=1.1, label="GPU %")
        if warn:
            for m, _ in warn:
                ax.axvline(m, color="#e44", lw=1.5, alpha=0.6)
            ax.plot([], [], color="#e44", lw=1.5,
                    label="thermal throttle")
        ax.set_ylim(0, 105)
        ax.set_xlabel("minutes ago", fontsize=8, color="#8fa3b3")
        ax.legend(fontsize=7, loc="lower left", facecolor="#1d2126",
                  edgecolor="#3a434c", labelcolor="#8fa3b3")
        fig.tight_layout(pad=0.4)
        import io as _io
        buf = _io.BytesIO()
        fig.savefig(buf, facecolor=fig.get_facecolor())
        plt.close(fig)
        return buf.getvalue()

    def status(self):
        state, _ = self.pstate()
        loss = self.rundir / "loss.log"
        epoch, pace, lines = "—", "—", []
        pct = 0
        if loss.exists():
            lines = loss.read_text().splitlines()[-3:]
            if lines:
                import re
                m = re.match(r"e(\d+) i(\d+)/(\d+).*?=\s*([0-9.]+) it/s",
                             lines[-1])
                if m:
                    e, i, per = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    epoch = f"{e} / {self.epochs}"
                    pace = f"{m.group(4)} it/s"
                    pct = round(100 * (e + i / per) / self.epochs, 1)
        last = self.rundir / "last.pt"
        ckpt = (time.strftime("%H:%M:%S", time.localtime(last.stat().st_mtime))
                if last.exists() else "none yet")
        try:
            sched = "On" if subprocess.run(
                ["launchctl", "list", "com.nereus.train-night"],
                capture_output=True).returncode == 0 else "Off"
        except FileNotFoundError:
            sched = "n/a"
        if self.sysmetrics:
            _, cpu, gpu, tok = self.sysmetrics[-1]
            sysline = (f"CPU {cpu:.0f}% · GPU "
                       f"{gpu if gpu is not None else '?'}% · thermal "
                       f"{'nominal' if tok else 'WARNING (throttling)'}")
        else:
            sysline = "sampling…"
        return {"state": state, "run": self.run, "arch": self.arch,
                "epoch": epoch, "pace": pace, "pct": pct, "ckpt": ckpt,
                "log": lines, "sched": sched, "sys": sysline,
                **self._cfg()}

    def sched_toggle(self):
        if not PLIST.exists():
            raise RuntimeError(
                "LaunchAgent not installed -- see ml/yolox_urchin/README.md")
        on = subprocess.run(["launchctl", "list", "com.nereus.train-night"],
                            capture_output=True).returncode == 0
        subprocess.run(["launchctl", "unload" if on else "load", str(PLIST)],
                       check=True)


def make_handler(ctl):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            body = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html")
            elif self.path == "/api/status":
                self._send(200, json.dumps(ctl.status()))
            elif self.path.startswith("/plot.png"):
                png = ctl.plot_png()
                if png:
                    self._send(200, png, "image/png")
                else:
                    self._send(404, "no data yet", "text/plain")
            elif self.path.startswith("/sys.png"):
                png = ctl.sys_png()
                if png:
                    self._send(200, png, "image/png")
                else:
                    self._send(404, "sampling", "text/plain")
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            try:
                if self.path == "/api/start":
                    ctl.start()
                elif self.path == "/api/pause":
                    ctl.signal_run(signal.SIGSTOP)
                elif self.path == "/api/resume":
                    ctl.signal_run(signal.SIGCONT)
                elif self.path == "/api/stop":
                    ctl.signal_run(signal.SIGTERM)
                elif self.path == "/api/sched":
                    ctl.sched_toggle()
                else:
                    return self._send(404, "not found", "text/plain")
                self._send(200, "{}")
            except Exception as e:
                self._send(409, str(e), "text/plain")
    return H


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8898)
    ap.add_argument("--score-every", type=int, default=0,
                    help="score rung A every N completed epochs (freezes "
                         "training ~10 min per score); 0 = session "
                         "boundaries only")
    ap.add_argument("train_args", nargs=argparse.REMAINDER,
                    help="-- then train.py args (must include --run-name)")
    args = ap.parse_args(argv)
    targs = args.train_args
    if targs and targs[0] == "--":
        targs = targs[1:]
    ctl = Ctl(targs)
    ctl.score_every = args.score_every
    threading.Thread(target=ctl._scorer_loop, daemon=True).start()
    threading.Thread(target=ctl._sampler_loop, daemon=True).start()
    srv = ThreadingHTTPServer((args.bind, args.port), make_handler(ctl))
    # The browser polls every 2 s; without daemon threads a live request
    # thread outlives Ctrl-C and the dead-looking process squats on the
    # port (Errno 48 on relaunch -- bitten live 2026-08-23).
    srv.daemon_threads = True
    # The page holds NO state worth flushing and the training child runs
    # in its own session -- so on SIGINT/SIGTERM, exit immediately and
    # unconditionally. Guarantees the port is released.
    for _sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(_sig, lambda *_: os._exit(0))
    print(f"training control for run '{ctl.run}' on "
          f"http://localhost:{args.port}/  (Ctrl-C to stop the PAGE; "
          f"the training run itself is only stopped by its Stop button)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
