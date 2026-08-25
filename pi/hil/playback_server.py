#!/usr/bin/env python3
"""HIL playback server (S8 bite E) — the screen half of the urchin HIL.

Serves ONE fullscreen page (black, cursor hidden, fixed 16:9 content box)
that the bench cameras look at, plus a tiny JSON API the HIL harness
drives. Stdlib only; runs on nereus000 under the workbench runner.

Modes (the harness switches them via POST /api/set):
  loop   a Monterey clip looping — the demo eyeball + end-to-end fps runs
  step   one canonical still from stills_v1 — the scored matrix stimulus
         (the harness steps the index; the page never advances itself)
  calib  4 corner markers + center cross at KNOWN content fractions
         (MARKERS below) — the one-time screen→camera homography shot
  black  blank screen (idle / power-floor readings)

Media layout (--media, default ~/hil_monterey):
  *.mp4                       the clips (remuxed from the Mac's .mov)
  stills/frames/*.jpg         the frozen 80-still set (scp of stills_v1)
  stills/stills_manifest.json canonical still order — the page and the
                              scorer MUST agree on it, so it is the same
                              file hil_stills.py wrote

Keyboard (hand-driving without the harness): f fullscreen · l loop ·
s step · c calib · b black · ←/→ step ±1 · [/] switch clip.
"""
import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Marker CENTERS as fractions of the content box (the displayed still's
# own pixel grid). The harness's calibration solver reads these from
# /api/state — one definition, zero drift. Order: TL, TR, BR, BL
# (homography convention). Extreme-corner placement (3% inset, Nick
# 2026-08-25): the cameras are 16:10 vs the 16:9 content, so corner
# markers let the cameras zoom until the marker rectangle nearly fills
# the frame — every content pixel then lands as large as possible on
# the sensor (pixels-on-target is the accuracy currency).
# 2026-08-25 (Nick's sizing-ladder pick): markers sit at the corners of
# aim box D (70% scale, 16:10) — both cameras frame that box fully at
# their working distance. The cameras therefore see only the central
# 63% x 70% of the content; the harness scores only the GT visible in
# frame. Visible still pixels land ~0.53x on the sensor (was 0.28x).
MARKERS = [(0.185, 0.15), (0.815, 0.15), (0.815, 0.85), (0.185, 0.85)]
MARKER_W = 0.045             # marker square width, fraction of content width
MODES = ("loop", "step", "calib", "black", "boxes")

# "boxes" mode: nested 16:10 rectangles (the cameras' native aspect) for
# picking the camera distance — Nick zooms until one letter fills his
# view. The largest 16:10 rect inside the 16:9 content is 90% of its
# width at full height; each entry scales that. Fractions of the content
# box, served via /api/state so page and LCD client draw identically.
AIM_BOXES = [(1.00, "#ff4040", "A"), (0.90, "#ffb000", "B"),
             (0.80, "#30d030", "C"), (0.70, "#40b0ff", "D"),
             (0.60, "#e060ff", "E")]


def aim_boxes():
    out = []
    for s, color, label in AIM_BOXES:
        bw, bh = 0.9 * s, s
        out.append({"label": label, "color": color, "scale": s,
                    "x": 0.5 - bw / 2, "y": 0.5 - bh / 2,
                    "w": bw, "h": bh})
    return out

MIME = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".json": "application/json"}


def load_media(media_dir):
    """-> (clips, stills) as sorted relative paths under media_dir.
    Stills follow the manifest's canonical order when present (the frozen
    set hil_stills.py wrote); a bare frames/ dir falls back to sorted."""
    clips = sorted(f for f in os.listdir(media_dir)
                   if f.lower().endswith(".mp4"))
    stills = []
    sdir = os.path.join(media_dir, "stills")
    man = os.path.join(sdir, "stills_manifest.json")
    if os.path.isfile(man):
        with open(man) as fh:
            m = json.load(fh)
        for c in m["clips"]:
            for idx in c["sampled_indices"]:
                stills.append(f"stills/frames/{c['still_prefix']}_f{idx:04d}.jpg")
        missing = [s for s in stills
                   if not os.path.isfile(os.path.join(media_dir, s))]
        if missing:
            raise SystemExit(f"FAIL: manifest names {len(missing)} stills "
                             f"not on disk, first: {missing[0]}")
    elif os.path.isdir(os.path.join(sdir, "frames")):
        stills = sorted("stills/frames/" + f for f in
                        os.listdir(os.path.join(sdir, "frames"))
                        if f.lower().endswith(".jpg"))
    return clips, stills


