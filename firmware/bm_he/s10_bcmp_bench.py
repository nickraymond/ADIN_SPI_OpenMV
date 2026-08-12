# s10_bcmp_bench.py -- S10 INTERIM 2a runner. Runs ON the AE3's HP core
# (stock/fixture firmware, no flash) via mpremote from nereus000:
#
#   mpremote connect <by-id> cp bm_he.elf :/flash/bm_he.elf   # once/build
#   mpremote connect <by-id> run s10_bcmp_bench.py
#
# Loads the bm_core stack app onto the HE core at runtime (remoteproc ELF
# load into SRAM9_B), acts as the far end of the mock NetworkDevice's fake
# wire, and prints the verdict table:
#   A -- BM stack up on HE (bm_os/lwIP/BCMP init ladder RUNNING, node id +
#        both IPv6 addresses derived per bm_lwip.c:289-295)
#   B -- BCMP heartbeats emitted on schedule and wire-correct (Ethernet II
#        0x86DD / IPv6 next-header 0xBC / BCMP type 0x01 / node id in src
#        addr bytes 8-15 / BCMP checksum verifies / boot-time monotonic);
#        frames also written to /flash/bm_he_hb.pcap for Wireshark
#        (bm_core ships a dissector: proto_bcmp.lua).
#
# Copy the capture off afterwards:
#   mpremote connect <by-id> cp :/flash/bm_he_hb.pcap .

import openamp
import struct
import time
import machine

ELF_PATHS = ("/flash/bm_he.elf", "bm_he.elf")
BM_STATUS_PAGE = 0x600BFE00
PCAP_PATH = "/flash/bm_he_hb.pcap"

NODE_ID = 0x424D4845AE30BEEF  # bm_stubs.c BM_HE_NODE_ID
HB_PERIOD_S = 10              # bcmp.c bcmp_heartbeat_s
CAPTURE_S = 25                # >= 2 timer heartbeats + the link-up one
MIN_HEARTBEATS = 2

# wire protocol (bm_he.h)
WCMD_FRAME_TX = 0x11
WCMD_FRAME_RX = 0x12
WCMD_LINK = 0x13
WCMD_QUERY = 0x14
WREP_STATUS = 0x94

STAGES = {0: "-", 1: "BOOT", 2: "RTOS", 3: "RPMSG", 4: "L2", 5: "IP",
          6: "BCMP", 7: "RUNNING"}


def m32(addr):
    return machine.mem32[addr] & 0xFFFFFFFF


def bm_page():
    if m32(BM_STATUS_PAGE) != 0x424D4845:  # 'BMHE'
        return None
    f = [m32(BM_STATUS_PAGE + 4 * i) for i in range(10)]
    return {"stage": STAGES.get(f[1], f[1]), "err": f[2], "tick": f[3],
            "tx": f[4], "rx": f[5], "hb": f[6],
            "ring": (f[7], f[8], f[9])}


def dump_dbg_ring():
    p = bm_page()
    if not p or not p["ring"][0]:
        return
    addr, size, widx = p["ring"]
    import uctypes
    ring = bytes(uctypes.bytearray_at(addr, size))
    n = min(widx, size)
    start = widx % size if widx > size else 0
    text = (ring[start:n] + ring[:start]) if widx > size else ring[:n]
    print("-- HE debug ring " + "-" * 44)
    for line in text.decode().split("\n"):
        if line:
            print("   | " + line)
    print("-" * 61)


# ---- BCMP wire-format checks (offsets per bm_core network_frames.h) ----

