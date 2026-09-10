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
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recorder as R                                        # noqa: E402
import record_run as RR                                     # noqa: E402
import storage as ST                                        # noqa: E402
import transcode as T                                       # noqa: E402

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
          ".json": "application/json", ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg"}


class RecorderState:
    """One recording at a time. A second request is refused, never queued."""

    def __init__(self, root, ring_bytes=ST.DEFAULT_RING_BYTES,
                 min_free_bytes=ST.DEFAULT_MIN_FREE_BYTES,
                 keep_latest=ST.DEFAULT_KEEP_LATEST):
        self.root = root
        #: The recordings store is CAPPED. Video can never grow into the
        #: filesystem, because the oldest sessions are evicted first.
        self.ring_bytes = ring_bytes
        self.min_free_bytes = min_free_bytes
        self.keep_latest = keep_latest
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
                                   stop_event=self.stop,
                                   ring_bytes=self.ring_bytes,
                                   min_free_bytes=self.min_free_bytes,
                                   keep_latest=self.keep_latest, **kw)
            self.last = res
            self.note("DONE: %s" % (res.get("summary") or "").replace("\n", " | "))
        except Exception as e:                              # noqa: BLE001
            import traceback
            self.note("FAILED: %s: %s" % (type(e).__name__, e))
            self.note(traceback.format_exc()[-600:])
            self.last = {"ok": False, "errors": [str(e)], "cameras": []}
        finally:
            self.busy = False



def host_health():
    """WiFi, CPU temperature, load and throttling for the metrics strip.

    WiFi comes from pi/field/netinfo, which already handles this rig's traps --
    `iw` lives in /sbin and is not on the pi user's PATH, so a viewer running
    as pi would otherwise report "unknown" signal forever.

    Temperature is read from sysfs rather than vcgencmd: it needs no subprocess
    and works on both rigs. `throttled` IS vcgencmd, and is worth having --
    the 45 min soak found the Pi 5 actively throttling during a transcode while
    recording never came close.
    """
    out = {"temp_c": None, "load1": None, "throttled": None,
           "throttled_now": None, "wifi": {}}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            out["temp_c"] = round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    try:
        with open("/proc/loadavg") as f:
            out["load1"] = float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        p = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                           text=True, timeout=5)
        val = (p.stdout or "").strip().split("=")[-1]
        out["throttled"] = val
        # Low 4 bits are the NOW bits; the high ones are sticky has-occurred.
        out["throttled_now"] = bool(int(val, 16) & 0xF) if val.startswith("0x") else None
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        import netinfo
        out["wifi"] = netinfo.net_status()
    except Exception:                                       # noqa: BLE001
        out["wifi"] = {}
    return out


