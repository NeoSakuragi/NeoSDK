; Neo Geo Clark Animation Viewer
; Assembled with vasm (Motorola syntax)
; Displays Clark's sprites from C ROM, cycles animations with Left/Right

; === Hardware registers ===
REG_VRAMADDR	equ	$3C0000
REG_VRAMRW	equ	$3C0002
REG_VRAMMOD	equ	$3C0004
REG_IRQACK	equ	$3C000C
REG_WATCHDOG	equ	$300001

; === BIOS variables ===
BIOS_SYSTEM_MODE equ	$10FD80
BIOS_USER_REQUEST equ	$10FDAE
BIOS_USER_MODE	equ	$10FDCB
BIOS_P1CURRENT	equ	$10FD96
BIOS_P1CHANGE	equ	$10FD97

; === BIOS calls ===
SYSTEM_INT1	equ	$C00438
SYSTEM_INT2	equ	$C0043E
SYSTEM_RETURN	equ	$C00444
SYSTEM_IO	equ	$C0044A
FIX_CLEAR	equ	$C004C2
LSP_1ST		equ	$C004C8

; === Palette RAM ===
PALRAM		equ	$400000

; === Our variables in work RAM ===
WRAM		equ	$100000
vblank_flag	equ	WRAM+0
cur_anim	equ	WRAM+2
cur_frame	equ	WRAM+4
frame_timer	equ	WRAM+6
NUM_ANIMS	equ	5
MAX_FRAMES	equ	20

; =====================================================================
; Vector table ($000000)
; =====================================================================
	org	$000000
	dc.l	$0010F300	; Initial SSP
	dc.l	$00C00402	; Reset PC -> BIOS init
	dc.l	$00C00408	; Bus error
	dc.l	$00C0040E	; Address error
	dc.l	$00C00414	; Illegal instruction
	dc.l	$00C0041A	; Divide by zero
	dc.l	$00C00420	; CHK
	dc.l	$00C00426	; TRAPV
	dc.l	$00C00426	; Privilege violation
	dc.l	$00C00420	; Trace
	dc.l	$00C00426	; Line-A
	dc.l	$00C00426	; Line-F
	dcb.l	3,0		; Reserved
	dc.l	$00C0042C	; Uninitialized interrupt
	dcb.l	8,0		; Reserved
	dc.l	$00C00432	; Spurious interrupt
	dc.l	vblank_handler	; Level 1 = VBlank
	dc.l	$00C0043E	; Level 2 = Timer -> BIOS
	dcb.l	5,0		; Level 3-7
	dcb.l	16,$FFFFFFFF	; TRAP 0-15
	dcb.l	16,0		; Pad to $100

; =====================================================================
; Game header ($000100)
; =====================================================================
	org	$000100
	dc.b	"NEO-GEO",0	; Magic
	dc.w	$0999		; NGH number
	dc.l	$00100000	; P ROM size (2MB)
	dc.l	0		; No backup RAM
	dc.w	0		; No backup RAM size
	dc.b	2		; Eye catcher mode 2 = skip
	dc.b	0		; Logo sprite bank
	dc.l	soft_dip	; JP DIP
	dc.l	soft_dip	; US DIP
	dc.l	soft_dip	; EU DIP

	org	$000122
	jmp	(user_handler).l	; USER callback ($122-$127)
	jmp	(stub_rts).l		; PLAYER_START ($128-$12D)
	jmp	(stub_rts).l		; DEMO_END ($12E-$133)
	jmp	(stub_rts).l		; COIN_SOUND ($134-$139)

	org	$000182
	dc.l	security_code	; Pointer to security code (BIOS dereferences this)

	org	$000186
security_code:
	; Full 61-word (122-byte) security code — must match BIOS internal copy
	dc.w	$7600,$4A6D,$0A14,$6600,$003C,$206D,$0A04,$3E2D
	dc.w	$0A08,$13C0,$0030,$0001,$3210,$0C01,$00FF,$671A
	dc.w	$3028,$0002,$B02D,$0ACE,$6610,$3028,$0004,$B02D
	dc.w	$0ACF,$6606,$B22D,$0AD0,$6708,$5088,$51CF,$FFD4
	dc.w	$3607,$4E75,$206D,$0A04,$3E2D,$0A08,$3210,$E049
	dc.w	$0C01,$00FF,$671A,$3010,$B02D,$0ACE,$6612,$3028
	dc.w	$0002,$E048,$B02D,$0ACF,$6606,$B22D,$0AD0,$6708
	dc.w	$5888,$51CF,$FFD8,$3607,$4E75

; =====================================================================
; Code starts here
; =====================================================================
	org	$000200

stub_rts:
	rts

; === Soft DIP settings ===
soft_dip:
	dc.b	"CLARK VIEWER",0
	dcb.b	19,0		; Pad to 32 bytes

; === VBlank handler ===
vblank_handler:
	btst	#7,BIOS_SYSTEM_MODE
	bne.s	.game_vblank
	jmp	(SYSTEM_INT1).l	; BIOS handles it