def ipv6_pseudo_checksum(src, dst, payload, next_header):
    # RFC 2460 upper-layer checksum, as bm_lwip's ip6_chksum_pseudo does.
    s = 0
    for b in (src, dst):
        for i in range(0, 16, 2):
            s += (b[i] << 8) | b[i + 1]
    s += len(payload) & 0xFFFFFFFF
    s += next_header
    data = payload if len(payload) % 2 == 0 else payload + b"\x00"
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def parse_frame(frame):
    """Return (kind, detail) -- kind: 'heartbeat', 'bcmp', 'other'."""
    if len(frame) < 54 + 2:
        return "other", "runt (%d B)" % len(frame)
    ethertype = (frame[12] << 8) | frame[13]
    if ethertype != 0x86DD:
        return "other", "ethertype 0x%04x" % ethertype
    if frame[20] != 0xBC:
        return "other", "ipv6 next-header 0x%02x" % frame[20]
    src = frame[22:38]
    dst = frame[38:54]
    plen = (frame[18] << 8) | frame[19]
    bcmp = frame[54:54 + plen]
    btype, bcksum = struct.unpack_from("<HH", bcmp, 0)
    # Checksum verifies over the BCMP payload with the checksum field
    # zeroed, against the IPv6 pseudo-header (bm_lwip.c:118).
    zeroed = bcmp[:2] + b"\x00\x00" + bcmp[4:]
    calc = ipv6_pseudo_checksum(src, dst, zeroed, 0xBC)
    # packet.c stores it little-endian in the struct; calc is network sum.
    csum_ok = bcksum == ((calc >> 8) | ((calc & 0xFF) << 8)) or bcksum == calc
    detail = {"src": src, "dst": dst, "type": btype, "csum_ok": csum_ok}
    if btype == 0x01 and len(bcmp) >= 13 + 12:
        boot_us, lease = struct.unpack_from("<QI", bcmp, 13)
        detail["boot_us"] = boot_us
        detail["lease_s"] = lease
        return "heartbeat", detail
    return "bcmp", detail


# ---- pcap writer (classic format, LINKTYPE_ETHERNET) --------------------

class Pcap:
    def __init__(self, path):
        self.f = open(path, "wb")
        self.f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0,
                                 65535, 1))
        self.n = 0

    def frame(self, data, ts):
        sec = int(ts)
        usec = int((ts - sec) * 1e6)
        self.f.write(struct.pack("<IIII", sec, usec, len(data), len(data)))
        self.f.write(data)
        self.n += 1

    def close(self):
        self.f.close()


# ---- wire endpoint --------------------------------------------------------

class Wire:
    def __init__(self):
        self.ept = None
        self.status = None
        self.frames = []          # (bytes, t_seconds)

    def ns(self, src, name):
        if name == "bm-wire":
            self.ept = openamp.Endpoint("bm-wire", self.rx, dest=src)

    def rx(self, src, data):
        b = bytes(data)
        if len(b) < 4:
            return
        cmd, port, ln = struct.unpack_from("<BBH", b, 0)
        if cmd == WCMD_FRAME_TX and len(b) >= 4 + ln:
            # ticks-based timestamp: MicroPython time.time() has 1 s
            # resolution and a 2000 epoch -- relative times read better
            # in Wireshark anyway.
            self.frames.append((b[4:4 + ln],
                                time.ticks_ms() / 1000.0, port))
        elif cmd == WREP_STATUS:
            self.status = b[4:4 + ln]

    def query(self, timeout_ms=2000):
        self.status = None
        self.ept.send(struct.pack("<BBH", WCMD_QUERY, 0, 0), timeout=1000)
        t0 = time.ticks_ms()
        while self.status is None:
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                raise OSError("no status reply on bm-wire")
            time.sleep_ms(2)
        return struct.unpack("<Q16s16sIIIIIIII", self.status)


def load_remote():
    for p in ELF_PATHS:
        try:
            rp = openamp.RemoteProc(p)
            print("loader : ELF %s" % p)
            return rp
        except OSError:
            pass
    raise OSError("bm_he.elf not found on the board VFS")


def ip6_str(b):
    return ":".join("%x" % ((b[i] << 8) | b[i + 1]) for i in range(0, 16, 2))


