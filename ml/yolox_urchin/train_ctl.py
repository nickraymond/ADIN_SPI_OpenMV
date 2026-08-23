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
import json
import os
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
  <div class="row"><span class="k">Epoch</span><span id="epoch">—</span></div>
  <div class="row"><span class="k">Pace</span><span id="pace">—</span></div>
  <div class="row"><span class="k">Last checkpoint</span><span id="ckpt">—</span></div>
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
 <div style="margin-top:.8rem"><span class="k" style="font-size:12px">Log tail</span>
  <pre id="log">—</pre></div>
</div>
<script>
const $=id=>document.getElementById(id);
async function refresh(){
  const s = await (await fetch('api/status')).json();
  $('state').textContent = s.state; $('state').className = 'badge '+s.state;
  $('run').textContent = s.run + (s.arch ? ' · '+s.arch : '');
  $('epoch').textContent = s.epoch;
  $('pace').textContent = s.pace;
  $('ckpt').textContent = s.ckpt;
  $('fill').style.width = s.pct + '%';
  $('log').innerHTML = s.log.join('<br>');
  $('b-start').disabled = (s.state !== 'STOPPED');
  $('b-pause').disabled = (s.state === 'STOPPED' || s.state === 'STOPPING');
  $('b-pause').innerHTML = (s.state === 'PAUSED') ? '&#9654; Resume' : '&#10074;&#10074; Pause';
  $('b-stop').disabled = (s.state === 'STOPPED' || s.state === 'STOPPING');
  $('b-sched').textContent = s.sched;
  if(s.state==='PAUSED') $('hint').textContent =
    'Frozen — laptop fully yours. Resume continues mid-instruction.';
  else if(s.state==='STOPPING') $('hint').textContent =
    'Stop requested — checkpointing, exits within one iteration.';
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
        self.lock = threading.Lock()

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
        return {"state": state, "run": self.run, "arch": self.arch,
                "epoch": epoch, "pace": pace, "pct": pct, "ckpt": ckpt,
                "log": lines, "sched": sched}

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
    ap.add_argument("train_args", nargs=argparse.REMAINDER,
                    help="-- then train.py args (must include --run-name)")
    args = ap.parse_args(argv)
    targs = args.train_args
    if targs and targs[0] == "--":
        targs = targs[1:]
    ctl = Ctl(targs)
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
