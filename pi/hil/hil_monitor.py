#!/usr/bin/env python3
"""Live HIL monitor (S8 bite E4) — watch the run, review the boards.

Serves one page (default :8092, trusted-LAN posture like the workbench:
bind 0.0.0.0, loud banner, no auth — view it from the Mac, NEVER a
browser on the Pi) showing, live during a harness run:

  - the current still with Nick's GT boxes (green) and each board's
    detections mapped back through that board's homography (one colour
    per board) — "where are the boxes being inferred"
  - per board: the latest camera JPEG the board shipped, with its
    detections drawn in CAMERA pixels (no homography needed — this is
    the truthful "what is this camera reporting" view), plus status,
    timings and barrier state
  - review controls (review mode): Next still / Auto / Pause / grab a
    camera frame — POSTs land on a queue the harness feeds into the
    Conductor, so the human is a first-class barrier input, not a hack

The harness owns all state; this module only holds a thread-safe copy
and serves it. No numpy, no PIL — box drawing is done by the browser.
"""
import json
import os
import queue
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REVIEW_ACTIONS = ("next", "auto", "pause", "resume", "jpeg", "jpeg_all",
                  "abort")


class Monitor:
    def __init__(self, playback_port=8091, still_dir=None):
        # still_dir: the stills' frames/ directory. The page fetches the
        # source still SAME-ORIGIN (/still/<name>) — a cross-port fetch
        # to the playback server gets blocked by some browser policies
        # (measured: ERR_BLOCKED_BY_CLIENT on the first page shakeout).
        self.still_dir = still_dir
        self.lock = threading.Lock()
        self.state = {"playback_port": playback_port, "boards": {},
                      "still": None, "run": {}}
        self.cams = {}                 # label -> (jpeg bytes, ts)
        self.events = deque(maxlen=40)
        self.review_q = queue.Queue()
        self.srv = None

    # ---- harness-side feeders -----------------------------------------
    def set_run(self, run_dict):
        with self.lock:
            self.state["run"] = run_dict

    def set_still(self, still_dict):
        """{"name","url_path","gt":[[x1,y1,x2,y2] fractions], "index"}"""
        with self.lock:
            self.state["still"] = still_dict

    def set_board(self, label, **kw):
        with self.lock:
            self.state["boards"].setdefault(label, {}).update(kw)

    def set_cam(self, label, jpg_bytes):
        with self.lock:
            self.cams[label] = (jpg_bytes, time.time())

    def log(self, msg):
        with self.lock:
            self.events.append(f"{time.strftime('%H:%M:%S')} {msg}")

    def snapshot(self):
        with self.lock:
            # default=float: a stray numpy scalar in fed state must
            # degrade to a number, not kill the page (it did, on the
            # first live run — every /api/monitor died empty)
            out = json.loads(json.dumps(self.state, default=float))
            out["events"] = list(self.events)
            out["cam_ts"] = {lb: ts for lb, (_j, ts) in self.cams.items()}
            return out

    # ---- server lifecycle ---------------------------------------------
    def start(self, port=8092, bind="0.0.0.0"):
        self.srv = ThreadingHTTPServer((bind, port), _make_handler(self))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return port

    def stop(self):
        if self.srv is not None:
            self.srv.shutdown()


def _make_handler(mon):

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                self._get()
            except Exception as e:      # degrade LOUDLY, never an empty
                try:                    # reply (the first live run's bug)
                    self._json({"error": f"monitor: {e!r}"}, 500)
                except Exception:
                    pass

        def _get(self):
            if self.path == "/" or self.path.startswith("/?"):
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type",
                                 "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/monitor":
                self._json(mon.snapshot())
            elif self.path.startswith("/still/"):
                name = self.path[len("/still/"):].split("?")[0]
                base = mon.still_dir
                if base is None:
                    return self._json({"error": "no still_dir"}, 404)
                path = os.path.realpath(os.path.join(base, name))
                if not (path.startswith(os.path.realpath(base) + os.sep)
                        and os.path.isfile(path)):
                    return self._json({"error": "no such still"}, 404)
                with open(path, "rb") as fh:
                    jpg = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.end_headers()
                self.wfile.write(jpg)
            elif self.path.startswith("/cam/"):
                label = self.path[len("/cam/"):].split(".")[0].split("?")[0]
                with mon.lock:
                    ent = mon.cams.get(label)
                if ent is None:
                    return self._json({"error": "no camera frame yet"}, 404)
                jpg = ent[0]
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(jpg)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/api/review":
                return self._json({"error": "not found"}, 404)
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                action = req["action"]
            except (ValueError, KeyError, json.JSONDecodeError):
                return self._json({"error": "bad body"}, 400)
            if action not in REVIEW_ACTIONS:
                return self._json(
                    {"error": f"action must be one of {REVIEW_ACTIONS}"},
                    400)
            mon.review_q.put((action, req.get("board")))
            self._json({"ok": True})

    return Handler