class TranscodeQueue:
    """On-demand, one-at-a-time conversion of a recorded clip to playable mp4.

    Recording no longer transcodes automatically. Nick's reasoning, and it is
    sound on both counts: converting a clip the storage ring may delete unseen
    wastes energy, and if the rig is killed mid-dive he would rather lose the
    last 5 minutes than half of a 45 minute file. So the .mjpeg is the
    deliverable, and conversion is something he ASKS for on the clips he wants
    to watch -- paying the energy cost deliberately.

    The soak measured why that matters: capture held 52-70 C with no throttling,
    while a 10 minute x264 pass ran 64.8-86.7 C and threw active throttle bits.
    Recording is thermally cheap; converting is not.

    ONE worker, so two conversions can never compete for the same cores, and it
    runs as a daemon thread that a shutdown does not wait on -- a queued
    transcode is always redoable, unlike a recording.
    """

    #: Seconds of CPU per second of video, seeded from the S33 soak on this
    #: rig class (5 min of HD in ~205-226 s => ~0.71x realtime) and then
    #: LEARNED from real conversions, because the seed is one resolution on
    #: one rig and an estimate that never updates is a guess with a decimal
    #: point on it.
    DEFAULT_FACTOR = 0.71

    def __init__(self, root):
        self.root = root
        self._q = []
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self.current = None
        self.current_started = None
        self.current_estimate_s = None
        self.factor = self.DEFAULT_FACTOR
        self._observed = []
        self.done = []
        self.failed = []
        self._worker = threading.Thread(target=self._run)
        self._worker.daemon = True
        self._worker.start()

    def submit(self, session, camera):
        key = "%s/%s" % (session, camera)
        with self._wake:
            if key == self.current or key in self._q:
                return False, "already queued"
            self._q.append(key)
            self._wake.notify()
        return True, "queued"

    def clip_seconds(self, session, camera):
        """How long the clip actually runs, from the manifest it was written
        with. Frames over capture fps -- never wall time, which includes the
        gaps a dropped-frame run leaves behind."""
        try:
            with open(os.path.join(self.root, session, "manifest.json")) as f:
                man = json.load(f)
        except (OSError, ValueError):
            return None
        for c in man.get("cameras", []):
            if c.get("label") != camera:
                continue
            fps = c.get("capture_fps") or c.get("delivered_fps")
            frames = c.get("written_frames") or c.get("frames")
            if fps and frames:
                return float(frames) / float(fps)
            return None
        return None

    def estimate(self, session, camera):
        """(seconds, basis). None when the clip cannot be measured -- the page
        says 'unknown' rather than inventing a number."""
        secs = self.clip_seconds(session, camera)
        with self._lock:
            factor = self.factor
            samples = len(self._observed)
        if secs is None:
            return None, {"factor": factor, "samples": samples,
                          "why": "no frame count in the manifest"}
        return secs * factor, {"factor": round(factor, 3),
                               "samples": samples, "clip_s": round(secs, 1)}

    def _learn(self, session, camera, elapsed):
        secs = self.clip_seconds(session, camera)
        if not secs or elapsed <= 0:
            return
        with self._lock:
            self._observed.append(elapsed / secs)
            self._observed = self._observed[-10:]
            self.factor = sum(self._observed) / len(self._observed)

    def snapshot(self):
        with self._lock:
            started, est = self.current_started, self.current_estimate_s
            frac = None
            if started and est:
                frac = min(0.99, max(0.0, (time.time() - started) / est))
            return {"current": self.current, "queued": list(self._q),
                    "current_started": started,
                    "current_estimate_s": (round(est, 1) if est else None),
                    "current_elapsed_s": (round(time.time() - started, 1)
                                          if started else None),
                    "current_fraction": (round(frac, 3) if frac is not None
                                         else None),
                    "factor": round(self.factor, 3),
                    "factor_samples": len(self._observed),
                    "done": self.done[-20:], "failed": self.failed[-20:]}

    def _run(self):
        while True:
            with self._wake:
                while not self._q:
                    self._wake.wait(60)
                self.current = self._q.pop(0)
            sess, _, cam = self.current.partition("/")
            est, _ = self.estimate(sess, cam)
            t0 = time.time()
            with self._lock:
                self.current_started = t0
                self.current_estimate_s = est
            try:
                self._convert(self.current)
                self._learn(sess, cam, time.time() - t0)
            except Exception as e:                      # noqa: BLE001
                with self._lock:
                    self.failed.append({"job": self.current, "err": str(e)})
            finally:
                with self._lock:
                    self.current = None
                    self.current_started = None
                    self.current_estimate_s = None

    def _convert(self, key):
        session, camera = key.split("/", 1)
        sdir = os.path.join(self.root, session)
        mjpeg = os.path.join(sdir, "%s.mjpeg" % camera)
        mp4 = os.path.join(sdir, "%s.mp4" % camera)
        if not os.path.isfile(mjpeg):
            with self._lock:
                self.failed.append({"job": key, "err": "no .mjpeg for that camera"})
            return

        # The CAPTURE cadence is the timebase, exactly as the automatic path
        # used: the board timestamps nothing, so converting at anything else
        # plays the clip at the wrong speed.
        fps = 30.0
        man_path = os.path.join(sdir, "manifest.json")
        man = None
        try:
            with open(man_path) as f:
                man = json.load(f)
            for c in man.get("cameras", []):
                if c.get("label") == camera and c.get("capture_fps"):
                    fps = float(c["capture_fps"])
        except (OSError, ValueError, TypeError):
            pass

        print("transcode: %s at %.2f fps" % (key, fps), flush=True)
        res = T.transcode(mjpeg, mp4, fps)

        if res.get("ok") and man is not None:
            # Record it in the manifest so the library shows the mp4 without
            # having to guess from the filesystem.
            for c in man.get("cameras", []):
                if c.get("label") == camera:
                    c["mp4"] = "%s.mp4" % camera
                    c["transcode"] = res
            try:
                tmp = man_path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(man, f, indent=1)
                os.replace(tmp, man_path)
            except OSError:
                pass
        with self._lock:
            (self.done if res.get("ok") else self.failed).append(
                {"job": key, "wall_s": res.get("wall_s"),
                 "bytes": res.get("bytes"), "encoder": res.get("encoder"),
                 "err": None if res.get("ok") else res.get("stderr", "")[:200]})
        print("transcode: %s %s" % (key, "OK" if res.get("ok") else "FAILED"),
              flush=True)


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
.metrics{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 14px}
.metric{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);
  border-radius:8px;padding:10px 12px}
