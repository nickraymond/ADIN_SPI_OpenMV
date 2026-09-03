# s28_board_burst.py -- S28 bite 1, board side (AE3 first; N6 later).
#
# Pushed into the raw REPL by pi/s28/s28_burst_capture.py (never written
# to the board). COMMAND-DRIVEN: the host sends one JSON object per line
# on stdin; the board executes it and replies with #-tagged JSON lines.
# Printable ASCII only on the wire (the raw REPL intercepts 0x01-0x04).
#
# Ops (host -> board, one JSON line each):
#   {"op":"cfg","pixformat":"BAYER|RGB565|GRAYSCALE","framesize":"VGA|HD",
#    "fps":30}                        -> #OK {w,h,mem_free}
#   {"op":"conv","secs":6}            -> #CONV {rows:[[t_ms,exp_us,
#                                        gain_db,r,g,b],..]}  (autos ON,
#                                        sampled ~4 Hz while snapshotting)
#   {"op":"lock"}                     -> #LOCK {exposure_us,gain_db,rgb}
#                                        (freeze AE+gain+WB at converged)
#   {"op":"manual","exposure_us":E,"fps":F,"gain_db":G}  -> #LOCK {...}
#                                        fps set FIRST (bite-0 fact: the
#                                        exposure clamp reads the CURRENT
#                                        frame time), gain optional
#   {"op":"expo_probe","fps":F,"targets":[us,..]} -> one #T {fps,cmd,got}
#                                        per target (readback table)
#   {"op":"burst","n":8,"mode":"paced"|"tight"}   -> n x (#M {..} + one
#                                        bare-b64 line of the RAW frame)
#   {"op":"quit"}                     -> #DONE {}
#
# Wire notes (E7/E4 precedents): frame payload is ONE b64 line built
# from 3072-byte chunks (3072 % 3 == 0, so concatenation is valid b64);
# the announced "b64" length lets the host verify the line exactly. The
# CDC turns \n into \r\n -- host strips. Errors are #E {op,err} -- loud,
# and the command loop CONTINUES (one failed op must not cost the
# attach; bite-R attach budget).
#
# "tight" burst holds all n frames in heap for true back-to-back cadence
# (gap == sensor period, no emission in between); it refuses loudly if
# n*frame_size + slack exceeds free heap. "paced" emits between captures
# (gap ~= emission time, recorded per frame -- fine on a static scene).
#
# READY/HEARTBEAT PROTOCOL (mirrors S8 bite E4, proven on this wire):
# between ops the board DRAINS stdin, emits #RDY once (ready for a
# command), then heartbeats #W every 2 s while polling. The host waits
# for readiness before sending and RESENDS a command if it keeps seeing
# #W past a grace window -- because a first command byte CAN be lost on
# this raw-REPL wire (measured E4). The pre-poll drain absorbs a
# duplicate that a resend-race delivers, so a resent command is never
# double-executed.
import binascii
import gc
import json
import select
import sys
import time

import csi

IDLE_S = 600          # no command for this long -> quit to a usable REPL
HB_MS = 2000          # #W heartbeat cadence while awaiting a command
SLACK = 262144        # heap slack the tight burst must leave free

_poll = select.poll()
_poll.register(sys.stdin, select.POLLIN)


def emit(tag, obj):
    print(tag + " " + json.dumps(obj))


# PAG7936 frame-time registers (µs) + the sensor-update commit. Writing
# these DIRECTLY extends the frame time WITHOUT csi.framerate() — which
# does a full mode-register rewrite + capture abort that wedges the AE3
# (bite 3 finding, 2026-09-03: root-caused to set_framerate's
# omv_csi_abort + configure; proven fix, exposure readback exact, frame
# period scales, no wedge up or down). The exposure clamp
# (set_auto_exposure) reads the LIVE frame-time regs, so extending the
# frame time first lets a long exposure through. Slack keeps exp under
# frame_time - the sensor's 80 µs margin.
_FT_H, _FT_M, _FT_L = 0x004E, 0x004D, 0x004C
_SENSOR_UPDATE, _SU_FLAG = 0x00EB, 0x80
FT_SLACK = 5000            # µs of headroom above the exposure


def set_frame_time(csi0, ft_us):
    """Extend/shrink the sensor frame time (µs) via direct register
    writes — the wedge-free replacement for csi.framerate()."""
    ft_us = min(max(int(ft_us), 200), 2000000)     # 21-bit reg, ~2.1 s
    h = csi0.__read_reg(_FT_H)
    csi0.__write_reg(_FT_H, (h & 0xE0) | ((ft_us >> 16) & 0x1F))
    csi0.__write_reg(_FT_M, (ft_us >> 8) & 0xFF)
    csi0.__write_reg(_FT_L, ft_us & 0xFF)
    csi0.__write_reg(_SENSOR_UPDATE, _SU_FLAG)


