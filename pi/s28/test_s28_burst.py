"""Host tests for S28 bite 1 (locked-burst capture + stats).

Protocol proven against a fake board BEFORE hardware (the E4 contract):
frame round-trip, corrupt-b64 skip-and-realign, error surfacing, death
mid-frame; plus the pure decode/verdict math on synthetic frames with
KNOWN noise. numpy + PIL required (the Pi has them; on the Mac use the
fomo venv):

    ~/nereus_ml/venvs/fomo/bin/python -m pytest pi/s28/test_s28_burst.py -q
"""
import base64
import json
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "pi", "hil"))
sys.path.insert(0, os.path.join(_ROOT, "bench"))

from s28_session import (BurstSession, bayer_planes, bracket_check,  # noqa: E402
                         flicker_verdict, lock_verdict, noise_stats,
                         orient_check, patch_region, rgb565_to_rgb,
                         scale_cam_map)
import s28_patch_card as card                         # noqa: E402


# ------------------------------------------------------------------ fakes
class FakeSerial:
    def __init__(self, lines, end_reason="eot", last_error=""):
        self.lines = list(lines)
        self.end_reason = end_reason
        self.last_error = last_error

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


def make_session(lines):
    sent = []
    sess = BurstSession(FakeSerial(lines), sent.append)
    return sess, sent


def frame_lines(data, seq=0, **extra):
    """A well-formed #M header + payload line, CRLF like the real CDC."""
    b64 = base64.b64encode(data)
    hdr = {"seq": seq, "bytes": len(data), "b64": len(b64),
           "exp_us": 20000, "gain_db": 6.0,
           "rgb_gain_db": [1.0, 0.0, 2.0], "mem_free": 1 << 20,
           "gap_ms": 40, "mode": "paced"}
    hdr.update(extra)
    return [("#M " + json.dumps(hdr)).encode() + b"\n", b64 + b"\r\n"]


# --------------------------------------------------------------- protocol
def test_send_is_one_json_line():
    sess, sent = make_session([])
    sess.send(op="burst", n=8, mode="tight")
    assert len(sent) == 1 and sent[0].endswith(b"\n")
    assert json.loads(sent[0]) == {"op": "burst", "n": 8, "mode": "tight"}


def test_info_then_ok():
    sess, _ = make_session([
        b'#I {"fw": "test", "w": 640, "h": 400, "mem_free": 1}\n',
        b'stray non-hash output\n',
        b'#OK {"op": "cfg", "w": 640, "h": 400, "mem_free": 1}\n'])
    tag, obj = sess.next_event()
    assert tag == "info" and sess.info["w"] == 640
    tag, obj = sess.expect({"ok"})
    assert obj["op"] == "cfg"


def test_frame_roundtrip():
    data = bytes(range(256)) * 3
    sess, _ = make_session(frame_lines(data))
    tag, obj = sess.next_event()
    assert tag == "frame" and obj["_data"] == data


def test_bad_b64_length_skips_and_realigns():
    good = b"\x01\x02\x03" * 100
    lines = frame_lines(good, seq=0)
    lines[1] = lines[1][:-10]             # corrupt: shorter than announced
    lines += frame_lines(good, seq=1)
    sess, _ = make_session(lines)
    frames = sess.collect_burst(2)
    assert len(frames) == 1 and frames[0]["seq"] == 1
    assert sess.skips == 1


def test_board_error_raises_with_message():
    sess, _ = make_session([b'#E {"op": "burst", "err": "MemoryError"}\n'])
    try:
        sess.expect({"frame"})
        assert False, "should raise"
    except RuntimeError as e:
        assert "MemoryError" in str(e)


def test_end_mid_frame_reported():
    lines = frame_lines(b"abc")[:1]       # header, then stream death
    sess, _ = make_session(lines)
    tag, obj = sess.next_event()
    assert tag == "end" and obj["mid_frame"] == 0


def test_done_and_table_tags():
    sess, _ = make_session([
        b'#T {"fps": 5, "cmd": 100000, "got": 99992}\n',
        b'#DONE {}\n'])
    tag, obj = sess.next_event()
    assert tag == "table" and obj["got"] == 99992
    tag, _ = sess.next_event()
    assert tag == "done"


# ----------------------------------------------------------------- decode
def test_rgb565_decode_known_pixel():
    # pure red 0xF800, pure blue 0x001F — big-endian byte order
    buf = bytes([0xF8, 0x00, 0x00, 0x1F])
    rgb = rgb565_to_rgb(buf, 2, 1)
    assert rgb[0, 0].tolist() == [248, 0, 0]
    assert rgb[0, 1].tolist() == [0, 0, 248]
    # same buffer, byteswapped interpretation differs
    rgb2 = rgb565_to_rgb(buf, 2, 1, byteswap=True)
    assert rgb2[0, 0].tolist() != [248, 0, 0]