.metric .k{font-size:11px;color:var(--dim);text-transform:uppercase;
  letter-spacing:.05em}
.metric .v{font-size:19px;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.metric .s{font-size:11px;color:var(--dim)}
.thumbs{display:flex;gap:6px;margin:8px 0}
.thumbs img{width:150px;height:94px;object-fit:cover;border-radius:4px;
  border:1px solid var(--line);background:#000}
.thumbwrap{position:relative}
.thumbwrap b{position:absolute;left:4px;bottom:3px;font-size:10px;
  background:#000a;padding:1px 5px;border-radius:3px}
.chip{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;
  background:#22262e;color:var(--dim);margin-right:6px}
input[type=range]{width:100%}
.meter{background:#0b0d11;border:1px solid var(--line);border-radius:6px;
  height:22px;overflow:hidden;position:relative}
.meter>i{display:block;height:100%;background:var(--ok);transition:width .3s}
.meter.warn>i{background:var(--warn)} .meter.bad>i{background:var(--bad)}
.meter>span{position:absolute;left:8px;top:0;line-height:22px;font-size:11px;
  color:#e8eaed;text-shadow:0 1px 2px #000;font-family:ui-monospace,Menlo,monospace}
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


#: The record controls, kept verbatim so the recording server is unchanged.
RECORD_CARD = """<div class=card>
  <div class=row>
    <div><label>Target fps</label><input id=fps type=number value=30 min=1 max=120></div>
    <div><label>Duration (s)</label><input id=duration type=number value=180 min=1 max=3600></div>
    <div><label>Cameras</label><select id=cameras>
      <option value="N6,AE3,IMX">All three</option>
      <option value="N6,AE3">N6 + AE3</option>
      <option value="N6">N6 only</option>
      <option value="AE3">AE3 only</option>
      <option value="IMX">IMX708 only</option></select></div>
  </div>
  <div class=dim style="margin:12px 0 6px;font-size:12px">
    Each camera has its own settings, because they are not equals: HD q70 gives
    the N6 30.2 fps and the AE3 2.3. Defaults below are the measured picks.
  </div>
  <div class=row>@@CAMS@@</div>
  <div id=verdict class=dim style="margin:10px 0"></div>
  <button id=go onclick=startRec()>&#9679; Record</button>
  <a href="/" class=dim style="margin-left:10px">refresh</a>
</div>"""

#: Shown where the record controls would be on the review-only server. It
#: names WHERE recording lives rather than just hiding the panel -- a viewer
#: with no explanation reads as a page that has lost its buttons.
REVIEW_ONLY_PANEL = """<div class=card>
  <div class=dim style="font-size:13px;line-height:1.5">
    <b>Review only.</b> This page plays, converts and downloads what the rig
    has already recorded. Recording is started from the <b>workbench card</b>
    on port 8088 &mdash; that page owns the board lock, and two recorders on
    one board is the failure this rig knows best.
  </div>
</div>"""


def index_page(state, sessions, ceilings, review_only=False):
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
        for role in ("N6", "AE3", "IMX"):
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
        thumbs = "".join(
            "<div class=thumbwrap><img loading=lazy src='/media/%s/%s' alt='%s'>"
            "<b>%s</b></div>"
            % (urllib.parse.quote(m["name"]), urllib.parse.quote(c["thumb"]),
               html.escape(c.get("label", "")), html.escape(c.get("label", "")))
            for c in m.get("cameras", []) if c.get("thumb"))
        # Per camera: is it playable yet, and a button to make it so.
        acts = []
        for c in m.get("cameras", []):
            lbl = html.escape(c.get("label", "?"))
            if c.get("mp4"):
                acts.append("<span class=chip style='color:var(--ok)'>%s mp4 %s</span>"
                            % (lbl, _fmt_bytes(c.get("mp4_bytes", 0))))
            else:
                acts.append("<button class=sec style='padding:4px 10px;font-size:12px'"
                            " onclick=\"mk(event,'%s','%s')\">Make %s playable</button>"
                            % (html.escape(m["name"]), lbl, lbl))
        # Comparing cameras means watching them together, so offer the whole
        # session in one press rather than one press per camera.
        unconverted = [c.get("label") for c in m.get("cameras", [])
                       if not c.get("mp4") and c.get("mjpeg")]
        if len(unconverted) > 1:
            acts.insert(0, "<button style='padding:4px 10px;font-size:12px'"
                           " onclick=\"mkAll(event,'%s',%s)\">"
                           "Make all %d playable</button>"
                           % (html.escape(m["name"]),
                              html.escape(json.dumps(unconverted)),
                              len(unconverted)))
        per_cam = (s.get("per_camera") or {})
        setting_chips = "".join(
            "<span class=chip>%s %s q%s</span>"
            % (html.escape(r), html.escape(str(v.get("framesize", "?"))),
               html.escape(str(v.get("quality", "?"))))
            for r, v in sorted(per_cam.items())) or (
            "<span class=chip>%s</span><span class=chip>q%s</span>"
            % (html.escape(str(s.get("framesize", "?"))),
               html.escape(str(s.get("quality", "?")))))
        lib.append(
            "<div class=card><a href='/view/%s'><b>%s</b></a>"
            "<div class=dim>%s</div>"
            "<div class=thumbs>%s</div>"
            "<div style='margin-top:2px'>%s"
            "<span class=chip>%s fps asked</span><span class=chip>%ss</span>"
            "<span class=chip>%s</span></div>"
            "<div class=dim style='margin-top:6px'>%s</div>"
            "<div style='margin-top:8px;display:flex;gap:8px;flex-wrap:wrap'>%s</div>"
            "</div>"
            % (urllib.parse.quote(m["name"]), html.escape(m["name"]),
               html.escape(m.get("created_iso", "?")),
               thumbs or "<span class=dim style='font-size:12px'>no thumbnail</span>",
               setting_chips,
               html.escape(str(s.get("fps_requested", "?"))),
               html.escape(str(s.get("duration_s", "?"))),
               _fmt_bytes(total), html.escape(cams_txt), "".join(acts)))
    if not lib:
        lib = ["<div class=card class=dim>No recordings yet.</div>"]

    return _shell("Video recorder", _fill("""
<h1>Video recorder</h1>
<div class=metrics>
  <div class=metric><div class=k>WiFi</div><div class=v id=m_wifi>&hellip;</div>
    <div class=s id=m_wifi_s></div></div>
  <div class=metric><div class=k>CPU temp</div><div class=v id=m_temp>&hellip;</div>
    <div class=s id=m_temp_s></div></div>
  <div class=metric><div class=k>SD card</div><div class=v id=m_sd>&hellip;</div>
    <div class=s id=m_sd_s></div></div>
  <div class=metric><div class=k>Recording ring</div><div class=v id=m_ring>&hellip;</div>
    <div class=s id=m_ring_s></div></div>
</div>
@@RECORD@@
<h2>Storage</h2>
<div class=card>
  <div style="margin-bottom:12px">
    <label>Recording ring &mdash; capped; oldest sessions are deleted first</label>
    <div class=meter id=ringmeter><i id=ringbar style="width:0%"></i><span id=ringtxt>&hellip;</span></div>
  </div>
  <div>
    <label>SD card (whole filesystem)</label>
    <div class=meter id=sdmeter><i id=sdbar style="width:0%"></i><span id=sdtxt>&hellip;</span></div>
  </div>
  <div class=dim style="margin-top:10px;font-size:12px" id=storenote></div>
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
<div class=card style="padding:10px 14px">
  <div class=dim style="font-size:12px">Clips are kept as MJPEG and are <b>not</b>
  converted automatically &mdash; conversion is the expensive step (it drove this
  Pi to 86.7&nbsp;C and throttled it, where recording stayed at 55&nbsp;C), and a
  clip the ring later deletes should never have cost that. Press
  <b>Make&nbsp;playable</b> on the ones you want to watch.</div>
  <div id=tq style="margin-top:6px;font-size:12px"></div>
</div>
@@LIB@@
<script>
const CEIL = @@CEIL@@;
function camSettings(){
  const csel=document.getElementById('cameras');
  if(!csel) return {};
  const cams=csel.value.split(',');
  const out={};
  for(const c of cams){
    const fs=document.getElementById('fs_'+c), q=document.getElementById('q_'+c);
    if(fs&&q) out[c]={framesize:fs.value, quality:+q.value};
  }
  return out;
}
function verdict(){
  if(!document.getElementById('fps')) return;   // review-only: no record form
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
// Review-only builds ship no record form, so every one of these lookups
// returns null. Guarding here rather than shipping two scripts.
const RECORDING_UI = !!document.getElementById('go');
if(RECORDING_UI)
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
// Reload ONLY on the busy->idle edge, i.e. when a recording has just finished
// and the library needs to gain a row. The first version reloaded whenever it
// found the recorder idle -- which is almost always -- so the page reloaded
// every 1.2 s forever and flickered continuously, unusable on a tablet.
let wasBusy = false;
async function poll(){
  try{
    const j = await (await fetch('/api/status')).json();
    document.getElementById('log').textContent=(j.log||[]).join('\\n')||'idle';
    { const go=document.getElementById('go'); if(go) go.disabled=j.busy; }
    if(!j.busy && wasBusy){ location.reload(); return; }   // the edge, once
    wasBusy = j.busy;
    setTimeout(poll, j.busy ? 1000 : 4000);
  }catch(e){ setTimeout(poll, 5000); }
}
poll();
function gb(b){return (b/1e9).toFixed(2)+' GB';}
function setMeter(meter,bar,txt,pct,label){
  const el=document.getElementById(meter);
  el.classList.remove('warn','bad');
  if(pct>=90) el.classList.add('bad'); else if(pct>=75) el.classList.add('warn');
  document.getElementById(bar).style.width=Math.min(100,Math.max(0,pct))+'%';
  document.getElementById(txt).textContent=label;
}
async function storage(){
  try{
    const r=await fetch('/api/storage'); const s=await r.json();
    const rp = s.ring_used_pct||0;
    setMeter('ringmeter','ringbar','ringtxt', rp,
      gb(s.ring_used_bytes)+' of '+gb(s.ring_bytes)+'  ('+rp.toFixed(1)+'%)  '+s.sessions+(s.sessions===1?' recording':' recordings'));
    const sp = s.sd_used_pct||0;
    setMeter('sdmeter','sdbar','sdtxt', sp,
      gb(s.sd_used_bytes)+' of '+gb(s.sd_total_bytes)+'  ('+sp.toFixed(1)+'%)  '+gb(s.sd_free_bytes)+' free');
    document.getElementById('storenote').innerHTML =
      'When the ring is full the oldest recording is deleted to make room, so the card cannot fill up. '+
      'The newest '+ (s.keep_latest!==undefined?s.keep_latest:2) +' are never deleted, nor is a recording in progress. '+
      (s.oldest? ('Oldest: <b>'+s.oldest+'</b>. ') : '') +
      'A floor of '+gb(s.min_free_bytes)+' free is enforced on the card itself as well.';
  }catch(e){}
}
storage(); setInterval(storage, 5000);
function tint(el, pct, warn, bad){
  el.style.color = pct>=bad ? 'var(--bad)' : (pct>=warn ? 'var(--warn)' : 'var(--fg)');
}
async function health(){
  try{
    const h = await (await fetch('/api/health')).json();
    const w = h.wifi||{}, st = h.storage||{};
    const dbm = (w.signal_dbm!==undefined && w.signal_dbm!==null) ? w.signal_dbm : null;
    const mw=document.getElementById('m_wifi');
    mw.textContent = dbm!==null ? (dbm+' dBm') : (w.iface? 'no signal' : 'unknown');
    // -50 excellent, -60 good, -70 fair, below -75 is where this rig drops.
    mw.style.color = dbm===null? 'var(--dim)'
        : dbm>=-60? 'var(--ok)' : dbm>=-72? 'var(--warn)' : 'var(--bad)';
    document.getElementById('m_wifi_s').textContent =
      [w.iface, w.ssid, (w.grade||''), (w.rx_bitrate_mbps? w.rx_bitrate_mbps+' Mb/s rx':'')]
      .filter(Boolean).join(' \u00b7 ') || 'no wireless link';

    const mt=document.getElementById('m_temp');
    mt.textContent = h.temp_c!==null? h.temp_c.toFixed(1)+' \u00b0C' : 'n/a';
    if(h.temp_c!==null) tint(mt, h.temp_c, 70, 80);
    document.getElementById('m_temp_s').textContent =
      (h.load1!==null? 'load '+h.load1.toFixed(2):'') +
      (h.throttled_now? '  \u00b7 THROTTLING NOW' : (h.throttled? '  \u00b7 '+h.throttled : ''));
    document.getElementById('m_temp_s').style.color = h.throttled_now? 'var(--bad)':'var(--dim)';

    const sp = st.sd_used_pct||0;
    const ms=document.getElementById('m_sd');
    ms.textContent = sp.toFixed(1)+'%'; tint(ms, sp, 75, 90);
    document.getElementById('m_sd_s').textContent =
      gb(st.sd_free_bytes)+' free of '+gb(st.sd_total_bytes);

    const rp = st.ring_used_pct||0;
    const mr=document.getElementById('m_ring');
    mr.textContent = rp.toFixed(1)+'%'; tint(mr, rp, 75, 90);
    document.getElementById('m_ring_s').textContent =
      gb(st.ring_used_bytes)+' of '+gb(st.ring_bytes)+' \u00b7 '+
      st.sessions+(st.sessions===1?' recording':' recordings');
  }catch(e){}
}
health(); setInterval(health, 5000);
async function mk(ev, session, camera){
  const b=ev.target; b.disabled=true; b.textContent='queued\u2026';
  const r=await fetch('/api/transcode',{method:'POST',
    body:JSON.stringify({session:session,camera:camera})});
  const j=await r.json();
  if(!j.ok){ alert(j.err||'refused'); b.disabled=false; b.textContent='retry'; return; }
  tqPoll();
}
async function mkAll(ev, session, cams){
  const b=ev.target; b.disabled=true; b.textContent='queued '+cams.length+'\u2026';
  for(const c of cams){
    await fetch('/api/transcode',{method:'POST',
      body:JSON.stringify({session:session,camera:c})});
  }
  tqPoll();
}
async function tqPoll(){
  const r=await fetch('/api/transcode'); const j=await r.json();
  const el=document.getElementById('tq');
  const busy = j.current || (j.queued && j.queued.length);
  if(busy){
    el.innerHTML = '<b>Converting:</b> '+(j.current||'-')+
      (j.queued.length? '  &middot; queued: '+j.queued.join(', '):'')+
      '  <span class=dim>(this is the energy-expensive step; recording is not)</span>';
    setTimeout(tqPoll, 3000);
  } else {
    const last=(j.done||[]).slice(-1)[0];
    el.innerHTML = last ? ('<span class=ok>Last converted:</span> '+last.job+
        ' &mdash; '+(last.wall_s||0).toFixed(1)+' s, '+((last.bytes||0)/1e6).toFixed(0)+' MB'+
        ' <a href="/">refresh to play it</a>') : '';
    if((j.failed||[]).length){
      const f=j.failed.slice(-1)[0];
      el.innerHTML += ' <span class=bad>Last failure: '+f.job+' &mdash; '+(f.err||'')+'</span>';
    }
  }
}
tqPoll();
</script>
""",
        RECORD=(REVIEW_ONLY_PANEL if review_only
                else _fill(RECORD_CARD, CAMS=cam_html)),
        CAMS="",
        ROWS="".join(rows) or "<tr><td colspan=3 class=dim>none measured</td></tr>",
        LIB="".join(lib), CEIL=json.dumps(ceilings)))


#: Display order in the viewer. Nick, 2026-09-09: "IMX should be at the top
#: since that is my reference video for the dive." The proxy sits directly
#: under its own science stream, and anything unknown falls to the end rather
#: than being hidden -- a camera missing from this list must still appear.
CAMERA_ORDER = ("IMX", "IMX_proxy", "N6", "AE3")


def _cam_rank(label):
    try:
        return CAMERA_ORDER.index(label)
    except ValueError:
        return len(CAMERA_ORDER)


def viewer_page(m):
    s = m.get("settings", {})
    cams = [c for c in m.get("cameras", []) if c.get("mp4") or c.get("mjpeg")]
    cams.sort(key=lambda c: _cam_rank(c.get("label", "")))
    vids, rows = [], []
    for i, c in enumerate(cams):
        label = html.escape(c.get("label", "?"))
        src = c.get("mp4")
        note = ""
        if not src:
            # Not an error: clips are kept as MJPEG and converted only on
            # request. Offer the conversion rather than a player that cannot
            # play, and show the thumbnail so the clip is still identifiable.
            src = c.get("mjpeg")
            th = ("<img src='/media/%s/%s' style='width:100%%;border-radius:6px'>"
                  % (urllib.parse.quote(m["name"]),
                     urllib.parse.quote(c.get("thumb") or ""))
                  if c.get("thumb") else "")
            # Thumbnail and button only (Nick, 2026-09-09). The standing
            # paragraph appeared under every unconverted clip, three times
            # a page; the cost is now stated once, at the moment it
            # matters, in the confirm dialog that names the real minutes.
            note = ("%s<button onclick=\"mkv(event,'%s','%s')\">"
                    "Make playable</button>"
                    % (th, html.escape(m["name"]),
                       html.escape(c.get("label", ""))))
        geom = html.escape("%dx%d" % (c.get("banner", {}).get("w", 0),
                                      c.get("banner", {}).get("h", 0)))
        if c.get("mp4"):
            # Click to go full screen. On an iPad the tiled view is small,
            # and the point of the review loop is actually LOOKING at the
            # footage. title= says so, because a video that silently does
            # something on click is a video nobody clicks.
            body = ("<video id=v%d preload=metadata src='/media/%s/%s' "
                    "data-offset='%s' onclick='goFull(this)' "
                    "style='cursor:zoom-in' "
                    "title='Click for full screen'></video>"
                    % (i, urllib.parse.quote(m["name"]),
                       urllib.parse.quote(src), c.get("start_offset_s", 0)))
        else:
            body = note
            note = ""
        vids.append("<div class=vid><b>%s</b> <span class=dim>%s</span>%s%s</div>"
                    % (label, geom, note, body))
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
function goFull(v){
  // Click any clip to fill the screen. Safari on iOS exposes only the
  // webkit entry point, and it is the browser this has to work in.
  if(v.requestFullscreen) v.requestFullscreen().catch(()=>{});
  else if(v.webkitEnterFullscreen) v.webkitEnterFullscreen();
  else if(v.webkitRequestFullscreen) v.webkitRequestFullscreen();
}

function humanT(sec){
  if(sec==null || !isFinite(sec)) return 'unknown';
  const s=Math.round(sec);
  if(s<90) return s+' s';
  const m=Math.round(s/60);
  return m<90 ? m+' min' : (s/3600).toFixed(1)+' h';
}

async function mkv(ev, session, camera){
  const b=ev.target;
  // ASK BEFORE SPENDING THE HEAT. Converting is the expensive operation on
  // this rig -- capture holds 52-70 C, an x264 pass throws throttle bits --
  // so the cost is stated up front and the answer is the operator's.
  let est=null, basis={};
  try{
    const e=await (await fetch('/api/transcode/estimate?session='
      +encodeURIComponent(session)+'&camera='+encodeURIComponent(camera))).json();
    est=e.seconds; basis=e.basis||{};
  }catch(err){ /* fall through and ask anyway */ }
  const how = basis.samples
    ? ' (from '+basis.samples+' previous conversion'+(basis.samples===1?'':'s')+' on this rig)'
    : ' (estimated; not yet measured on this rig)';
  const msg = est==null
    ? 'Cannot estimate how long this will take'+(basis.why?' — '+basis.why:'')
      +'.\\n\\nConvert anyway?'
    : 'This will take about '+humanT(est)+how+'.\\n\\nConvert '+camera+' now?';
  if(!window.confirm(msg)) return;

  b.disabled=true; b.textContent='converting\u2026';
  const bar=document.createElement('div');
  bar.style.cssText='margin:8px 0;height:10px;border-radius:5px;background:#222;overflow:hidden';
  const fill=document.createElement('i');
  fill.style.cssText='display:block;height:100%%;width:0%%;background:#2c6e49;transition:width 1s linear';
  bar.appendChild(fill);
  const lab=document.createElement('div');
  lab.className='dim'; lab.style.fontSize='12px';
  lab.textContent='queued\u2026';
  b.parentNode.insertBefore(bar,b.nextSibling);
  b.parentNode.insertBefore(lab,bar.nextSibling);

  const r=await fetch('/api/transcode',{method:'POST',
    body:JSON.stringify({session:session,camera:camera})});
  const j=await r.json();
  if(!j.ok){ alert(j.err||'refused'); b.disabled=false; b.textContent='retry';
             bar.remove(); lab.remove(); return; }
  (async function wait(){
    const q=await (await fetch('/api/transcode')).json();
    const key=session+'/'+camera;
    if(q.current===key){
      const f=q.current_fraction;
      // The bar is time-against-estimate, and it says so rather than
      // pretending to know how many frames ffmpeg has actually written.
      fill.style.width=((f==null?0:f)*100).toFixed(1)+'%%';
      const left=(q.current_estimate_s!=null && q.current_elapsed_s!=null)
        ? Math.max(0,q.current_estimate_s-q.current_elapsed_s) : null;
      lab.textContent='converting \u2014 '+humanT(q.current_elapsed_s)+' elapsed'
        +(left!=null? ', about '+humanT(left)+' left (estimate)':'');
      setTimeout(wait,1000); return;
    }
    if((q.queued||[]).includes(key)){
      lab.textContent='queued behind '+(q.current||'another clip')+'\u2026';
      setTimeout(wait,2000); return;
    }
    if((q.done||[]).some(d=>d.job===key)){
      fill.style.width='100%%'; lab.textContent='done \u2014 reloading\u2026';
      location.reload(); return;
    }
    const f=(q.failed||[]).find(d=>d.job===key);
    b.disabled=false; b.textContent = f? ('failed: '+(f.err||'').slice(0,60)) : 'retry';
  })();
}
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


def make_handler(state, root, tq, review_only=False):
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
            if path == "/api/transcode":
                return self._json(200, tq.snapshot())
            if path == "/api/transcode/estimate":
                q = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query)
                sess = (q.get("session") or [""])[0]
                cam = (q.get("camera") or [""])[0]
                if not SAFE.match(sess or ""):
                    return self._json(400, {"ok": False, "err": "bad session"})
                secs, basis = tq.estimate(sess, cam)
                return self._json(200, {"ok": True, "seconds": secs,
                                        "basis": basis})
            if path == "/api/health":
                h = host_health()
                h["storage"] = ST.status(root, state.ring_bytes,
                                         state.min_free_bytes)
                return self._json(200, h)
            if path == "/api/storage":
                st = ST.status(root, state.ring_bytes, state.min_free_bytes)
                st["keep_latest"] = state.keep_latest
                return self._json(200, st)
            if path == "/" or path == "/index.html":
                return self._send(200, index_page(state, R.load_sessions(root),
                                                  RR.load_ceilings(),
                                                  review_only),
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
            if path == "/api/transcode":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                except (ValueError, OSError) as e:
                    return self._json(400, {"ok": False, "err": str(e)})
                sess = str(body.get("session", ""))
                cam = str(body.get("camera", ""))
                if not (SAFE.match(sess) and cam in ("N6", "AE3", "IMX")):
                    return self._json(400, {"ok": False, "err": "bad session or camera"})
                if not os.path.isfile(os.path.join(root, sess, "%s.mjpeg" % cam)):
                    return self._json(404, {"ok": False, "err": "no clip for that camera"})
                ok, msg = tq.submit(sess, cam)
                return self._json(200, {"ok": ok, "err": None if ok else msg})
            if path != "/api/record":
                return self._send(404, "not found", "text/plain")
            if review_only:
                # This instance runs ALONGSIDE a dive recording so the library
                # is always reachable. Two processes recording the same boards
                # is the failure this rig knows best (SPEC: one owner per board
                # port, ever), so the always-on page cannot become a second
                # recorder -- the UI is gone AND the endpoint refuses.
                return self._json(403, {
                    "ok": False,
                    "err": "this is the review-only server -- recording is "
                           "started from the workbench card, not here"})
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
                    if c in ("N6", "AE3", "IMX")]
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
                if role not in ("N6", "AE3", "IMX") or not isinstance(v, dict):
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
    ap.add_argument("--ring-gb", type=float, default=ST.DEFAULT_RING_BYTES / 1e9,
                    help="cap on the recordings directory; oldest sessions are "
                         "evicted first so the filesystem cannot fill")
    ap.add_argument("--min-free-gb", type=float,
                    default=ST.DEFAULT_MIN_FREE_BYTES / 1e9,
                    help="free space floor enforced on the card itself, "
                         "independently of the ring budget")
    ap.add_argument("--review-only", action="store_true",
                    help="serve the library for playback/convert/download but "
                         "REFUSE to record (the always-on review server)")
    ap.add_argument("--keep-latest", type=int, default=ST.DEFAULT_KEEP_LATEST,
                    help="newest N recordings are never evicted")
    a = ap.parse_args(argv)
    os.makedirs(a.root, exist_ok=True)
    state = RecorderState(a.root, int(a.ring_gb * 1e9),
                          int(a.min_free_gb * 1e9), a.keep_latest)
    print("recordings ring: %.1f GB, min free %.1f GB, keep newest %d"
          % (a.ring_gb, a.min_free_gb, a.keep_latest), flush=True)
    # Serve FIRST, touch hardware later: the workbench health-gates LIVE on this
    # page answering within 60 s, and board discovery can take longer (D48).
    tq = TranscodeQueue(a.root)
    srv = QuietServer((a.bind, a.http_port),
                      make_handler(state, a.root, tq, a.review_only))
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
