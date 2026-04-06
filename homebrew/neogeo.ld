/* Neo Geo linker script for homebrew ROMs */
OUTPUT_FORMAT("elf32-m68k")
OUTPUT_ARCH(m68k)
ENTRY(_start)

MEMORY
{
    /* P ROM: mapped at $000000, up to 1MB */
    rom (rx)  : ORIGIN = 0x000000, LENGTH = 1M
    /* Work RAM: $100000-$10FFFF (64KB) */
    wram (rwx) : ORIGIN = 0x100000, LENGTH = 64K
}

SECTIONS
{
    /* Vector table + header + security code: $000000-$0001FF */
    .vectors 0x000000 : {
        *(.text.vectors)
    } > rom

    /* Code + rodata starts after the vectors/header section */
    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.data .data.*)
    } > rom

    /* BSS in work RAM */
    .bss : {
        __bss_start = .;
        *(.bss .bss.*)
        *(COMMON)
        __bss_end = .;
    } > wram

    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
        *(.gnu.attributes)
    }
}
