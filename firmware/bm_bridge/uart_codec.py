# uart_codec.py -- bm_sbc `uart_l2` wire codec, dual-runtime.
#
# Byte-exact reimplementation of bm_sbc/src/transports/uart_l2/
# (frame_codec.c + cobs.c + crc32c.c, bm_core-era rev with bm_sbc main
# @ 6bc9524):
#
#   wire  = COBS( [len_hi][len_lo][l2 frame...][CRC-32C, 4 B big-endian] ) + 0x00
#   CRC-32C (Castagnoli, reflected poly 0x82F63B78) over length + frame bytes.
#
# Runs under BOTH MicroPython (AE3 HP core -- the S14 relay bench and the
# S16 bridge TX path, with @micropython.viper fast paths) and CPython
# (host tests + the Pi-side counter). Golden vectors in
# host_test/test_uart_codec.py were generated from bm_sbc's own C
# implementation (see that file's header).
#
# S14 finding target: whether the viper paths sustain >=2 Mbps on the HP
# core is exactly what bench rung B measures -- do not assume.

try:
    import micropython
    from micropython import const
    _MP = True
except ImportError:
    _MP = False

    def const(x):
        return x

FRAME_OVERHEAD = const(6)          # 2-byte length + 4-byte CRC-32C
MAX_L2_SIZE = const(1522)          # frame_codec.h FRAME_CODEC_MAX_L2_SIZE
_POLY = const(0x82F63B78)

# --------------------------------------------------------------------------
# CRC-32C table (built once at import; 1 KB as array('I') so viper can
# take a ptr32 view).
# --------------------------------------------------------------------------
import array

def _build_table():
    t = array.array("I", bytes(4) * 256)
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ _POLY if c & 1 else c >> 1
        t[i] = c
    return t

_TABLE = _build_table()


