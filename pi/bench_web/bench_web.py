#!/usr/bin/env python3
"""bench_web.py — S18 bites C1 + C2: the camera bench control page.

Serves the operator page on :8090 and turns its clicks into commands on the
bite-B control socket (``/run/bm/bench.sock``). Stdlib only, same rule as the
frozen S3 stream server.

Three things it does that a thin proxy would not:

  * **The click guard lives here, not in the browser.** A sensor re-init that
    arrives too soon after a capture throws ``Sensor control failed.`` and
    wedges the camera for the rest of the bridge's life (SPEC §Open
    questions). A guard that lives only in JavaScript is defeated by a page
    reload or a second tab, so the server is what enforces it; the page
    mirrors the gate so the operator can see why a button is grey.

  * **It gates on the SAVE state, not on the camera's mode.** ``mode_active``
    in the camera reply is *currently commanded*, not *currently busy* — it
    stays 1 after a still completes and only a ``stop`` clears it
    (``firmware/bm_he/src/camera_svc.c``). Bite B's save counters are what
    actually move when a frame lands, so they are the completion signal.

  * **It re-uses bench_ctl.py** rather than speaking the socket itself, so the
    web tool, ``bench-ctl.sh`` and the trial scripts cannot drift on framing,
    the bound reply address or the id match.

It never touches the frozen S3 ingest. The live view is an ``<img>`` pointed
straight at that server's ``/stream`` — no bytes are copied through here.

Bite C2 adds the gallery of stored captures, and with it the only two routes
that move image bytes:

  * ``/captures/<name>.jpg`` reads a stored still off the disk. Every path it
    can be made to open is fenced three independent ways — see
    ``CaptureStore``, which is where the whole argument lives.

  * ``/api/frame.jpg`` fetches the frozen S3 server's cached latest frame and
    hands it back **same-origin**, on demand only. This exists for one
    concrete reason: the live view is an ``<img>`` on port 8080, a different
    origin, so drawing it into a canvas taints the canvas and ``getImageData``
    throws — there is no live histogram without a same-origin copy. It reads
    that server's in-memory ``/frame.jpg`` like any browser would; the
    single-producer ingest on ``:8081`` is still never touched.

Run:  python3 pi/bench_web/bench_web.py        # page at http://<host>:8090/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bm_bench")
)
from bench_ctl import BenchCtl, BenchCtlError  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
BODY_MAX = 4096  # a command object is tens of bytes; anything larger is junk

# Accepted by the camera commands. Kept in one place so a typo in the page
# cannot reach the socket, and so the refusal is ours (clear) rather than the
# bridge's (a wedged sensor is the alternative).
RES_OK = ("qvga", "vga", "hd")
PF_OK = ("color", "mono")

# Bite B's own filename shape: cap_20260816T223333Z_seq000395. Nothing else is
# a capture, and the character set has no separator, no dot and no NUL in it.
CAPTURE_RE = re.compile(r"^cap_\d{8}T\d{6}Z_seq\d{6}$")


class CaptureStore:
    """Read-only view of ``~/bench_captures``. The gallery enumerates SIDECARS.

    The sidecar is the commit record: bite B writes the JPEG and renames it
    *before* the sidecar, so a sidecar can never name a half-written image,
    while a stray ``.jpg`` may well be one. Listing the images instead would
    be listing the thing that is allowed to be incomplete.

    **Nothing an operator types ever becomes a path.** A request for a stored
    still passes three independent fences, any one of which would do on its
    own:

      1. The name is URL-decoded *first*, then matched whole against
         ``CAPTURE_RE``. ``../``, ``..%2f``, an absolute path, a backslash and
         an embedded NUL all fail the character set, and decoding first is
         what stops ``%2e%2e`` sneaking past a regex applied too early.
      2. It must be a capture we would have listed — ``<stem>.json`` has to
         exist in the root. So the reachable set is exactly the set of
         committed captures.
      3. ``realpath`` of the assembled path must land back on the path we
         assembled. That is what catches a symlink planted *inside* the
         directory, which the first two fences would happily accept.

    Note what is deliberately NOT used: the sidecar's own ``file`` field. It
    is our app's data, but it is data, and building a path out of it would
    hand path construction to file contents. The JPEG name is derived from the
    sidecar's *filename* (already regex-clean), and a disagreement with the
    ``file`` field is reported rather than followed.

    The root is fixed at startup. A status reply carries ``save.dir``, but a
    remote-supplied directory must never be able to move the fence, so that
    field is only ever compared against this root, never assigned to it.
    """

    LIST_MAX = 60          # a strip the operator can actually scan
    JPEG_MAX = 8 << 20     # HD lands at ~42 KB; this only bounds a surprise

    def __init__(self, root):
        self.root = os.path.realpath(os.path.expanduser(root))

    # -- listing -----------------------------------------------------------
    def listing(self, limit=LIST_MAX):
        try:
            names = sorted((n for n in os.listdir(self.root)
                            if n.endswith(".json") and CAPTURE_RE.match(n[:-5])),
                           reverse=True)          # filenames sort as timestamps
        except OSError as e:
            return {"ok": False, "dir": self.root, "items": [], "skipped": 0,
                    "err": "cannot read %s: %s" % (self.root, e.strerror or e)}
        items, skipped = [], 0
        for name in names[:limit]:
            item = self._item(name)
            if item is None:
                skipped += 1
            else:
                items.append(item)
        return {"ok": True, "dir": self.root, "items": items,
                "skipped": skipped, "total": len(names)}

    def _item(self, name):
        """One sidecar → one gallery entry, or None if it will not parse."""
        stem = name[:-5]
        try:
            with open(os.path.join(self.root, name), "rb") as fh:
                side = json.loads(fh.read().decode("utf-8", "replace"))
            if not isinstance(side, dict):
                return None
        except (OSError, ValueError):
            return None
        jpeg = stem + ".jpg"
        try:
            size = os.path.getsize(os.path.join(self.root, jpeg))
        except OSError:
            size = None      # sidecar without an image: shown, and marked
        req, frame = side.get("req") or {}, side.get("frame") or {}
        reply, ledger = side.get("reply") or {}, side.get("ledger") or {}
        return {
            "stem": stem,
            "jpeg": jpeg,
            "utc": side.get("utc") or stem[4:20],
            "source": side.get("source"),
            "on_disk": size,
            # The sidecar named a different file than it is paired with. Never
            # followed (see the class docstring) — reported, because it means
            # something wrote this directory that bite B did not.
            "file_field": side.get("file"),
            "name_mismatch": bool(side.get("file")) and side.get("file") != jpeg,
            "req": {"q": req.get("q"), "res": req.get("res"), "pf": req.get("pf")},
            "reply": {"ok": reply.get("ok"), "res": reply.get("res"),
                      "pf": reply.get("pf"), "pub_errs": reply.get("pub_errs"),
                      "seen": reply.get("seen")},
            "frame": {"seq": frame.get("seq"), "size_bytes": frame.get("size_bytes"),
                      "chunks": frame.get("chunks")},
            "ledger": {"gaps_delta": ledger.get("gaps_delta"),
                       "dropped_delta": ledger.get("dropped_delta")},
        }

    # -- one stored still --------------------------------------------------
    def resolve(self, name):
        """URL name → an absolute path inside the root. Raises KeyError."""
        name = unquote(name)
        if not name.endswith(".jpg") or not CAPTURE_RE.match(name[:-4]):
            raise KeyError("not a capture name")            # fence 1
        path = os.path.join(self.root, name)
        if not os.path.isfile(os.path.join(self.root, name[:-4] + ".json")):
            raise KeyError("no sidecar: not a committed capture")    # fence 2
        if os.path.realpath(path) != path or os.path.islink(path):
            raise KeyError("resolves outside the capture directory")  # fence 3
        if not os.path.isfile(path):
            raise KeyError("image is missing")
        if os.path.getsize(path) > self.JPEG_MAX:
            raise KeyError("image is implausibly large")
        return path

    def read(self, name):
        with open(self.resolve(name), "rb") as fh:
            return fh.read(self.JPEG_MAX + 1)[:self.JPEG_MAX]


def fetch_live_frame(host, port, timeout=2.0, cap=CaptureStore.JPEG_MAX):
    """The frozen S3 server's cached latest frame → (code, bytes-or-message).

    A plain GET of a frame it already holds in memory, which is what
    ``/frame.jpg`` is for; it neither opens a stream nor touches the ingest.
    """
    url = "http://%s:%d/frame.jpg" % (host, port)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200, r.read(cap + 1)[:cap]
    except urllib.error.HTTPError as e:
        # 503 is the honest "no frame received yet" — pass it through as-is.
        return e.code, ("stream server: %s" % e.reason).encode()
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 503, ("no frame from %s: %s — is t1l-stream-server running?"
                     % (url, e)).encode()


class BenchGate:
    """Decides when the next camera command may go out. No I/O; injected clock.

    Two independent holds, and the page shows which one is active:

      **BUSY** — a capture is in flight, or a stream is running. One camera
      command at a time, always.

      **SETTLE** — the previous capture finished less than ``settle`` seconds
      ago *and* the next command would change resolution or pixel format.
      Only a genuine geometry delta re-inits the sensor (S18 bite A), so
      QVGA→QVGA colour repeats are never held; the transition that can wedge
      the bench is.

    Measured basis for ``settle`` (SPEC §Open questions; S18 bite B2
    on-chain ladder 2026-08-17): the required quiet time scales with the
    previous frame's published bytes (~1.5 s/KB fits every measured
    point). QVGA-source re-inits pass at 6.3 s; **VGA-source fails at
    10 s and passes at 15 s** (dark frames — daylight is ~2.6× bigger);
    HD is UNMEASURED. The default is 20 s — the safe side of the
    measured VGA boundary, matching the bridge's own
    ``REINIT_MIN_QUIET_MS`` gate underneath (bm_bridge.py). The
    reef-scene matrix session owes the daylight/HD numbers before this
    constant is lowered.

    ``stop`` is never gated. It is the escape hatch, and a bench tool whose
    stop button can be greyed out is worse than no stop button.
    """

    # Bite B's still-save gives up 8 s after arming; this is that plus slack,
    # so a capture whose frame never arrives cannot hold the gate for ever.
    CAPTURE_GRACE = 12.0

    def __init__(self, settle: float = 20.0, clock=time.monotonic):
        self.settle = float(settle)
        self._clock = clock
        self.mode = "idle"  # idle | capture | stream
        self.armed_at = 0.0
        self.saves_at_arm = 0
        self.errors_at_arm = 0
        self.stream_until = 0.0
        self.settle_until = 0.0
        self.last_res = None  # what the sensor was last asked for, by us
        self.last_pf = None
        self.last_release = ""

    # -- queries -----------------------------------------------------------
    def check(self, res, pf, now=None):
        """May a camera command for (res, pf) go out? → (ok, reason, retry_in)."""
        now = self._clock() if now is None else now
        if self.mode == "capture":
            return (False, "a capture is still in flight",
                    max(0.0, self.armed_at + self.CAPTURE_GRACE - now))
        if self.mode == "stream":
            return (False, "a stream is running — press Stop first",
                    max(0.0, self.stream_until - now))
        if res != self.last_res or pf != self.last_pf:
            wait = self.settle_until - now
            if wait > 0:
                return (False,
                        "sensor settle after the last capture — %s %s needs a "
                        "sensor re-init, and one too soon wedges the camera"
                        % (res, pf), wait)
        return (True, "", 0.0)

    def snapshot(self, now=None):
        now = self._clock() if now is None else now
        ok, reason, retry = self.check(self.last_res, self.last_pf, now)
        # `check` above answers for a repeat of the last mode; report the
        # settle window separately so the page can grey the OTHER modes too.
        return {
            "mode": self.mode,
            "busy": self.mode != "idle",
            "ready": ok,
            "reason": reason,
            "retry_in": round(retry, 1),
            "settle": self.settle,
            "settle_in": round(max(0.0, self.settle_until - now), 1),
            "res": self.last_res,
            "pf": self.last_pf,
            "last_release": self.last_release,
            "stream_left": round(max(0.0, self.stream_until - now), 1)
            if self.mode == "stream" else 0.0,
        }

    # -- transitions -------------------------------------------------------
    def arm_capture(self, res, pf, status, now=None):
        now = self._clock() if now is None else now
        save = (status or {}).get("save") or {}
        self.mode = "capture"
        self.armed_at = now
        # Counters, not the state string: `save.state` still reads "saved"
        # from the PREVIOUS capture at the moment we arm, and would release
        # the gate on the very next poll.
        self.saves_at_arm = _int(save.get("saved"))
        self.errors_at_arm = _int(save.get("errors"))
        self.last_res, self.last_pf = res, pf

    def arm_stream(self, res, pf, secs, now=None):
        now = self._clock() if now is None else now
        self.mode = "stream"
        self.armed_at = now
        self.stream_until = now + max(0.0, float(secs or 0))
        self.last_res, self.last_pf = res, pf

    def note_stop(self, now=None):
        """A stop ends the command but leaves a hot sensor — settle still applies."""
        self._release("stopped", self._clock() if now is None else now)

    def observe(self, status, now=None):
        """Fold a fresh status snapshot in. Called on every poll."""
        now = self._clock() if now is None else now
        if self.mode == "capture":
            save = (status or {}).get("save") or {}
            if _int(save.get("saved")) > self.saves_at_arm:
                self._release("saved", now)
            elif _int(save.get("errors")) > self.errors_at_arm:
                self._release("save error", now)
            elif now - self.armed_at >= self.CAPTURE_GRACE:
                # No frame and no error inside the save's own timeout: the
                # capture is not coming. Release rather than wedge the page.
                self._release("no frame within %.0f s" % self.CAPTURE_GRACE, now)
        elif self.mode == "stream" and now >= self.stream_until:
            self._release("stream elapsed", now)

    def _release(self, why, now):
        if self.mode == "idle":
            return
        self.mode = "idle"
        self.last_release = why
        self.settle_until = now + self.settle


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


class Bench:
    """The control socket plus the gate, behind one lock.

    One lock for both, so two browser tabs cannot interleave a check and an
    arm — which is exactly the fast-click race the gate exists to stop.
    """

    def __init__(self, gate: BenchGate, path=None, timeout=3.0):
        self.gate = gate
        self._lock = threading.Lock()
        self._ctl = BenchCtl(path=path, timeout=timeout)
        self._open = False

    def _request(self, obj):
        """Caller holds the lock. Reopens once, so a telemetry restart does
        not need a web-server restart."""
        try:
            if not self._open:
                self._ctl.open()
                self._open = True
            return self._ctl.request(obj)
        except BenchCtlError:
            self._ctl.close()
            self._open = False
            raise

    def status(self):
        with self._lock:
            st = self._request({"cmd": "status"})
            self.gate.observe(st)
            return st, self.gate.snapshot()

    def camera(self, cmd: dict, res, pf, secs=None):
        """Gated. Returns (http_code, body)."""
        with self._lock:
            # Arm from a FRESH status, never a cached one: the save counters
            # are the completion signal, and starting from a stale count can
            # release the gate before the frame lands.
            st = self._request({"cmd": "status"})
            self.gate.observe(st)
            ok, reason, retry = self.gate.check(res, pf)
            if not ok:
                return 409, {"ok": False, "err": reason,
                             "retry_in": round(retry, 1),
                             "gate": self.gate.snapshot()}
            rep = self._request(cmd)
            if rep.get("ok"):
                if cmd["cmd"] == "stream":
                    self.gate.arm_stream(res, pf, secs)
                else:
                    self.gate.arm_capture(res, pf, st)
            return 200, {"ok": bool(rep.get("ok")), "reply": rep,
                         "gate": self.gate.snapshot()}

    def stop(self):
        with self._lock:
            rep = self._request({"cmd": "stop"})
            self.gate.note_stop()
            return 200, {"ok": bool(rep.get("ok")), "reply": rep,
                         "gate": self.gate.snapshot()}

    def passthrough(self, cmd: dict):
        """Light and strobe: a different node, no sensor, so no gate."""
        with self._lock:
            rep = self._request(cmd)
            return 200, {"ok": bool(rep.get("ok")), "reply": rep,
                         "gate": self.gate.snapshot()}


# ---------------------------------------------------------------------------
# Request parsing — every operator-supplied value is checked here, so nothing
# unvalidated reaches the socket.
# ---------------------------------------------------------------------------

def build_camera_cmd(verb: str, body: dict):
    """→ (cmd, res, pf, secs) or raises ValueError with an operator-readable why."""
    res = str(body.get("res", "")).lower()
    pf = str(body.get("pf", "")).lower()
    if res not in RES_OK:
        raise ValueError("res must be one of %s" % ", ".join(RES_OK))
    if pf not in PF_OK:
        raise ValueError("pf must be one of %s" % ", ".join(PF_OK))
    q = _num(body, "q", 10, 95, int)
    cmd = {"cmd": verb, "q": q, "res": res, "pf": pf}
    secs = None
    if verb == "stream":
        cmd["mbps"] = _num(body, "mbps", 0.0, 100.0, float)
        cmd["fps"] = _num(body, "fps", 0.1, 60.0, float)
        secs = _num(body, "secs", 1, 3600, int)
        cmd["secs"] = secs
    return cmd, res, pf, secs


def _num(body, key, lo, hi, cast):
    try:
        v = cast(body[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError("%s is missing or not a number" % key) from None
    if not lo <= v <= hi:
        raise ValueError("%s must be between %s and %s" % (key, lo, hi))
    return v


def build_light_cmd(verb: str, body: dict):
    if verb == "light":
        return {"cmd": "light", "level": _num(body, "level", 0, 100, int)}
    return {"cmd": "strobe",
            "on_ms": _num(body, "on_ms", 10, 10000, int),
            "off_ms": _num(body, "off_ms", 10, 10000, int),
            "count": _num(body, "count", 1, 200, int)}


def make_handler(bench: Bench, cfg: dict, store: CaptureStore = None,
                 stream_host="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "S18BenchWeb/1"

        def log_message(self, fmt, *args):
            # The page polls once a second; logging that buries everything
            # that matters. Commands are logged where they are handled.
            pass

        # -- plumbing ------------------------------------------------------
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _body(self):
            n = _int(self.headers.get("Content-Length"))
            if n <= 0:
                return {}
            if n > BODY_MAX:
                raise ValueError("body too large")
            raw = self.rfile.read(n)
            try:
                obj = json.loads(raw.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError("body is not JSON") from None
            if not isinstance(obj, dict):
                raise ValueError("body must be a JSON object")
            return obj

        # -- routes --------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._page()
            if path == "/api/config":
                return self._json(200, cfg)
            if path == "/api/status":
                try:
                    st, gate = bench.status()
                except BenchCtlError as e:
                    return self._json(503, {"ok": False, "err": str(e)})
                return self._json(200, {"ok": True, "status": st, "gate": gate})
            if path == "/api/captures":
                return self._json(200, store.listing())
            if path == "/api/frame.jpg":
                code, body = fetch_live_frame(stream_host, cfg["stream_port"])
                if code != 200:
                    return self._json(503, {"ok": False,
                                            "err": body.decode("utf-8", "replace")})
                return self._send(200, body, "image/jpeg")
            if path.startswith("/captures/"):
                return self._capture(path[len("/captures/"):])
            self.send_error(404)

        def _capture(self, name):
            try:
                body = store.read(name)
            except KeyError as e:
                # 404 for every refusal: a traversal attempt learns nothing
                # about what does or does not exist on this disk. The reason
                # goes in the BODY -- `message` lands in the status line,
                # which must encode as latin-1, and a non-ASCII character
                # there kills the connection instead of answering (measured).
                return self.send_error(404, "no such capture", str(e.args[0]))
            except OSError as e:
                return self.send_error(500, "cannot read capture: %s" % e)
            self._send(200, body, "image/jpeg")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if not path.startswith("/api/"):
                return self.send_error(404)
            verb = path[len("/api/"):]
            try:
                body = self._body()
            except ValueError as e:
                return self._json(400, {"ok": False, "err": str(e)})

            try:
                if verb in ("capture", "stream"):
                    cmd, res, pf, secs = build_camera_cmd(verb, body)
                    code, out = bench.camera(cmd, res, pf, secs)
                elif verb == "stop":
                    code, out = bench.stop()
                elif verb in ("light", "strobe"):
                    code, out = bench.passthrough(build_light_cmd(verb, body))
                else:
                    return self.send_error(404)
            except ValueError as e:
                return self._json(400, {"ok": False, "err": str(e)})
            except BenchCtlError as e:
                return self._json(503, {"ok": False, "err": str(e)})
            print("%s %s -> %d %s" % (verb, body, code,
                                      "ok" if out.get("ok") else
                                      out.get("err", "refused")), flush=True)
            self._json(code, out)

        def _page(self):
            try:
                with open(os.path.join(STATIC, "bench.html"), "rb") as fh:
                    body = fh.read()
            except OSError as e:
                return self.send_error(500, "cannot read bench.html: %s" % e)
            self._send(200, body, "text/html; charset=utf-8")

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--sock", default=None,
                    help="control socket (default $S18_CTL_SOCK or /run/bm/bench.sock)")
    ap.add_argument("--stream-port", type=int, default=8080,
                    help="the frozen S3 stream server's HTTP port")
    ap.add_argument("--stream-host", default="127.0.0.1",
                    help="host to read the live frame from (same box by default)")
    ap.add_argument("--captures",
                    default=os.environ.get("S18_CAPTURE_DIR", "~/bench_captures"),
                    help="bite B's capture directory (read-only; $S18_CAPTURE_DIR)")
    ap.add_argument("--settle", type=float, default=8.0,
                    help="seconds to hold a sensor re-init after a capture")
    args = ap.parse_args(argv)

    bench = Bench(BenchGate(settle=args.settle), path=args.sock)
    store = CaptureStore(args.captures)
    cfg = {"stream_port": args.stream_port, "settle": args.settle,
           "capture_dir": store.root}
    httpd = ThreadingHTTPServer((args.bind, args.port),
                                make_handler(bench, cfg, store, args.stream_host))
    print("bench-web: http://%s:%d/  (socket %s, settle %.1f s, captures %s)"
          % (args.bind, args.port, bench._ctl.path, args.settle, store.root),
          flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
