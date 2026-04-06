| Neo Geo CRT0 — Bootstrap and BIOS glue (GAS syntax, m68k-linux-gnu)
| Calls into C: game_init() and game_main_loop()

    .section .text.vectors, "ax"
    .global _start

| =====================================================================
| Vector table ($000000)
| =====================================================================
_start:
    .long   0x0010F300          /* Initial SSP */
    .long   0x00C00402          /* Reset PC -> BIOS init */
    .long   0x00C00408          /* Bus error */
    .long   0x00C0040E          /* Address error */
    .long   0x00C00414          /* Illegal instruction */
    .long   0x00C0041A          /* Divide by zero */
    .long   0x00C0041A          /* CHK instruction */
    .long   0x00C0041A          /* TRAPV instruction */
    .long   0x00C0041A          /* Privilege violation */
    .long   0x00C00420          /* Trace */
    .long   0x00C00426          /* Line-A */
    .long   0x00C00426          /* Line-F */
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF  /* Reserved */
    .long   0x00C0042C          /* Uninitialized interrupt */
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF  /* Reserved */
    .long   0x00C00432          /* Spurious interrupt */
    .long   vblank_handler      /* Level 1 = VBlank */
    .long   0x00C0043E          /* Level 2 = Timer -> BIOS */
    .long   0x00000000          /* Level 3 (unused) */
    .long   0, 0, 0, 0         /* Level 4-7 */
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF  /* TRAP 0-15 */
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF  /* FPU + Reserved */
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF
    .long   0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF

| =====================================================================
| Game header ($000100)
| =====================================================================
    .org    0x100
    .ascii  "NEO-GEO\0"        /* Magic */
    .word   0x0999              /* NGH number */
    .long   0x00100000          /* P ROM size */
    .long   0                   /* No backup RAM */
    .word   0                   /* No backup RAM size */
    .byte   2                   /* Eye catcher mode 2 = skip */
    .byte   0                   /* Logo sprite bank */
    .long   soft_dip            /* JP DIP */
    .long   soft_dip            /* US DIP */
    .long   soft_dip            /* EU DIP */

    .org    0x122
    | Must be absolute long JMP (6 bytes each) at fixed offsets
    .word   0x4EF9              /* JMP abs.l opcode */
    .long   user_handler        /* USER callback ($122-$127) */
    .word   0x4EF9
    .long   stub_rts            /* PLAYER_START ($128-$12D) */
    .word   0x4EF9
    .long   stub_rts            /* DEMO_END ($12E-$133) */
    .word   0x4EF9
    .long   stub_rts            /* COIN_SOUND ($134-$139) */

    .org    0x13A
    .fill   70, 1, 0xFF         /* Required padding */
    .word   0x0000              /* Reserved */
    .long   security_code       /* Pointer to security code */

    .org    0x186
security_code:
    .word   0x7600, 0x4A6D, 0x0A14, 0x6600, 0x003C, 0x206D, 0x0A04, 0x3E2D
    .word   0x0A08, 0x13C0, 0x0030, 0x0001, 0x3210, 0x0C01, 0x00FF, 0x671A
    .word   0x3028, 0x0002, 0xB02D, 0x0ACE, 0x6610, 0x3028, 0x0004, 0xB02D
    .word   0x0ACF, 0x6606, 0xB22D, 0x0AD0, 0x6708, 0x5088, 0x51CF, 0xFFD4
    .word   0x3607, 0x4E75, 0x206D, 0x0A04, 0x3E2D, 0x0A08, 0x3210, 0xE049
    .word   0x0C01, 0x00FF, 0x671A, 0x3010, 0xB02D, 0x0ACE, 0x6612, 0x3028
    .word   0x0002, 0xE048, 0xB02D, 0x0ACF, 0x6606, 0xB22D, 0x0AD0, 0x6708
    .word   0x5888, 0x51CF, 0xFFD8, 0x3607, 0x4E75

| =====================================================================
| Code section
| =====================================================================
    .org    0x200

stub_rts:
    rts

soft_dip:
    .ascii  "IORI VIEWER\0"
    .fill   20, 1, 0            /* Pad to 32 bytes */

| ─── VBlank handler ──────────────────────────────────────────────────
    .global vblank_flag
vblank_handler:
    btst    #7, 0x10FD80        /* BIOS_SYSTEM_MODE */
    bne.s   .Lgame_vblank
    jmp     0xC00438            /* SYSTEM_INT1 — BIOS handles it */

.Lgame_vblank:
    movew   #4, 0x3C000C        /* ACK VBlank */
    moveb   #0, 0x300001        /* Watchdog */
    jsr     0xC0044A            /* SYSTEM_IO (reads inputs) */
    moveb   #1, vblank_flag
    rte

| ─── USER handler (dispatches to C) ─────────────────────────────────
    .global user_handler
user_handler:
    moveb   0x10FDAE, %d0       /* BIOS_USER_REQUEST */
    andiw   #0x00FF, %d0
    cmpib   #0, %d0
    beq     do_init
    cmpib   #2, %d0
    beq     do_game
    jmp     0xC00444            /* SYSTEM_RETURN */

do_init:
    moveb   #0, 0x300001        /* Watchdog */
    jsr     0xC004C8            /* LSP_1ST (clear sprites) */
    jsr     0xC004C2            /* FIX_CLEAR */
    jsr     game_init           /* C function */
    jmp     0xC00444            /* SYSTEM_RETURN */

do_game:
    orib    #0x80, 0x10FD80     /* Set system mode bit 7 */
    movew   #0x2000, %sr        /* Enable interrupts */
    jsr     game_init           /* Re-init (BIOS may have cleared palette) */

.Lmain_loop:
    clrb    vblank_flag
.Lwait:
    tstb    vblank_flag
    beq.s   .Lwait
    jsr     game_tick           /* C function — called each VBlank */
    bra.s   .Lmain_loop

| ─── BSS ─────────────────────────────────────────────────────────────
    .section .bss
    .align  2
vblank_flag:
    .skip   2
