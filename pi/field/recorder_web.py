#!/usr/bin/env python3
"""S32 video recorder -- the page. Record, then watch and download.

Two views:

  INDEX   the record form (framesize / quality / fps / duration / cameras) with
          each camera's MEASURED ceiling shown against the current pick, plus
          the library of every recording on disk, newest first.

  VIEWER  one session: both cameras side by side, playing together off ONE
          scrubber, with the settings that produced them and the file sizes.

Two things here are load-bearing rather than decorative:

  * HTTP Range support. HTML5 `<video>` cannot seek without it, so a scrubber
    over a non-Range server silently does nothing after the first buffer. The
    scrubber IS the feature, so ranges are implemented, not stubbed.

  * The clips are aligned by each camera's recorded start offset. Two boards
    pushed in parallel still start milliseconds apart, and the viewer seeks
    each video to `t + its own offset` rather than assuming they began
    together.

Exposure follows the S25 decision: bind on the trusted LAN, loud banner, no
auth. It is started by the workbench, which owns the board lock.
"""

import html
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recorder as R                                        # noqa: E402
import record_run as RR                                     # noqa: E402

FRAMESIZES = ("QVGA", "VGA", "HD")
#: The AE3 needs the low rungs -- its VGA ladder plateaus at ~13.8 fps and q30
#: is where it gets essentially all of that (13.47). The N6 barely uses them.
QUALITIES = (10, 30, 50, 70, 80, 85, 90, 95)
#: Nick's call 2026-09-08, after looking at byte-exact q70 and q90 frames from
#: the same scene: "q70 looks fine, make it the default for N6". It is also the
#: only HD rung that reaches 30 fps end to end (30.24 measured vs q90's 19.9)
#: and costs 3.6x fewer bytes.
DEFAULT_QUALITY = 70
CTYPES = {".mp4": "video/mp4", ".mjpeg": "video/x-motion-jpeg",
          ".json": "application/json"}


class RecorderState:
    """One recording at a time. A second request is refused, never queued."""

    def __init__(self, root):
        self.root = root
        #: Set by SIGINT/SIGTERM. A recording in flight then ENDS rather than
        #: being killed: the pumps return, the writers drain, and the manifest
        #: is written and marked interrupted. Without this, stopping a long
        #: recording left a multi-GB .mjpeg with no manifest, which the library
        #: silently skips -- the clip simply vanished.
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.busy = False
        self.log = []
        self.last = None
        self.started = 0.0
        self.settings = {}

    def note(self, msg):
        self.log.append("%s  %s" % (time.strftime("%H:%M:%S"), msg))
        del self.log[:-200]

    def snapshot(self):
        return {"busy": self.busy, "log": self.log[-40:], "last": self.last,
                "elapsed": round(time.time() - self.started, 1) if self.busy else 0,
                "settings": self.settings}

    def start(self, **kw):
        with self.lock:
            if self.busy:
                return False, "a recording is already running"
            self.busy = True
            self.log = []
            self.started = time.time()
            self.settings = dict(kw)
        t = threading.Thread(target=self._run, kwargs=kw)
        t.daemon = True
        t.start()
        return True, "started"

    def _run(self, **kw):
        try:
            self.note("starting: %s" % json.dumps(kw))
            # ONE sink. Passing self.note as both log and progress printed
            # every line to the status pane twice.
            res = RR.run_recording(root=self.root, log=self.note,
                                   stop_event=self.stop, **kw)
            self.last = res
            self.note("DONE: %s" % (res.get("summary") or "").replace("\n", " | "))
        except Exception as e:                              # noqa: BLE001
            import traceback
            self.note("FAILED: %s: %s" % (type(e).__name__, e))
            self.note(traceback.format_exc()[-600:])
            self.last = {"ok": False, "errors": [str(e)], "cameras": []}
        finally:
            self.busy = False


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------

