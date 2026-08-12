# s10_bcmp_bench.py -- S10 INTERIM 2b runner. Runs ON the AE3's HP core
# (stock/fixture firmware, no flash) via mpremote from nereus000:
#
#   mpremote connect <by-id> cp bm_he.elf :/flash/bm_he.elf     # once/build
#   mpremote connect <by-id> cp s10_peer.py :/flash/s10_peer.py # once/build
#   mpremote connect <by-id> run s10_bcmp_bench.py
#
# Loads the bm_core stack app onto the HE core at runtime (remoteproc ELF
# load into SRAM9_B), then plays a full python PEER NODE (s10_peer.py) on
# the far end of the mock NetworkDevice's fake wire. Verdicts:
#   A -- BM stack up on HE (init ladder RUNNING, node id + both IPv6
#        addresses per bm_lwip.c:289-295)                       [2a]
#   B -- HE's BCMP heartbeats emitted on schedule + wire-correct [2a]
#   C -- neighbor table forms: peer heartbeats -> HE lists the peer as an
#        online neighbor in a BcmpNeighborTableReply             [2b]
#   D -- BCMP ping peer->HE answered (echo reply: id/seq/payload echoed)
#   E -- BCMP ping HE->peer answered AND accepted (WCMD_PING ->
#        HE's echo request on the wire -> peer replies -> ping.c's
#        acceptance line lands on the HE debug ring)
#
# Both directions of the conversation land in /flash/bm_he_hb.pcap
# (Wireshark: Sofar's proto_bcmp.lua dissector decodes BCMP). Copy off:
#   mpremote connect <by-id> cp :/flash/bm_he_hb.pcap .

import openamp
import struct
import time
import machine

try:
    import s10_peer as peer
except ImportError:
    raise OSError("s10_peer.py not on the board VFS -- "
                  "mpremote cp s10_peer.py :/flash/s10_peer.py")

ELF_PATHS = ("/flash/bm_he.elf", "bm_he.elf")
BM_STATUS_PAGE = 0x600BFE00
PCAP_PATH = "/flash/bm_he_hb.pcap"

NODE_ID = 0x424D4845AE30BEEF  # bm_stubs.c BM_HE_NODE_ID
HB_PERIOD_S = 10              # bcmp.c bcmp_heartbeat_s
CAPTURE_S = 25                # >= 2 HE timer heartbeats
MIN_HEARTBEATS = 2
PEER_HB_EVERY_S = 5           # keep the peer's lease (10 s) fresh
REPLY_TIMEOUT_S = 3

PING_D_PAYLOAD = b"S10-2b peer->HE"
PING_E_PAYLOAD = b"S10-2b HE->peer"

# wire protocol (bm_he.h)
WCMD_FRAME_TX = 0x11
WCMD_FRAME_RX = 0x12
WCMD_LINK = 0x13
WCMD_QUERY = 0x14
WCMD_PING = 0x15
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


def read_dbg_ring():
    p = bm_page()
    if not p or not p["ring"][0]:
        return ""
    addr, size, widx = p["ring"]
    import uctypes
    ring = bytes(uctypes.bytearray_at(addr, size))
    n = min(widx, size)
    start = widx % size if widx > size else 0
    text = (ring[start:n] + ring[:start]) if widx > size else ring[:n]
    return text.decode()


def dump_dbg_ring():
    text = read_dbg_ring()
    if not text:
        return
    print("-- HE debug ring " + "-" * 44)
    for line in text.split("\n"):
        if line:
            print("   | " + line)
    print("-" * 61)


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

def now_s():
    # ticks-based: MicroPython time.time() is 1 s resolution / 2000 epoch;
    # relative times read better in Wireshark anyway.
    return time.ticks_ms() / 1000.0