def snap(csi0, tries=4):
    """snap(csi0) that self-heals the transient 'Frame capture has
    timed out.' -- measured 2026-09-02: the first capture after a
    framerate/mode change can time out, then succeed on a retry. A
    persistent timeout still raises (a real fault, reported as #E)."""
    for i in range(tries):
        try:
            return csi0.snapshot()
        except RuntimeError as e:
            if "timed out" in str(e) and i < tries - 1:
                time.sleep_ms(60)
                continue
            raise


def _drain_stdin():
    """Discard any buffered stdin (E4 duplicate-absorption): a resend
    that raced a slow op sits here and would otherwise be read as a
    spurious next command."""
    while _poll.poll(0):
        if sys.stdin.read(1) == "":
            break


def read_cmd():
    """Drain, announce ready (#RDY), then park polling stdin for one JSON
    command line -- heartbeating #W so the host can tell parked-alive
    from dead and can detect a lost command byte. -> command dict, or a
    synthetic quit on idle timeout (a dead host must not wedge the board).
    """
    _drain_stdin()
    emit("#RDY", {})
    buf = ""
    t0 = time.ticks_ms()
    last_hb = t0
    while True:
        if _poll.poll(100):
            ch = sys.stdin.read(1)
            if ch == "\n":
                s = buf.strip()
                if not s:
                    buf = ""
                    continue
                try:
                    return json.loads(s)
                except ValueError:
                    emit("#E", {"op": "parse", "err": "bad json line"})
                    buf = ""
                    continue
            if ch != "\r":
                buf += ch
            if len(buf) > 1024:          # malformed flood -- fail loud
                emit("#E", {"op": "parse", "err": "line too long"})
                buf = ""
        else:
            now = time.ticks_ms()
            if time.ticks_diff(now, last_hb) >= HB_MS:
                emit("#W", {})
                last_hb = now
            if time.ticks_diff(now, t0) > IDLE_S * 1000:
                return {"op": "quit"}


def meta_read(csi0):
    """Sensor readbacks that ride every frame -- the lock PROOF."""
    return {"exp_us": csi0.exposure_us(),
            "gain_db": round(csi0.gain_db(), 3),
            "rgb_gain_db": [round(v, 3) for v in csi0.rgb_gain_db()],
            "mem_free": gc.mem_free()}


def send_frame(img, meta):
    buf = img.bytearray()
    n = len(buf)
    meta["bytes"] = n
    meta["b64"] = (n + 2) // 3 * 4
    emit("#M", meta)
    mv = memoryview(buf)
    for i in range(0, n, 3072):
        e = binascii.b2a_base64(mv[i:i + 3072])
        sys.stdout.write(e[:-1] if e[-1:] == b"\n" else e)
    sys.stdout.write("\n")


def op_cfg(csi0, cmd):
    """Reconfigure the sensor -- but ONLY on a real change. Re-applying
    the CURRENT mode redundantly hung the direct-csi path on this build
    (measured 2026-09-02: a cfg to the boot mode RGB565/VGA never
    returned, while a genuine change to BAYER worked). So: skip unchanged
    fields, drain the pipeline BEFORE re-init (a re-init too soon after a
    capture is the SPEC sensor-control hazard), and emit a #D breadcrumb
    around each sensor call so a hang names its exact step."""
    global _cur_pf, _cur_fs, _cur_fps
    pf = cmd["pixformat"]
    fs = cmd.get("framesize", "VGA")
    fps = cmd.get("fps")
    changed = (pf != _cur_pf) or (fs != _cur_fs)
    if changed:
        # drain: let the current pipeline finish a frame before re-init
        emit("#D", {"op": "cfg", "step": "drain"})
        snap(csi0)
        emit("#D", {"op": "cfg", "step": "pixformat"})
        csi0.pixformat(getattr(csi, pf))
        emit("#D", {"op": "cfg", "step": "framesize"})
        csi0.framesize(getattr(csi, fs))
        _cur_pf, _cur_fs = pf, fs
    if fps is not None and fps != _cur_fps:
        emit("#D", {"op": "cfg", "step": "framerate"})
        csi0.framerate(fps)
        _cur_fps = fps
    emit("#D", {"op": "cfg", "step": "settle"})
    for _ in range(3):
        snap(csi0)                  # settle the (possibly new) mode
    img = snap(csi0)
    emit("#OK", {"op": "cfg", "w": img.width(), "h": img.height(),
                 "pixformat": pf, "changed": changed,
                 "mem_free": gc.mem_free()})


def op_conv(csi0, cmd):
    """Autos ON; snapshot continuously (on-chip AE adapts per frame) and
    sample the readbacks so the host can SEE convergence, not assume it.
    The WB stats EMA has a time constant (bite 0) -- secs must exceed it."""
    csi0.auto_exposure(True)
    csi0.auto_gain(True)
    csi0.auto_whitebal(True)
    secs = min(int(cmd.get("secs", 6)), 30)
    rows = []
    t0 = time.ticks_ms()
    last = -1000
    while time.ticks_diff(time.ticks_ms(), t0) < secs * 1000:
        snap(csi0)
        t = time.ticks_diff(time.ticks_ms(), t0)
        if t - last >= 250:
            last = t
            rgb = csi0.rgb_gain_db()
            rows.append([t, csi0.exposure_us(),
                         round(csi0.gain_db(), 3),
                         round(rgb[0], 3), round(rgb[1], 3),
                         round(rgb[2], 3)])
    emit("#CONV", {"rows": rows})


