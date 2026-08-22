#!/usr/bin/env python3
"""label_gui.py -- S8 B3: browser label-review GUI over labels.jsonl.

    ~/nereus_ml/venvs/fomo/bin/python ml/fomo/label_gui.py            # defaults
    python3 ml/fomo/label_gui.py ~/nereus_ml/datasets/two_ball --port 8899

Runs on the MAC (where the datasets live), serves a single page to any
browser on the LAN. Nick reviews every training frame, corrects the
auto-label boxes by hand, and saves straight back to the SAME labels.jsonl
that ml/fomo/train.py consumes -- no export, no format conversion.

Format contract (the whole point -- see TRACKER S8 bite B3):
- one JSON object per line: {"file","w","h","classes","boxes",["reviewed"]}
- boxes stay 6-field [class_idx, x, y, w, h, pixels]; hand-drawn or resized
  boxes carry pixels = w*h (a fill estimate; the trainer never reads it)
- "reviewed": true is ADDED on save -- the progress marker. train.py
  ignores unknown keys (verified by reading its load_split).
- classes come FROM THE FILE and are extendable in the GUI, never
  hard-wired: bite E's urchins ride this same tool. Class removal is
  refused (additive only) -- a removed class would renumber every box
  behind the labels' back.

Saves are atomic (tmp + os.replace) and whole-file: one corrupted line
cannot be produced by a crash mid-write. Only stdlib; no Pi, no boards.
"""
import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SAFE_SEG = re.compile(r"^[A-Za-z0-9._-]+$")


# ---------------------------------------------------------------------------
# Dataset access -- pure functions, testable without a server
# ---------------------------------------------------------------------------

def find_sets(root):
    """-> sorted ["run/board", ...] for every labels.jsonl under root."""
    out = []
    if not os.path.isdir(root):
        return out
    for run in sorted(os.listdir(root)):
        rdir = os.path.join(root, run)
        if not os.path.isdir(rdir):
            continue
        for board in sorted(os.listdir(rdir)):
            if os.path.isfile(os.path.join(rdir, board, "labels.jsonl")):
                out.append("%s/%s" % (run, board))
    return out


def set_dir(root, set_id):
    """Confined resolve of "run/board" under root; ValueError on escape."""
    parts = set_id.split("/")
    if (len(parts) != 2
            or not all(SAFE_SEG.match(p) and p.strip(".") for p in parts)):
        # strip(".") guards "." and ".." -- the dot class in SAFE_SEG
        # admits them, and ".." is exactly the escape this refuses.
        raise ValueError("bad set id %r" % set_id)
    return os.path.join(root, parts[0], parts[1])


def load_records(root, set_id):
    path = os.path.join(set_dir(root, set_id), "labels.jsonl")
    with open(path) as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def save_records(root, set_id, records):
    """Atomic whole-file rewrite of the set's labels.jsonl."""
    path = os.path.join(set_dir(root, set_id), "labels.jsonl")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    os.replace(tmp, path)


def validate_boxes(boxes, nclass):
    """Boxes from the browser -> canonical 6-field int lists, or ValueError."""
    out = []
    if not isinstance(boxes, list):
        raise ValueError("boxes must be a list")
    for b in boxes:
        if not (isinstance(b, list) and len(b) == 6):
            raise ValueError("each box must be [class,x,y,w,h,pixels]")
        ci, x, y, w, h, px = (int(v) for v in b)
        if not 0 <= ci < nclass:
            raise ValueError("box class %d out of range (%d classes)"
                             % (ci, nclass))
        if w <= 0 or h <= 0:
            raise ValueError("box w/h must be positive")
        out.append([ci, x, y, w, h, px])
    return out


