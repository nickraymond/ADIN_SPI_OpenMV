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
import transcode as T                                       # noqa: E402

CEILINGS_PATH = os.path.join(_HERE, "camera_ceilings.json")


def load_ceilings(path=CEILINGS_PATH):
    """Measured per-camera ceilings. Missing file = no claims, not a guess."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"cameras": {}, "note": "no measured ceilings on this host"}


def ceiling_for(ceilings, role, framesize, quality):
    """Measured fps for this cell as (value, kind), or (None, "").

    DELIVERED beats ENCODER wherever we have it. The two differ a lot and the
    encoder number is the flattering one: on nereus000 the N6 encodes HD q85 at
    34.9 fps but delivers 23.8 end to end, because the board writes each 195 KB
    frame over USB inside the same loop that encodes it. Guarding on 34.9 would
    promise 30 fps and quietly hand back 24.
    """
    cam = (ceilings.get("cameras") or {}).get(role)
    if not cam:
        return None, ""
    key = "%s_q%d" % (framesize, quality)
    d = (cam.get("delivered") or {}).get(key)
    if d:
        return d, "delivered"
    c = (cam.get("cells") or {}).get(key)
    if c:
        return c, "encoder"
    return None, ""


def check_request(ceilings, role, framesize, quality, fps):
    """Compare the ask against the measurement. Returns (verdict, message).

    Verdicts: "ok" | "tight" | "impossible" | "unmeasured".
    The card warns or refuses on this rather than letting a board silently
    under-deliver, which is Nick's explicit requirement: the cameras are not
    equals and the UI must not pretend they are.
    """
    ceil, kind = ceiling_for(ceilings, role, framesize, quality)
    if ceil is None:
        return "unmeasured", ("%s at %s q%d has never been measured on this "
                              "rig -- it will record at whatever it manages"
                              % (role, framesize, quality))
    what = ("delivers" if kind == "delivered" else "encodes (link cost not "
            "included, so the real rate is lower)")
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


def run_recording(root, framesize="HD", quality=85, fps=30.0, duration_s=5.0,
                  cameras=("N6", "AE3"), transcode=True, log=print,
                  progress=None):
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

    note("discovering boards by role...")
    ports, problems, found = R.find_boards(tuple(cameras))
    for p in problems:
        result["warnings"].append(p)
        note("! %s" % p)
    missing = [c for c in cameras if c not in ports]
    if missing:
        result["errors"].append("cameras not found: %s" % ", ".join(missing))
    live = [c for c in cameras if c in ports]
    if not live:
        result["summary"] = "no cameras found; nothing recorded"
        return result

    # Verdicts BEFORE recording, so the operator learns the truth up front.
    for role in live:
        verdict, msg = check_request(ceilings, role, framesize, quality, fps)
        note("%s: %s" % (verdict.upper(), msg))
        if verdict in ("impossible", "tight", "unmeasured"):
            result["warnings"].append(msg)

    session = R.Session(root)
    session.manifest["settings"] = {
        "framesize": framesize, "quality": quality, "fps_requested": fps,
        "duration_s": duration_s, "cameras": list(live),
    }
    note("session %s" % session.name)

    pace = R.pace_ms_for(fps)
    recorders, rings, writers, states = {}, {}, {}, {}
    stop = threading.Event()

    # -- open + start every board, in parallel so they overlap ---------------
    def bring_up(role):
        cfg = {"framesize": framesize, "quality": quality, "pixfmt": "RGB565",
               "duration_s": duration_s, "pace_ms": pace,
               "max_frames": int(duration_s * 200) + 100}
        rec = R.BoardRecorder(role, ports[role], cfg,
                              session.path("%s.mjpeg" % role), log=note)
        recorders[role] = rec
        try:
            rec.open()
            if not rec.start(R.build_board_script(cfg)):
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
        ceil = ceiling_for(ceilings, role, framesize, quality)[0] or fps
        cam_c = ceilings.get("cameras", {}).get(role, {})
        key = "%s_q%d" % (framesize, quality)
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
