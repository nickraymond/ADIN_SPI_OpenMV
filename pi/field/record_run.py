#!/usr/bin/env python3
"""Run one recording across the cameras, land the files, write the manifest.

One recording session = one directory holding one .mjpeg and one .mp4 per
camera plus a manifest.json describing exactly what was asked for and exactly
what was delivered. Those two are recorded SEPARATELY and on purpose: "record
5 s of HD at 30 fps" and "we got 4.2 s at 24 fps" are different facts, and a
tool that shows only the first is the "plausible still image" bug this repo has
paid for three times (S24).

The cameras are started in parallel threads so they overlap as closely as the
paste-mode push allows, and each camera's own start timestamp is recorded, so
the viewer can align two clips that did not begin on exactly the same
millisecond rather than pretending they did.
"""

import json
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recorder as R                                        # noqa: E402
import storage as ST                                        # noqa: E402
import transcode as T                                       # noqa: E402

CEILINGS_PATH = os.path.join(_HERE, "camera_ceilings.json")


def load_ceilings(path=CEILINGS_PATH):
    """Measured per-camera ceilings. Missing file = no claims, not a guess."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"cameras": {}, "note": "no measured ceilings on this host"}


def combo_key(cameras):
    """Canonical name for a set of cameras recording together.

    Sorted, so "N6,IMX" and "IMX,N6" are the same measurement rather than two.
    """
    return "+".join(sorted(c for c in cameras if c))


def ceiling_for(ceilings, role, framesize, quality, combo=None):
    """Measured fps for this cell as (value, kind), or (None, "").

    Three sources, best first:

      1. DELIVERED FOR THIS EXACT CAMERA COMBINATION. Cameras contend, and the
         effect is large enough to matter: on nereus002 the N6 delivered 28.75
         fps alone, 26.8 beside the IMX and 25.1 with both others. A ceiling
         measured solo therefore OVERSTATES what a three-camera session gets,
         which is exactly the direction that turns a guard into a liar.
      2. Delivered in any combination -- better than nothing, and flagged as
         such so the message can say which.
      3. The encoder ceiling, which excludes the board's own USB write and is
         always the most optimistic of the three.
    """
    cam = (ceilings.get("cameras") or {}).get(role)
    if not cam:
        return None, ""
    key = "%s_q%d" % (framesize, quality)
    if combo:
        byc = (cam.get("delivered_by_combo") or {}).get(combo) or {}
        if byc.get(key):
            return byc[key], "delivered together"
    d = (cam.get("delivered") or {}).get(key)
    if d:
        return d, "delivered"
    c = (cam.get("cells") or {}).get(key)
    if c:
        return c, "encoder"
    return None, ""


def check_request(ceilings, role, framesize, quality, fps, combo=None):
    """Compare the ask against the measurement. Returns (verdict, message).

    Verdicts: "ok" | "tight" | "impossible" | "unmeasured".
    The card warns or refuses on this rather than letting a board silently
    under-deliver, which is Nick's explicit requirement: the cameras are not
    equals and the UI must not pretend they are.
    """
    ceil, kind = ceiling_for(ceilings, role, framesize, quality, combo)
    if ceil is None:
        return "unmeasured", ("%s at %s q%d has never been measured on this "
                              "rig -- it will record at whatever it manages"
                              % (role, framesize, quality))
    what = {"delivered together": "delivers, with these cameras together,",
            "delivered": "delivers alone (more cameras will lower this)",
            }.get(kind, "encodes (link cost not included, so the real rate is "
                        "lower)")
    if fps > ceil:
        return "impossible", ("%s %s %s q%d at %.1f fps; %.0f fps is above that, "
                              "so expect ~%.1f"
                              % (role, what, framesize, quality, ceil, fps, ceil))
    if fps > ceil * 0.9:
        return "tight", ("%s %s %s q%d at %.1f fps; %.0f fps leaves only %.0f%% "
                         "margin" % (role, what, framesize, quality, ceil, fps,
                                     100.0 * (ceil - fps) / ceil))
    return "ok", ("%s %s %s q%d at %.1f fps; %.0f fps has %.0f%% margin"
                  % (role, what, framesize, quality, ceil, fps,
                     100.0 * (ceil - fps) / ceil))


#: What each camera should shoot when the operator has not said otherwise.
#: These are Nick's calls, made against measurements taken on this rig:
#:   N6  -- HD q70. The only HD rung that reaches 30 fps end to end (30.24 vs
#:          q90's 19.9) and 3.6x fewer bytes, after he compared byte-exact q70
#:          and q90 frames from the same scene.
#:   AE3 -- VGA q50, Nick's pick 2026-09-08 after seeing q30 and q50 side by
#:          side. The AE3 has no hardware JPEG, so at HD it manages 2.29 fps.
#:          Its VGA ladder is 9.26 / 11.66 / 13.47 / 13.78 fps at q70 / q50 /
#:          q30 / q10, PLATEAUING at ~13.8 because below q30 the colour convert
#:          and DCT dominate rather than entropy coding. q50 costs 1.8 fps
#:          against q30 and visibly removes the blocking in flat wall and floor
#:          areas. At 11.66 fps it sits just under his stated 12 floor, which he
#:          chose knowingly.
#:   IMX -- HD q70 to start with. It is on the CSI bus with a hardware ISP and
#:          needs no USB pump at all, so it is the least constrained of the
#:          three; the card will call its cells unmeasured until this rig has
#:          actually recorded them.
CAMERA_DEFAULTS = {
    "N6": {"framesize": "HD", "quality": 70},
    "AE3": {"framesize": "VGA", "quality": 50},
    "IMX": {"framesize": "HD", "quality": 70},
}

#: Cameras that are NOT serial boards. These are never looked for by
#: pi/field/discover.py -- asking a CSI camera its role over a raw REPL is
#: meaningless, and probing a serial port for it would open a board's port for
#: no reason.
CSI_ROLES = {"IMX": 0}


def settings_for(role, framesize, quality, per_camera=None):
    """What this camera should actually shoot.

    Explicit per-camera override wins; otherwise the camera's own measured
    default; otherwise whatever was asked for globally. The cameras are not
    equals and one global setting cannot serve them -- HD q70 gives the N6
    30 fps and the AE3 2.29.
    """
    per_camera = per_camera or {}
    got = dict(CAMERA_DEFAULTS.get(role, {"framesize": framesize,
                                          "quality": quality}))
    got.update({k: v for k, v in (per_camera.get(role) or {}).items()
                if v is not None})
    return got.get("framesize", framesize), int(got.get("quality", quality))


def estimate_bytes(ceilings, chosen, fps, duration_s, combo=None):
    """Roughly how much disk this recording will want, so the ring can make room
    BEFORE it starts rather than discovering the problem mid-write.

    Uses each camera's measured bytes/frame and its measured delivered rate --
    asking for 30 fps from a camera that does 11.7 must not reserve 30 fps of
    disk. The +15% covers the mp4 the transcode adds beside the .mjpeg
    (measured: the 20 min soak produced 2.715 GB of MJPEG and a 322 MB mp4).
    """
    total = 0
    for role, s in chosen.items():
        key = "%s_q%d" % (s["framesize"], s["quality"])
        cam = (ceilings.get("cameras") or {}).get(role, {})
        bpf = (cam.get("delivered_bytes", {}).get(key)
               or cam.get("bytes", {}).get(key) or 450000)
        rate = ceiling_for(ceilings, role, s["framesize"], s["quality"],
                           combo)[0] or fps
        total += bpf * min(fps, rate) * duration_s
    return int(total * 1.15)


def run_recording(root, framesize="HD", quality=85, fps=30.0, duration_s=180.0,
                  cameras=("N6", "AE3"), transcode=False, log=print,
                  progress=None, stop_event=None, per_camera=None,
                  ring_bytes=ST.DEFAULT_RING_BYTES,
                  min_free_bytes=ST.DEFAULT_MIN_FREE_BYTES,
                  keep_latest=ST.DEFAULT_KEEP_LATEST):
    ceilings = load_ceilings()
    result = {"ok": False, "errors": [], "warnings": [], "cameras": [],
              "summary": ""}

    def note(msg):
        log(msg)
        # The web UI passed the SAME callable as both log and progress and got
        # every line twice. `is not` did NOT catch it: `self.note` builds a
        # fresh bound-method object on each attribute access, so the two are
        # equal but not identical. Compare with ==, and the caller no longer
        # passes both anyway.
        if progress is not None and progress != log:
            progress(msg)

    serial_want = [c for c in cameras if c not in CSI_ROLES]
    csi_want = [c for c in cameras if c in CSI_ROLES]

    ports = {}
    if serial_want:
        note("discovering boards by role...")
        ports, problems, found = R.find_boards(tuple(serial_want))
        for p in problems:
            result["warnings"].append(p)
            note("! %s" % p)

    # A CSI camera is not a serial board and must not be probed like one.
    csi_live = []
    for role in csi_want:
        idx = CSI_ROLES[role]
        ok, why = R.csi_present(idx)
        if ok:
            csi_live.append(role)
        else:
            result["warnings"].append("%s: %s" % (role, why))
            note("! %s: %s" % (role, why))

    missing = [c for c in cameras
               if c not in ports and c not in csi_live]
    if missing:
        result["errors"].append("cameras not found: %s" % ", ".join(missing))
    live = [c for c in cameras if c in ports or c in csi_live]
    if not live:
        result["summary"] = "no cameras found; nothing recorded"
        return result

    # Resolve each camera's own settings first, then judge each against ITS cell
    # AS PART OF THIS COMBINATION -- cameras contend, and a solo number would
    # promise more than a three-camera session can deliver.
    combo = combo_key(live)
    chosen = {}
    for role in live:
        fs_r, q_r = settings_for(role, framesize, quality, per_camera)
        chosen[role] = {"framesize": fs_r, "quality": q_r}
        verdict, msg = check_request(ceilings, role, fs_r, q_r, fps, combo)
        note("%s: %s" % (verdict.upper(), msg))
        if verdict in ("impossible", "tight", "unmeasured"):
            result["warnings"].append(msg)

    # Make room BEFORE recording. The ring evicts oldest-first so a rig left
    # running cannot reach the filesystem; the OS is what this protects.
    need = estimate_bytes(ceilings, chosen, fps, duration_s, combo)
    note("storage: this clip needs ~%.2f GB" % (need / 1e9))
    pre = ST.enforce(root, ring_bytes=ring_bytes, min_free_bytes=min_free_bytes,
                     keep_latest=keep_latest, need_bytes=need, log=note)
    if pre["deleted"]:
        note("storage: evicted %d oldest session(s) to make room: %s"
             % (len(pre["deleted"]), ", ".join(pre["deleted"])))
    if pre["shortfall_bytes"] > 0:
        result["warnings"].append(
            "storage: %.2f GB short even after evicting everything evictable; "
            "the newest %d sessions and the active one are never deleted"
            % (pre["shortfall_bytes"] / 1e9, keep_latest))

    session = R.Session(root)
    session.manifest["settings"] = {
        "framesize": framesize, "quality": quality, "fps_requested": fps,
        "duration_s": duration_s, "cameras": list(live),
        # What each camera was ACTUALLY told to shoot. The top-level values are
        # what the operator asked for globally; these are what ran, and they
        # differ per camera on purpose.
        "per_camera": chosen,
        # The set that recorded TOGETHER. Delivered rates are only comparable
        # within the same combination, because the cameras contend.
        "combo": combo,
    }
    note("session %s" % session.name)

    pace = R.pace_ms_for(fps)
    recorders, rings, writers, states = {}, {}, {}, {}
    # Stopping a long recording must CLOSE it, not orphan it. The operator
    # pressing Stop (or the workbench's SIGINT) sets this; the pumps return, the
    # writers drain what is already queued, and the manifest is written and
    # marked interrupted. Nick's dive case is "leave it recording, then stop",
    # so the stop path is a normal ending, not an error path.
    stop = stop_event if stop_event is not None else threading.Event()

    # -- open + start every board, in parallel so they overlap ---------------
    def bring_up(role):
        cfg = {"framesize": chosen[role]["framesize"],
               "quality": chosen[role]["quality"], "pixfmt": "RGB565",
               "duration_s": duration_s, "pace_ms": pace, "fps": fps,
               "max_frames": int(duration_s * 200) + 100}
        out = session.path("%s.mjpeg" % role)
        if role in CSI_ROLES:
            rec = R.CsiRecorder(role, cfg, out, log=note,
                                camera=CSI_ROLES[role])
            script = None
        else:
            rec = R.BoardRecorder(role, ports[role], cfg, out, log=note)
            script = R.build_board_script(cfg)
        recorders[role] = rec
        try:
            rec.open()
            if not rec.start(script):
                note("%s FAILED to start: %s" % (role, rec.error))
        except Exception as e:                          # noqa: BLE001
            rec.error = "%s: %s" % (type(e).__name__, e)
            note("%s FAILED to open: %s" % (role, rec.error))

    threads = [threading.Thread(target=bring_up, args=(r,)) for r in live]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    started = [r for r in live
               if recorders[r].started_at and not recorders[r].error]
    if not started:
        for r in live:
            result["errors"].append("%s: %s" % (r, recorders[r].error or "no banner"))
        for r in live:
            recorders[r].close()
        result["summary"] = "no camera started; nothing recorded"
        session.manifest["errors"] = result["errors"]
        session.save()
        return result

    # -- ring per camera, sized from ITS OWN byte rate ----------------------
    mem = R.mem_available_bytes()
    for role in started:
        rec = recorders[role]
        fs_r, q_r = chosen[role]["framesize"], chosen[role]["quality"]
        ceil = ceiling_for(ceilings, role, fs_r, q_r, combo)[0] or fps
        cam_c = ceilings.get("cameras", {}).get(role, {})
        key = "%s_q%d" % (fs_r, q_r)
        est_bpf = (cam_c.get("delivered_bytes", {}).get(key)
                   or cam_c.get("bytes", {}).get(key) or 450000)
        byte_rate = est_bpf * min(fps, ceil)
        cap = R.ring_size_bytes(byte_rate, mem)
        rings[role] = R.RingBuffer(cap)
        states[role] = {"done": False}
        note("%s ring %.0f MB (est %.1f MB/s)" % (role, cap / 1e6, byte_rate / 1e6))
        w = threading.Thread(target=R.writer_thread,
                             args=(rings[role], rec.out_path, states[role]))
        w.daemon = True
        w.start()
        writers[role] = w

    # -- pump ---------------------------------------------------------------
    note("recording %.1f s ..." % duration_s)
    pumps = []
    for role in started:
        t = threading.Thread(target=recorders[role].pump,
                             args=(rings[role], stop))
        t.start()
        pumps.append(t)
    for t in pumps:
        t.join()

    for role in started:
        states[role]["done"] = True
        rings[role].close()
    for role in started:
        writers[role].join(timeout=120)
        recorders[role].close()

    # -- results, asked-for vs delivered ------------------------------------
    earliest = min(recorders[r].started_at for r in started)
    for role in started:
        rec = recorders[role]
        st = rec.stats()
        st["ring_capacity_bytes"] = rings[role].capacity
        st["ring_high_water_bytes"] = rings[role].high_water
        st["ring_dropped_frames"] = rings[role].dropped_frames
        st["written_bytes"] = states[role].get("written_bytes", 0)
        st["written_frames"] = states[role].get("written_frames", 0)
        st["max_write_stall_s"] = states[role].get("max_write_stall_s", 0)
        st["start_offset_s"] = round(rec.started_at - earliest, 3)
        st["mjpeg"] = "%s.mjpeg" % role

        # The mp4's timebase must be the CAPTURE cadence, not the host's
        # arrival rate. The board timestamps nothing, so a clip written at the
        # arrival rate plays at the wrong speed: the first run recorded 120
        # frames over the board's 5.039 s (23.8 fps) but the host measured a
        # 5.93 s arrival window, and the resulting mp4 ran 5.98 s -- 19% slow.
        # The board's own frames/wall is authoritative; arrival rate is a
        # transport measurement and is reported separately.
        tw = (rec.trailer or {}).get("wall_ms")
        tf = (rec.trailer or {}).get("frames")
        if tw and tf and tw > 0:
            capture_fps = tf * 1000.0 / tw
            st["capture_fps"] = round(capture_fps, 2)
        else:
            capture_fps = st["delivered_fps"] or fps
            st["capture_fps"] = round(capture_fps, 2)
            result["warnings"].append(
                "%s sent no trailer; the mp4 timebase falls back to the host "
                "arrival rate and may play at the wrong speed" % role)

        # A THUMBNAIL ALWAYS, a transcode only on request.
        #
        # Nick's rule, and the reasoning is his: do not spend energy converting
        # something the ring may delete unseen, and if the camera is killed
        # mid-dive lose the last 5 min rather than a half-written 45 min file.
        # A thumbnail costs a read and a write -- one frame copied verbatim,
        # no decode -- so it is always worth having, and it is what makes the
        # library browsable without converting anything.
        if st["written_frames"] > 0:
            tn = R.write_thumbnail(rec.out_path, session.path("%s_thumb.jpg" % role))
            if tn:
                st["thumb"] = "%s_thumb.jpg" % role

        if transcode and st["written_frames"] > 0:
            note("%s transcoding %d frames at %.2f fps ..."
                 % (role, st["written_frames"], capture_fps))
            tr = T.transcode(rec.out_path, session.path("%s.mp4" % role),
                             capture_fps if capture_fps > 0 else fps)
            st["transcode"] = tr
            if tr["ok"]:
                st["mp4"] = "%s.mp4" % role
                note("%s mp4 %.1f MB via %s in %.1f s"
                     % (role, tr["bytes"] / 1e6, tr["encoder"], tr["wall_s"]))
            else:
                result["warnings"].append("%s transcode failed: %s"
                                          % (role, tr.get("stderr", "")[:200]))
                note("%s transcode FAILED: %s" % (role, tr.get("stderr", "")[:200]))
        result["cameras"].append(st)
        session.manifest["cameras"].append(st)

    session.manifest["ring"] = {"mem_available_bytes": mem}
    # Enforce again now that the real sizes are on disk -- the pre-flight used
    # an estimate. The session just recorded is passed as `active` so it can
    # never be the thing evicted to make room for itself.
    post = ST.enforce(root, ring_bytes=ring_bytes, min_free_bytes=min_free_bytes,
                      keep_latest=keep_latest, active=session.name, log=note)
    session.manifest["storage"] = {"before": pre, "after": post}
    if post["deleted"]:
        note("storage: evicted %d session(s) after the recording: %s"
             % (len(post["deleted"]), ", ".join(post["deleted"])))
    note("storage: ring %.2f / %.2f GB used, card %.1f%% full"
         % (post.get("used_bytes", 0) / 1e9, ring_bytes / 1e9,
            post.get("sd_used_pct") or 0.0))
    if stop.is_set():
        session.manifest["interrupted"] = True
        result["warnings"].append(
            "stopped before the requested duration -- the clip holds what was "
            "recorded up to that point, and its frame count says how much")
    session.save()

    lines = []
    for st in result["cameras"]:
        lines.append("%s: %d frames, %.1f fps delivered (asked %.0f), %.1f MB, "
                     "drops ring=%d seq=%s"
                     % (st["label"], st["written_frames"], st["delivered_fps"],
                        fps, st["written_bytes"] / 1e6,
                        st["ring_dropped_frames"], st["seq_gaps"]))
    result["summary"] = "%s\n%s" % (session.name, "\n".join(lines))
    result["session"] = session.name
    result["dir"] = session.dir
    result["ok"] = any(st["written_frames"] > 0 for st in result["cameras"])
    return result
