#!/usr/bin/env python3
"""Host tests for the S14 relay protocol: a CPython-simulated pump session
parsed exactly the way bench/s14_relay_counter.py parses it.

Covers: the leading-delimiter rule, S14F seq accounting, aggregated
frames, S14END summary extraction, crc-mode symmetry (c/z/n), and the
banner/CFG text lines interleaving with the binary stream.

Run: python3 firmware/bm_bridge/host_test/test_relay_protocol.py
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import uart_codec as uc  # noqa: E402

CHECKS = [0, 0]


def check(desc, ok):
    CHECKS[0] += 1
    if not ok:
        CHECKS[1] += 1
        print("  FAIL: %s" % desc)
    else:
        print("  pass: %s" % desc)


def crc_fn_for(mode):
    if mode == "z":
        from binascii import crc32
        return lambda mv: crc32(mv) & 0xFFFFFFFF
    if mode == "n":
        return lambda mv: 0
    return None


def simulate_pump_stream(n_frames, unit=100, agg=1, crc="c"):
    """Byte stream a pump rung emits, incl. surrounding text lines."""
    crc_fn = crc_fn_for(crc)
    out = [b"\r\nS14-PUMP ready\r\n", b"CFG {}\r\n", b"\x00"]
    l2_len = 8 + unit * agg
    for seq in range(n_frames):
        l2 = bytearray(l2_len)
        l2[0:4] = b"S14F"
        struct.pack_into("<I", l2, 4, seq)
        l2[8:] = bytes((seq + i) & 0xFF for i in range(unit * agg))
        payload = bytearray(l2_len + uc.FRAME_OVERHEAD)
        wire = bytearray(uc.cobs_max_encoded(l2_len + uc.FRAME_OVERHEAD) + 1)
        w = uc.frame_encode_into(wire, payload, l2, l2_len, crc_fn)
        out.append(bytes(wire[:w]))
    summary = {"rung": "B", "frames": n_frames, "secs": 1.0, "aborted": 0}
    end = b"S14END" + json.dumps(summary).encode()
    payload = bytearray(len(end) + uc.FRAME_OVERHEAD)
    wire = bytearray(uc.cobs_max_encoded(len(end) + uc.FRAME_OVERHEAD) + 1)
    w = uc.frame_encode_into(wire, payload, end, len(end), crc_fn)
    out.append(bytes(wire[:w]))
    out.append(b"DONE {}\r\n")
    return b"".join(out)


def parse_like_counter(stream, crc="c", chunk=1000):
    splitter = uc.StreamSplitter(crc_fn_for(crc))
    frames = 0
    seq_gaps = 0
    last_seq = -1
    summary = None
    for i in range(0, len(stream), chunk):
        for l2 in splitter.feed(stream[i : i + chunk]):
            if l2.startswith(b"S14END"):
                summary = json.loads(l2[6:].decode())
            elif l2.startswith(b"S14F") and len(l2) >= 8:
                seq = struct.unpack_from("<I", l2, 4)[0]
                if last_seq >= 0 and seq != last_seq + 1:
                    seq_gaps += 1
                last_seq = seq
                frames += 1
    return frames, seq_gaps, summary, splitter.errors


def test_clean_session():
    print("clean session:")
    for crc in ("c", "z", "n"):
        stream = simulate_pump_stream(25, unit=100, agg=1, crc=crc)
        frames, gaps, summary, errs = parse_like_counter(stream, crc=crc)
        ok = frames == 25 and gaps == 0 and summary and summary["frames"] == 25
        # banner+CFG merge into the pre-delimiter segment -> exactly 1 error;
        # trailing "DONE" text has no delimiter after it -> stays buffered.
        check("crc=%s frames+summary" % crc, ok)
        check("crc=%s text costs 1 decode err" % crc, errs == 1)


def test_crc_mode_mismatch():
    print("crc-mode mismatch is loud:")
    stream = simulate_pump_stream(10, crc="z")
    frames, _, summary, errs = parse_like_counter(stream, crc="c")
    check("z-stream vs c-counter: all frames rejected",
          frames == 0 and summary is None and errs >= 10)


def test_aggregation():
    print("aggregation:")
    stream = simulate_pump_stream(12, unit=468, agg=3, crc="c")
    frames, gaps, summary, _ = parse_like_counter(stream, crc="c")
    check("agg=3 frames intact (l2=%d B)" % (8 + 468 * 3),
          frames == 12 and gaps == 0 and summary is not None)


def test_gap_detection():
    print("gap detection:")
    stream = simulate_pump_stream(30, unit=64)
    # Excise one whole wire frame (find 0x00 boundaries after the leading
    # delimiter; drop the 5th segment).
    parts = stream.split(b"\x00")
    victim = 5
    parts = parts[:victim] + parts[victim + 1 :]
    frames, gaps, summary, _ = parse_like_counter(b"\x00".join(parts))
    check("one dropped frame -> 1 gap", frames == 29 and gaps == 1)
    check("summary still recovered", summary is not None)


def test_mid_stream_garbage():
    print("mid-stream garbage:")
    stream = simulate_pump_stream(20, unit=64)
    idx = len(stream) // 2
    dirty = stream[:idx] + b"Traceback (most recent call last)\x00" + stream[idx:]
    frames, gaps, summary, errs = parse_like_counter(dirty)
    # The injected text splits one real frame in half: the halves fail
    # decode (2 errors + the text itself may form segments), one frame is
    # lost as a gap, and the stream resyncs.
    check("resyncs, summary recovered", summary is not None)
    check("errors counted", errs >= 2)
    check("at most 1 frame lost", frames >= 19)


if __name__ == "__main__":
    for t in (test_clean_session, test_crc_mode_mismatch, test_aggregation,
              test_gap_detection, test_mid_stream_garbage):
        t()
    print("\n%d checks, %d failures" % (CHECKS[0], CHECKS[1]))
    sys.exit(1 if CHECKS[1] else 0)
