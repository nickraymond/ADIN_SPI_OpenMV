// lwipopts.h -- bm_he (S10 INTERIM 2). lwIP 2.2.1 (openmv tree,
// lib/micropython/lib/lwip) in OS mode under FreeRTOS via the vendored
// contrib sys_arch. Sized for one 10 Mbps point-to-point IPv6 link
// (Bristlemouth): no IPv4, no TCP, no sockets -- BCMP rides raw IPv6
// (next-header 0xBC) and middleware later rides UDP.
#ifndef LWIPOPTS_H
#define LWIPOPTS_H

// --- system -------------------------------------------------------------
#define NO_SYS                          0
#define SYS_LIGHTWEIGHT_PROT            1
#define LWIP_TCPIP_CORE_LOCKING         1
#define LWIP_NETCONN                    0
#define LWIP_SOCKET                     0
#define LWIP_NO_UNISTD_H                1

#define TCPIP_THREAD_NAME               "tcpip"
#define TCPIP_THREAD_STACKSIZE          4096   // bytes (contrib sys_arch
                                               // STACKSIZE_IS_STACKWORDS=0)
#define TCPIP_THREAD_PRIO               8      // above L2 (7), below DFU (11)
#define TCPIP_MBOX_SIZE                 16
#define DEFAULT_RAW_RECVMBOX_SIZE       8
#define DEFAULT_UDP_RECVMBOX_SIZE       8
#define DEFAULT_ACCEPTMBOX_SIZE         4

// --- protocols ----------------------------------------------------------
#define LWIP_ARP                        0
#define LWIP_ETHERNET                   1      // eth framing without ARP
                                               // (defaults to LWIP_ARP=0)
#define LWIP_IPV4                       0
#define LWIP_IPV6                       1
#define LWIP_ICMP6                      1
#define LWIP_IPV6_MLD                   1      // bm_lwip joins ff03::1
#define LWIP_ND6_QUEUEING               0
#define LWIP_IPV6_AUTOCONFIG            0      // bm sets addrs statically
#define LWIP_IPV6_DUP_DETECT_ATTEMPTS   0      // point-to-point, ids unique
#define LWIP_IPV6_SEND_ROUTER_SOLICIT   0
#define LWIP_IPV6_FRAG                  1      // BCMP "FIXME split payloads"
#define LWIP_IPV6_REASS                 1      //   relies on IP-level frag
#define LWIP_RAW                        1      // BCMP pcb (proto 0xBC)
#define LWIP_UDP                        1      // bm middleware (S12)
#define LWIP_TCP                        0
#define LWIP_DNS                        0
#define LWIP_IGMP                       0
#define LWIP_NETIF_LOOPBACK             0
#define LWIP_STATS                      0
#define LWIP_CHECKSUM_CTRL_PER_NETIF    0

// --- memory (RAM is the scarce resource: 256 KB total for everything) ---
#define MEM_LIBC_MALLOC                 0
#define MEMP_MEM_MALLOC                 0
#define MEM_ALIGNMENT                   4
#define MEM_SIZE                        (16 * 1024)
#define PBUF_POOL_SIZE                  12
#define PBUF_POOL_BUFSIZE               1536   // MTU 1500 + eth hdr, aligned
#define MEMP_NUM_PBUF                   16
#define MEMP_NUM_RAW_PCB                4
#define MEMP_NUM_UDP_PCB                6
#define MEMP_NUM_SYS_TIMEOUT            (LWIP_NUM_SYS_TIMEOUT_INTERNAL + 4)
#define MEMP_NUM_TCPIP_MSG_INPKT        16
#define MEMP_NUM_TCPIP_MSG_API          8
#define MEMP_NUM_ND6_QUEUE              4
#define LWIP_ND6_NUM_NEIGHBORS          8
#define LWIP_ND6_NUM_DESTINATIONS       8
#define MEMP_NUM_MLD6_GROUP             6

// --- diagnostics --------------------------------------------------------
// LWIP_PLATFORM_DIAG/ASSERT route to the debug ring in arch/cc.h.
#define LWIP_DEBUG                      0

#endif // LWIPOPTS_H
