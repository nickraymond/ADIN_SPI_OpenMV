"""Tests for the S31 N6 H.264 bench harness. No hardware, no board contact.

These cover the two things that can silently produce a wrong ANSWER rather
than a crash: the single-owner preflight, and the refusal to take a ratio
from frames that are not true-motion.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import n6_h264_run as R  # noqa: E402


def cell(codec, size="VGA", quality=30, bpf=10000.0, fps=30.0):
    return {
        "codec": codec, "size": size, "w": 640, "h": 480, "quality": quality,
        "frames": 90, "bytes": bpf * 90, "bytes_per_frame": bpf,
        "encode_ms_per_frame": 5.0, "loop_ms_per_frame": 1000.0 / fps,
        "achieved_fps": fps, "true_motion": fps >= 25.0,
        "heap_free_after": 25000000,
    }


# --- parsing ---------------------------------------------------------------

def test_parse_splits_the_three_line_kinds():
    out = "\n".join([
        'INFO {"codec_module": true}',
        'RESULT %s' % json.dumps(cell("mjpeg")),
        'ERROR {"err": "boom"}',
        "some unrelated board chatter",
    ])
    info, results, errors = R.parse(out)
    assert len(info) == 1 and info[0]["codec_module"] is True
    assert len(results) == 1 and results[0]["codec"] == "mjpeg"
    assert len(errors) == 1 and errors[0]["err"] == "boom"


def test_parse_records_a_malformed_result_as_an_error_not_a_silent_drop():
    info, results, errors = R.parse("RESULT {not json}")
    assert results == []
    assert errors and "unparsed" in errors[0]


# --- the probe is shipped whole, so the board runs what we reviewed --------

def test_build_script_appends_the_call_to_the_real_probe_source():
    plan = {"frames": 3, "sizes": ["VGA"]}
    src = R.build_script(plan)
    assert "def cell_h264(" in src, "probe body must be shipped, not re-implemented"
    assert src.rstrip().endswith("run(%s)" % json.dumps(plan))


# --- single-owner preflight -----------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_preflight_refuses_while_a_demo_is_running(monkeypatch, capsys):
    monkeypatch.setattr(R.urllib.request, "urlopen",
                        lambda *a, **k: _Resp({"running": "s8-ball-demo"}))
    with pytest.raises(SystemExit) as e:
        R.preflight("http://nereus000:8088")
    msg = str(e.value)
    assert "s8-ball-demo" in msg
    assert "FROM THE PAGE" in msg, "must not suggest killing the process"


def test_preflight_refuses_when_the_workbench_is_unreachable(monkeypatch):
    def boom(*a, **k):
        raise OSError("no route to host")
    monkeypatch.setattr(R.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit) as e:
        R.preflight("http://nereus000:8088")
    # Unreachable must FAIL, not fall through to opening the port.
    assert "could not reach" in str(e.value)


def test_preflight_passes_when_idle(monkeypatch, capsys):
    monkeypatch.setattr(R.urllib.request, "urlopen",
                        lambda *a, **k: _Resp({"running": None}))
    R.preflight("http://nereus000:8088")
    assert "idle" in capsys.readouterr().out


# --- the ratio guard: the thing that makes a number honest ----------------

def _doc(results):
    return {"port": "x", "plan": {}, "info": [], "results": results, "errors": []}


def test_compare_computes_a_ratio_only_from_true_motion_cells(tmp_path, capsys):
    before = tmp_path / "b.json"
    after = tmp_path / "a.json"
    before.write_text(json.dumps(_doc([cell("mjpeg", bpf=14000.0)])))
    after.write_text(json.dumps(_doc([
        cell("mjpeg", bpf=14000.0),
        cell("h264", bpf=3500.0),
    ])))
    R.compare(str(before), str(after))
    out = capsys.readouterr().out
    assert "4.00x" in out


def test_compare_refuses_a_ratio_when_the_loop_could_not_keep_up(tmp_path, capsys):
    """A slow loop samples a slower scene, so consecutive frames share more
    than a real 30 fps clip would and the ratio flatters H.264. Refuse it."""
    before = tmp_path / "b.json"
    after = tmp_path / "a.json"
    before.write_text(json.dumps(_doc([cell("mjpeg", bpf=14000.0)])))
    after.write_text(json.dumps(_doc([
        cell("mjpeg", bpf=14000.0),
        cell("h264", bpf=3500.0, fps=6.0),   # 6 fps -> not true motion
    ])))
    R.compare(str(before), str(after))
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "4.00x" not in out, "a ratio must not be printed for slow-loop cells"


def test_compare_flags_an_mjpeg_regression_across_the_flash(tmp_path, capsys):
    before = tmp_path / "b.json"
    after = tmp_path / "a.json"
    before.write_text(json.dumps(_doc([cell("mjpeg", bpf=14000.0)])))
    after.write_text(json.dumps(_doc([cell("mjpeg", bpf=20000.0)])))
    R.compare(str(before), str(after))
    assert "CHECK" in capsys.readouterr().out


def test_compare_does_not_flag_small_scene_drift(tmp_path, capsys):
    before = tmp_path / "b.json"
    after = tmp_path / "a.json"
    before.write_text(json.dumps(_doc([cell("mjpeg", bpf=14000.0)])))
    after.write_text(json.dumps(_doc([cell("mjpeg", bpf=14300.0)])))
    R.compare(str(before), str(after))
    assert "CHECK" not in capsys.readouterr().out


# --- port hygiene ----------------------------------------------------------

def test_main_refuses_a_ttyacm_port(monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["n6_h264_run.py", "--port", "/dev/ttyACM0", "--no-preflight"])
    with pytest.raises(SystemExit):
        R.main()