def test_bayer_bggr_planes():
    # 2x2 BGGR tile: B=10 G=20/30 R=40, tiled 4x4
    tile = np.array([[10, 20], [30, 40]], np.uint8)
    a = np.tile(tile, (2, 2))
    p = bayer_planes(a.tobytes(), 4, 4)
    assert float(p["b"].mean()) == 10.0
    assert float(p["r"].mean()) == 40.0
    assert float(p["g"].mean()) == 25.0
    assert p["r"].shape == (2, 2)


# --------------------------------------------------------------- verdicts
def test_lock_verdict_pass_and_fail():
    rows = [{"exp_us": 20000, "gain_db": 6.0,
             "rgb_gain_db": [1.0, 0.0, 2.0], "gap_ms": 40}
            for _ in range(4)]
    ok, _ = lock_verdict(rows)
    assert ok
    rows[2] = dict(rows[2], exp_us=20008)   # AE leaked one step
    ok, detail = lock_verdict(rows)
    assert not ok and detail["exp_us"] == [20000, 20008]


def test_noise_stats_measures_temporal_sigma():
    rng = np.random.default_rng(0)
    stack = 100.0 + rng.normal(0, 2.0, size=(16, 40, 40)).astype(
        np.float32)
    st = noise_stats(stack)
    assert abs(st["sigma_t"] - 2.0) < 0.15
    assert abs(st["pair_sigma"] - 2.0) < 0.3   # consistency check holds


def test_flicker_safe_vs_aliased():
    rng = np.random.default_rng(1)
    base = 100.0 + rng.normal(0, 2.0, size=(16, 64, 64))
    means = [float(f.mean()) for f in base]
    v, _ = flicker_verdict(means, 2.0, 64 * 64)
    assert v == "SAFE"
    # global PWM-style modulation moves whole frames together
    mod = base + np.linspace(-3, 3, 16)[:, None, None]
    means = [float(f.mean()) for f in mod]
    v, d = flicker_verdict(means, 2.0, 64 * 64)
    assert v == "ALIASED" and d["ratio"] > 5


def test_bracket_check_linear_and_clipped():
    ok, rows = bracket_check([(20000, 30.0), (80000, 118.0),
                              (160000, 250.0)])
    assert ok                        # 3.93x vs 4 in tol; 250 = clipped
    assert rows[1]["clipped"]
    ok, rows = bracket_check([(20000, 30.0), (80000, 60.0)])
    assert not ok                    # 2x where 4x was commanded


# ------------------------------------------------------------ calibration
def _identity_map(w, h):
    from hil_harness import CamMap
    return CamMap(np.diag([w, h, 1.0]))


def test_patch_region_identity_map():
    m = _identity_map(640, 400)
    patch = ("p", 0.5, 0.5, 0.2, 0.2, (0, 0, 0))
    x0, y0, x1, y1 = patch_region(m, patch, 640, 400, shrink=0.0)
    assert (x0, y0, x1, y1) == (256, 160, 384, 240)
    # shrink halves each dimension about the center
    x0, y0, x1, y1 = patch_region(m, patch, 640, 400, shrink=0.5)
    assert (x0, y0, x1, y1) == (288, 180, 352, 220)
    # off-frame is None, never a wrong region
    off = ("p", 1.4, 0.5, 0.2, 0.2, (0, 0, 0))
    assert patch_region(m, off, 640, 400) is None


def test_scale_cam_map_hd_to_vga():
    m = _identity_map(1280, 800)
    half = scale_cam_map(m, 0.5)
    pts = np.array([[0.25, 0.5]])
    assert np.allclose(half.frac_to_cam(pts),
                       m.frac_to_cam(pts) * 0.5)


def test_orient_check_on_rendered_card():
    im = np.asarray(card.render_card(640, 400))
    m = _identity_map(640, 400)
    assert orient_check(im, m, card.PATCHES)
    # swapped channels must fail the check
    assert not orient_check(im[:, :, ::-1], m, card.PATCHES)


# ------------------------------------------------------------- patch card
def test_patch_card_pixels_and_playback_compat(tmp_path):
    im = card.render_card()
    w, h = im.size
    for name, cx, cy, _, _, rgb in card.PATCHES:
        px = im.getpixel((int(cx * w), int(cy * h)))
        assert px == rgb, "patch %s wrong: %s != %s" % (name, px, rgb)
    stills = card.write_media(str(tmp_path))
    assert len(stills) == 2
    from playback_server import load_media       # stdlib-only import
    clips, listed = load_media(str(tmp_path))
    assert listed == stills and clips == []