CSS = """
:root{--bg:#0f1115;--fg:#e8eaed;--dim:#9aa0a6;--line:#2a2f37;--ok:#33d17a;
      --warn:#f5c211;--bad:#f66151;--card:#171a21;--acc:#62a0ea}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:var(--acc)} .wrap{max-width:1200px;margin:0 auto;padding:16px}
.banner{background:#3a2a00;color:#f5c211;padding:6px 16px;font-size:12px;
  border-bottom:1px solid #5a4200}
h1{font-size:20px;margin:12px 0} h2{font-size:15px;margin:18px 0 8px;
  color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:14px;margin-bottom:14px}
label{display:block;font-size:12px;color:var(--dim);margin-bottom:4px}
select,input[type=number]{background:#0b0d11;color:var(--fg);border:1px solid
  var(--line);border-radius:6px;padding:7px 9px;width:100%}
.row{display:flex;gap:12px;flex-wrap:wrap} .row>div{flex:1;min-width:120px}
button{background:var(--acc);color:#0b0d11;border:0;border-radius:6px;
  padding:10px 18px;font-weight:600;cursor:pointer;font-size:14px}
button:disabled{opacity:.5;cursor:not-allowed}
button.sec{background:#2a2f37;color:var(--fg)}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase}
.ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)}
.dim{color:var(--dim)} .mono{font-family:ui-monospace,Menlo,Consolas,monospace}
pre{background:#0b0d11;border:1px solid var(--line);border-radius:6px;
  padding:10px;overflow:auto;max-height:240px;font-size:12px;margin:0}
.vids{display:flex;gap:12px;flex-wrap:wrap}
.vid{flex:1;min-width:320px} video{width:100%;background:#000;border-radius:6px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.chip{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;
  background:#22262e;color:var(--dim);margin-right:6px}
input[type=range]{width:100%}
"""


def _fmt_bytes(n):
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def _shell(title, body):
    return ("<!doctype html><html><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body>"
            "<div class=banner>Nereus bench &mdash; open on the trusted LAN, "
            "no authentication. Recording controls real hardware.</div>"
            "<div class=wrap>%s</div></body></html>" % (html.escape(title), CSS, body))


def _fill(tpl, **kw):
    """Substitute @@TOKEN@@ placeholders literally.

    These templates carry JavaScript and CSS, both of which contain bare "%"
    (percentages in margins, and "% margin" in the verdict text). Python's
    %-formatting ate those and raised "not enough arguments for format string",
    which took the whole index page down while the JSON API stayed fine. A
    literal replace cannot collide with page content, so the class of bug is
    removed rather than patched one escape at a time.
    """
    for key, val in kw.items():
        tpl = tpl.replace("@@%s@@" % key, val)
    return tpl


