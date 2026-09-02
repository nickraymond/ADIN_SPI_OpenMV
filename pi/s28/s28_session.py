"""S28 burst session — the TESTABLE CORE (hil_protocol precedent).

No serial, no HTTP: the session wraps any reader with the SerialBoard
readline() contract plus a ``send(bytes)`` callable; the test suite
injects fakes. Also home to the pure decode/verdict math the stats pass
and the tests share:

  BurstSession      command/reply driver over the s28_board_burst wire
  rgb565_to_rgb     RGB565 buffer -> HxWx3 uint8 (byte order verified
                    against the patch card at run time, never assumed)
  bayer_planes      BGGR buffer -> {"r","g","b"} planes (G averaged)
  scale_cam_map     re-scale an E11 CamMap between framesizes
  lock_verdict      were the sensor settings ACTUALLY frozen?
  flicker_verdict   is the scene (LCD) modulating frame-to-frame?
  bracket_check     did +2/+3 EV brighten linearly in Bayer?
"""
import base64
import json
import time

import numpy as np


# ------------------------------------------------------------ wire driver
class BurstSession:
    """Drive the s28_board_burst.py wire: send one JSON command line,
    collect its #-tagged replies. reader: readline() -> bytes line or
    b'' at end (end_reason/last_error attributes); writer: callable(bytes).
    """

    # #RDY = board is in its command loop (ready for a command AND, when
    # it follows an op's replies, that op is complete). #W = a parked
    # heartbeat; seen past grace after a send it means the byte was lost.
    RESEND_GRACE = 3.0
    MAX_RESEND = 5

    def __init__(self, reader, writer):
        self.rd = reader
        self.wr = writer
        self.info = None
        self.skips = 0                    # b64-length mismatches survived
        self.resends = 0                  # lost command bytes recovered

    def send(self, **cmd):
        self.wr((json.dumps(cmd) + "\n").encode())

    def next_event(self, timeout_s=60):
        """-> (tag, payload). Tags: info/ok/conv/lock/table/frame/err/
        done/ready/wait/end/skip. A frame payload carries '_data'."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            line = self.rd.readline()
            if line == b"":
                return "end", {"reason": getattr(self.rd, "end_reason", ""),
                               "error": getattr(self.rd, "last_error", "")}
            if not line.startswith(b"#"):
                continue                  # stray output — ignore
            try:
                tag, payload = line.split(b" ", 1)
                obj = json.loads(payload)
            except ValueError:
                continue
            if tag == b"#I":
                self.info = obj
                return "info", obj
            if tag == b"#RDY":
                return "ready", obj
            if tag == b"#W":
                return "wait", obj
            if tag == b"#OK":
                return "ok", obj
            if tag == b"#CONV":
                return "conv", obj
            if tag == b"#LOCK":
                return "lock", obj
            if tag == b"#T":
                return "table", obj
            if tag == b"#E":
                return "err", obj
            if tag == b"#DONE":
                return "done", obj
            if tag == b"#M":
                raw = self.rd.readline()
                if raw == b"":
                    return "end", {"reason": getattr(self.rd, "end_reason",
                                                     ""),
                                   "error": getattr(self.rd, "last_error",
                                                    ""),
                                   "mid_frame": obj.get("seq")}
                b64 = raw.rstrip(b"\r\n")
                if len(b64) != obj["b64"]:
                    # line-oriented stream: skip and realign, never die
                    # on one corrupt line (hil_protocol lesson)
                    self.skips += 1
                    return "skip", obj
                obj["_data"] = base64.b64decode(b64)
                return "frame", obj
        raise IOError("board silent for %ds" % timeout_s)

    def wait_info(self, timeout_s=30):
        """Consume the boot #I. Ready/wait heartbeats before it fall
        through (they cannot precede #I, but be liberal)."""
        while True:
            tag, obj = self.next_event(timeout_s)
            if tag == "info":
                return obj
            if tag == "end":
                raise RuntimeError("board died before #I: %s"
                                   % obj.get("reason"))

    @staticmethod
    def _watchdog(sig, frm):
        raise IOError("op watchdog fired — board silent past its budget")

    def command(self, timeout_s=60, **cmd):
        """Send one op and return its typed replies, robust to a lost
        command byte (the E4 failure mode). Sends, then collects replies
        until the op-complete #RDY; a #W heartbeat seen past RESEND_GRACE
        with nothing collected yet means the byte never landed -> resend
        (bounded; the board's pre-poll drain makes a duplicate harmless).
        -> list of (tag, obj). err/end raise.

        A SIGALRM watchdog is the BACKSTOP for a board that hangs mid-op
        (e.g. a C-level snapshot that never returns): SerialBoard's
        readline() blocks forever on a silent-but-alive board, defeating
        the per-event deadline, so without this a hung op runs until an
        external kill — which leaves the AE3 wedged. On fire the collector
        raises here, and its caller stops the board cleanly.
        """
        armed = False
        try:
            import signal
            signal.signal(signal.SIGALRM, self._watchdog)
            signal.alarm(int(timeout_s) + 5)
            armed = True
        except (ValueError, AttributeError, OSError):
            pass                          # not main thread / no SIGALRM
        try:
            return self._command_loop(timeout_s, cmd)
        finally:
            if armed:
                signal.alarm(0)

    def _command_loop(self, timeout_s, cmd):
        self.send(**cmd)
        sent_t = time.monotonic()
        resends = 0
        got = []
        while True:
            tag, obj = self.next_event(timeout_s)
            if tag == "ready":
                if got:
                    return got            # op complete
                # a #RDY that predates our send (board was already
                # parked) — ignore and keep waiting for the op's replies
                continue
            if tag == "wait":
                if (not got and resends < self.MAX_RESEND
                        and time.monotonic() - sent_t > self.RESEND_GRACE):
                    self.send(**cmd)
                    self.resends += 1
                    resends += 1
                    sent_t = time.monotonic()
                continue
            if tag == "err":
                raise RuntimeError("board error in op %r: %s"
                                   % (cmd.get("op"), obj.get("err")))
            if tag == "end":
                raise RuntimeError("board stream ended in op %r: %s %s"
                                   % (cmd.get("op"), obj.get("reason"),
                                      obj.get("error")))
            if tag == "skip":
                got.append((tag, obj))    # frame consumed, payload lost
            elif tag in ("ok", "conv", "lock", "table", "frame"):
                got.append((tag, obj))
            # info/other: ignore

    def quit(self, timeout_s=15):
        """Ask the board to end; tolerate an already-unwinding board."""
        self.send(op="quit")
        try:
            while True:
                tag, _ = self.next_event(timeout_s)
                if tag in ("done", "end"):
                    return
        except (RuntimeError, IOError):
            return


# ---------------------------------------------------------------- decode
def rgb565_to_rgb(buf, w, h, byteswap=False):
    """RGB565 buffer -> HxWx3 uint8. `byteswap` handles the two possible
    16-bit byte orders — callers VERIFY orientation against the patch
    card (orient_check) rather than trusting either guess."""
    a = np.frombuffer(buf, np.uint8).reshape(h, w, 2)
    if byteswap:
        v = (a[:, :, 1].astype(np.uint16) << 8) | a[:, :, 0]
    else:
        v = (a[:, :, 0].astype(np.uint16) << 8) | a[:, :, 1]
    r = ((v >> 11) & 0x1F).astype(np.uint8) << 3
    g = ((v >> 5) & 0x3F).astype(np.uint8) << 2
    b = (v & 0x1F).astype(np.uint8) << 3
    return np.stack([r, g, b], axis=-1)


def bayer_planes(buf, w, h):
    """BGGR (bite-0 fact: SUBFORMAT_ID_BGGR) -> dict of float32 planes,
    each (h/2, w/2): r, b, and g = mean of the two green sites."""
    a = np.frombuffer(buf, np.uint8).reshape(h, w).astype(np.float32)
    return {"b": a[0::2, 0::2], "g": (a[0::2, 1::2] + a[1::2, 0::2]) / 2.0,
            "r": a[1::2, 1::2]}


def gray_plane(buf, w, h):
    return np.frombuffer(buf, np.uint8).reshape(h, w).astype(np.float32)


def rgb565_to_gray(buf, w, h, byteswap=False):
    """RGB565 buffer -> HxW float32 luma. Calibration captures the
    board's normal RGB565 (GRAYSCALE HD hangs the direct-csi path on
    this build — measured 2026-09-02) and converts to gray HERE, exactly
    as the proven hil_harness calibration does with its JPEG frames."""
    rgb = rgb565_to_rgb(buf, w, h, byteswap=byteswap).astype(np.float32)
    return (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1]
            + 0.114 * rgb[:, :, 2])


