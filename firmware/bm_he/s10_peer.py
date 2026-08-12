# s10_peer.py -- S10 INTERIM 2b: a python Bristlemouth peer node.
#
# Pure frame builders/parsers -- no machine/openamp imports -- so the same
# file runs on the AE3's HP core (imported by s10_bcmp_bench.py from
# /flash) AND under CPython (host_test/test_peer.py). The peer speaks
# byte-exact BCMP: Ethernet II 0x86DD / IPv6 next-header 0xBC / 13-byte
# BcmpHeader (messages.h) / message body, checksummed like bm_core does.
#
# Wire-format sources (all vendored in this repo, rev d4ecc38):
#   header layout        vendor/bm_core/bcmp/messages.h:10-19 (packed LE)
#   message bodies       messages.h (BcmpHeartbeat/EchoRequest/EchoReply/
#                        NeighborTableRequest/Reply, PortInfo/NeighborInfo)
#   checksum             RFC 2460 pseudo-header sum over the BCMP payload
#                        with the csum field zeroed (bm_lwip.c:114,
#                        packet.c:456-458). lwIP's ip6_chksum_pseudo
#                        returns a value whose NATIVE (LE) store yields
#                        network-order bytes (inet_chksum.c algorithm
#                        notes), so on the wire the field is simply the
#                        big-endian sum -- confirmed live in 2a (the
#                        "swapped" compare branch is the one that matched).
#   egress nibble        senders put the egress port in src-addr byte 2's
#                        LOW nibble and checksum over it (l2.c:37,270);
#                        RX sets the HIGH (ingress) nibble and clears it
#                        again before validating (packet.c:452-454), so
#                        byte 2 = 0x0<egress> at both checksum sites.
#   MAC derivation       00:00 + low 4 bytes of node id (device.c:20-36);
#                        IPv6-multicast MAC = 33:33 + dst addr last 4.

import struct

# The peer's identity (as synthetic as the HE's BM_HE_NODE_ID -- real
# derivation is a hardware-day question).
PEER_NODE_ID = 0x50454552AE30D00D  # "PEER" + ae30d00d

MULTICAST_LL = b"\xff\x02" + b"\x00" * 13 + b"\x01"  # ff02::1

ETH_IPV6 = 0x86DD
IP_PROTO_BCMP = 0xBC
BCMP_HDR_LEN = 13
BCMP_OFFSET = 54          # 14 eth + 40 IPv6
HB_LEASE_S = 10           # match bcmp.c's bcmp_heartbeat_s

# BcmpMessageType (messages.h:471+)
T_HEARTBEAT = 0x01
T_ECHO_REQUEST = 0x02
T_ECHO_REPLY = 0x03
T_DEVICE_INFO_REQUEST = 0x04
T_NEIGHBOR_TABLE_REQUEST = 0x08
T_NEIGHBOR_TABLE_REPLY = 0x09


def node_mac(node_id):
    return b"\x00\x00" + struct.pack(">I", node_id & 0xFFFFFFFF)


def node_ll_ip(node_id, port_nibble=0):
    # fe80::<id>, id in bytes 8-15 big-endian (bm_lwip.c:289); byte 2
    # carries the ingress/egress nibbles on the wire (l2.c:37-40).
    return (b"\xfe\x80" + bytes([port_nibble]) + b"\x00" * 5 +
            struct.pack(">Q", node_id))


def pseudo_csum(src, dst, payload, next_header=IP_PROTO_BCMP):
    # RFC 2460 upper-layer checksum, big-endian ("true network") value.
    s = 0
    for b in (src, dst):
        for i in range(0, 16, 2):
            s += (b[i] << 8) | b[i + 1]
    s += len(payload)
    s += next_header
    data = payload if len(payload) % 2 == 0 else payload + b"\x00"
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def build_frame(bcmp_type, body, src_node=PEER_NODE_ID, dst_ip=MULTICAST_LL,
                hdr_seq=0, egress_port=1):
    """Full Ethernet frame carrying one BCMP message."""
    src_ip = node_ll_ip(src_node, egress_port)  # csum covers the egress
    bcmp = struct.pack("<HHBBIBBB", bcmp_type, 0, 0, 0, hdr_seq, 0, 0,
                       0) + body
    csum = pseudo_csum(src_ip, dst_ip, bcmp)
    bcmp = bcmp[:2] + struct.pack(">H", csum) + bcmp[4:]
    if dst_ip[0] == 0xFF:  # IPv6 multicast MAC (RFC 2464 §7)
        dst_mac = b"\x33\x33" + dst_ip[12:16]
    else:
        dst_mac = node_mac(struct.unpack(">Q", dst_ip[8:16])[0])
    eth = dst_mac + node_mac(src_node) + struct.pack(">H", ETH_IPV6)
    ipv6 = (b"\x60\x00\x00\x00" + struct.pack(">H", len(bcmp)) +
            bytes([IP_PROTO_BCMP, 255]) + src_ip + dst_ip)
    return eth + ipv6 + bcmp