def index_page(state, sessions, ceilings):
    cams = (ceilings.get("cameras") or {})
    rows = []
    for role, cam in sorted(cams.items()):
        deliv = cam.get("delivered") or {}
        cells = cam.get("cells") or {}
        rows.append(
            "<tr><td class=mono>%s</td>"
            "<td class=mono>%s</td><td class=mono class=dim>%s</td></tr>"
            % (html.escape(role),
               html.escape(", ".join("%s %.1f" % (k, v)
                                     for k, v in sorted(deliv.items())))
               or "<span class=dim>none yet</span>",
               html.escape(", ".join("%s %.1f" % (k, v)
                                     for k, v in sorted(cells.items())))))
    def cam_controls():
        """One framesize + quality control per camera, defaulted from the
        SAME table the recorder uses, so the form cannot drift from what
        actually runs."""
        out = []
        for role in ("N6", "AE3"):
            d = RR.CAMERA_DEFAULTS.get(role, {"framesize": "HD",
                                              "quality": DEFAULT_QUALITY})
            fs = "".join("<option%s>%s</option>"
                         % (" selected" if f == d["framesize"] else "", f)
                         for f in FRAMESIZES)
            qs = "".join("<option%s>%d</option>"
                         % (" selected" if q == d["quality"] else "", q)
                         for q in QUALITIES)
            out.append(
                "<div><label>%s frame size</label>"
                "<select id='fs_%s' class=camctl>%s</select></div>"
                "<div><label>%s quality</label>"
                "<select id='q_%s' class=camctl>%s</select></div>"
                % (role, role, fs, role, role, qs))
        return "".join(out)

    cam_html = cam_controls()

    lib = []
    for m in sessions:
        if m.get("broken"):
            lib.append("<div class=card><b class=bad>%s</b><div class=dim>"
                       "unreadable manifest: %s</div></div>"
                       % (html.escape(m["name"]), html.escape(m["broken"])))
            continue
        s = m.get("settings", {})
        total = sum((c.get("mp4_bytes", 0) + c.get("mjpeg_bytes", 0))
                    for c in m.get("cameras", []))
        cams_txt = ", ".join("%s %d fr @ %.1f fps"
                             % (c.get("label", "?"), c.get("written_frames", 0),
                                c.get("delivered_fps", 0))
                             for c in m.get("cameras", []))
        lib.append(
            "<div class=card><a href='/view/%s'><b>%s</b></a>"
            "<div class=dim>%s</div>"
            "<div style='margin-top:6px'>"
            "<span class=chip>%s</span><span class=chip>q%s</span>"
            "<span class=chip>%s fps asked</span><span class=chip>%ss</span>"
            "<span class=chip>%s</span></div>"
            "<div class=dim style='margin-top:6px'>%s</div></div>"
            % (urllib.parse.quote(m["name"]), html.escape(m["name"]),
               html.escape(m.get("created_iso", "?")),
               html.escape(str(s.get("framesize", "?"))),
               html.escape(str(s.get("quality", "?"))),
               html.escape(str(s.get("fps_requested", "?"))),
               html.escape(str(s.get("duration_s", "?"))),
               _fmt_bytes(total), html.escape(cams_txt)))
    if not lib:
        lib = ["<div class=card class=dim>No recordings yet.</div>"]

    return _shell("Video recorder", _fill("""
<h1>Video recorder</h1>
<div class=card>
  <div class=row>
    <div><label>Target fps</label><input id=fps type=number value=30 min=1 max=120></div>
    <div><label>Duration (s)</label><input id=duration type=number value=5 min=1 max=3600></div>
    <div><label>Cameras</label><select id=cameras>
      <option value="N6,AE3">N6 + AE3</option>
      <option value="N6">N6 only</option>
      <option value="AE3">AE3 only</option></select></div>
  </div>
  <div class=dim style="margin:12px 0 6px;font-size:12px">
    Each camera has its own settings, because they are not equals: HD q70 gives
    the N6 30.2 fps and the AE3 2.3. Defaults below are the measured picks.
  </div>
  <div class=row>@@CAMS@@</div>
  <div id=verdict class=dim style="margin:10px 0"></div>
  <button id=go onclick=startRec()>&#9679; Record</button>
  <a href="/" class=dim style="margin-left:10px">refresh</a>
</div>
<div class=card><h2 style="margin-top:0">Status</h2><pre id=log>idle</pre></div>
<h2>Measured camera limits on this rig</h2>
<div class=card><table>
<tr><th>Camera</th><th>Delivered fps (end to end)</th><th>Encoder fps (link cost excluded)</th></tr>
@@ROWS@@</table>
<div class=dim style="margin-top:8px;font-size:12px">
Delivered is what a recording actually lands on disk. It is lower than the
encoder number because the board writes each frame over USB inside the same
single-threaded loop that encodes it, so a bigger frame costs twice. The guard
above uses delivered wherever it exists.</div></div>
<h2>Recordings</h2>
@@LIB@@
<script>
const CEIL = @@CEIL@@;
function camSettings(){
  const cams=document.getElementById('cameras').value.split(',');
  const out={};
  for(const c of cams){
    const fs=document.getElementById('fs_'+c), q=document.getElementById('q_'+c);
    if(fs&&q) out[c]={framesize:fs.value, quality:+q.value};
  }
  return out;
}
function verdict(){
  const fps=+document.getElementById('fps').value, per=camSettings();
  let out=[];
  for(const c of Object.keys(per)){
    const cam=((CEIL.cameras||{})[c]||{});
    const key=per[c].framesize+'_q'+per[c].quality;
    // DELIVERED (measured end to end) beats the encoder number, which excludes
    // the board's own USB write and is therefore always the optimistic one.
    const dv=(cam.delivered||{})[key], ev=(cam.cells||{})[key];
    const v=(dv!==undefined)?dv:ev;
    const kind=(dv!==undefined)?'measured end to end':'encoder only, real rate is lower';
    const at=c+' at '+per[c].framesize+' q'+per[c].quality+': ';
    if(v===undefined){out.push('<span class=warn>'+at+'never measured on this rig</span>');}
    else if(fps>v){out.push('<span class=bad>'+at+v.toFixed(1)+' fps ('+kind+') &mdash; '+fps+' fps is NOT achievable, expect ~'+v.toFixed(1)+'</span>');}
    else if(fps>v*0.9){out.push('<span class=warn>'+at+v.toFixed(1)+' fps ('+kind+') &mdash; only '+(100*(v-fps)/v).toFixed(0)+'% margin</span>');}
    else {out.push('<span class=ok>'+at+v.toFixed(1)+' fps ('+kind+') &mdash; '+(100*(v-fps)/v).toFixed(0)+'% margin</span>');}
  }
  document.getElementById('verdict').innerHTML=out.join('<br>');
}
for(const id of ['fps','cameras']) document.getElementById(id).addEventListener('change',verdict);
for(const el of document.querySelectorAll('.camctl')) el.addEventListener('change',verdict);
verdict();
async function startRec(){
  const b=document.getElementById('go'); b.disabled=true;
  const body={fps:+document.getElementById('fps').value,
    duration:+document.getElementById('duration').value,
    cameras:document.getElementById('cameras').value,
    per_camera:camSettings()};
  const r=await fetch('/api/record',{method:'POST',body:JSON.stringify(body)});
  const j=await r.json();
  if(!j.ok){alert(j.err||'refused');b.disabled=false;}
  poll();
}
async function poll(){
  const r=await fetch('/api/status'); const j=await r.json();
  document.getElementById('log').textContent=(j.log||[]).join('\\n')||'idle';
  document.getElementById('go').disabled=j.busy;
  if(j.busy){setTimeout(poll,800);} else {setTimeout(()=>location.reload(),1200);}
}
poll();
</script>
""",
        CAMS=cam_html,
        ROWS="".join(rows) or "<tr><td colspan=3 class=dim>none measured</td></tr>",
        LIB="".join(lib), CEIL=json.dumps(ceilings)))