# ----------------------------------------------------------- calibration
def scale_cam_map(m, s):
    """Re-scale an E11 CamMap solved at one framesize to another (HD->VGA
    is exactly 0.5 on this sensor: 1280x800 -> 640x400). k1 is
    dimensionless by construction (R normalizes) so only H/center/R scale.
    """
    from hil_harness import CamMap
    S = np.diag([s, s, 1.0])
    return CamMap(S @ m.H, m.k1, m.cx * s, m.cy * s, m.R * s)


def patch_region(cam_map, patch, cam_w, cam_h, shrink=0.5):
    """Patch (name,cx,cy,w,h,rgb in content fractions) -> (x0,y0,x1,y1)
    camera-px box: the four corners map through the homography, then the
    axis-aligned inner box shrinks by `shrink` to dodge edges/bleed.
    Returns None (loudly loggable) if the region leaves the frame."""
    _, cx, cy, w, h, _ = patch
    corners = np.array([[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2],
                        [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]])
    px = cam_map.frac_to_cam(corners)
    x0, y0 = px[:, 0].min(), px[:, 1].min()
    x1, y1 = px[:, 0].max(), px[:, 1].max()
    dx, dy = (x1 - x0) * shrink / 2, (y1 - y0) * shrink / 2
    x0, y0, x1, y1 = x0 + dx, y0 + dy, x1 - dx, y1 - dy
    x0, y0 = int(round(x0)), int(round(y0))
    x1, y1 = int(round(x1)), int(round(y1))
    if x0 < 0 or y0 < 0 or x1 > cam_w or y1 > cam_h or x1 <= x0 or \
            y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def orient_check(rgb_frame, cam_map, patches):
    """Verify RGB565 byte order + Bayer/RGB channel identity against the
    card: the red patch must be reddest, the blue patch bluest. -> True
    if this decode orientation is correct (caller retries byteswapped)."""
    byname = {p[0]: p for p in patches}
    h, w = rgb_frame.shape[:2]
    ok = 0
    for name, ch in (("red", 0), ("blue", 2)):
        reg = patch_region(cam_map, byname[name], w, h)
        if reg is None:
            return False
        x0, y0, x1, y1 = reg
        means = rgb_frame[y0:y1, x0:x1].reshape(-1, 3).mean(axis=0)
        if int(np.argmax(means)) == ch:
            ok += 1
    return ok == 2