def apply_frame_update(records, file, boxes, classes):
    """Update one frame's record in place; enforce additive-only classes.

    Returns the updated record. ValueError on unknown file, class-list
    regression, or malformed boxes.
    """
    rec = next((r for r in records if r.get("file") == file), None)
    if rec is None:
        raise ValueError("no frame named %r in this set" % file)
    old = rec.get("classes", [])
    if not isinstance(classes, list) or classes[:len(old)] != old:
        raise ValueError("classes may only be EXTENDED (%r does not start "
                         "with %r)" % (classes, old))
    rec["boxes"] = validate_boxes(boxes, len(classes))
    rec["reviewed"] = True
    if classes != old:
        # keep the class list consistent across the whole file
        for r in records:
            r["classes"] = list(classes)
    return rec


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>nereus label review</title><style>
 body { background:#14171a; color:#dde3e8; font:14px system-ui; margin:0; }
 #top { padding:.5rem .8rem; background:#1d2126; border-bottom:1px solid #2c333a;
        display:flex; gap:1rem; align-items:center; flex-wrap:wrap; }
 select,button { background:#262c33; color:#dde3e8; border:1px solid #3a434c;
                 border-radius:5px; padding:.25rem .5rem; font:inherit; }
 #wrap { display:flex; justify-content:center; padding:.8rem; }
 canvas { border:1px solid #2c333a; cursor:crosshair; }
 #legend span { padding:.1rem .5rem; border-radius:4px; margin-right:.4rem; }
 .k { color:#8fa3b3; } kbd { background:#262c33; border-radius:3px;
      padding:0 .3rem; border:1px solid #3a434c; }
 #help { padding:0 .8rem .8rem; color:#8fa3b3; }
</style></head><body>
<div id="top">
 <select id="set"></select>
 <span id="pos" class="k"></span>
 <span id="prog" class="k"></span>
 <span id="legend"></span>
 <button id="addclass">+ class</button>
 <span id="msg" class="k"></span>
</div>
<div id="wrap"><canvas id="cv"></canvas></div>
<div id="help">
 <kbd>&larr;</kbd><kbd>&rarr;</kbd> frame (saves + marks reviewed) &nbsp;
 drag = draw box &nbsp; drag inside = move &nbsp; drag corner = resize &nbsp;
 click = select &nbsp; <kbd>1</kbd>-<kbd>9</kbd> class &nbsp;
 <kbd>x</kbd> delete &nbsp; <kbd>u</kbd> undo &nbsp;
 <kbd>space</kbd> next unreviewed
</div>
<script>
const PAL = ['#00e5ff','#ff00e5','#ffe500','#00e500','#ff8000','#ffffff'];
let sets=[], setId=null, recs=[], idx=0, img=new Image(), scale=1;
let sel=-1, curClass=0, hist=[], drag=null, dirty=false;
const cv=document.getElementById('cv'), cx=cv.getContext('2d');
const $=id=>document.getElementById(id);

function classes(){ return recs[idx] ? (recs[idx].classes||[]) : []; }
function boxes(){ return recs[idx] ? recs[idx].boxes : []; }
function msg(t){ $('msg').textContent=t; }

async function loadSets(){
  sets = await (await fetch('api/sets')).json();
  $('set').innerHTML = sets.map(s=>
    `<option value="${s.set}">${s.set} (${s.reviewed}/${s.frames})</option>`).join('');
  if(sets.length){ await loadSet(sets[0].set); }
}
async function loadSet(id){
  setId=id; $('set').value=id;
  recs = (await (await fetch('api/set/'+id)).json()).records;
  idx=0; await show();
}
function pushHist(){ hist.push(JSON.stringify(boxes())); if(hist.length>50) hist.shift(); }
async function show(){
  sel=-1; hist=[]; dirty=false;
  const r=recs[idx]; if(!r) return;
  img=new Image();
  await new Promise(res=>{ img.onload=res; img.src='img/'+setId+'/'+r.file; });
  scale=Math.min(1.6, (window.innerWidth-40)/img.width);
  cv.width=img.width*scale; cv.height=img.height*scale;
  draw();
  $('pos').textContent=(idx+1)+'/'+recs.length+'  '+r.file+(r.reviewed?' ✓':'');
  const done=recs.filter(x=>x.reviewed).length;
  $('prog').textContent='reviewed '+done+'/'+recs.length;
  $('legend').innerHTML=classes().map((c,i)=>
    `<span style="background:${PAL[i%PAL.length]};color:#000">${i+1} ${c}</span>`).join('');
}
function draw(){
  cx.drawImage(img,0,0,cv.width,cv.height);
  boxes().forEach((b,i)=>{
    cx.strokeStyle=PAL[b[0]%PAL.length]; cx.lineWidth=(i===sel)?3:2;
    cx.strokeRect(b[1]*scale,b[2]*scale,b[3]*scale,b[4]*scale);
    cx.fillStyle=PAL[b[0]%PAL.length];
    cx.font='12px system-ui';
    cx.fillText(classes()[b[0]]||b[0], b[1]*scale+2, Math.max(10,b[2]*scale-4));
    if(i===sel){ cx.fillRect((b[1]+b[3])*scale-4,(b[2]+b[4])*scale-4,8,8); }
  });
}
function hit(px,py){ // -> [index, 'corner'|'inside'] or [-1]
  const bs=boxes();
  for(let i=bs.length-1;i>=0;i--){ const b=bs[i];
    const x=b[1]*scale,y=b[2]*scale,w=b[3]*scale,h=b[4]*scale;
    if(Math.abs(px-(x+w))<7 && Math.abs(py-(y+h))<7) return [i,'corner'];
    if(px>=x&&px<=x+w&&py>=y&&py<=y+h) return [i,'inside'];
  }
  return [-1,null];
}
cv.onmousedown=e=>{
  const px=e.offsetX,py=e.offsetY, [i,mode]=hit(px,py);
  pushHist();
  if(i>=0){ sel=i; drag={mode,i,px,py,orig:boxes()[i].slice()}; }
  else { const b=[curClass,Math.round(px/scale),Math.round(py/scale),1,1,0];
         boxes().push(b); sel=boxes().length-1;
         drag={mode:'corner',i:sel,px,py,orig:b.slice()}; }
  draw();
};
cv.onmousemove=e=>{
  if(!drag) return;
  const b=boxes()[drag.i], dx=(e.offsetX-drag.px)/scale, dy=(e.offsetY-drag.py)/scale;
  if(drag.mode==='inside'){ b[1]=Math.round(drag.orig[1]+dx); b[2]=Math.round(drag.orig[2]+dy); }
  else { b[3]=Math.max(1,Math.round(drag.orig[3]+dx)); b[4]=Math.max(1,Math.round(drag.orig[4]+dy)); }
  dirty=true; draw();
};
cv.onmouseup=()=>{ if(drag){ const b=boxes()[drag.i]; b[5]=b[3]*b[4]; drag=null; dirty=true; draw(); } };
document.onkeydown=async e=>{
  if(e.target.tagName==='SELECT') return;
  if(e.key==='ArrowRight'){ await step(1); }
  else if(e.key==='ArrowLeft'){ await step(-1); }
  else if(e.key===' '){ e.preventDefault(); await nextUnreviewed(); }
  else if(e.key>='1'&&e.key<='9'){ const c=+e.key-1;
    if(c<classes().length){ curClass=c;
      if(sel>=0){ pushHist(); boxes()[sel][0]=c; dirty=true; draw(); } } }
  else if(e.key==='x'||e.key==='Delete'){ if(sel>=0){ pushHist();
      boxes().splice(sel,1); sel=-1; dirty=true; draw(); } }
  else if(e.key==='u'){ if(hist.length){ recs[idx].boxes=JSON.parse(hist.pop());
      sel=-1; dirty=true; draw(); } }
};
async function save(){
  const r=recs[idx]; if(!r) return;
  const body={set:setId, file:r.file, boxes:r.boxes, classes:classes()};
  const rsp=await fetch('api/frame',{method:'POST',body:JSON.stringify(body)});
  if(!rsp.ok){ msg('SAVE FAILED: '+await rsp.text()); throw new Error('save'); }
  r.reviewed=true; dirty=false; msg('saved '+r.file);
}
async function step(d){ await save(); idx=Math.max(0,Math.min(recs.length-1,idx+d)); await show(); }
async function nextUnreviewed(){ await save();
  const n=recs.findIndex((r,i)=>i>idx&&!r.reviewed);
  idx=(n>=0)?n:idx; await show(); }
$('set').onchange=async e=>{ if(dirty) await save(); await loadSet(e.target.value); };
$('addclass').onclick=async ()=>{
  const name=prompt('new class name'); if(!name) return;
  recs.forEach(r=>{ r.classes=classes().concat([name]); });
  dirty=true; await save(); await show();
};
window.onbeforeunload=e=>{ if(dirty) return 'unsaved boxes'; };
loadSets();
</script></body></html>"""


def make_handler(root):
    class Handler(BaseHTTPRequestHandler):
        # one writer at a time; the browser is single-user but keys can race
        _lock = threading.Lock()

        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, PAGE, "text/html; charset=utf-8")
                if path == "/api/sets":
                    out = []
                    for s in find_sets(root):
                        recs = load_records(root, s)
                        out.append({
                            "set": s, "frames": len(recs),
                            "reviewed": sum(1 for r in recs
                                            if r.get("reviewed")),
                            "classes": recs[0].get("classes", [])
                            if recs else []})
                    return self._send(200, json.dumps(out))
                if path.startswith("/api/set/"):
                    recs = load_records(root, path[len("/api/set/"):])
                    return self._send(200, json.dumps({"records": recs}))
                if path.startswith("/img/"):
                    rest = path[len("/img/"):]
                    seg = rest.split("/")
                    if len(seg) != 3 or not all(
                            SAFE_SEG.match(p) and p.strip(".") for p in seg):
                        return self._send(404, "bad image path", "text/plain")
                    fp = os.path.join(set_dir(root, "/".join(seg[:2])),
                                      seg[2])
                    with open(fp, "rb") as fh:
                        return self._send(200, fh.read(), "image/jpeg")
                return self._send(404, "not found", "text/plain")
            except (ValueError, OSError) as e:
                return self._send(404, str(e), "text/plain")

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/api/frame":
                return self._send(404, "not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                with self._lock:
                    recs = load_records(root, body["set"])
                    apply_frame_update(recs, body["file"], body["boxes"],
                                       body["classes"])
                    save_records(root, body["set"], recs)
                return self._send(200, json.dumps({"ok": True}))
            except (ValueError, KeyError, OSError) as e:
                return self._send(400, str(e), "text/plain")

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", nargs="?",
                    default=os.path.expanduser("~/nereus_ml/datasets/two_ball"))
    ap.add_argument("--bind", default="0.0.0.0",
                    help="LAN by default so the workbench guide's link works "
                         "from any browser (trusted-LAN posture, S25)")
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args(argv)
    sets = find_sets(args.root)
    if not sets:
        print("no labels.jsonl under %s -- run ml/fomo/relabel.py first"
              % args.root, file=sys.stderr)
        return 1
    print("label GUI: %d sets under %s" % (len(sets), args.root))
    print("open http://%s:%d/  (Ctrl-C to stop)"
          % (os.uname().nodename, args.port))
    srv = ThreadingHTTPServer((args.bind, args.port),
                              make_handler(args.root))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped; labels are saved per frame, nothing pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
