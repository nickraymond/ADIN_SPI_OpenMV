/* bm_he.ld -- S10 INTERIM 2: bm_core stack app for the AE3's M55_HE core.
 *
 * Same region + rationale as the bite-1 spike's he_spike.ld (SRAM9_B upper
 * half, 0x60080000, 256 KB -- global address writable by the HP loader,
 * provably untouched by the flashed images per the D24 build maps).
 *
 * Deltas from he_spike.ld:
 *   - THREE reserved areas at the top (LENGTH stops the linker below
 *     the lowest of them; nothing here is loaded or zeroed):
 *       0x600BFA00..0x600BFDFF  he_sample page   (he_sample.h, S19)
 *       0x600BFE00..0x600BFEFF  bm status page   (bm_he.h)
 *       0x600BFF00..0x600BFFFF  he_spike status page + guard (he_spike.h)
 *   - .noinit section: NOT zeroed by Reset_Handler -- bcmp's dfu_core
 *     keeps its reboot-info struct across resets there.
 * MSP grows down from the he_sample page.
 *
 * S19 bite 1 took 1 KB off LENGTH for the sample page. It is a fixed
 * address, not a pointer published on the status page (as the debug ring
 * is), because the thing under investigation is heap exhaustion: the
 * instrument must not depend on a struct that the failure could corrupt.
 */

ENTRY(_vectors)

MEMORY
{
    APP (rwx) : ORIGIN = 0x60080000, LENGTH = 0x0003FA00
}

_he_sample_page = 0x600BFA00;
_bm_status_page = 0x600BFE00;
_estack         = _he_sample_page;

SECTIONS
{
    .text : ALIGN(4)
    {
        KEEP(*(.vectors))
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > APP

    .data : ALIGN(4)
    {
        *(.data*)
        . = ALIGN(4);
    } > APP

    .bss (NOLOAD) : ALIGN(4)
    {
        __bss_start__ = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end__ = .;
    } > APP

    .noinit (NOLOAD) : ALIGN(4)
    {
        *(.noinit*)
        . = ALIGN(4);
    } > APP

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }
}
