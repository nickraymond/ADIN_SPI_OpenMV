# test_bench_ctl.py -- host tests for the bench control client.
# No Pi, no hardware: the "node" is a real AF_UNIX SOCK_DGRAM socket on this
# machine, because the bug these tests pin down lives in the kernel's DGRAM
# connection semantics, not in our parsing.
#
# THE BUG (observed live on nereus001 2026-08-18): bm-telemetry restarts and
# rebinds /run/bm/bench.sock. A connected DGRAM client points at the OLD
# socket, not the path, so every later request died with a raw OSError
# (ENOTCONN) for the rest of the client's life -- and bench_web, which only
# catches BenchCtlError, dropped the HTTP connection instead of answering 503.
# The fix is reconnect-once-and-retry in request(); these tests are its
# acceptance.
#
# Run:  python3 pi/bm_bench/test_bench_ctl.py

import json
import os
import socket
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bench_ctl import BenchCtl, BenchCtlError  # noqa: E402


class FakeNode:
    """A stand-in bm-telemetry: binds the path, echoes {"id", "ok", "node"}.

    Runs on a thread so the client can block in recv() while we answer.
    close() tears the socket down and unlinks the path -- exactly what a
    systemd stop does; a fresh FakeNode at the same path is the restart.
    """

    def __init__(self, path, name):
        self.path = path
        self.name = name
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(path)
        self.sock.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                data, sender = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                return
            req = json.loads(data.decode())
            rep = {"id": req.get("id"), "ok": True, "node": self.name}
            self.sock.sendto(json.dumps(rep).encode(), sender)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.sock.close()
        if os.path.exists(self.path):
            os.unlink(self.path)


class TestReconnect(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="bench_ctl_test_")
        self.path = os.path.join(self.dir, "bench.sock")
        self.client = None
        self.nodes = []

    def tearDown(self):
        if self.client is not None:
            self.client.close()
        for n in self.nodes:
            n.close()
        for name in os.listdir(self.dir):
            try:
                os.unlink(os.path.join(self.dir, name))
            except OSError:
                pass
        os.rmdir(self.dir)

    def node(self, name):
        n = FakeNode(self.path, name)
        self.nodes.append(n)
        return n

    def connect(self):
        self.client = BenchCtl(path=self.path, timeout=2.0).open()
        return self.client

    def test_round_trip(self):
        self.node("n1")
        rep = self.connect().request({"cmd": "status"})
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["node"], "n1")

    def test_survives_the_socket_being_recreated_between_requests(self):
        # THE observed failure: request 1 to the original node, then the node
        # restarts (socket closed, path unlinked and rebound), then request 2
        # on the SAME client. Before the fix, request 2 -- and every request
        # after it, forever -- raised a raw OSError.
        n1 = self.node("n1")
        c = self.connect()
        self.assertEqual(c.request({"cmd": "status"})["node"], "n1")
        n1.close()
        self.node("n2")
        rep = c.request({"cmd": "status"})
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["node"], "n2", "reply must come from the NEW node")
        # And the client is healthy for the rest of the session, not just once.
        self.assertEqual(c.request({"cmd": "status"})["node"], "n2")

    def test_node_down_is_the_socket_down_error_not_a_raw_oserror(self):
        # Restart with nobody home: the reconnect fails, and the caller gets
        # the same BenchCtlError the socket-absent case raises -- which is
        # what bench_web already catches and turns into a 503.
        n1 = self.node("n1")
        c = self.connect()
        c.request({"cmd": "status"})
        n1.close()
        with self.assertRaises(BenchCtlError) as cm:
            c.request({"cmd": "status"})
        self.assertIn(self.path, str(cm.exception))

    def test_stale_path_with_no_listener_is_a_benchctlerror(self):
        # The path exists but nothing is bound (a crash that never cleaned
        # up). connect() fails; the client must report it, not crash.
        n1 = self.node("n1")
        c = self.connect()
        c.request({"cmd": "status"})
        n1.close()
        # Plant a dead socket file at the path.
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        dead.bind(self.path)
        dead.close()
        with self.assertRaises(BenchCtlError):
            c.request({"cmd": "status"})

    def test_recovery_after_a_failed_reconnect(self):
        # Down, refused, then back up: the client object stays usable. A
        # bench_web that keeps one Bench for its whole life depends on this.
        n1 = self.node("n1")
        c = self.connect()
        c.request({"cmd": "status"})
        n1.close()
        with self.assertRaises(BenchCtlError):
            c.request({"cmd": "status"})
        self.node("n3")
        self.assertEqual(c.request({"cmd": "status"})["node"], "n3")

    def test_ids_stay_monotonic_across_a_reconnect(self):
        # The id match is what keeps a late reply from being read as the
        # answer to the next request; a reconnect must not reset it.
        n1 = self.node("n1")
        c = self.connect()
        first = c.request({"cmd": "status"})["id"]
        n1.close()
        self.node("n2")
        second = c.request({"cmd": "status"})["id"]
        self.assertGreater(second, first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
