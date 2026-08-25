#!/usr/bin/env python3
"""Read-only monitor for the RF-DETR gate run (E3) — Nick's watch page.

train_ctl.py is the repo's training UI, but it OWNS the process it
spawns; the gate run is already detached. This page only reads: the
Lightning metrics.csv (epoch/step/lr/loss/val columns as they appear),
the console log tail, and the process state. Same visual language as
train_ctl so it reads as the familiar cockpit, minus the switches.

  ~/nereus_ml/venvs/rfdetr/bin/python ml/rfdetr_gate/gate_monitor.py \
      [--run ~/nereus_ml/runs/rfdetr_gate] [--port 8893]

Trusted-LAN posture (S25): binds 0.0.0.0. Stdlib only.
"""
import argparse
import csv
import json
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUN = Path.home() / "nereus_ml" / "runs" / "rfdetr_gate"
LOG = Path.home() / "nereus_ml" / "runs" / "rfdetr_gate_console.log"
_hist = []                     # (wall_ts, step) samples for pace


def proc_state():
    try:
        out = subprocess.run(["pgrep", "-f", "run_gate.py"],
                             capture_output=True, text=True)
        return "RUNNING" if out.stdout.strip() else "STOPPED"
    except Exception:
        return "?"


def read_metrics(run):
    rows = []
    p = run / "metrics.csv"
    if not p.exists():
        return rows
    with open(p) as fh:
        for r in csv.DictReader(fh):
            rows.append({k: v for k, v in r.items() if v not in ("", None)})
    return rows


def log_tail(n=14):
    if not LOG.exists():
        return []
    raw = LOG.read_bytes()[-20000:].decode("utf-8", "replace")
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw).replace("\r", "\n")
    lines = [ln.rstrip() for ln in raw.split("\n") if ln.strip()]
    return lines[-n:]


def gate_lines():
    """mAP50 / verdict lines from the whole log — the history Nick
    asked to watch over time."""
    if not LOG.exists():
        return []
    raw = re.sub(r"\x1b\[[0-9;]*m", "",
                 LOG.read_text(errors="replace"))
    return [ln.strip() for ln in raw.split("\n")
            if re.search(r"GATE|TRAIN WALL|mAP50=|\[eval\]", ln)][-12:]