# ---------------------------------------------------------------- verdicts
def lock_verdict(metas):
    """Frames' sensor readbacks must be IDENTICAL across the burst —
    the notes' load-bearing rule. -> (ok, detail dict)."""
    if not metas:
        return False, {"err": "no frames"}
    exp = sorted({m["exp_us"] for m in metas})
    gain = sorted({m["gain_db"] for m in metas})
    wb = sorted({tuple(m["rgb_gain_db"]) for m in metas})
    ok = len(exp) == 1 and len(gain) == 1 and len(wb) == 1
    return ok, {"exp_us": exp, "gain_db": gain,
                "rgb_gain_db": [list(t) for t in wb],
                "gaps_ms": [m.get("gap_ms") for m in metas]}


def noise_stats(stack):
    """stack: N x H x W float. -> per-pixel temporal noise (the number
    stacking attacks), spatial sigma, and a consistency check: for pure
    temporal noise sigma(f1-f2)/sqrt(2) == sigma_t."""
    mean_img = stack.mean(axis=0)
    sigma_t = float(stack.std(axis=0, ddof=1).mean()) if len(stack) > 1 \
        else float("nan")
    sigma_s = float(mean_img.std())
    pair = float((stack[1] - stack[0]).std() / np.sqrt(2)) \
        if len(stack) > 1 else float("nan")
    return {"mean": float(mean_img.mean()), "sigma_t": round(sigma_t, 3),
            "sigma_s": round(sigma_s, 3), "pair_sigma": round(pair, 3)}


def flicker_verdict(frame_means, sigma_t, n_pix, factor=5.0):
    """LCD-PWM check: under independent per-pixel noise the sigma of the
    FRAME MEAN is sigma_t/sqrt(n_pix) — tiny. Backlight PWM/refresh
    modulates the whole frame together, inflating it by orders of
    magnitude. -> (verdict, detail); verdict 'ALIASED' when measured
    frame-mean sigma > factor x expectation."""
    fm = np.asarray(frame_means, np.float64)
    if len(fm) < 3 or not np.isfinite(sigma_t):
        return "UNKNOWN", {"n": len(fm)}
    measured = float(fm.std(ddof=1))
    expected = float(sigma_t / np.sqrt(n_pix))
    ratio = measured / expected if expected > 0 else float("inf")
    verdict = "ALIASED" if ratio > factor else "SAFE"
    return verdict, {"frame_mean_sigma": round(measured, 4),
                     "expected_sigma": round(expected, 6),
                     "ratio": round(ratio, 1)}


def bracket_check(rung_means, tol=0.25):
    """rung_means: [(exposure_us, linear patch mean), ...] with the base
    rung first. Brightening must track the exposure ratio (Bayer linear,
    below saturation). -> (ok, rows) where each row carries the measured
    vs expected ratio; a clipped rung (mean > 240) is excluded from the
    pass/fail with a note — clipping is expected in green/blue."""
    base_e, base_m = rung_means[0]
    rows, ok = [], True
    for e, m in rung_means[1:]:
        want = e / base_e
        clipped = m > 240.0
        got = (m / base_m) if base_m > 0 else float("inf")
        row = {"exp_ratio": round(want, 2), "mean_ratio": round(got, 2),
               "clipped": clipped}
        if not clipped and abs(got - want) / want > tol:
            ok = False
            row["fail"] = True
        rows.append(row)
    return ok, rows