def viewer_page(m):
    s = m.get("settings", {})
    cams = [c for c in m.get("cameras", []) if c.get("mp4") or c.get("mjpeg")]
    vids, rows = [], []
    for i, c in enumerate(cams):
        label = html.escape(c.get("label", "?"))
        src = c.get("mp4")
        note = ""
        if not src:
            src = c.get("mjpeg")
            note = ("<div class=warn style='font-size:12px'>no mp4 &mdash; the "
                    "transcode failed, so this is raw MJPEG and your browser "
                    "probably will not play it. Download it instead.</div>")
        vids.append(
            "<div class=vid><b>%s</b> <span class=dim>%s</span>%s"
            "<video id=v%d preload=metadata src='/media/%s/%s' "
            "data-offset='%s'></video></div>"
            % (label, html.escape("%dx%d" % (c.get("banner", {}).get("w", 0),
                                             c.get("banner", {}).get("h", 0))),
               note, i, urllib.parse.quote(m["name"]),
               urllib.parse.quote(src), c.get("start_offset_s", 0)))
        gaps = c.get("seq_gaps")
        rows.append(
            "<tr><td class=mono>%s</td><td>%d</td><td>%.2f</td><td>%.2f</td>"
            "<td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td></tr>"
            % (label, c.get("written_frames", 0), c.get("delivered_fps", 0),
               c.get("mb_per_s", 0),
               ("<span class=ok>0</span>" if not gaps else
                "<span class=bad>%s</span>" % gaps),
               ("<span class=ok>0</span>" if not c.get("ring_dropped_frames")
                else "<span class=bad>%d</span>" % c["ring_dropped_frames"]),
               _fmt_bytes(c.get("mjpeg_bytes", 0)),
               _fmt_bytes(c.get("mp4_bytes", 0)),
               "<a href='/download/%s/%s'>mp4</a> &middot; "
               "<a href='/download/%s/%s'>mjpeg</a> &middot; "
               "<a href='/still/%s/%s/%d' target=_blank>original frame</a>"
               % (urllib.parse.quote(m["name"]),
                  urllib.parse.quote(c.get("mp4") or ""),
                  urllib.parse.quote(m["name"]),
                  urllib.parse.quote(c.get("mjpeg") or ""),
                  urllib.parse.quote(m["name"]),
                  urllib.parse.quote(c.get("mjpeg") or ""),
                  min(30, max(0, c.get("written_frames", 1) // 2)))))

    return _shell("Recording %s" % m["name"], """
<h1><a href='/' class=dim>&larr;</a> %s</h1>
<div class=card>
  <span class=chip>%s</span><span class=chip>q%s</span>
  <span class=chip>%s fps asked</span><span class=chip>%s s</span>
  <span class=chip>%s</span><span class=chip>%s</span>
</div>
<div class=card>
  <div class=vids>%s</div>
  <div style="margin-top:12px">
    <input id=scrub type=range min=0 max=1000 value=0 step=1>
    <div style="display:flex;gap:10px;align-items:center;margin-top:8px">
      <button id=play onclick=toggle()>&#9654; Play</button>
      <button class=sec onclick=seekBy(-1)>-1s</button>
      <button class=sec onclick=seekBy(1)>+1s</button>
      <span id=time class="dim mono">0.00 / 0.00 s</span>
      <span class=dim style="font-size:12px">one scrubber drives every camera</span>
    </div>
  </div>
</div>
<h2>Delivered</h2>
<div class=card><table>
<tr><th>Camera</th><th>Frames</th><th>fps</th><th>MB/s</th><th>Lost in flight</th>
<th>Ring drops</th><th>MJPEG</th><th>MP4</th><th>Download</th></tr>
%s</table>
<div class=dim style="margin-top:8px;font-size:12px">
"Lost in flight" counts gaps in the board's own frame numbering &mdash; frames
it encoded that never arrived. That is a different failure from a frame the
board never managed to make, and they are not added together.<br>
<b>Judging image quality? Use "original frame", not the video above.</b> The
player shows an x264 transcode, so an artefact there may be x264's rather than
the camera's. "original frame" serves the board's own JPEG byte-for-byte, which
is the only thing that answers what q70 or q90 really looks like.</div></div>
<h2>Manifest</h2>
<div class=card><pre>%s</pre></div>
<script>
const vs=[...document.querySelectorAll('video')];
const scrub=document.getElementById('scrub'), tlab=document.getElementById('time');
let dur=0, playing=false;
function offs(v){return parseFloat(v.dataset.offset||'0')||0;}
function refresh(){
  dur=Math.max(0,...vs.map(v=>(isFinite(v.duration)?v.duration+offs(v):0)));
  tlab.textContent=(master()).toFixed(2)+' / '+dur.toFixed(2)+' s';
}
function master(){ // wall-clock time of the session, not of any one clip
  const v=vs[0]; return v? v.currentTime+offs(v):0;
}
vs.forEach(v=>{
  v.addEventListener('loadedmetadata',refresh);
  v.addEventListener('timeupdate',()=>{
    if(v!==vs[0])return; refresh();
    if(dur>0)scrub.value=Math.round(1000*master()/dur);
  });
  v.addEventListener('ended',()=>{playing=false;document.getElementById('play').innerHTML='&#9654; Play';});
});
function seekAll(t){
  vs.forEach(v=>{
    const local=t-offs(v);
    if(isFinite(v.duration))v.currentTime=Math.max(0,Math.min(v.duration,local));
  });
  refresh();
}
scrub.addEventListener('input',()=>{ if(dur>0) seekAll(dur*scrub.value/1000); });
function seekBy(d){ seekAll(Math.max(0,Math.min(dur,master()+d))); }
function toggle(){
  playing=!playing;
  document.getElementById('play').innerHTML=playing?'&#10073;&#10073; Pause':'&#9654; Play';
  vs.forEach(v=>{ playing? v.play().catch(()=>{}) : v.pause(); });
}
refresh();
</script>
""" % (html.escape(m["name"]),
       html.escape(str(s.get("framesize", "?"))),
       html.escape(str(s.get("quality", "?"))),
       html.escape(str(s.get("fps_requested", "?"))),
       html.escape(str(s.get("duration_s", "?"))),
       html.escape(m.get("created_iso", "?")),
       html.escape(m.get("host", "?")),
       "".join(vids) or "<div class=dim>no playable files</div>",
       "".join(rows),
       html.escape(json.dumps(m, indent=1))))


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------

#: A path segment we will look up under the recordings root. Must start with an
#: alphanumeric, so "." and ".." and any dotfile are refused here rather than
#: relying solely on the realpath containment check below it. Both gates stay:
#: the regex is the cheap one, containment is the one that must not be wrong.
SAFE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def make_handler(state, root):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype, extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, code, obj):
            self._send(code, json.dumps(obj), "application/json")

        def _safe_path(self, name, fname):
            """Reject anything that is not a plain name in the root."""
            if not (SAFE.match(name or "") and SAFE.match(fname or "")):
                return None
            p = os.path.realpath(os.path.join(root, name, fname))
            if not p.startswith(os.path.realpath(root) + os.sep):
                return None
            return p if os.path.isfile(p) else None

        def _serve_still(self, path, n):
            """Serve the nth JPEG out of a .mjpeg, BYTE-EXACT from the board.

            This exists because the .mp4 beside it cannot answer "what does
            MJPEG q70 actually look like" -- it is an x264 transcode, so any
            artefact you see there might be x264's. Only the board's own bytes
            settle a quality question, and this hands them over untouched.

            Scanned in chunks rather than read whole: a 20 minute recording is
            2.7 GB and must not be loaded into RAM to fetch frame 50.
            """
            soi, eoi = b"\xff\xd8", b"\xff\xd9"
            buf = bytearray()
            idx = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        return self._send(404, "only %d frames in that clip" % idx,
                                          "text/plain")
                    buf += chunk
                    while True:
                        s = buf.find(soi)
                        if s < 0:
                            del buf[:max(0, len(buf) - 1)]
                            break
                        e = buf.find(eoi, s + 2)
                        if e < 0:
                            del buf[:s]
                            break
                        if idx == n:
                            return self._send(200, bytes(buf[s:e + 2]), "image/jpeg")
                        del buf[:e + 2]
                        idx += 1

        def _serve_file(self, path, download=False):
            """Serve with Range support -- without it, video seeking is dead."""
            size = os.path.getsize(path)
            ctype = CTYPES.get(os.path.splitext(path)[1], "application/octet-stream")
            extra = {"Accept-Ranges": "bytes"}
            if download:
                extra["Content-Disposition"] = ('attachment; filename="%s"'
                                                % os.path.basename(path))
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            code = 200
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
                if m:
                    g1, g2 = m.group(1), m.group(2)
                    if g1:
                        start = int(g1)
                        end = int(g2) if g2 else size - 1
                    elif g2:                       # suffix range: last N bytes
                        start = max(0, size - int(g2))
                    end = min(end, size - 1)
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */%d" % size)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    code = 206
                    extra["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
            length = end - start + 1
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(262144, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/healthz":
                return self._send(200, "ok", "text/plain")
            if path == "/api/status":
                return self._json(200, state.snapshot())
            if path == "/api/sessions":
                return self._json(200, R.load_sessions(root))
            if path == "/" or path == "/index.html":
                return self._send(200, index_page(state, R.load_sessions(root),
                                                  RR.load_ceilings()),
                                  "text/html; charset=utf-8")
            parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/")]
            if len(parts) == 2 and parts[0] == "view":
                for m in R.load_sessions(root):
                    if m.get("name") == parts[1]:
                        return self._send(200, viewer_page(m),
                                          "text/html; charset=utf-8")
                return self._send(404, "no such recording", "text/plain")
            if len(parts) == 3 and parts[0] in ("media", "download"):
                p = self._safe_path(parts[1], parts[2])
                if not p:
                    return self._send(404, "not found", "text/plain")
                return self._serve_file(p, download=(parts[0] == "download"))
            # /still/<session>/<camera>.mjpeg/<n> -- the board's original JPEG
            if len(parts) == 4 and parts[0] == "still":
                p = self._safe_path(parts[1], parts[2])
                if not p or not p.endswith(".mjpeg"):
                    return self._send(404, "not found", "text/plain")
                try:
                    n = int(parts[3])
                except ValueError:
                    return self._send(400, "frame index must be a number",
                                      "text/plain")
                if not (0 <= n < 1000000):
                    return self._send(400, "frame index out of range", "text/plain")
                return self._serve_still(p, n)
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if path != "/api/record":
                return self._send(404, "not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError) as e:
                return self._json(400, {"ok": False, "err": str(e)})
            try:
                fps = float(body.get("fps", 30))
                dur = float(body.get("duration", 5))
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "err": "bad number"})
            if not (0 < fps <= 200 and 0 < dur <= 3600):
                return self._json(400, {"ok": False, "err": "value out of range"})
            cams = [c for c in str(body.get("cameras", "N6,AE3")).split(",")
                    if c in ("N6", "AE3")]
            if not cams:
                return self._json(400, {"ok": False, "err": "no valid camera"})

            # Per-camera settings. Every field is validated against the same
            # enums the form offers -- nothing free-form reaches the board
            # config, and an unknown camera is dropped rather than passed on.
            raw = body.get("per_camera") or {}
            if not isinstance(raw, dict):
                return self._json(400, {"ok": False, "err": "per_camera must be an object"})
            per = {}
            for role, v in raw.items():
                if role not in ("N6", "AE3") or not isinstance(v, dict):
                    continue
                fs_r = v.get("framesize")
                if fs_r is not None and fs_r not in FRAMESIZES:
                    return self._json(400, {"ok": False,
                                            "err": "bad framesize for %s" % role})
                q_r = v.get("quality")
                if q_r is not None:
                    try:
                        q_r = int(q_r)
                    except (TypeError, ValueError):
                        return self._json(400, {"ok": False,
                                                "err": "bad quality for %s" % role})
                    if not (10 <= q_r <= 100):
                        return self._json(400, {"ok": False,
                                                "err": "quality out of range for %s" % role})
                per[role] = {"framesize": fs_r, "quality": q_r}

            ok, msg = state.start(fps=fps, duration_s=dur, cameras=cams,
                                  per_camera=per)
            return self._json(200 if ok else 409, {"ok": ok, "err": None if ok else msg})

    return H


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=8093)
    ap.add_argument("--root", default=os.path.expanduser("~/recordings"))
    a = ap.parse_args(argv)
    os.makedirs(a.root, exist_ok=True)
    state = RecorderState(a.root)
    # Serve FIRST, touch hardware later: the workbench health-gates LIVE on this
    # page answering within 60 s, and board discovery can take longer (D48).
    srv = QuietServer((a.bind, a.http_port), make_handler(state, a.root))
    print("recorder on http://%s:%d/  root=%s" % (a.bind, a.http_port, a.root),
          flush=True)

    def shutdown(signum, frame):
        # Ask an in-flight recording to END, then give it room to close the
        # file and write its manifest. The recipe declares stop_grace = 45 for
        # exactly this window; exiting immediately would strand a multi-GB clip
        # with no manifest, and the library would silently not show it.
        #
        # The waiting and the srv.shutdown() MUST happen on another thread.
        # BaseServer.shutdown() blocks until serve_forever() returns, and
        # serve_forever() runs on this thread -- calling it from inside the
        # handler deadlocks the process, which then ignores SIGINT *and*
        # SIGTERM. The workbench correctly refuses to SIGKILL, so the card
        # lands in "stuck" and the board stays held. Measured, once.
        print("recorder: signal %d -- closing any recording in flight"
              % signum, flush=True)
        state.stop.set()

        def finish():
            deadline = time.time() + 40
            while state.busy and time.time() < deadline:
                time.sleep(0.5)
            if state.busy:
                print("recorder: recording did not close within 40 s; exiting "
                      "anyway -- check the last session for a missing manifest",
                      flush=True)
            else:
                print("recorder: recording closed cleanly", flush=True)
            srv.shutdown()

        threading.Thread(target=finish, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        state.stop.set()
    print("recorder: stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