class Wire:
    def __init__(self):
        self.ept = None
        self.status = None
        self.frames = []          # HE -> wire: (bytes, t_seconds, port)
        self.injected = []        # wire -> HE: (bytes, t_seconds), for pcap

    def ns(self, src, name):
        if name == "bm-wire":
            self.ept = openamp.Endpoint("bm-wire", self.rx, dest=src)

    def rx(self, src, data):
        b = bytes(data)
        if len(b) < 4:
            return
        cmd, port, ln = struct.unpack_from("<BBH", b, 0)
        if cmd == WCMD_FRAME_TX and len(b) >= 4 + ln:
            self.frames.append((b[4:4 + ln], now_s(), port))
        elif cmd == WREP_STATUS:
            self.status = b[4:4 + ln]

    def inject(self, frame):
        self.injected.append((frame, now_s()))
        self.ept.send(struct.pack("<BBH", WCMD_FRAME_RX, 1, len(frame)) +
                      frame, timeout=1000)

    def ping_cmd(self, target_node, payload):
        body = struct.pack("<Q", target_node) + payload
        self.ept.send(struct.pack("<BBH", WCMD_PING, 0, len(body)) + body,
                      timeout=1000)

    def query(self, timeout_ms=2000):
        self.status = None
        self.ept.send(struct.pack("<BBH", WCMD_QUERY, 0, 0), timeout=1000)
        t0 = time.ticks_ms()
        while self.status is None:
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                raise OSError("no status reply on bm-wire")
            time.sleep_ms(2)
        return struct.unpack("<Q16s16sIIIIIIII", self.status)

    def wait_for(self, cursor, pred, timeout_s=REPLY_TIMEOUT_S):
        """Scan self.frames from index `cursor` for a parsed frame matching
        pred(d); returns (d, new_cursor) or (None, new_cursor)."""
        t0 = time.ticks_ms()
        while True:
            while cursor < len(self.frames):
                d = peer.parse(self.frames[cursor][0])
                cursor += 1
                if pred(d):
                    return d, cursor
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_s * 1000:
                return None, cursor
            time.sleep_ms(20)


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

    # ---- capture window: HE heartbeats out, peer heartbeats in ----------
    print("capturing %d s of wire traffic (peer node 0x%016x heartbeating "
          "every %d s)..." % (CAPTURE_S, peer.PEER_NODE_ID, PEER_HB_EVERY_S))
    w.frames = []
    hb_boot_us = 1000 * 1000 * 1000   # peer's fake boot clock, monotonic
    next_hb_ms = 0
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < CAPTURE_S * 1000:
        if time.ticks_diff(time.ticks_ms(), t0) >= next_hb_ms:
            w.inject(peer.build_heartbeat(hb_boot_us))
            hb_boot_us += PEER_HB_EVERY_S * 1000 * 1000
            next_hb_ms += PEER_HB_EVERY_S * 1000
        time.sleep_ms(50)

    # ---- verdict B: HE heartbeats on the wire ---------------------------
    heartbeats = []
    others = []
    for frame, ts, port in w.frames:
        d = peer.parse(frame)
        if d["kind"] == "heartbeat" and d["src_node"] == NODE_ID:
            heartbeats.append(d)
        else:
            others.append(d)
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

    cursor = len(w.frames)   # phases below only look at newer frames

    # ---- verdict C: neighbor table lists the peer -----------------------
    w.inject(peer.build_neighbor_request(NODE_ID))
    reply, cursor = w.wait_for(cursor,
                               lambda d: d["kind"] == "neighbor_reply")
    entry = None
    if reply:
        for n in reply["neighbors"]:
            if n["node_id"] == peer.PEER_NODE_ID:
                entry = n
                break
    verdicts["C"] = (reply is not None and reply["csum_ok"] and
                     reply["node_id"] == NODE_ID and entry is not None and
                     entry["port"] == 1 and entry["online"] == 1)
    if reply:
        print("C: neighbor table  : %s  (from 0x%016x, %d port(s), %d "
              "neighbor(s), peer %s)"
              % ("PASS" if verdicts["C"] else "FAIL", reply["node_id"],
                 reply["port_len"], reply["neighbor_len"],
                 "online, port %d" % entry["port"] if entry else "MISSING"))
    else:
        print("C: neighbor table  : FAIL  (no BcmpNeighborTableReply in "
              "%d s)" % REPLY_TIMEOUT_S)

    # ---- verdict D: ping peer -> HE -------------------------------------
    w.inject(peer.build_echo_request(NODE_ID, 0xD00D, 1, PING_D_PAYLOAD))
    reply, cursor = w.wait_for(cursor, lambda d: d["kind"] == "echo_reply")
    verdicts["D"] = (reply is not None and reply["csum_ok"] and
                     reply["node_id"] == NODE_ID and
                     reply["id"] == 0xD00D and reply["seq"] == 1 and
                     reply["payload"] == PING_D_PAYLOAD)
    if reply:
        print("D: ping peer->HE   : %s  (reply from 0x%016x, id 0x%04x, "
              "seq %d, payload %s, csum %s)"
              % ("PASS" if verdicts["D"] else "FAIL", reply["node_id"],
                 reply["id"], reply["seq"],
                 "echoed" if reply["payload"] == PING_D_PAYLOAD else "BAD",
                 "OK" if reply["csum_ok"] else "BAD"))
    else:
        print("D: ping peer->HE   : FAIL  (no echo reply in %d s)"
              % REPLY_TIMEOUT_S)

    # ---- verdict E: ping HE -> peer -------------------------------------
    # ping.c's acceptance narrative on the debug ring is the "reply
    # accepted" proof; count its marker before and after. (The line's
    # node id prints garbage under newlib-nano %llx -- 2a fact -- so
    # match the stable text, not the id.)
    ring_marker = "bytes from"
    marks_before = read_dbg_ring().count(ring_marker)
    w.ping_cmd(peer.PEER_NODE_ID, PING_E_PAYLOAD)
    req, cursor = w.wait_for(cursor, lambda d: d["kind"] == "echo_request")
    req_ok = (req is not None and req["csum_ok"] and
              req["node_id"] == peer.PEER_NODE_ID and
              req["id"] == (NODE_ID & 0xFFFF) and
              req["payload"] == PING_E_PAYLOAD)
    accepted = False
    if req_ok:
        w.inject(peer.build_echo_reply(req["id"], req["seq"],
                                       req["payload"],
                                       hdr_seq=req["hdr_seq"]))
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < REPLY_TIMEOUT_S * 1000:
            if read_dbg_ring().count(ring_marker) > marks_before:
                accepted = True
                break
            time.sleep_ms(100)
    verdicts["E"] = req_ok and accepted
    if req is None:
        print("E: ping HE->peer   : FAIL  (no echo request on the wire in "
              "%d s)" % REPLY_TIMEOUT_S)
    else:
        print("E: ping HE->peer   : %s  (request to 0x%016x id 0x%04x "
              "seq %d payload %s csum %s; reply %s by ping.c)"
              % ("PASS" if verdicts["E"] else "FAIL", req["node_id"],
                 req["id"], req["seq"],
                 "ok" if req["payload"] == PING_E_PAYLOAD else "BAD",
                 "OK" if req["csum_ok"] else "BAD",
                 "ACCEPTED" if accepted else "NOT ACCEPTED"))

    # ---- pcap: both directions, chronological ---------------------------
    pcap = Pcap(PCAP_PATH)
    everything = [(f, ts) for f, ts, _ in w.frames] + w.injected
    everything.sort(key=lambda e: e[1])
    for frame, ts in everything:
        pcap.frame(frame, ts)
    pcap.close()
    print("   pcap: %s (%d frames, both directions) -- mpremote cp :%s ."
          % (PCAP_PATH, pcap.n, PCAP_PATH))
    if others:
        print("   non-heartbeat HE frames in capture window: %d (first: %s)"
              % (len(others), others[0].get("why", others[0]["kind"])))

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
    print("S10 INTERIM 2b verdict : %s  (%s)"
          % (gate, " ".join("%s:%s" % (k, "PASS" if verdicts[k] else "FAIL")
                            for k in sorted(verdicts))))
    return gate


main()
