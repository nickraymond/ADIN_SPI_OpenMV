// startup.c -- vectors + reset path for the HE spike (no CMSIS SystemInit:
// the SE has already brought the core up; we only touch what we use).
//
// Vector table: M55_HE external IRQs go up to at least SPI0_IRQ = 137
// (lib/alif/Device/core/M55_HE/include/M55_HE.h:295); 16 + 192 entries
// covers the family with margin. VTOR alignment for 208 words is 1024 B;
// the table sits at the image base 0x60080000 (aligned far beyond that).

#include <stdint.h>
#include <string.h>
#include "he_spike.h"

#define NUM_IRQ 192

extern uint32_t _estack, __bss_start__, __bss_end__;
extern int main(void);

// FreeRTOS ARM_CM55_NTZ port handlers (portasm.c / port.c).
extern void SVC_Handler(void);
extern void PendSV_Handler(void);
extern void SysTick_Handler(void);
// Ours.
extern void he_mhu_rx_irq(void);        // mhu.c   (IRQ 41, M55_HE.h:216)
extern void he_spi0_irq(void);          // spi_probe.c (IRQ 137, M55_HE.h:295)

static he_status_page_t *const SP = (he_status_page_t *)STATUS_PAGE_ADDR;

void he_set_err(uint32_t err) {
    if (SP->err == HE_ERR_NONE) {
        SP->err = err;
    }
}

static void Default_Handler(void) {
    he_set_err(HE_ERR_HARDFAULT);
    for (;;) {
    }
}

void Reset_Handler(void);

// The range initializer below is deliberately overwritten for the two
// IRQs we own -- silence GCC's override-init warning for this table only.
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
    // External IRQs: default everything, then the two we own.
    [16 ... 16 + NUM_IRQ - 1] = (uint32_t)Default_Handler,
    [16 + 41] = (uint32_t)he_mhu_rx_irq,   // MHU_M55HP_M55HE_0_RX
    [16 + 137] = (uint32_t)he_spi0_irq,    // SPI0
};
#pragma GCC diagnostic pop

// Minimal M55 register poking; addresses from ARMv8-M arch (core_cm55.h).
#define SCB_VTOR   (*(volatile uint32_t *)0xE000ED08u)
#define SCB_CPACR  (*(volatile uint32_t *)0xE000ED88u)
#define DCB_DEMCR  (*(volatile uint32_t *)0xE000EDFCu)
#define DWT_CTRL   (*(volatile uint32_t *)0xE0001000u)
#define DWT_CYCCNT (*(volatile uint32_t *)0xE0001004u)

// MPU: carve the OpenAMP SHM window non-cacheable, exactly like the HP
// host's metal port does (ports/alif/mpmetalport.c metal_sys_init) --
// both sides must agree or vring updates go stale in D-cache. We keep
// caches DISABLED in this spike (reset default), so this is belt and
// braces for a future cache-enable; documented perf lever, not a gate.

void Reset_Handler(void) {
    SCB_VTOR = (uint32_t)&_vectors[0];
    SCB_CPACR |= (0xFu << 20);          // CP10/CP11 full access (FPU)
    __asm volatile ("dsb; isb");

    memset(&__bss_start__, 0,
           (uint32_t)&__bss_end__ - (uint32_t)&__bss_start__);
    // .data is linked and loaded in place (VMA == LMA), no copy needed.

    // Status page lives outside .bss on purpose: survives our own resets,
    // gets stamped fresh here.
    SP->magic = HE_MAGIC;
    SP->stage = HE_STAGE_BOOT;
    SP->tick = 0;
    SP->err = HE_ERR_NONE;
    SP->rsc_status = 0;
    SP->rx_count = 0;
    SP->tx_count = 0;
    SP->irq_count = 0;

    // DWT cycle counter: the bench's clock (160 MHz nominal,
    // boards/OPENMV_AE3/board_config.mk M55_HE_CPU_FREQ_HZ).
    DCB_DEMCR |= (1u << 24);            // TRCENA
    DWT_CYCCNT = 0;
    DWT_CTRL |= 1u;                     // CYCCNTENA

    main();
    for (;;) {
    }
}