.game_vblank:
	; Acknowledge VBlank
	move.w	#4,REG_IRQACK
	; Kick watchdog
	move.b	#0,REG_WATCHDOG
	; Read inputs
	jsr	SYSTEM_IO
	; Signal main loop
	move.b	#1,vblank_flag
	rte

; === USER handler ===
user_handler:
	move.b	BIOS_USER_REQUEST,d0
	andi.w	#$00FF,d0
	cmpi.b	#0,d0
	beq	user_init
	cmpi.b	#2,d0
	beq	game_main
	; Unknown request
	jmp	(SYSTEM_RETURN).l

; === Init (USER_REQ = 0) ===
user_init:
	; Do NOT set SYS bit 7 here - BIOS clears it after init
	; Kick watchdog
	move.b	#0,REG_WATCHDOG
	; Clear sprites
	jsr	LSP_1ST
	; Clear fix layer
	jsr	FIX_CLEAR
	; Set up palette 1 (character palette, 16 colors)
	lea	palette_data,a0
	lea	PALRAM+$20,a1	; Palette 1 starts at $400020
	moveq	#15,d0
.pal_loop:
	move.w	(a0)+,(a1)+
	dbra	d0,.pal_loop
	; Background color = dark blue
	move.w	#$1008,PALRAM	; palette 0, color 0
	; Init variables
	clr.w	cur_anim
	clr.w	cur_frame
	clr.w	frame_timer
	clr.b	vblank_flag
	; Return to BIOS
	jmp	(SYSTEM_RETURN).l

; === Game main (USER_REQ = 2) ===
game_main:
	; NOW set system mode bit 7 so game controls VBlank
	ori.b	#$80,BIOS_SYSTEM_MODE
	; Render first frame
	bsr	render_sprites

	; Main loop
.loop:
	; Wait for VBlank
	clr.b	vblank_flag
.wait:
	tst.b	vblank_flag
	beq.s	.wait

	; Handle input
	bsr	handle_input
	; Advance animation timer
	bsr	advance_frame
	; Render current frame
	bsr	render_sprites
	bra.s	.loop

; === Input handler ===
handle_input:
	move.b	BIOS_P1CHANGE,d0
	; Right = next animation
	btst	#3,d0
	bne.s	.next
	; Left = previous animation
	btst	#2,d0
	bne.s	.prev
	rts

.next:
	move.w	cur_anim,d0
	addq.w	#1,d0
	cmpi.w	#NUM_ANIMS,d0
	blo.s	.set
	moveq	#0,d0
	bra.s	.set

.prev:
	move.w	cur_anim,d0
	subq.w	#1,d0
	bpl.s	.set
	moveq	#NUM_ANIMS-1,d0

.set:
	move.w	d0,cur_anim
	clr.w	cur_frame
	clr.w	frame_timer
	rts

; === Advance animation frame ===
advance_frame:
	move.w	frame_timer,d0
	addq.w	#1,d0
	move.w	d0,frame_timer
	cmpi.w	#8,d0		; 8 VBlanks per frame
	blo.s	.done
	; Next frame
	clr.w	frame_timer
	move.w	cur_frame,d0
	addq.w	#1,d0
	; Get frame count for current animation
	move.w	cur_anim,d1
	add.w	d1,d1		; d1 * 2 (word index)
	lea	anim_frame_counts,a0
	move.w	(a0,d1.w),d1	; d1 = max frames
	cmp.w	d1,d0
	blo.s	.setf
	moveq	#0,d0		; wrap around
.setf:
	move.w	d0,cur_frame
.done:
	rts

; === Render sprites for current animation/frame ===
render_sprites:
	; Calculate pointer: anim_ptrs[cur_anim * MAX_FRAMES + cur_frame]
	move.w	cur_anim,d0
	mulu.w	#MAX_FRAMES,d0	; d0 = anim * MAX_FRAMES
	move.w	cur_frame,d1
	add.w	d1,d0		; d0 = index
	add.w	d0,d0
	add.w	d0,d0		; d0 * 4 (longword index)
	lea	frame_ptrs,a0
	movea.l	(a0,d0.w),a1	; a1 = pointer to VRAM command list

	; First word = command count
	move.w	(a1)+,d2
	subq.w	#1,d2		; for dbra

	; Execute VRAM commands: pairs of (address, data)
.cmd_loop:
	move.w	(a1)+,REG_VRAMADDR
	move.w	(a1)+,REG_VRAMRW
	dbra	d2,.cmd_loop
	rts

; =====================================================================
; Data section - will be filled by Python build script
; =====================================================================
	org	$008000

palette_data:
	; 16 words - filled by build script
	dcb.w	16,0

anim_frame_counts:
	; 5 words - frames per animation
	dcb.w	NUM_ANIMS,0

	org	$009000
frame_ptrs:
	; MAX_FRAMES * NUM_ANIMS longword pointers
	dcb.l	MAX_FRAMES*NUM_ANIMS,0

	org	$00A000
vram_cmd_data:
	; VRAM command lists start here - filled by build script