def build_heartbeat(boot_us, lease_s=HB_LEASE_S, src_node=PEER_NODE_ID):
    return build_frame(T_HEARTBEAT, struct.pack("<QI", boot_us, lease_s),
                       src_node)


def build_neighbor_request(target_node, src_node=PEER_NODE_ID):
    return build_frame(T_NEIGHBOR_TABLE_REQUEST,
                       struct.pack("<Q", target_node), src_node)


def build_echo_request(target_node, id16, seq16, payload,
                       src_node=PEER_NODE_ID):
    return build_frame(
        T_ECHO_REQUEST,
        struct.pack("<QHHH", target_node, id16, seq16, len(payload)) +
        payload, src_node)


def build_echo_reply(id16, seq16, payload, hdr_seq=0, src_node=PEER_NODE_ID):
    # node_id = the RESPONDING node (messages.h:47); id/seq/payload echo
    # the request's (ping.c:127-140 validates id + payload).
    return build_frame(
        T_ECHO_REPLY,
        struct.pack("<QHHH", src_node, id16, seq16, len(payload)) + payload,
        src_node, hdr_seq=hdr_seq)


def parse(frame):
    """Parse one wire frame -> dict with at least {'kind': ...}.

    kinds: 'heartbeat', 'echo_request', 'echo_reply', 'neighbor_reply',
    'bcmp' (other BCMP types), 'other' (non-BCMP). BCMP kinds carry
    src/dst/type/hdr_seq/csum_ok + per-kind fields.
    """
    if len(frame) < BCMP_OFFSET + BCMP_HDR_LEN:
        return {"kind": "other", "why": "runt (%d B)" % len(frame)}
    ethertype = (frame[12] << 8) | frame[13]
    if ethertype != ETH_IPV6:
        return {"kind": "other", "why": "ethertype 0x%04x" % ethertype}
    if frame[20] != IP_PROTO_BCMP:
        return {"kind": "other", "why": "next-header 0x%02x" % frame[20]}
    src = frame[22:38]
    dst = frame[38:54]
    plen = (frame[18] << 8) | frame[19]
    bcmp = frame[BCMP_OFFSET:BCMP_OFFSET + plen]
    if len(bcmp) < BCMP_HDR_LEN:
        return {"kind": "other", "why": "short bcmp (%d B)" % len(bcmp)}
    btype, _, _, _, hdr_seq, _, _, _ = struct.unpack_from("<HHBBIBBB",
                                                          bcmp, 0)
    zeroed = bcmp[:2] + b"\x00\x00" + bcmp[4:]
    csum_ok = bcmp[2:4] == struct.pack(">H", pseudo_csum(src, dst, zeroed))
    d = {"kind": "bcmp", "type": btype, "src": src, "dst": dst,
         "hdr_seq": hdr_seq, "csum_ok": csum_ok,
         "src_node": struct.unpack(">Q", src[8:16])[0]}
    body = bcmp[BCMP_HDR_LEN:]
    if btype == T_HEARTBEAT and len(body) >= 12:
        d["kind"] = "heartbeat"
        d["boot_us"], d["lease_s"] = struct.unpack_from("<QI", body, 0)
    elif btype in (T_ECHO_REQUEST, T_ECHO_REPLY) and len(body) >= 14:
        d["kind"] = ("echo_request" if btype == T_ECHO_REQUEST
                     else "echo_reply")
        node, d["id"], d["seq"], pl = struct.unpack_from("<QHHH", body, 0)
        # echo_request: node = target; echo_reply: node = responder
        d["node_id"] = node
        d["payload"] = bytes(body[14:14 + pl])
        d["payload_len_ok"] = len(body) >= 14 + pl
    elif btype == T_NEIGHBOR_TABLE_REPLY and len(body) >= 11:
        d["kind"] = "neighbor_reply"
        d["node_id"], d["port_len"], d["neighbor_len"] = \
            struct.unpack_from("<QBH", body, 0)
        off = 11 + 2 * d["port_len"]          # BcmpPortInfo = 2 B
        d["ports"] = [{"state": body[11 + 2 * i], "type": body[12 + 2 * i]}
                      for i in range(d["port_len"])]
        d["neighbors"] = []
        for _ in range(d["neighbor_len"]):    # BcmpNeighborInfo = 10 B
            if off + 10 > len(body):
                break
            nid, port, online = struct.unpack_from("<QBB", body, off)
            d["neighbors"].append({"node_id": nid, "port": port,
                                   "online": online})
            off += 10
    return d
