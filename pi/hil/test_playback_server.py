#!/usr/bin/env python3
"""Host tests for playback_server.py (stdlib; run anywhere):
  python3 pi/hil/test_playback_server.py
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playback_server import (MARKERS, State, load_media,  # noqa: E402
                             make_handler)


def make_media(root, n_stills=3, manifest=True):
    os.makedirs(os.path.join(root, "stills", "frames"))
    with open(os.path.join(root, "clip_a.mp4"), "wb") as fh:
        fh.write(b"\x00" * 1000)
    with open(os.path.join(root, "clip_b.mp4"), "wb") as fh:
        fh.write(b"\x00" * 500)
    idxs = [7, 42, 99][:n_stills]
    for i in idxs:
        with open(os.path.join(root, "stills", "frames",
                               f"tc_f{i:04d}.jpg"), "wb") as fh:
            fh.write(b"J" * 10)
    if manifest:
        man = {"clips": [{"still_prefix": "tc", "sampled_indices": idxs}]}
        with open(os.path.join(root, "stills",
                               "stills_manifest.json"), "w") as fh:
            json.dump(man, fh)


class LoadMedia(unittest.TestCase):

    def test_manifest_order_is_canonical(self):
        with tempfile.TemporaryDirectory() as d:
            make_media(d)
            clips, stills = load_media(d)
            self.assertEqual(clips, ["clip_a.mp4", "clip_b.mp4"])
            self.assertEqual(stills, ["stills/frames/tc_f0007.jpg",
                                      "stills/frames/tc_f0042.jpg",
                                      "stills/frames/tc_f0099.jpg"])

    def test_manifest_naming_a_missing_still_is_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            make_media(d)
            os.remove(os.path.join(d, "stills", "frames", "tc_f0042.jpg"))
            with self.assertRaises(SystemExit):
                load_media(d)

    def test_no_manifest_falls_back_to_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            make_media(d, manifest=False)
            _, stills = load_media(d)
            self.assertEqual(len(stills), 3)
            self.assertEqual(stills, sorted(stills))


class StateMachine(unittest.TestCase):

    def setUp(self):
        self.st = State(["a.mp4", "b.mp4"],
                        ["stills/frames/x.jpg", "stills/frames/y.jpg"])

    def test_defaults_and_seq(self):
        s = self.st.snapshot()
        self.assertEqual((s["mode"], s["seq"]), ("loop", 1))
        self.assertEqual(s["markers"], MARKERS)

    def test_set_bumps_seq_and_validates(self):
        ok, _ = self.st.set({"mode": "step", "still": 1})
        self.assertTrue(ok)
        self.assertEqual(self.st.snapshot()["seq"], 2)
        ok, err = self.st.set({"mode": "flicker"})
        self.assertFalse(ok)
        self.assertIn("mode", err)
        ok, err = self.st.set({"clip": 5})
        self.assertFalse(ok)
        self.assertIn("out of range", err)

    def test_step_wraps(self):
        self.st.set({"mode": "step", "still": 1})
        self.st.set({"step": 1})
        self.assertEqual(self.st.snapshot()["still"], 0)
        self.st.set({"step": -1})
        self.assertEqual(self.st.snapshot()["still"], 1)

    def test_step_mode_without_stills_refused(self):
        st = State(["a.mp4"], [])
        ok, err = st.set({"mode": "step"})
        self.assertFalse(ok)
        self.assertIn("no stills", err)


class HttpApi(unittest.TestCase):
    """The real handler over a real socket — the artifact, not the units."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        make_media(cls.tmp.name)
        clips, stills = load_media(cls.tmp.name)
        cls.state = State(clips, stills)
        cls.srv = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(cls.state, cls.tmp.name))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"} if body else {}
        c.request(method, path,
                  json.dumps(body) if body is not None else None, headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_page_and_state(self):
        r, data = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"HIL playback", data)
        r, data = self.req("GET", "/api/state")
        self.assertEqual(json.loads(data)["mode"], "loop")

    def test_set_roundtrip_and_bad_body(self):
        r, data = self.req("POST", "/api/set", {"mode": "calib"})
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(data)["mode"], "calib")
        r, _ = self.req("POST", "/api/set", {"mode": "nope"})
        self.assertEqual(r.status, 400)

    def test_media_confinement(self):
        r, _ = self.req("GET", "/media/../playback_server.py")
        self.assertEqual(r.status, 404)
        r, _ = self.req("GET", "/media/nothere.jpg")
        self.assertEqual(r.status, 404)

    def test_media_full_and_range(self):
        r, data = self.req("GET", "/media/clip_a.mp4")
        self.assertEqual((r.status, len(data)), (200, 1000))
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/media/clip_a.mp4", headers={"Range": "bytes=10-19"})
        r = c.getresponse()
        data = r.read()
        c.close()
        self.assertEqual((r.status, len(data)), (206, 10))
        self.assertEqual(r.getheader("Content-Range"), "bytes 10-19/1000")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/media/clip_a.mp4",
                  headers={"Range": "bytes=2000-"})
        r = c.getresponse()
        r.read()
        c.close()
        self.assertEqual(r.status, 416)


if __name__ == "__main__":
    unittest.main(verbosity=2)
