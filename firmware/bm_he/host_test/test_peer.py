#!/usr/bin/env python3
# test_peer.py -- CPython tests for s10_peer.py (the 2b python peer's
# frame builders/parsers -- the module the on-board runner imports).
# No hardware, no MicroPython: the module is deliberately pure.
#
# The checksum tests matter most: generation must land byte-exact where
# bm_core's packet.c:456-458 validates. Two independent props are used:
# (1) the ones-complement invariant (pseudo-header sum over a frame WITH
# its checksum in place folds to 0xFFFF), (2) survival of the l2/packet.c
# ingress-nibble round trip (l2.c:39 sets src[2] high nibble on RX,
# packet.c:452-454 reads + clears it before checksumming).

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import s10_peer as peer

HE_NODE = 0x424D4845AE30BEEF

checks = fails = 0


def check(cond, what):
    global checks, fails
    checks += 1
    if not cond:
        fails += 1
        print("FAIL: %s" % what)


def ones_complement_total(frame):
    """Pseudo-header sum over the BCMP payload WITH its checksum bytes in
    place. For a correctly checksummed packet this folds to 0xFFFF."""
    src = frame[22:38]
    dst = frame[38:54]
    plen = (frame[18] << 8) | frame[19]
    payload = frame[54:54 + plen]
    s = 0
    for b in (src, dst):
        for i in range(0, 16, 2):
            s += (b[i] << 8) | b[i + 1]
    s += plen + 0xBC
    data = payload if len(payload) % 2 == 0 else payload + b"\x00"
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return s


def rx_ingress_round_trip(frame, port=1):
    """What the HE side does to an injected frame before checksumming:
    l2 ORs the ingress nibble into src byte 2 (l2.c:39, offset 24 in the
    frame), packet.c reads it back and clears it (packet.c:452-454)."""
    f = bytearray(frame)
    f[24] |= port << 4          # l2 RX mutation
    ingress = (f[24] >> 4) & 0xF
    f[24] &= 0x0F               # packet.c clear_ingress_port
    return bytes(f), ingress


# ---- heartbeat: build -> parse round trip --------------------------------

hb = peer.build_heartbeat(1_000_000, lease_s=10)
d = peer.parse(hb)
check(d["kind"] == "heartbeat", "hb kind: %s" % d)
check(d["src_node"] == peer.PEER_NODE_ID, "hb src node")
check(d["boot_us"] == 1_000_000 and d["lease_s"] == 10, "hb fields")
check(d["csum_ok"], "hb checksum self-consistent")
check(ones_complement_total(hb) == 0xFFFF, "hb ones-complement invariant")

# frame skeleton facts
check(hb[0:6] == b"\x33\x33\x00\x00\x00\x01", "multicast MAC for ff02::1")
check(hb[6:12] == b"\x00\x00\xae\x30\xd0\x0d", "src MAC = 00:00 + low4(id)")
check(hb[12:14] == b"\x86\xdd" and hb[20] == 0xBC, "ethertype/next-header")
check(hb[22:24] == b"\xfe\x80" and hb[24] == 0x01,
      "src = fe80::, egress nibble 0x01 in byte 2")
check(hb[30:38] == struct.pack(">Q", peer.PEER_NODE_ID),
      "node id in src addr bytes 8-15")
check(hb[38:54] == peer.MULTICAST_LL, "dst = ff02::1")

# survives the RX-side ingress-nibble round trip byte-exact
mutated, ingress = rx_ingress_round_trip(hb)
check(ingress == 1, "ingress port recovered")
check(mutated == hb, "checksum bytes unchanged by ingress round trip")
check(peer.parse(mutated)["csum_ok"], "checksum valid post round trip")

# ---- echo request / reply -------------------------------------------------

req = peer.build_echo_request(HE_NODE, 0xD00D, 7, b"payload!")
d = peer.parse(req)
check(d["kind"] == "echo_request", "echo req kind")
check(d["node_id"] == HE_NODE, "echo req target")
check(d["id"] == 0xD00D and d["seq"] == 7, "echo req id/seq")
check(d["payload"] == b"payload!" and d["payload_len_ok"], "echo req payload")
check(d["csum_ok"], "echo req checksum")
check(ones_complement_total(req) == 0xFFFF, "echo req invariant")

rep = peer.build_echo_reply(0xBEEF, 3, b"pong", hdr_seq=42)
d = peer.parse(rep)
check(d["kind"] == "echo_reply", "echo reply kind")
check(d["node_id"] == peer.PEER_NODE_ID, "echo reply responder id")
check(d["id"] == 0xBEEF and d["seq"] == 3, "echo reply id/seq")
check(d["payload"] == b"pong", "echo reply payload")
check(d["hdr_seq"] == 42, "echo reply mirrors header seq")
check(d["csum_ok"], "echo reply checksum")

# odd-length payload exercises the checksum padding path
odd = peer.build_echo_request(HE_NODE, 1, 1, b"odd")
check(peer.parse(odd)["csum_ok"], "odd-length payload checksum")
check(ones_complement_total(odd) == 0xFFFF, "odd-length invariant")

# ---- neighbor table request / reply --------------------------------------

nreq = peer.build_neighbor_request(HE_NODE)
d = peer.parse(nreq)
check(d["kind"] == "bcmp" and d["type"] == peer.T_NEIGHBOR_TABLE_REQUEST,
      "neighbor request type")
check(d["csum_ok"], "neighbor request checksum")

# Hand-build the reply the HE would send (messages.h: BcmpNeighborTableReply
# u64 node + u8 port_len + u16 neighbor_len, BcmpPortInfo 2 B each,
# BcmpNeighborInfo 10 B each) and parse it.
body = (struct.pack("<QBH", HE_NODE, 1, 2) +
        bytes([1, 0]) +                                   # port 1 up
        struct.pack("<QBB", peer.PEER_NODE_ID, 1, 1) +    # us, online
        struct.pack("<QBB", 0x1122334455667788, 2, 0))    # someone else
nrep = peer.build_frame(peer.T_NEIGHBOR_TABLE_REPLY, body, HE_NODE)
d = peer.parse(nrep)
check(d["kind"] == "neighbor_reply", "neighbor reply kind")
check(d["node_id"] == HE_NODE, "neighbor reply node id")
check(d["port_len"] == 1 and d["ports"][0]["state"] == 1, "port list")
check(d["neighbor_len"] == 2 and len(d["neighbors"]) == 2, "neighbor count")
check(d["neighbors"][0] == {"node_id": peer.PEER_NODE_ID, "port": 1,
                            "online": 1}, "neighbor entry 0")
check(d["neighbors"][1]["online"] == 0, "neighbor entry 1 offline")

# ---- classification edges -------------------------------------------------

check(peer.parse(b"\x00" * 20)["kind"] == "other", "runt is other")
not_v6 = bytearray(hb)
not_v6[12:14] = b"\x08\x00"
check(peer.parse(bytes(not_v6))["kind"] == "other", "non-IPv6 is other")
bad_csum = bytearray(hb)
bad_csum[56] ^= 0xFF          # flip a checksum byte
check(not peer.parse(bytes(bad_csum))["csum_ok"], "corrupt csum detected")

# WCMD_PING payload layout the firmware unpacks (wire_ping_t: u64 + echo)
ping_body = struct.pack("<Q", peer.PEER_NODE_ID) + b"echo"
check(len(ping_body) == 12 and ping_body[8:] == b"echo",
      "wire_ping_t layout: u64 target + echo bytes")

print("s10_peer host tests: %d checks, %d failures" % (checks, fails))
sys.exit(1 if fails else 0)
