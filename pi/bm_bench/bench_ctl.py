#!/usr/bin/env python3
"""Client for the bench control socket (S18 bite B).

The Telemetry node's bench_apps carries an AF_UNIX SOCK_DGRAM socket at
/run/bm/bench.sock: one JSON object in, one JSON object out. This module is
the only place that speaks it, so the shell wrapper (bench-ctl.sh), the S18
web server (pi/bench_web, bite C) and the trial scripts all agree on the
framing, the timeout and the id matching.

Stdlib only, same rule as the frozen S3 stream server.

Two things this client does that a naive one would not:

  * It BINDS its own socket path. A DGRAM sender that never binds has no
    address, so the app cannot reply -- the reply is silently lost and the
    command looks like it timed out. The app logs that case; this client
    makes it impossible.

  * It matches the echoed `id`. Replies are datagrams, so a late reply to a
    timed-out request would otherwise be read as the answer to the next one.
    Mismatched replies are drained and ignored, not returned.

Usage as a library:

    from bench_ctl import BenchCtl
    with BenchCtl() as c:
        st = c.request({"cmd": "status"})
        c.capture(q=50, res="hd", pf="color")

Usage from a shell (see also bench-ctl.sh):

    python3 bench_ctl.py status
    python3 bench_ctl.py capture 50 hd color
    python3 bench_ctl.py stream 2.0 15 60 50 vga color
    python3 bench_ctl.py '{"cmd": "capture", "q": 90, "save": false}'
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile

DEFAULT_SOCK = "/run/bm/bench.sock"
DEFAULT_TIMEOUT = 3.0
# The app refuses anything larger; matching it here turns an oversize reply
# into a clear error instead of a truncated parse.
REPLY_MAX = 8192


class BenchCtlError(RuntimeError):
    """The request could not be delivered, or the node refused it."""


class BenchCtl:
    def __init__(self, path: str = None, timeout: float = DEFAULT_TIMEOUT):
        self.path = path or os.environ.get("S18_CTL_SOCK", DEFAULT_SOCK)
        self.timeout = timeout
        self._seq = 0
        self._sock = None
        self._mypath = None

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> "BenchCtl":
        if self._sock is not None:
            return self
        if not os.path.exists(self.path):
            raise BenchCtlError(
                f"{self.path} does not exist — is bm-telemetry running? "
                "(sudo systemctl start bm-telemetry)"
            )
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        # Bind an own address so the node has somewhere to reply to.
        self._mypath = os.path.join(
            tempfile.gettempdir(), f"bench_ctl_{os.getpid()}_{id(self)}.sock"
        )
        try:
            s.bind(self._mypath)
            s.settimeout(self.timeout)
            s.connect(self.path)
        except OSError as e:
            s.close()
            self._cleanup_path()
            raise BenchCtlError(f"cannot reach {self.path}: {e}") from e
        self._sock = s
        return self

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._cleanup_path()

    def _cleanup_path(self) -> None:
        if self._mypath and os.path.exists(self._mypath):
            try:
                os.unlink(self._mypath)
            except OSError:
                pass
        self._mypath = None

    def __enter__(self) -> "BenchCtl":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- the one primitive -------------------------------------------------
    def request(self, obj: dict) -> dict:
        """Send one command, return the parsed reply. Raises on timeout."""
        if self._sock is None:
            self.open()
        self._seq += 1
        msg = dict(obj)
        msg.setdefault("v", 1)
        msg["id"] = self._seq
        self._sock.send(json.dumps(msg).encode())

        # Drain until the reply carrying our id arrives, or we run out of
        # time. A stale reply is discarded, never returned as this one's.
        deadline = self._seq
        while True:
            try:
                data = self._sock.recv(REPLY_MAX)
            except socket.timeout as e:
                raise BenchCtlError(
                    f"no reply to {msg.get('cmd')!r} within {self.timeout}s "
                    f"(journalctl -u bm-telemetry -n 50)"
                ) from e
            try:
                rep = json.loads(data.decode())
            except ValueError as e:
                raise BenchCtlError(f"unparseable reply: {data[:120]!r}") from e
            if rep.get("id") == deadline:
                return rep
            # else: a late reply to an earlier request — drop it and keep waiting

    # -- verbs (thin, so the grammar lives in one place) -------------------
    def status(self) -> dict:
        return self.request({"cmd": "status"})

    def capture(self, q=None, res=None, pf=None, save=None) -> dict:
        return self.request(_cam("capture", q, res, pf, save=save))

    def stream(self, mbps=None, fps=None, secs=None, q=None, res=None,
               pf=None) -> dict:
        cmd = _cam("stream", q, res, pf)
        for k, v in (("mbps", mbps), ("fps", fps), ("secs", secs)):
            if v is not None:
                cmd[k] = v
        return self.request(cmd)

    def stop(self) -> dict:
        return self.request({"cmd": "stop"})

    def cam_status(self) -> dict:
        return self.request({"cmd": "cam-status"})

    def light(self, level: int) -> dict:
        return self.request({"cmd": "light", "level": level})

    def strobe(self, on_ms=200, off_ms=200, count=5) -> dict:
        return self.request(
            {"cmd": "strobe", "on_ms": on_ms, "off_ms": off_ms, "count": count}
        )


def _cam(verb, q, res, pf, save=None) -> dict:
    out = {"cmd": verb}
    if q is not None:
        out["q"] = int(q)
    if res is not None:
        out["res"] = res
    if pf is not None:
        out["pf"] = pf
    if save is not None:
        out["save"] = bool(save)
    return out


# ---------------------------------------------------------------------------
# CLI: the same argument order as the FIFO operator CLI, so muscle memory
# transfers between `bm-cmd.sh capture 50 hd color` and this.
# ---------------------------------------------------------------------------

def _cli(argv) -> int:
    if not argv:
        print(__doc__.strip())
        return 2
    first = argv[0]
    if first.lstrip().startswith("{"):
        obj = json.loads(first)
    else:
        verb, args = first, argv[1:]

        def arg(i, cast=str):
            return cast(args[i]) if len(args) > i and args[i] != "-" else None

        if verb == "capture":
            obj = _cam("capture", arg(0, int), arg(1), arg(2))
        elif verb == "stream":
            obj = _cam("stream", arg(3, int), arg(4), arg(5))
            for k, i, cast in (("mbps", 0, float), ("fps", 1, float),
                               ("secs", 2, int)):
                v = arg(i, cast)
                if v is not None:
                    obj[k] = v
        elif verb == "light":
            obj = {"cmd": "light", "level": arg(0, int) or 0}
        elif verb == "strobe":
            obj = {"cmd": "strobe", "on_ms": arg(0, int) or 200,
                   "off_ms": arg(1, int) or 200, "count": arg(2, int) or 5}
        else:
            obj = {"cmd": verb}

    try:
        with BenchCtl() as c:
            rep = c.request(obj)
    except BenchCtlError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(json.dumps(rep, indent=2, sort_keys=True))
    # A refusal is a non-zero exit: a script that ignores it should still fail.
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
