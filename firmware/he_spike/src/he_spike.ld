/* he_spike.ld -- S10 bite 1: FreeRTOS spike app for the AE3's M55_HE core.
 *
 * The whole image lives in the UPPER HALF of SRAM9_B (0x60080000, 256 KB):
 *   - global address: HP writes it directly (runtime load, no flash) and
 *     SE_services boot_cpu() passes non-TCM addresses through unchanged
 *     (lib/alif/Device/common/include/system_utils.h GlobalToLocal).
 *   - provably free: the flashed HP image's map places nothing above
 *     0x60080000 (its .gpu_memory ends exactly there); the stock HE image
 *     doesn't touch SRAM9_B at all. Verified in the D24 build maps.
 *
 * Reserved carve-outs at the top:
 *   0x600BFF00..0x600BFF7F  status page (runner peeks via machine.mem32)
 *   0x600BFF80..0x600BFFFF  guard
 * Main stack (MSP) grows down from the status page.
 *
 * OpenAMP SHM (owned by the HP host, NOT us) is 0x60000000..0x60010000 --
 * rsc table + vrings + buffer pool; we only ever access it through
 * rpmsg_remote.c.
 */

ENTRY(_vectors)

MEMORY
{
    APP (rwx) : ORIGIN = 0x60080000, LENGTH = 0x0003FF00
}

_status_page = 0x600BFF00;
_estack      = _status_page;   /* MSP top; FreeRTOS tasks use their own stacks */

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

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }
}