# ----------------------------------------------------- stats end-to-end
def test_stats_end_to_end(tmp_path, monkeypatch, capsys):
    """Synthetic run dir -> stats.json with lock/noise/flicker keys."""
    rng = np.random.default_rng(2)
    run = tmp_path / "run1"
    (run / "frames" / "card_bayer_vga").mkdir(parents=True)
    (run / "frames" / "pwm_lcd_e00500").mkdir(parents=True)
    w, h = 64, 32
    meta = []
    for k in range(4):
        f = 128.0 + rng.normal(0, 2.0, size=(h, w))
        rel = "frames/card_bayer_vga/f%03d.bin" % k
        (run / rel).write_bytes(np.clip(f, 0, 255).astype(
            np.uint8).tobytes())
        meta.append({"stage": "card_bayer_vga", "seq": k, "file": rel,
                     "w": w, "h": h, "pixformat": "BAYER",
                     "exp_us": 20000, "gain_db": 6.0,
                     "rgb_gain_db": [1.0, 0.0, 2.0], "gap_ms": 40})
    for k in range(8):
        f = 60.0 + rng.normal(0, 2.0, size=(h, w))
        rel = "frames/pwm_lcd_e00500/f%03d.bin" % k
        (run / rel).write_bytes(np.clip(f, 0, 255).astype(
            np.uint8).tobytes())
        meta.append({"stage": "pwm_lcd_e00500", "seq": k, "file": rel,
                     "w": w, "h": h, "pixformat": "BAYER",
                     "exp_us": 500, "gain_db": 6.0,
                     "rgb_gain_db": [1.0, 0.0, 2.0], "gap_ms": 33})
    with open(run / "meta.jsonl", "w") as fh:
        for r in meta:
            fh.write(json.dumps(r) + "\n")
    with open(run / "calib_AE3.json", "w") as fh:
        json.dump({"H": np.diag([w, h, 1.0]).tolist(), "k1": 0.0,
                   "cx": w / 2, "cy": h / 2, "R": 40.0,
                   "cam_w": w, "cam_h": h, "diag": {}}, fh)

    import s28_burst_stats
    monkeypatch.setattr(sys, "argv",
                        ["s28_burst_stats", "--run", str(run)])
    s28_burst_stats.main()
    out = json.load(open(run / "stats.json"))
    assert out["lock"]["card_bayer_vga"]["ok"]
    assert out["flicker"]["pwm_lcd_e00500"]["verdict"] in ("SAFE",
                                                           "ALIASED")
    assert "card_bayer_vga" in out["noise"]


# -------------------------------------------------------- board script
def test_board_script_is_valid_python():
    src = open(os.path.join(_HERE, "s28_board_burst.py")).read()
    compile(src, "s28_board_burst.py", "exec")
    # host-side contract strings the collector relies on
    for tag in ("#I", "#OK", "#CONV", "#LOCK", "#T", "#M", "#E",
                "#DONE"):
        assert '"%s"' % tag in src or "'%s'" % tag in src or \
            'emit("%s"' % tag in src


def test_collector_script_is_valid_python():
    src = open(os.path.join(_HERE, "s28_burst_capture.py")).read()
    compile(src, "s28_burst_capture.py", "exec")


# ------------------------------------------------------ workbench guard
def _fake_urlopen(payload):
    import io as _io
    import contextlib

    @contextlib.contextmanager
    def opener(url, timeout=0):
        yield _io.BytesIO(json.dumps(payload).encode())
    return opener


def test_workbench_guard_refuses_live_and_settle(monkeypatch):
    import urllib.request
    import pytest
    import s28_burst_capture as cap
    monkeypatch.setattr(urllib.request, "urlopen",
                        _fake_urlopen({"state": "live",
                                       "recipe": "s8-hil-review"}))
    with pytest.raises(SystemExit, match="stop it from"):
        cap.workbench_idle_or_die("http://x:8088")
    monkeypatch.setattr(urllib.request, "urlopen",
                        _fake_urlopen({"state": "idle", "settle_s": 22}))
    with pytest.raises(SystemExit, match="settle window"):
        cap.workbench_idle_or_die("http://x:8088")
    monkeypatch.setattr(urllib.request, "urlopen",
                        _fake_urlopen({"state": "idle", "settle_s": 0}))
    cap.workbench_idle_or_die("http://x:8088")   # idle -> no raise