# One colour per board on the still view; GT is always green.
BOARD_COLORS = ["#ffd400", "#40c4ff", "#ff70d0", "#b0ff60"]

PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>HIL monitor</title><style>
 body{margin:0;background:#111;color:#cde;font:13px/1.45 monospace}
 h1{font-size:15px;margin:8px 12px;color:#8fb}
 #bar{margin:0 12px 8px;color:#9ab}
 .wrap{display:flex;flex-wrap:wrap;gap:12px;margin:0 12px 12px}
 .panel{background:#181c20;border:1px solid #2a3138;border-radius:6px;
        padding:8px;flex:1 1 420px;min-width:380px}
 .imgbox{position:relative;width:100%%}
 .imgbox img{width:100%%;display:block;border-radius:3px}
 .bx{position:absolute;border:2px solid;pointer-events:none;
     font-size:10px;color:#fff}
 .bx span{background:rgba(0,0,0,.55);padding:0 2px}
 button{background:#24303a;color:#cde;border:1px solid #3a4a58;
        border-radius:4px;padding:6px 14px;margin-right:6px;cursor:pointer}
 button:hover{background:#31424f}
 #hold{color:#ffd400;font-weight:bold}
 .dead{color:#f66;font-weight:bold}
 pre{background:#14171a;padding:6px;border-radius:4px;max-height:150px;
     overflow-y:auto;white-space:pre-wrap}
 /* click-to-enlarge (E5 polish): the clicked panel goes fullscreen but
    keeps its DOM, so boxes and the live poll keep updating inside it */
 .imgbox{cursor:zoom-in}
 .panel.zoom{position:fixed;inset:0;z-index:50;margin:0;border-radius:0;
     overflow:auto;background:#0d1013;display:flex;flex-direction:column}
 .panel.zoom .imgbox{cursor:zoom-out;margin:auto;width:96vw;
     max-width:170vh}
</style></head><body>
<h1>HIL closed-loop monitor</h1>
<div id="bar">connecting&hellip;</div>
<div style="margin:0 12px 10px">
 <button onclick="rv('pause')">Pause</button>
 <button onclick="rv('resume')">Resume</button>
 <button onclick="rv('next')">Next still &rarr;</button>
 <button onclick="rv('auto')">Switch to AUTO</button>
 <button style="border-color:#a44"
  onclick="if(confirm('End the run and score what was collected?'))rv('abort')">
  Abort run</button>
 <span id="hold"></span>
</div>
<div id="summary" class="panel"
     style="display:none;margin:0 12px 10px"></div>
<div class="wrap">
 <div class="panel" style="flex:2 1 560px">
  <div>still <span id="stname">&mdash;</span>
   &nbsp; <span style="color:#3c5">GT green</span>,
   detections per board coloured</div>
  <div id="layerbar" style="margin:.25rem 0">
   <span id="layerchecks"></span>
   &nbsp; GT &ge;<input id="pxthr" type="number" value="30" min="0"
    max="200" step="1" style="width:3.5em;background:#1d2126;color:#cde;
    border:1px solid #3a4a58;border-radius:3px"
    onchange="pxThr=+this.value">px
   in <select id="pxterms" style="background:#1d2126;color:#cde;
    border:1px solid #3a4a58;border-radius:3px"
    onchange="pxTerms=this.value"></select> terms
   <label><input type="checkbox" id="pxhide"
    onchange="pxHide=this.checked"> hide smaller GT</label>
   <span class="muted" style="color:#8fa3b3">(dashed = below
    threshold)</span>
  </div>
  <div class="imgbox" id="stillbox" onclick="zoom(this)"
       title="click to enlarge"><img id="still"></div>
 </div>
 <div id="boards" style="display:contents"></div>
</div>
<div class="panel" style="margin:0 12px 12px"><pre id="log"></pre></div>
<script>
const COLORS=%s;
let camTs={};
// E8 display controls: per-layer visibility, GT px-floor filter.
let show={};                 // layer ('GT' or board label) -> bool
let pxThr=30, pxHide=false, pxTerms=null;
let layerSig='';
function buildControls(boards){
 const sig=boards.join(',');
 if(sig===layerSig) return;
 layerSig=sig;
 const chk=document.getElementById('layerchecks');
 chk.innerHTML=['GT'].concat(boards).map((n,i)=>{
  if(show[n]===undefined) show[n]=true;
  const col=n==='GT'?'#3c5':COLORS[(i-1)%%COLORS.length];
  return '<label style="color:'+col+'"><input type="checkbox" '+
   (show[n]?'checked':'')+' onchange="show[\\''+n+'\\']=this.checked"> '+
   n+'</label>';
 }).join(' ');
 const sel=document.getElementById('pxterms');
 sel.innerHTML=boards.map(b=>'<option>'+b+'</option>').join('');
 if(!pxTerms || !boards.includes(pxTerms)) pxTerms=boards[0]||null;
 sel.value=pxTerms;
}
function addGT(el, st){
 if(!show.GT) return;
 const pxs=((st.gt_px||{})[pxTerms])||[];
 (st.gt||[]).forEach((b,i)=>{
  const px=pxs[i];
  const sub=(px!=null && px<pxThr);
  if(sub && pxHide) return;
  const d=document.createElement('div'); d.className='bx';
  d.style.borderColor='#3c5';
  if(sub){ d.style.opacity=.45; d.style.borderStyle='dashed'; }
  d.style.left=(b[0]*100)+'%%'; d.style.top=(b[1]*100)+'%%';
  d.style.width=((b[2]-b[0])*100)+'%%';
  d.style.height=((b[3]-b[1])*100)+'%%';
  if(sub && px!=null) d.innerHTML='<span>'+px.toFixed(0)+'px</span>';
  el.appendChild(d);});
}
function summaryCard(run){
 const el=document.getElementById('summary');
 const s=run.summary;
 if(run.stage!=='finished' || !s){ el.style.display='none'; return; }
 const p=s.params||{};
 el.style.display='';
 el.innerHTML='<b style="color:#8fb">RUN COMPLETE — scored summary'+
  '</b> <span style="color:#8fa3b3">('+(p.phases||'')+', '+
  (p.framesize||'')+', GT floor '+(p.min_gt_px??'?')+'px — from '+
  'rows.jsonl, matches summary.json)</span>'+
  '<table style="margin-top:.3rem;border-collapse:collapse">'+
  '<tr>'+['board','recall','precision','GT','match','false','frames',
          'wall s'].map(h=>'<td style="padding:.1rem .8rem;color:'+
          '#8fa3b3">'+h+'</td>').join('')+'</tr>'+
  Object.keys(s.boards||{}).sort().map(lb=>{
   const b=s.boards[lb];
   return '<tr>'+[lb,
    b.recall==null?'—':b.recall.toFixed(3),
    b.prec==null?'—':b.prec.toFixed(3),
    b.gt,b.match,b['false'],b.frames,b.wall_s]
    .map(v=>'<td style="padding:.1rem .8rem">'+v+'</td>').join('')+
    '</tr>';}).join('')+'</table>';
}
async function rv(action,board){
 await fetch('/api/review',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({action,board})});
}
function zoom(box){
 const p=box.closest('.panel');
 document.querySelectorAll('.panel.zoom').forEach(
  x=>{if(x!==p)x.classList.remove('zoom')});
 p.classList.toggle('zoom');
}
document.addEventListener('keydown',e=>{ if(e.key==='Escape')
 document.querySelectorAll('.panel.zoom').forEach(
  x=>x.classList.remove('zoom')); });
function boxes(el,list,color,label){
 el.querySelectorAll('.bx').forEach(b=>b.remove());
 (list||[]).forEach(b=>{
  const d=document.createElement('div'); d.className='bx';
  d.style.borderColor=color;
  d.style.left=(b[0]*100)+'%%'; d.style.top=(b[1]*100)+'%%';
  d.style.width=((b[2]-b[0])*100)+'%%'; d.style.height=((b[3]-b[1])*100)+'%%';
  if(b.length>4) d.innerHTML='<span>'+(label?label+' ':'')+
    b[4].toFixed(2)+'</span>';
  el.appendChild(d);});
}
function addBoxes(el,list,color,label){
 (list||[]).forEach(b=>{
  const d=document.createElement('div'); d.className='bx';
  d.style.borderColor=color;
  d.style.left=(b[0]*100)+'%%'; d.style.top=(b[1]*100)+'%%';
  d.style.width=((b[2]-b[0])*100)+'%%'; d.style.height=((b[3]-b[1])*100)+'%%';
  if(b.length>4) d.innerHTML='<span>'+(label?label+' ':'')+
    b[4].toFixed(2)+'</span>';
  el.appendChild(d);});
}
async function poll(){
 let js;
 try{ js=await (await fetch('/api/monitor')).json(); }
 catch(e){ setTimeout(poll,1000); return; }
 const run=js.run||{};
 document.getElementById('bar').textContent=
  'stage '+(run.stage||'?')+'   mode '+(run.mode||'?')+
  (run.paused?'  PAUSED':'')+'   phase '+(run.phase||'?')+
  ' ('+((run.phase_i|0)+1)+'/'+(run.n_phases||'?')+')'+
  '   still '+((run.still_i|0)+1)+'/'+(run.n_stills||'?')+
  '   settle-discards '+(run.settle_discards??'?')+
  '   stray '+(run.stray_frames??'?');
 document.getElementById('hold').textContent=
  run.stage==='hold' ? '⏸ REVIEW HOLD — press Next when done' : '';
 const st=js.still;
 if(st){
  document.getElementById('stname').textContent=
    st.name+'  ('+(st.index+1)+')';
  const img=document.getElementById('still');
  const want=location.origin+'/still/'+st.name;
  // retry a failed load on the next poll instead of sticking broken
  if(!img.src.startsWith(want) ||
     (img.complete && img.naturalWidth===0)){
   img.src=want+'?r='+Math.random().toString(36).slice(2,8);
  }
  const box=document.getElementById('stillbox');
  box.querySelectorAll('.bx').forEach(b=>b.remove());
  addGT(box,st);
  Object.keys(js.boards).sort().forEach((lb,i)=>{
   if(show[lb]!==false)
    addBoxes(box,js.boards[lb].dets_still,COLORS[i%%COLORS.length],lb);});
 }
 buildControls(Object.keys(js.boards).sort());
 summaryCard(run);
 const host=document.getElementById('boards');
 Object.keys(js.boards).sort().forEach((lb,i)=>{
  let p=document.getElementById('bp_'+lb);
  if(!p){
   p=document.createElement('div'); p.className='panel'; p.id='bp_'+lb;
   p.innerHTML='<div id="bh_'+lb+'"></div>'+
    '<div class="imgbox" id="cb_'+lb+'" onclick="zoom(this)" '+
    'title="click to enlarge"><img id="ci_'+lb+'"></div>'+
    '<div id="bs_'+lb+'" style="white-space:pre"></div>';
   host.appendChild(p);
  }
  const b=js.boards[lb], col=COLORS[i%%COLORS.length];
  document.getElementById('bh_'+lb).innerHTML=
   '<b style="color:'+col+'">'+lb+'</b>  '+
   (b.status==='dead'?'<span class="dead">DEAD — '+
     (b.drop_reason||'')+'</span>':b.status||'');
  document.getElementById('bs_'+lb).textContent=
   'frames this still  '+(b.got??'?')+'\\n'+
   'inference          '+(b.inf_ms??'?')+' ms\\n'+
   'e2e frame          '+(b.e2e_ms??'?')+' ms\\n'+
   'dets               '+(b.n_det??'?')+
   (b.acc?'\\naccuracy (run)     recall '+
     (b.acc.recall==null?'\\u2014':b.acc.recall.toFixed(2))+
     ' \\u00b7 prec '+
     (b.acc.prec==null?'\\u2014':b.acc.prec.toFixed(2))+
     ' (GT '+b.acc.gt+(b.acc.floor?' \\u2265'+b.acc.floor+'px':'')+')':'')+
   (b.model?'\\nmodel              '+b.model:'');
  const ts=(js.cam_ts||{})[lb];
  if(ts && camTs[lb]!==ts){
   camTs[lb]=ts;
   document.getElementById('ci_'+lb).src='/cam/'+lb+'.jpg?t='+ts;
  }
  const cb=document.getElementById('cb_'+lb);
  cb.querySelectorAll('.bx').forEach(x=>x.remove());
  addBoxes(cb,b.dets_cam,col);
 });
 document.getElementById('log').textContent=(js.events||[]).join('\\n');
 setTimeout(poll,500);
}
poll();
</script></body></html>""" % json.dumps(BOARD_COLORS)
