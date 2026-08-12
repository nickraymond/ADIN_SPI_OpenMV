// startup.c -- vectors + reset path for bm_he. Derived from the bite-1
// spike's startup.c (same core facts: M55_HE external IRQs to >= 137,
// M55_HE.h; VTOR at image base) with three deltas: the bm status page,
// no SPI vector (no SPI in this app), and .noinit preserved across
// resets (bcmp dfu_core keeps its reboot-info struct there).

#include <stdint.h>
#include <string.h>
#include "he_spike.h"   // he_status_page_t -- still stamped; mhu.c/
                        // rpmsg_remote.c (reused as-is) write into it
#include "bm_he.h"

#define NUM_IRQ 192

extern uint32_t _estack, __bss_start__, __bss_end__;
extern int main(void);

// FreeRTOS ARM_CM55_NTZ port handlers (portasm.c / port.c).
extern void SVC_Handler(void);
extern void PendSV_Handler(void);
extern void SysTick_Handler(void);
// Ours.
extern void he_mhu_rx_irq(void);        // mhu.c (IRQ 41, M55_HE.h:216)

static he_status_page_t *const SP = (he_status_page_t *)STATUS_PAGE_ADDR;
static bm_status_page_t *const BP = (bm_status_page_t *)BM_STATUS_PAGE_ADDR;

void he_set_err(uint32_t err) {
    if (SP->err == HE_ERR_NONE) {
        SP->err = err;
    }
}

void bm_set_err(uint32_t err) {
    if (BP->err == 0) {
        BP->err = err;
    }
}

static void Default_Handler(void) {
    he_set_err(HE_ERR_HARDFAULT);
    for (;;) {
    }
}

void Reset_Handler(void);

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Woverride-init"
__attribute__((section(".vectors"), used))
const uint32_t _vectors[16 + NUM_IRQ] = {
    (uint32_t)&_estack,               // 0: initial MSP
    (uint32_t)Reset_Handler,          // 1: reset
    (uint32_t)Default_Handler,        // 2: NMI
    (uint32_t)Default_Handler,        // 3: HardFault
    (uint32_t)Default_Handler,        // 4: MemManage
    (uint32_t)Default_Handler,        // 5: BusFault
    (uint32_t)Default_Handler,        // 6: UsageFault
    (uint32_t)Default_Handler,        // 7: SecureFault
    0, 0, 0,                          // 8-10: reserved
    (uint32_t)SVC_Handler,            // 11: SVCall (FreeRTOS)
    (uint32_t)Default_Handler,        // 12: DebugMon
    0,                                // 13: reserved
    (uint32_t)PendSV_Handler,         // 14: PendSV (FreeRTOS)
    (uint32_t)SysTick_Handler,        // 15: SysTick (FreeRTOS)
    [16 ... 16 + NUM_IRQ - 1] = (uint32_t)Default_Handler,
    [16 + 41] = (uint32_t)he_mhu_rx_irq,   // MHU_M55HP_M55HE_0_RX
};
#pragma GCC diagnostic pop

// Minimal M55 register poking; addresses from ARMv8-M arch (core_cm55.h).
#define SCB_VTOR   (*(volatile uint32_t *)0xE000ED08u)
#define SCB_CPACR  (*(volatile uint32_t *)0xE000ED88u)
#define DCB_DEMCR  (*(volatile uint32_t *)0xE000EDFCu)
#define DWT_CTRL   (*(volatile uint32_t *)0xE0001000u)
#define DWT_CYCCNT (*(volatile uint32_t *)0xE0001004u)

void Reset_Handler(void) {
    SCB_VTOR = (uint32_t)&_vectors[0];
    SCB_CPACR |= (0xFu << 20);          // CP10/CP11 full access (FPU)
    __asm volatile ("dsb; isb");

    // .noinit is deliberately OUTSIDE this range (bm_he.ld) -- dfu_core's
    // reboot-info struct must survive our resets.
    memset(&__bss_start__, 0,
           (uint32_t)&__bss_end__ - (uint32_t)&__bss_start__);
    // .data is linked and loaded in place (VMA == LMA), no copy needed.

    SP->magic = HE_MAGIC;
    SP->stage = HE_STAGE_BOOT;
    SP->tick = 0;
    SP->err = HE_ERR_NONE;
    SP->rsc_status = 0;
    SP->rx_count = 0;
    SP->tx_count = 0;
    SP->irq_count = 0;

    memset((void *)BP, 0, sizeof(*BP));
    BP->magic = BM_MAGIC;
    BP->stage = BM_STAGE_BOOT;

    DCB_DEMCR |= (1u << 24);            // TRCENA
    DWT_CYCCNT = 0;
    DWT_CTRL |= 1u;                     // CYCCNTENA

    main();
    for (;;) {
    }
}