def status(run):
    rows = read_metrics(run)
    cur = rows[-1] if rows else {}
    step = int(float(cur.get("step", 0))) if cur else 0
    now = time.time()
    if not _hist or _hist[-1][1] != step:
        _hist.append((now, step))
        del _hist[:-100]
    pace = None
    if len(_hist) >= 2:
        (t0, s0), (t1, s1) = _hist[0], _hist[-1]
        if t1 > t0 and s1 > s0:
            pace = (s1 - s0) / (t1 - t0) * 60          # steps/min
    # val metric columns (appear once the epoch validates)
    vals = [{k: v for k, v in r.items()
             if k.startswith("val") or "map" in k.lower()
             or k in ("epoch", "step")}
            for r in rows if any(k.startswith("val") or "map" in k.lower()
                                 for k in r)]
    losses = [k for k in cur if "loss" in k.lower()]
    return {"state": proc_state(),
            "epoch": cur.get("epoch", "—"), "step": step,
            "lr": cur.get("train/lr", "—"),
            "loss": {k: cur[k] for k in losses},
            "pace_spm": round(pace, 1) if pace else None,
            "eta_h_1250": (round((1250 - step) / pace / 60, 1)
                           if pace and step < 1250 else None),
            "eta_h_5000": (round((5000 - step) / pace / 60, 1)
                           if pace and step < 5000 else None),
            "val_rows": vals[-6:],
            "gate": gate_lines(), "tail": log_tail(),
            "ts": time.strftime("%H:%M:%S")}


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>RF-DETR gate monitor</title><style>
 body { background:#14171a; color:#dde3e8; font:14px system-ui;
        max-width:44rem; margin:0 auto; padding:1.2rem; }
 .card { background:#1d2126; border:1px solid #2c333a; border-radius:8px;
         padding:1rem 1.2rem; margin-bottom:.8rem; }
 .row { display:flex; justify-content:space-between; padding:.15rem 0;
        font-size:13px; } .k { color:#8fa3b3; }
 .badge { padding:.2rem .7rem; border-radius:5px; font-weight:700; }
 .RUNNING { background:#1d4; color:#000; }
 .STOPPED { background:#a33; color:#fff; }
 pre { background:#14171a; border:1px solid #2c333a; border-radius:5px;
       padding:.5rem .7rem; font-size:11px; overflow-x:auto;
       white-space:pre-wrap; }
 h1 { font-size:1.1rem; } h2 { font-size:.85rem; color:#8fa3b3;
      text-transform:uppercase; letter-spacing:.08em; }
 td { padding:.15rem .7rem .15rem 0; font-size:12px;
      font-variant-numeric:tabular-nums; }
</style></head><body>
<h1>RF-DETR one-epoch gate <span id="state" class="badge">…</span></h1>
<div class="card">
 <div class="row"><span class="k">Epoch</span><span id="epoch">—</span></div>
 <div class="row"><span class="k">Optimizer step</span><span id="step">—</span></div>
 <div class="row"><span class="k">Pace</span><span id="pace">measuring…</span></div>
 <div class="row"><span class="k">ETA (if 1250 steps/epoch)</span><span id="eta1">—</span></div>
 <div class="row"><span class="k">ETA (if 5000 steps/epoch)</span><span id="eta5">—</span></div>
 <div class="row"><span class="k">LR</span><span id="lr">—</span></div>
 <div class="row"><span class="k">Loss</span><span id="loss">—</span></div>
 <div class="row"><span class="k">Updated</span><span id="ts">—</span></div>
</div>
<div class="card"><h2>mAP50 / gate history</h2><pre id="gate">—</pre>
<div id="valtbl"></div></div>
<div class="card"><h2>console tail</h2><pre id="tail">—</pre></div>
<p class="k" style="font-size:12px">Reference bars: YOLOX-S labeler
0.658 @ e1 · 0.800 final. Read-only — the run is a detached process;
stop it with <code>pkill -f run_gate.py</code> in a terminal.</p>
<script>
const $=id=>document.getElementById(id);
async function poll(){
 let s; try{ s=await (await fetch('/api/status')).json(); }
 catch(e){ setTimeout(poll,5000); return; }
 $('state').textContent=s.state; $('state').className='badge '+s.state;
 $('epoch').textContent=s.epoch; $('step').textContent=s.step;
 $('pace').textContent=s.pace_spm? s.pace_spm+' steps/min':'measuring…';
 $('eta1').textContent=s.eta_h_1250? s.eta_h_1250+' h':'—';
 $('eta5').textContent=s.eta_h_5000? s.eta_h_5000+' h':'—';
 $('lr').textContent=(+s.lr).toExponential? (+s.lr).toExponential(2):s.lr;
 $('loss').textContent=Object.entries(s.loss).map(
   ([k,v])=>k.replace('train/','')+' '+(+v).toFixed(3)).join('  ')||'—';
 $('ts').textContent=s.ts;
 $('gate').textContent=(s.gate||[]).join('\\n')||'(no scores yet)';
 $('tail').textContent=(s.tail||[]).join('\\n');
 if(s.val_rows && s.val_rows.length){
  $('valtbl').innerHTML='<table>'+s.val_rows.map(r=>
   '<tr>'+Object.entries(r).map(([k,v])=>'<td>'+k+'</td><td>'+
   (+v).toFixed? (+v).toFixed(4):v+'</td>').join('')+'</tr>').join('')+
   '</table>';
 }
 setTimeout(poll,5000);
}
poll();
</script></body></html>"""


def make_handler(run):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/api/status":
                body = json.dumps(status(run)).encode()
                ctype = "application/json"
            else:
                body = PAGE.encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", default=str(RUN))
    ap.add_argument("--port", type=int, default=8893)
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.bind, args.port),
                              make_handler(Path(args.run).expanduser()))
    print(f"gate monitor on http://{args.bind}:{args.port}/ "
          f"(run: {args.run})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