def _settle_and_lock_reply(csi0):
    for _ in range(2):
        snap(csi0)                  # flush frames exposed pre-change
    m = meta_read(csi0)
    emit("#LOCK", m)


def op_lock(csi0, cmd):
    e = csi0.exposure_us()
    g = csi0.gain_db()
    csi0.auto_exposure(False, exposure_us=e)
    csi0.auto_gain(False, gain_db=g)
    csi0.auto_whitebal(False)            # freezes the WB stats EMA (bite 0)
    _settle_and_lock_reply(csi0)


def op_manual(csi0, cmd):
    # Extend the frame time to fit the exposure via DIRECT register
    # writes (set_frame_time) — the wedge-free replacement for
    # csi.framerate(). Any exposure up to ~2 s is reachable this way, so
    # no fps arg is needed; the frame time adapts up AND down.
    exp = int(cmd["exposure_us"])
    set_frame_time(csi0, exp + FT_SLACK)
    if "gain_db" in cmd:
        csi0.auto_gain(False, gain_db=cmd["gain_db"])
    csi0.auto_exposure(False, exposure_us=exp)
    csi0.auto_whitebal(False)
    _settle_and_lock_reply(csi0)


def op_expo_probe(csi0, cmd):
    # Sweep exposure targets, each with the frame time extended to fit
    # (direct register writes — no csi.framerate, no wedge). Two snaps
    # per target: the first flushes the buffered (stale) frame, the
    # second is settled at the new exposure.
    for t in cmd["targets"]:
        t = int(t)
        set_frame_time(csi0, t + FT_SLACK)
        csi0.auto_exposure(False, exposure_us=t)
        snap(csi0)
        snap(csi0)
        emit("#T", {"cmd": t, "got": csi0.exposure_us()})


def op_burst(csi0, cmd):
    n = int(cmd.get("n", 8))
    mode = cmd.get("mode", "paced")
    if mode == "tight":
        probe = snap(csi0)
        size = len(probe.bytearray())
        gc.collect()
        need = n * size + SLACK
        if gc.mem_free() < need:
            emit("#E", {"op": "burst", "err": "tight needs %d B, free %d"
                        % (need, gc.mem_free())})
            return
        slots = [bytearray(size) for _ in range(n)]
        metas = []
        prev = time.ticks_ms()
        for k in range(n):
            img = snap(csi0)
            now = time.ticks_ms()
            m = meta_read(csi0)
            m["seq"] = k
            m["gap_ms"] = time.ticks_diff(now, prev)
            prev = now
            slots[k][:] = img.bytearray()
            metas.append(m)
        for k in range(n):
            metas[k]["mode"] = "tight"
            buf = slots[k]
            metas[k]["bytes"] = len(buf)
            metas[k]["b64"] = (len(buf) + 2) // 3 * 4
            emit("#M", metas[k])
            mv = memoryview(buf)
            for i in range(0, len(buf), 3072):
                e = binascii.b2a_base64(mv[i:i + 3072])
                sys.stdout.write(e[:-1] if e[-1:] == b"\n" else e)
            sys.stdout.write("\n")
        slots = None
        gc.collect()
    else:
        prev = time.ticks_ms()
        for k in range(n):
            img = snap(csi0)
            now = time.ticks_ms()
            m = meta_read(csi0)
            m["seq"] = k
            m["gap_ms"] = time.ticks_diff(now, prev)
            m["mode"] = "paced"
            prev = now
            send_frame(img, m)


OPS = {"cfg": op_cfg, "conv": op_conv, "lock": op_lock,
       "manual": op_manual, "expo_probe": op_expo_probe,
       "burst": op_burst}

# current sensor mode — op_cfg only re-inits on a real change (a
# redundant re-init hung this build; see op_cfg).
_cur_pf = "RGB565"
_cur_fs = "VGA"
_cur_fps = None

csi0 = csi.CSI()
csi0.reset()
csi0.pixformat(csi.RGB565)
csi0.framesize(csi.VGA)
for _ in range(3):
    csi0.snapshot()

_first = csi0.snapshot()
emit("#I", {"fw": sys.version, "w": _first.width(), "h": _first.height(),
            "mem_free": gc.mem_free()})

while True:
    cmd = read_cmd()
    if cmd.get("op") == "quit":
        break
    fn = OPS.get(cmd.get("op"))
    if fn is None:
        emit("#E", {"op": str(cmd.get("op")), "err": "unknown op"})
        continue
    try:
        fn(csi0, cmd)
    except MemoryError as e:
        gc.collect()
        emit("#E", {"op": cmd.get("op"), "err": "MemoryError: " + repr(e)})
    except Exception as e:
        emit("#E", {"op": cmd.get("op"), "err": repr(e)})

emit("#DONE", {})
