// mhu.c -- HP<->HE doorbell, HE side. The rpmsg notify path on this board
// is a single 32-bit MHU word each way (the value is ignored by both
// receivers):
//   HP -> HE: se_services_notify() -> SERVICES_send_msg -> MHU word on
//             MHU_M55HP_M55HE_0 channel 0 (micropython ports/alif/
//             se_services.c:217; rx side dispatches metal_rproc_notified
//             on ANY word, se_services.c:114).
//   HE -> HP: same thing in reverse; the HP rx handler ignores the data.
//
// Register layout: ARM MHUv2 frames as laid out in Alif's own driver
// (lib/alif/drivers/include/mhu_driver.h): per-channel 0x20-byte windows,
// frame-common registers after CHANNEL[124]. Protocol from
// drivers/source/mhu_sender.c MHU_send_message (access-request ->
// access-ready -> CH_SET) and mhu_receiver.c MHU_receive_message_irq_handler
// (CH_ST -> callback -> CH_CLR).
//
// Base addresses + IRQ, HE core view (lib/alif/Device/core/M55_HE/include/
// M55_HE_map.h:38-39, M55_HE.h:216):
//   RX (HP->HE): 0x40080000, IRQ 41    TX (HE->HP): 0x40090000
// We poll the TX ack (CH_ST clears when HP's receiver clears it) instead
// of taking the TX IRQ -- one less moving part.

#include "mhu.h"
#include "he_spike.h"

#define MHU_RX_BASE 0x40080000u
#define MHU_TX_BASE 0x40090000u
#define MHU_RX_IRQN 41

typedef struct {                    // sender channel window (0x20 B)
    volatile const uint32_t CH_ST;
    uint32_t r0, r1;
    volatile uint32_t CH_SET;
    volatile const uint32_t CH_INT_ST;
    volatile uint32_t CH_INT_CLR;
    volatile uint32_t CH_INT_EN;
    uint32_t r2;
} mhu_snd_ch_t;

typedef struct {                    // receiver channel window (0x20 B)
    volatile const uint32_t CH_ST;
    volatile const uint32_t CH_ST_MASKED;
    volatile uint32_t CH_CLR;
    uint32_t r0;
    volatile const uint32_t CH_MASK_ST;
    volatile uint32_t CH_MSK_SET;
    volatile uint32_t CH_MSK_CLR;
    uint32_t r1;
} mhu_rcv_ch_t;

// Frame-common registers live after 124 channel windows (mhu_driver.h:80).
#define SND_FRAME_OFF (124u * 0x20u)
typedef struct {
    volatile const uint32_t MHU_CFG;
    volatile uint32_t RESP_CFG;
    volatile uint32_t ACCESS_REQUEST;
    volatile const uint32_t ACCESS_READY;
    volatile const uint32_t INT_ST;
    volatile uint32_t INT_CLR;
    volatile uint32_t INT_EN;
} mhu_snd_frame_t;

typedef struct {
    volatile const uint32_t MHU_CFG;
    uint32_t r0[3];
    volatile const uint32_t INT_ST;
    volatile uint32_t INT_CLR;
    volatile uint32_t INT_EN;
} mhu_rcv_frame_t;

#define SND_CH(n)  ((mhu_snd_ch_t *)(MHU_TX_BASE + (n) * 0x20u))
#define SND_FRAME  ((mhu_snd_frame_t *)(MHU_TX_BASE + SND_FRAME_OFF))
#define RCV_CH(n)  ((mhu_rcv_ch_t *)(MHU_RX_BASE + (n) * 0x20u))
#define RCV_FRAME  ((mhu_rcv_frame_t *)(MHU_RX_BASE + SND_FRAME_OFF))

#define MHU_ACC_REQ 0x1u
#define MHU_ACC_RDY 0x1u
#define MHU_CHCOMB  0x4u

#define NVIC_ISER(n)  (*(volatile uint32_t *)(0xE000E100u + 4u * (n)))
#define NVIC_IPR(irq) (*(volatile uint8_t *)(0xE000E400u + (irq)))

static void (*s_doorbell)(void);
#define KICK_SPINS 100000u          // ~ms scale; a stuck ACCESS_READY means
                                    // the HP side is gone, fail loudly

void he_mhu_init(void (*rx_doorbell)(void)) {
    s_doorbell = rx_doorbell;
    RCV_CH(0)->CH_MSK_CLR = 0xFFFFFFFFu;   // unmask channel 0 bits
    RCV_FRAME->INT_EN |= MHU_CHCOMB;       // combined channel IRQ
    // Priority: numerically ABOVE (i.e. lower urgency than)
    // configMAX_SYSCALL_INTERRUPT_PRIORITY so FromISR calls are legal.
    NVIC_IPR(MHU_RX_IRQN) = 0xC0u;
    NVIC_ISER(MHU_RX_IRQN / 32u) = 1u << (MHU_RX_IRQN % 32u);
}

bool he_mhu_kick(void) {
    mhu_snd_frame_t *f = SND_FRAME;
    mhu_snd_ch_t *ch = SND_CH(0);

    f->ACCESS_REQUEST = MHU_ACC_REQ;
    for (uint32_t i = 0; i < KICK_SPINS; i++) {
        if (f->ACCESS_READY & MHU_ACC_RDY) {
            ch->CH_SET = 1u;               // any word; receivers ignore it
            // Delivered when the HP receiver clears the channel.
            for (uint32_t j = 0; j < KICK_SPINS; j++) {
                if (ch->CH_ST == 0u) {
                    f->ACCESS_REQUEST = 0u;
                    return true;
                }
            }
            break;
        }
    }
    f->ACCESS_REQUEST = 0u;
    return false;
}

void he_mhu_rx_irq(void) {
    he_status_page_t *sp = (he_status_page_t *)STATUS_PAGE_ADDR;
    sp->irq_count++;
    if (RCV_CH(0)->CH_ST) {
        RCV_CH(0)->CH_CLR = 0xFFFFFFFFu;
    }
    if (s_doorbell) {
        s_doorbell();
    }
}