def main():
    verdicts = {}
    w = Wire()
    openamp.new_service_callback(w.ns)

    rp = load_remote()
    rp.start()

    t0 = time.ticks_ms()
    while w.ept is None:
        if time.ticks_diff(time.ticks_ms(), t0) > 8000:
            print("bm status page:", bm_page())
            dump_dbg_ring()
            raise OSError("bm-wire never announced (see status page)")
        time.sleep_ms(5)

    # The stack initializes right after the announce; give the ladder a
    # moment, then poll the status until RUNNING (or 5 s).
    t0 = time.ticks_ms()
    while True:
        (node, ll, ucast, stage, err, txf, rxf, oversize, link, heap_free,
         heap_min) = w.query()
        if stage == 7 or err != 0 or \
                time.ticks_diff(time.ticks_ms(), t0) > 5000:
            break
        time.sleep_ms(100)

    # ---- verdict A: BM stack up ----------------------------------------
    ids_ok = node == NODE_ID
    # bm_lwip.c:289-295: ll = fe80::<id>, ucast = fd00::<id>, id in
    # bytes 8-15 big-endian.
    idb = struct.pack(">Q", NODE_ID)
    addr_ok = (ll[0:2] == b"\xfe\x80" and ll[8:16] == idb and
               ucast[0:2] == b"\xfd\x00" and ucast[8:16] == idb)
    verdicts["A"] = stage == 7 and err == 0 and ids_ok and addr_ok \
        and link == 1
    print("A: BM stack on HE  : %s  (stage %s, err 0x%x, link %s)"
          % ("PASS" if verdicts["A"] else "FAIL",
             STAGES.get(stage, stage), err, "up" if link else "DOWN"))
    print("   node id         : 0x%016x %s"
          % (node, "OK" if ids_ok else "MISMATCH"))
    print("   ipv6 ll / ucast : %s / %s  %s"
          % (ip6_str(ll), ip6_str(ucast), "OK" if addr_ok else "MISMATCH"))
    print("   heap free/min   : %d / %d B" % (heap_free, heap_min))

    # ---- verdict B: heartbeats on the wire ------------------------------
    print("capturing %d s of wire traffic..." % CAPTURE_S)
    w.frames = []
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < CAPTURE_S * 1000:
        time.sleep_ms(50)

    pcap = Pcap(PCAP_PATH)
    heartbeats = []
    others = []
    for frame, ts, port in w.frames:
        pcap.frame(frame, ts)
        kind, detail = parse_frame(frame)
        if kind == "heartbeat":
            heartbeats.append(detail)
        else:
            others.append((kind, detail))
    pcap.close()

    hb_ok = len(heartbeats) >= MIN_HEARTBEATS
    csum_ok = all(h["csum_ok"] for h in heartbeats)
    src_ok = all(h["src"][8:16] == idb for h in heartbeats)
    boots = [h["boot_us"] for h in heartbeats]
    mono_ok = all(b1 < b2 for b1, b2 in zip(boots, boots[1:]))
    verdicts["B"] = hb_ok and csum_ok and src_ok and mono_ok
    print("B: BCMP heartbeats : %s  (%d in %d s [>=%d], csum %s, "
          "src-node-id %s, boot-us monotonic %s)"
          % ("PASS" if verdicts["B"] else "FAIL", len(heartbeats),
             CAPTURE_S, MIN_HEARTBEATS,
             "OK" if csum_ok else "BAD", "OK" if src_ok else "BAD",
             "OK" if mono_ok else "BAD"))
    for h in heartbeats:
        print("   hb: boot %.3f s  lease %d s  dst %s"
              % (h["boot_us"] / 1e6, h["lease_s"], ip6_str(h["dst"])))
    if others:
        print("   other wire frames: %d (first: %s %s)"
              % (len(others), others[0][0], others[0][1]))
    print("   pcap: %s (%d frames) -- mpremote cp :%s ."
          % (PCAP_PATH, len(heartbeats) + len(others), PCAP_PATH))

    # ---- wrap up ---------------------------------------------------------
    (node, ll, ucast, stage, err, txf, rxf, oversize, link, heap_free,
     heap_min) = w.query()
    print("final: stage %s err 0x%x tx %d rx %d oversize %d heap %d/%d"
          % (STAGES.get(stage, stage), err, txf, rxf, oversize,
             heap_free, heap_min))
    dump_dbg_ring()
    rp.stop()
    print()
    gate = "PASS" if all(verdicts.values()) else "FAIL"
    print("S10 INTERIM 2a verdict : %s  (A:%s B:%s)"
          % (gate, "PASS" if verdicts["A"] else "FAIL",
             "PASS" if verdicts["B"] else "FAIL"))
    return gate


main()