def _crc32c_py(buf, n, crc):
    t = _TABLE
    c = crc ^ 0xFFFFFFFF
    for i in range(n):
        c = t[(c ^ buf[i]) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


if _MP:
    @micropython.viper
    def _crc32c_v(buf, n: int, crc: int) -> int:
        p = ptr8(buf)
        t = ptr32(_TABLE)
        c = uint(crc) ^ uint(0xFFFFFFFF)
        for i in range(n):
            c = uint(t[(c ^ uint(p[i])) & uint(0xFF)]) ^ (c >> 8)
        return int(c ^ uint(0xFFFFFFFF))


def crc32c(data, crc=0):
    """CRC-32C of `data` (bytes/bytearray/memoryview), chainable via `crc`."""
    n = len(data)
    if _MP:
        return _crc32c_v(data, n, crc) & 0xFFFFFFFF
    return _crc32c_py(data, n, crc) & 0xFFFFFFFF


# --------------------------------------------------------------------------
# COBS
# --------------------------------------------------------------------------

def cobs_max_encoded(n):
    """Worst-case COBS output size for an n-byte input (no delimiter)."""
    return n + (n // 254) + 1


def _cobs_encode_py(dst, src, n):
    code_idx = 0
    code = 1
    w = 1
    for i in range(n):
        b = src[i]
        if b == 0:
            dst[code_idx] = code
            code_idx = w
            w += 1
            code = 1
        else:
            dst[w] = b
            w += 1
            code += 1
            if code == 0xFF:
                dst[code_idx] = code
                code_idx = w
                w += 1
                code = 1
    dst[code_idx] = code
    return w


if _MP:
    @micropython.viper
    def _cobs_encode_v(dst, src, n: int) -> int:
        d = ptr8(dst)
        s = ptr8(src)
        code_idx = 0
        code = 1
        w = 1
        for i in range(n):
            b = int(s[i])
            if b == 0:
                d[code_idx] = code
                code_idx = w
                w += 1
                code = 1
            else:
                d[w] = b
                w += 1
                code += 1
                if code == 0xFF:
                    d[code_idx] = code
                    code_idx = w
                    w += 1
                    code = 1
        d[code_idx] = code
        return w


def _cobs_decode_py(dst, src, n):
    r = 0
    w = 0
    while r < n:
        code = src[r]
        if code == 0:
            return -1
        r += 1
        for _ in range(code - 1):
            if r >= n:
                return -1
            b = src[r]
            if b == 0:
                return -1
            dst[w] = b
            r += 1
            w += 1
        if code != 0xFF and r < n:
            dst[w] = 0
            w += 1
    return w


# --------------------------------------------------------------------------
# Frame codec
# --------------------------------------------------------------------------

def frame_encode_into(wire, payload_buf, l2, n, crc_fn=None):
    """Encode `l2[:n]` into `wire`; returns wire length INCLUDING the 0x00.

    `wire` must hold cobs_max_encoded(n + 6) + 1 bytes; `payload_buf` must
    hold n + 6. Both are caller-owned so the hot loop never allocates.
    `crc_fn` is BENCH-ONLY (S14 rung E swaps in binascii.crc32 to price
    CRC-32C-in-python): the real wire format is always crc32c (default).
    """
    if n == 0 or n > MAX_L2_SIZE:
        return 0
    payload_buf[0] = (n >> 8) & 0xFF
    payload_buf[1] = n & 0xFF
    # Slice-assign straight from the source buffer (bytes, bytearray or
    # memoryview) -- one memcpy, no intermediate bytes() allocation. The
    # S23 relay profile measured the old bytes(l2[:n]) detour as part of
    # a ~1.1 ms/message python tax in the drain hot path.
    payload_buf[2:2 + n] = l2[:n]
    c = (crc_fn or crc32c)(memoryview(payload_buf)[: 2 + n]) & 0xFFFFFFFF
    payload_buf[2 + n] = (c >> 24) & 0xFF
    payload_buf[3 + n] = (c >> 16) & 0xFF
    payload_buf[4 + n] = (c >> 8) & 0xFF
    payload_buf[5 + n] = c & 0xFF
    total = n + FRAME_OVERHEAD
    if _MP:
        w = _cobs_encode_v(wire, payload_buf, total)
    else:
        w = _cobs_encode_py(wire, payload_buf, total)
    wire[w] = 0
    return w + 1


def _cobs_crc_py(dst, src, n, st):
    """COBS-encode src[0:n] into dst while accumulating CRC-32C, with
    the encoder+CRC state carried in st = array('I', [code_idx, code,
    w, crc_running]) so one wire frame can be fed in pieces (header,
    body, CRC trailer). Python twin of the viper fast path -- byte-exact
    by the shared golden vectors."""
    t = _TABLE
    code_idx = st[0]
    code = st[1]
    w = st[2]
    c = st[3]
    for i in range(n):
        b = src[i]
        c = t[(c ^ b) & 0xFF] ^ (c >> 8)
        if b == 0:
            dst[code_idx] = code
            code_idx = w
            w += 1
            code = 1
        else:
            dst[w] = b
            w += 1
            code += 1
            if code == 0xFF:
                dst[code_idx] = code
                code_idx = w
                w += 1
                code = 1
    st[0] = code_idx
    st[1] = code
    st[2] = w
    st[3] = c & 0xFFFFFFFF


if _MP:
    @micropython.viper
    def _cobs_crc_v(dst, src, n: int, st):
        d = ptr8(dst)
        s = ptr8(src)
        t = ptr32(_TABLE)
        p = ptr32(st)
        code_idx = int(p[0])
        code = int(p[1])
        w = int(p[2])
        c = uint(p[3])
        for i in range(n):
            b = int(s[i])
            c = uint(t[(c ^ uint(b)) & uint(0xFF)]) ^ (c >> 8)
            if b == 0:
                d[code_idx] = code
                code_idx = w
                w += 1
                code = 1
            else:
                d[w] = b
                w += 1
                code += 1
                if code == 0xFF:
                    d[code_idx] = code
                    code_idx = w
                    w += 1
                    code = 1
        p[0] = code_idx
        p[1] = code
        p[2] = w
        p[3] = int(c)


_FUSE_STATE = array.array("I", [0, 0, 0, 0])
_FUSE_HDR = bytearray(2)
_FUSE_CRC = bytearray(4)


def frame_encode_fused(wire, l2, n):
    """One-pass encoder for the relay hot path (S23 GOLD): COBS-encode
    and CRC the frame in the SAME traversal, feeding header / body /
    CRC-trailer through the carried state -- no payload_buf copy, no
    separate CRC pass. Byte-identical to frame_encode_into (goldens pin
    it). NOT thread-safe (module-scope scratch state) -- the bridge's
    single-core pump is the only caller."""
    if n == 0 or n > MAX_L2_SIZE:
        return 0
    step = _cobs_crc_v if _MP else _cobs_crc_py
    st = _FUSE_STATE
    st[0] = 0          # code_idx
    st[1] = 1          # code
    st[2] = 1          # w
    st[3] = 0xFFFFFFFF  # running CRC (crc32c seed)
    _FUSE_HDR[0] = (n >> 8) & 0xFF
    _FUSE_HDR[1] = n & 0xFF
    step(wire, _FUSE_HDR, 2, st)
    step(wire, l2, n, st)
    c = (st[3] ^ 0xFFFFFFFF) & 0xFFFFFFFF   # CRC over header+frame, closed
    _FUSE_CRC[0] = (c >> 24) & 0xFF
    _FUSE_CRC[1] = (c >> 16) & 0xFF
    _FUSE_CRC[2] = (c >> 8) & 0xFF
    _FUSE_CRC[3] = c & 0xFF
    step(wire, _FUSE_CRC, 4, st)            # trailer COBS-continues; CRC
    w = st[2]                               # state now don't-care
    wire[st[0]] = st[1]                     # close the open COBS block
    wire[w] = 0
    return w + 1


def frame_encode(l2, crc_fn=None):
    """Convenience allocating encoder: returns the full wire bytes (with 0x00)."""
    n = len(l2)
    payload = bytearray(n + FRAME_OVERHEAD)
    wire = bytearray(cobs_max_encoded(n + FRAME_OVERHEAD) + 1)
    w = frame_encode_into(wire, payload, l2, n, crc_fn)
    return bytes(wire[:w])


def frame_decode(wire, crc_fn=None):
    """Decode one COBS segment (WITHOUT its 0x00 delimiter) -> l2 bytes.

    Returns None on any error (bad COBS, short payload, length or CRC
    mismatch) -- mirroring frame_decode()'s 0-return in C. `crc_fn` is
    BENCH-ONLY (must match the encoder's; the real wire is crc32c).
    """
    n = len(wire)
    if n < 2:
        return None
    dst = bytearray(n)          # decoded is always shorter than encoded
    m = _cobs_decode_py(dst, wire, n)
    if m < FRAME_OVERHEAD + 1:
        return None
    l2_len = (dst[0] << 8) | dst[1]
    if l2_len != m - FRAME_OVERHEAD:
        return None
    want = (dst[m - 4] << 24) | (dst[m - 3] << 16) | (dst[m - 2] << 8) | dst[m - 1]
    if ((crc_fn or crc32c)(memoryview(dst)[: m - 4]) & 0xFFFFFFFF) != want:
        return None
    return bytes(dst[2 : m - 4])


class StreamSplitter:
    """Accumulate a byte stream, yield decoded L2 frames at 0x00 delimiters.

    Portable (used by the Pi counter now, the S16 bridge RX later).
    Counts decode errors instead of raising -- stray text on the wire
    (a boot banner, a print) must not kill the link; COBS resyncs at the
    next delimiter.
    """

    def __init__(self, crc_fn=None):
        self._buf = b""
        self._crc_fn = crc_fn
        self.frames = 0
        self.errors = 0

    def feed(self, chunk):
        out = []
        self._buf += chunk
        while True:
            i = self._buf.find(b"\x00")
            if i < 0:
                break
            seg = self._buf[:i]
            self._buf = self._buf[i + 1 :]
            if not seg:
                continue            # empty segment between delimiters
            l2 = frame_decode(seg, self._crc_fn)
            if l2 is None:
                self.errors += 1
            else:
                self.frames += 1
                out.append(l2)
        return out


def self_test():
    """Cheap on-import-able sanity: known CRC + one round trip. Returns True."""
    assert crc32c(b"123456789") == 0xE3069283, "crc32c check value"
    f = bytes(range(1, 60)) + b"\x00\x00" + bytes(range(60))
    w = frame_encode(f)
    assert w[-1] == 0 and b"\x00" not in w[:-1], "cobs zero-freedom"
    assert frame_decode(w[:-1]) == f, "round trip"
    return True