class State:
    """The page's single source of truth; seq bumps on every change so the
    page's 300 ms poll knows when to react."""

    def __init__(self, clips, stills):
        self.lock = threading.Lock()
        self.clips, self.stills = clips, stills
        self.mode = "loop" if clips else "black"
        self.clip = 0
        self.still = 0
        self.seq = 1
        self.updated = time.time()

    def snapshot(self):
        with self.lock:
            return {"seq": self.seq, "mode": self.mode, "clip": self.clip,
                    "still": self.still, "clips": self.clips,
                    "stills": self.stills, "markers": MARKERS,
                    "marker_w": MARKER_W, "aim_boxes": aim_boxes(),
                    "updated": self.updated}

    def set(self, req):
        """Apply a validated /api/set body. -> (ok, error-or-None)."""
        with self.lock:
            mode = req.get("mode", self.mode)
            if mode not in MODES:
                return False, f"mode must be one of {MODES}"
            clip, still = self.clip, self.still
            if "clip" in req:
                clip = int(req["clip"])
                if not 0 <= clip < max(1, len(self.clips)):
                    return False, f"clip {clip} out of range"
            if "still" in req:
                still = int(req["still"])
            if "step" in req:
                still = self.still + int(req["step"])
            if self.stills:
                still %= len(self.stills)
            elif mode == "step":
                return False, "no stills on disk — step mode unavailable"
            self.mode, self.clip, self.still = mode, clip, still
            self.seq += 1
            self.updated = time.time()
            return True, None


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>HIL playback</title><style>
 html,body{margin:0;height:100%;background:#000;overflow:hidden;cursor:none}
 #box{position:absolute;background:#000}
 #vid,#img{width:100%;height:100%;display:none;object-fit:fill}
 #cal{position:absolute;inset:0;display:none}
</style></head><body>
<div id="box"><video id="vid" muted autoplay loop playsinline></video>
<img id="img"><canvas id="cal"></canvas></div>
<script>
let st=null, shownSeq=0;
const box=document.getElementById('box'), vid=document.getElementById('vid'),
      img=document.getElementById('img'), cal=document.getElementById('cal');
function layout(){                    // fixed 16:9 content box, centered
  const W=innerWidth,H=innerHeight,a=16/9;
  let w=W,h=W/a; if(h>H){h=H;w=H*a;}
  box.style.width=w+'px';box.style.height=h+'px';
  box.style.left=(W-w)/2+'px';box.style.top=(H-h)/2+'px';
  cal.width=w;cal.height=h; if(st&&st.mode==='calib')drawCal();
}
function drawCal(){
  // markers ONLY — a border or crosshair adds bright mass that biases the
  // harness's quadrant-centroid marker detection
  const g=cal.getContext('2d'),w=cal.width,h=cal.height;
  g.fillStyle='#000';g.fillRect(0,0,w,h);
  const mw=st.marker_w*w;
  for(const [fx,fy] of st.markers){
    g.fillStyle='#fff';g.fillRect(fx*w-mw/2,fy*h-mw/2,mw,mw);}
}
function drawBoxes(){
  const g=cal.getContext('2d'),w=cal.width,h=cal.height;
  g.fillStyle='#000';g.fillRect(0,0,w,h);
  for(const b of st.aim_boxes){
    g.strokeStyle=b.color;g.lineWidth=3;
    g.strokeRect(b.x*w,b.y*h,b.w*w,b.h*h);
    g.fillStyle=b.color;g.font=(0.05*h)+'px sans-serif';
    g.fillText(b.label+' '+Math.round(b.scale*100)+'%',
               b.x*w+8,b.y*h+0.05*h+4);}
}
function apply(){
  if(!st||st.seq===shownSeq)return; shownSeq=st.seq;
  vid.style.display=img.style.display=cal.style.display='none';
  if(st.mode==='loop'&&st.clips.length){
    const src='/media/'+st.clips[st.clip];
    if(!vid.src.endsWith(src)){vid.src=src;}
    vid.style.display='block';vid.play().catch(()=>{});
  }else if(st.mode==='step'&&st.stills.length){
    vid.pause();img.src='/media/'+st.stills[st.still];
    img.style.display='block';
  }else if(st.mode==='calib'){
    vid.pause();cal.style.display='block';drawCal();
  }else if(st.mode==='boxes'){
    vid.pause();cal.style.display='block';drawBoxes();
  }else{vid.pause();}
  document.title='HIL '+st.mode+(st.mode==='step'?' '+st.still:'');
}
async function poll(){
  try{const r=await fetch('/api/state');st=await r.json();apply();}
  catch(e){}
  setTimeout(poll,300);
}
async function set(body){await fetch('/api/set',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
addEventListener('keydown',e=>{
  const k=e.key;
  if(k==='f')document.documentElement.requestFullscreen().catch(()=>{});
  else if(k==='l')set({mode:'loop'});
  else if(k==='s')set({mode:'step'});
  else if(k==='c')set({mode:'calib'});
  else if(k==='x')set({mode:'boxes'});
  else if(k==='b')set({mode:'black'});
  else if(k==='ArrowRight')set({mode:'step',step:1});
  else if(k==='ArrowLeft')set({mode:'step',step:-1});
  else if(k===']')set({mode:'loop',clip:(st.clip+1)%st.clips.length});
  else if(k==='[')set({mode:'loop',clip:(st.clip+st.clips.length-1)%st.clips.length});
});
addEventListener('resize',layout);layout();poll();
</script></body></html>"""


def make_handler(state, media_dir):

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, *a):          # quiet; the runner tails stderr
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._json(state.snapshot())
            elif self.path.startswith("/media/"):
                self._media(self.path[len("/media/"):])
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/api/set":
                return self._json({"error": "not found"}, 404)
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json({"error": "bad JSON body"}, 400)
            ok, err = state.set(req)
            if not ok:
                return self._json({"error": err}, 400)
            self._json(state.snapshot())

        def _media(self, rel):
            # confined resolve — the same posture as the workbench thumbs
            path = os.path.realpath(os.path.join(media_dir, rel))
            if not (path.startswith(os.path.realpath(media_dir) + os.sep)
                    and os.path.isfile(path)):
                return self._json({"error": "no such media"}, 404)
            size = os.path.getsize(path)
            ctype = MIME.get(os.path.splitext(path)[1].lower(),
                             "application/octet-stream")
            # single-range support: Safari refuses <video> without it
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
            with open(path, "rb") as fh:
                if rng and m and (m.group(1) or m.group(2)):
                    start = int(m.group(1) or 0)
                    end = int(m.group(2)) if m.group(2) else size - 1
                    end = min(end, size - 1)
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range",
                                     f"bytes {start}-{end}/{size}")
                    length = end - start + 1
                    fh.seek(start)
                else:
                    self.send_response(200)
                    length = size
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return              # player seeked away — normal
                    remaining -= len(chunk)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--media", default=os.path.expanduser("~/hil_monterey"))
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8091)
    args = ap.parse_args()

    if not os.path.isdir(args.media):
        raise SystemExit(f"FAIL: media dir {args.media} does not exist")
    clips, stills = load_media(args.media)
    if not clips and not stills:
        raise SystemExit(f"FAIL: no .mp4 clips and no stills under "
                         f"{args.media} — nothing to show")
    state = State(clips, stills)
    srv = ThreadingHTTPServer((args.bind, args.port),
                              make_handler(state, args.media))
    print(f"HIL playback on http://{args.bind}:{args.port}/ — "
          f"{len(clips)} clips, {len(stills)} stills, mode={state.mode}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
