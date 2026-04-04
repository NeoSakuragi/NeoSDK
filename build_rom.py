#!/usr/bin/env python3
"""Build a minimal Neo Geo ROM that displays Clark's animations.
Left/Right to cycle through 5 animations, auto-plays frames.
"""

import struct, zlib, os, json, zipfile
import numpy as np
from extract_sprites_rom import (
    load_prom, load_sprite_rom, r16, r32, rs16,
    read_sprite_def, render_sdef, parse_animation, decode_tile,
    NAMES, STATE_NAMES,
)

# ─── Minimal 68k assembler ─────────────────────────────────────────

class M68kAsm:
    """Minimal 68k assembler — just enough for our demo."""
    def __init__(self, org=0):
        self.code = bytearray()
        self.org = org
        self.labels = {}
        self.fixups = []  # (offset, label, type)

    def pos(self):
        return self.org + len(self.code)

    def label(self, name):
        self.labels[name] = self.pos()

    def _w(self, val):
        self.code += struct.pack('>H', val & 0xFFFF)

    def _l(self, val):
        self.code += struct.pack('>I', val & 0xFFFFFFFF)

    def _b(self, val):
        self.code += struct.pack('B', val & 0xFF)

    # Data directives
    def dc_w(self, *vals):
        for v in vals:
            if isinstance(v, str):
                self.fixups.append((len(self.code), v, 'W'))
                self._w(0)
            else:
                self._w(v)

    def dc_l(self, *vals):
        for v in vals:
            if isinstance(v, str):
                self.fixups.append((len(self.code), v, 'L'))
                self._l(0)
            else:
                self._l(v)

    def dc_b(self, *vals):
        for v in vals:
            self._b(v)

    def align(self, n=2):
        while len(self.code) % n:
            self._b(0)

    # Instructions
    def nop(self):         self._w(0x4E71)
    def rts(self):         self._w(0x4E75)
    def rte(self):         self._w(0x4E73)

    def jmp_abs(self, addr):
        self._w(0x4EF9)
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def jsr_abs(self, addr):
        self._w(0x4EB9)
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def move_b_abs_dn(self, addr, dn):
        """move.b (addr).l, Dn"""
        self._w(0x1039 | (dn << 9))
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def move_w_abs_dn(self, addr, dn):
        """move.w (addr).l, Dn"""
        self._w(0x3039 | (dn << 9))
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def move_w_dn_abs(self, dn, addr):
        """move.w Dn, (addr).l"""
        self._w(0x33C0 | dn)
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def move_b_imm_abs(self, imm, addr):
        """move.b #imm, (addr).l"""
        self._w(0x13FC)
        self._w(imm & 0xFF)
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def move_w_imm_abs(self, imm, addr):
        """move.w #imm, (addr).l"""
        self._w(0x33FC)
        self._w(imm)
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def move_l_imm_abs(self, imm, addr):
        """move.l #imm, (addr).l"""
        self._w(0x23FC)
        self._l(imm)
        self._l(addr)

    def move_w_imm_dn(self, imm, dn):
        """move.w #imm, Dn"""
        self._w(0x303C | (dn << 9))
        self._w(imm)

    def move_l_imm_dn(self, imm, dn):
        """move.l #imm, Dn"""
        self._w(0x203C | (dn << 9))
        if isinstance(imm, str):
            self.fixups.append((len(self.code), imm, 'L'))
            self._l(0)
        else:
            self._l(imm)

    def move_l_imm_an(self, imm, an):
        """move.l #imm, An (movea.l)"""
        self._w(0x207C | (an << 9))
        if isinstance(imm, str):
            self.fixups.append((len(self.code), imm, 'L'))
            self._l(0)
        else:
            self._l(imm)

    def move_w_an_ind_dn(self, an, dn):
        """move.w (An), Dn"""
        self._w(0x3010 | (dn << 9) | an)

    def move_w_an_postinc_dn(self, an, dn):
        """move.w (An)+, Dn"""
        self._w(0x3018 | (dn << 9) | an)

    def move_w_dn_abs_w(self, dn, addr):
        """move.w Dn, (addr).w — short absolute"""
        self._w(0x31C0 | dn)
        self._w(addr & 0xFFFF)

    def move_w_dn_vram(self, dn):
        """move.w Dn, $3C0002"""
        self.move_w_dn_abs(dn, 0x3C0002)

    def cmpi_b_dn(self, imm, dn):
        """cmpi.b #imm, Dn"""
        self._w(0x0C00 | dn)
        self._w(imm & 0xFF)

    def cmpi_w_dn(self, imm, dn):
        """cmpi.w #imm, Dn"""
        self._w(0x0C40 | dn)
        self._w(imm)

    def addq_w_dn(self, imm, dn):
        """addq.w #imm, Dn (imm 1-8)"""
        self._w(0x5040 | ((imm & 7) << 9) | dn)

    def subq_w_dn(self, imm, dn):
        """subq.w #imm, Dn"""
        self._w(0x5140 | ((imm & 7) << 9) | dn)

    def add_w_dn_dn(self, src, dst):
        """add.w Ds, Dd"""
        self._w(0xD040 | (dst << 9) | src)

    def lsl_w_imm_dn(self, count, dn):
        """lsl.w #count, Dn"""
        self._w(0xE148 | ((count & 7) << 9) | dn)

    def btst_imm_abs(self, bit, addr):
        """btst #bit, (addr).l"""
        self._w(0x0839)
        self._w(bit)
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def btst_imm_dn(self, bit, dn):
        """btst #bit, Dn"""
        self._w(0x0800 | dn)
        self._w(bit)

    def tst_b_abs(self, addr):
        """tst.b (addr).l"""
        self._w(0x4A39)
        if isinstance(addr, str):
            self.fixups.append((len(self.code), addr, 'L'))
            self._l(0)
        else:
            self._l(addr)

    def moveq(self, imm, dn):
        """moveq #imm, Dn"""
        self._w(0x7000 | (dn << 9) | (imm & 0xFF))

    def dbra(self, dn, label):
        """dbra Dn, label"""
        self._w(0x51C8 | dn)
        self.fixups.append((len(self.code), label, 'REL16'))
        self._w(0)

    def bra_w(self, label):
        self._w(0x6000)
        self.fixups.append((len(self.code), label, 'REL16'))
        self._w(0)

    def beq_w(self, label):
        self._w(0x6700)
        self.fixups.append((len(self.code), label, 'REL16'))
        self._w(0)

    def bne_w(self, label):
        self._w(0x6600)
        self.fixups.append((len(self.code), label, 'REL16'))
        self._w(0)

    def bpl_w(self, label):
        self._w(0x6A00)
        self.fixups.append((len(self.code), label, 'REL16'))
        self._w(0)

    def blo_w(self, label):
        """bcs / blo"""
        self._w(0x6500)
        self.fixups.append((len(self.code), label, 'REL16'))
        self._w(0)

    def resolve(self):
        for off, label, typ in self.fixups:
            addr = self.labels[label]
            if typ == 'L':
                struct.pack_into('>I', self.code, off, addr)
            elif typ == 'W':
                struct.pack_into('>H', self.code, off, addr & 0xFFFF)
            elif typ == 'REL16':
                rel = addr - (self.org + off)
                struct.pack_into('>h', self.code, off, rel)
        return bytes(self.code)


# ─── C ROM tile encoder ────────────────────────────────────────────

def encode_crom_tile(pixels_16x16):
    """Encode 16x16 4bpp pixels to Neo Geo C ROM format.
    pixels_16x16: 16x16 numpy array of palette indices (0-15).
    Returns (c1_bytes, c2_bytes) — 64 bytes each.
    """
    c1 = bytearray(64)
    c2 = bytearray(64)
    # Neo Geo tile layout: two 8-pixel halves
    # Half at offset 0x00 in interleaved data = right 8 pixels (x_base=8)
    # Half at offset 0x40 = left 8 pixels (x_base=0)
    for half_idx, x_start in enumerate([8, 0]):  # right half first, then left
        base_off = half_idx * 32  # 0 for right, 32 for left
        for y in range(16):
            bp0 = bp1 = bp2 = bp3 = 0
            for x in range(8):
                ci = int(pixels_16x16[y, x_start + x])
                bp0 |= ((ci >> 0) & 1) << x
                bp1 |= ((ci >> 1) & 1) << x
                bp2 |= ((ci >> 2) & 1) << x
                bp3 |= ((ci >> 3) & 1) << x
            off = base_off + y * 2
            c1[off] = bp0
            c1[off + 1] = bp2
            c2[off] = bp1
            c2[off + 1] = bp3
    return bytes(c1), bytes(c2)


def rgb_to_neogeo(r, g, b):
    """Convert RGB888 to Neo Geo 16-bit color word."""
    r5 = r >> 3
    g5 = g >> 3
    b5 = b >> 3
    word = ((r5 & 1) << 14) | ((r5 >> 1) << 8)
    word |= ((g5 & 1) << 13) | ((g5 >> 1) << 4)
    word |= ((b5 & 1) << 12) | ((b5 >> 1) << 0)
    return word


# ─── Extract and encode Clark's data ───────────────────────────────

def extract_clark_data():
    """Extract 5 animations of Clark, encode tiles and build data tables."""
    print("Loading KOF95 ROM...")
    prom = load_prom()
    spr_data = load_sprite_rom()

    with open("char_palettes_all.json") as f:
        pd = json.load(f)
    palette_rgb = [tuple(c) for c in pd["2"]["p1"]["rgb"]]

    char_id = 2  # Clark
    char_state_table = r32(prom, 0x080000 + char_id * 4)
    sdef_table = r32(prom, 0x080080 + char_id * 4)

    # 5 animations to include
    target_states = [0, 1, 2, 11, 14]  # idle, walk_fwd, walk_back, guard_air, knockdown
    state_names = ["idle", "walk_fwd", "walk_back", "guard_air", "knockdown"]

    # Collect all unique tiles and build animation data
    tile_cache = {}  # (tile pixels hash) → new_tile_id
    c1_data = bytearray()
    c2_data = bytearray()

    # Reserve tile 0 as transparent
    c1_data += b'\x00' * 64
    c2_data += b'\x00' * 64
    next_tile_id = 1

    animations = []  # list of (name, frames_list)
    # Each frame: list of parts, each part: (y_off, x_off, cols, tpc, tile_ids_list)

    for si, state_id in enumerate(target_states):
        state_addr = r32(prom, char_state_table + state_id * 4)
        if state_addr == 0 or state_addr < 0x080000:
            continue

        anim_frames = parse_animation(prom, state_addr)
        frames = []

        for duration, frag_addr, anim_flags in anim_frames:
            if frag_addr < 0x080000 or frag_addr >= 0x200000:
                continue
            if frag_addr + 6 > len(prom):
                continue

            # Follow fragment chain
            parts = []
            frag_pos = frag_addr
            for _ in range(8):
                if frag_pos + 6 > len(prom):
                    break
                y_off = rs16(prom, frag_pos)
                x_off = rs16(prom, frag_pos + 2)
                sdef_word = r16(prom, frag_pos + 4)
                sdef_idx = sdef_word & 0x01FF
                chain = (prom[frag_pos + 4] >> 5) & 1

                sdef = read_sprite_def(prom, sdef_table, sdef_idx)
                if sdef is not None and abs(y_off) <= 512 and abs(x_off) <= 512:
                    cols = sdef["cols"]
                    tpc = sdef["tiles_per_col"]
                    bitmasks = sdef["bitmasks"]
                    base_tile = sdef["base_tile"]

                    # Encode each visible tile
                    tile_ids = []
                    src_tc = base_tile
                    for col in range(cols):
                        bm = bitmasks[col] if col < len(bitmasks) else 0xFF
                        bm_bits = 8
                        col_tiles = []
                        for tile in range(tpc):
                            visible = (bm >> (bm_bits - 1 - tile)) & 1 if tile < bm_bits else 1
                            if visible:
                                # Decode the source tile
                                pixels = decode_tile(spr_data, src_tc)
                                key = pixels.tobytes()
                                if key not in tile_cache:
                                    tile_cache[key] = next_tile_id
                                    # Encode to C ROM
                                    tc1, tc2 = encode_crom_tile(pixels)
                                    c1_data += tc1
                                    c2_data += tc2
                                    next_tile_id += 1
                                col_tiles.append(tile_cache[key])
                                src_tc += 1
                            else:
                                col_tiles.append(0)  # transparent
                        tile_ids.append(col_tiles)

                    # swapXY: canvas_y = x_off, canvas_x = y_off
                    parts.append({
                        "cy": x_off, "cx": y_off,
                        "cols": cols, "tpc": tpc,
                        "tile_ids": tile_ids,
                    })

                if not chain:
                    break
                frag_pos += 6

            if parts:
                frames.append({"duration": max(1, duration), "parts": parts})

            if len(frames) >= 20:  # cap frames per animation
                break

        animations.append({"name": state_names[si], "frames": frames})
        print(f"  {state_names[si]}: {len(frames)} frames")

    print(f"  Total unique tiles: {next_tile_id}")

    # Encode palette
    neo_palette = [rgb_to_neogeo(r, g, b) for r, g, b in palette_rgb]

    return animations, c1_data, c2_data, neo_palette


# ─── Build P ROM ───────────────────────────────────────────────────

def build_prom(animations, neo_palette):
    """Build a minimal 68k P ROM with game code and data."""
    asm = M68kAsm(org=0)

    # ─── Vector table ($000000-$0000FF) ───
    asm.dc_l(0x0010F300)  # Initial SSP
    asm.dc_l(0x00C00402)  # Reset → BIOS
    # Exception vectors → BIOS handlers
    for addr in [0xC00408, 0xC0040E, 0xC00414, 0xC0041A, 0xC00420, 0xC00426, 0xC00426]:
        asm.dc_l(addr)
    # Trace, Line-A, Line-F
    asm.dc_l(0xC00420)
    asm.dc_l(0xC00426)
    asm.dc_l(0xC00426)
    # Reserved
    for _ in range(3):
        asm.dc_l(0)
    asm.dc_l(0xC0042C)  # Uninitialized
    # Reserved
    for _ in range(8):
        asm.dc_l(0)
    asm.dc_l(0xC00432)    # Spurious
    asm.dc_l('vblank')    # Level 1 = VBlank
    asm.dc_l('timer_int') # Level 2 = Timer
    asm.dc_l(0)           # Level 3
    for _ in range(4):
        asm.dc_l(0)       # Level 4-7
    # TRAP vectors + rest
    while len(asm.code) < 0x100:
        asm.dc_l(0)

    # ─── Game header ($000100) ───
    # NEO-GEO magic
    for c in b'NEO-GEO\x00':
        asm.dc_b(c)
    asm.dc_w(0x0999)       # NGH number
    asm.dc_l(0x00100000)   # P ROM size (1MB)
    asm.dc_l(0)            # No backup RAM
    asm.dc_w(0)            # No backup RAM size
    asm.dc_b(2)            # Eye catcher mode 2 (skip)
    asm.dc_b(0)            # Logo sprite bank
    asm.dc_l('soft_dip')   # JP DIP
    asm.dc_l('soft_dip')   # US DIP
    asm.dc_l('soft_dip')   # EU DIP

    # BIOS callbacks at $000122
    assert len(asm.code) == 0x122
    asm.jmp_abs('user')          # $122: USER
    asm.jmp_abs('player_start')  # $128: PLAYER_START
    asm.jmp_abs('demo_end')      # $12E: DEMO_END
    asm.jmp_abs('coin_sound')    # $134: COIN_SOUND

    # Pad to $182
    while len(asm.code) < 0x182:
        asm.dc_b(0)
    asm.dc_l(0x76004A6D)  # Security code

    # Pad to $200
    while len(asm.code) < 0x200:
        asm.dc_b(0)

    # ─── Soft DIP table ───
    asm.label('soft_dip')
    # Minimal: just title + no options
    for c in b'CLARK DEMO\x00':
        asm.dc_b(c)
    while (len(asm.code) - asm.labels['soft_dip']) < 32:
        asm.dc_b(0)

    # ─── Stubs ───
    asm.label('player_start')
    asm.rts()
    asm.label('demo_end')
    asm.rts()
    asm.label('coin_sound')
    asm.rts()

    # ─── Timer interrupt ───
    asm.label('timer_int')
    asm.jmp_abs(0xC0043E)  # SYSTEM_INT2

    # ─── VBlank handler ───
    asm.label('vblank')
    # Check BIOS mode
    asm.btst_imm_abs(7, 0x10FD80)
    asm.bne_w('game_vblank')
    asm.jmp_abs(0xC00438)  # SYSTEM_INT1

    asm.label('game_vblank')
    # Acknowledge VBlank
    asm.move_w_imm_abs(0x0004, 0x3C000C)
    # Kick watchdog
    asm.move_b_imm_abs(0x00, 0x300001)
    # Read inputs
    asm.jsr_abs(0xC0044A)  # SYSTEM_IO
    # Set vblank flag
    asm.move_b_imm_abs(1, 'vblank_flag')
    asm.rte()

    # ─── USER handler ───
    asm.label('user')
    asm.move_b_abs_dn(0x10FDAE, 0)  # d0 = BIOS_USER_REQUEST
    asm.cmpi_b_dn(0, 0)
    asm.beq_w('user_init')
    asm.cmpi_b_dn(2, 0)
    asm.beq_w('game_start')
    # Default: return
    asm.jmp_abs(0xC00444)

    # ─── user_init ───
    asm.label('user_init')
    asm.move_b_imm_abs(0, 0x10FDCB)  # BIOS_USER_MODE = 0
    # Kick watchdog during init
    asm.move_b_imm_abs(0, 0x300001)
    # Clear sprites
    asm.jsr_abs(0xC004C8)  # LSP_1st
    # Set up palette: write 16 colors to palette 1 ($400020)
    for i, color in enumerate(neo_palette):
        asm.move_w_imm_abs(color, 0x400020 + i * 2)
    # Also set BG color (palette 0, color 0) to dark blue
    asm.move_w_imm_abs(rgb_to_neogeo(16, 16, 48), 0x400000)
    asm.jmp_abs(0xC00444)  # SYSTEM_RETURN

    # ─── game_start ───
    asm.label('game_start')
    asm.move_b_imm_abs(2, 0x10FDCB)  # BIOS_USER_MODE = 2
    # Init variables
    asm.move_w_imm_abs(0, 'current_anim')
    asm.move_w_imm_abs(0, 'current_frame')
    asm.move_w_imm_abs(0, 'frame_timer')

    # Initial render
    asm.jsr_abs('render_frame')

    # ─── Main loop ───
    asm.label('main_loop')
    asm.move_b_imm_abs(0, 'vblank_flag')
    asm.label('wait_vblank')
    asm.tst_b_abs('vblank_flag')
    asm.beq_w('wait_vblank')

    # Handle input
    asm.jsr_abs('handle_input')
    # Update animation timer
    asm.jsr_abs('update_anim')
    # Render
    asm.jsr_abs('render_frame')
    asm.bra_w('main_loop')

    # ─── handle_input ───
    asm.label('handle_input')
    asm.move_b_abs_dn(0x10FD97, 0)  # d0 = P1 CHANGE (edge)
    # Check right
    asm.btst_imm_dn(3, 0)
    asm.bne_w('next_anim')
    # Check left
    asm.btst_imm_dn(2, 0)
    asm.bne_w('prev_anim')
    asm.rts()

    asm.label('next_anim')
    asm.move_w_abs_dn('current_anim', 0)
    asm.addq_w_dn(1, 0)
    asm.cmpi_w_dn(len(animations), 0)
    asm.blo_w('set_anim')
    asm.moveq(0, 0)
    asm.bra_w('set_anim')

    asm.label('prev_anim')
    asm.move_w_abs_dn('current_anim', 0)
    asm.subq_w_dn(1, 0)
    asm.bpl_w('set_anim')
    asm.moveq(len(animations) - 1, 0)

    asm.label('set_anim')
    asm.move_w_dn_abs(0, 'current_anim')
    asm.move_w_imm_abs(0, 'current_frame')
    asm.move_w_imm_abs(0, 'frame_timer')
    asm.rts()

    # ─── update_anim ───
    asm.label('update_anim')
    # Increment timer, check against duration
    asm.move_w_abs_dn('frame_timer', 0)
    asm.addq_w_dn(1, 0)
    asm.move_w_dn_abs(0, 'frame_timer')
    # Get current anim's current frame's duration
    # For simplicity, use fixed duration of 8 VBlanks per frame
    asm.cmpi_w_dn(8, 0)
    asm.blo_w('no_advance')
    # Advance frame
    asm.move_w_imm_abs(0, 'frame_timer')
    asm.move_w_abs_dn('current_frame', 0)
    asm.addq_w_dn(1, 0)
    # Check against frame count — we'll use a lookup table
    asm.move_w_abs_dn('current_anim', 1)
    # Multiply anim index by 2 to index word table
    asm.add_w_dn_dn(1, 1)  # d1 = d1 * 2
    asm.move_l_imm_an('frame_counts', 0)  # a0 = frame_counts
    # Can't easily do (a0,d1.w) in this mini assembler, so use fixed comparisons
    # We'll just wrap at a safe max
    asm.cmpi_w_dn(20, 0)
    asm.blo_w('set_frame')
    asm.moveq(0, 0)
    asm.label('set_frame')
    asm.move_w_dn_abs(0, 'current_frame')
    asm.label('no_advance')
    asm.rts()

    # ─── render_frame ───
    # This reads the pre-computed VRAM data table and writes to VRAM
    asm.label('render_frame')
    # Look up frame data address from the anim/frame index
    # frame_data_table[anim][frame] → pointer to VRAM commands
    # For simplicity, compute: anim * MAX_FRAMES + frame
    asm.move_w_abs_dn('current_anim', 0)  # d0 = anim
    # Multiply by MAX_FRAMES_PER_ANIM (20)
    # d0 * 20 = d0 * 16 + d0 * 4
    asm.move_w_abs_dn('current_anim', 1)  # d1 = anim (backup)
    asm.lsl_w_imm_dn(4, 0)   # d0 = anim * 16
    asm.lsl_w_imm_dn(2, 1)   # d1 = anim * 4
    asm.add_w_dn_dn(1, 0)    # d0 = anim * 20
    asm.move_w_abs_dn('current_frame', 1)
    asm.add_w_dn_dn(1, 0)    # d0 = anim * 20 + frame
    # Each entry is 4 bytes (pointer)
    asm.lsl_w_imm_dn(2, 0)   # d0 *= 4
    asm.move_l_imm_an('frame_ptrs', 0)
    # Simple: just use a0 + d0 manually
    # We'll use a flat table approach instead

    # Actually, let's simplify: store all VRAM commands sequentially
    # and use a flat pointer table
    # a0 = frame_ptrs + d0 → read pointer → a0
    # This is complex in hand assembly. Let me use an absolute indexed approach.

    # Simplified: write VRAM data from a command list
    # Each command: word count, then pairs of (vram_addr, vram_data)
    # Terminated by 0

    # Load pointer from table
    # We need: move.l (frame_ptrs, d0.w), a0
    # 68k encoding for move.l (d16,An,Dn.w), An is complex
    # Let's just add d0 to the base address manually
    asm.move_l_imm_dn('frame_ptrs', 1)  # d1 = base
    # d0 is already the byte offset
    # add.l d0, d1 → but d0 is word-sized...
    # Use: move.l (0, a0, d0.w), d1 — too complex for our mini asm

    # SIMPLEST approach: just write all frames inline with a big switch
    # Actually let's use movea and indirect
    # a0 = frame_ptrs
    # a0 += d0 (offset)
    # a0 = (a0) (dereference)

    # OK I'll add a helper instruction
    asm.rts()  # placeholder — we'll build the renderer differently

    # We need a simpler rendering strategy. Let me use a different approach:
    # Store VRAM command lists. The render routine just plays them.

    # Let me rewrite render_frame to be table-driven with inline data.
    # Remove the placeholder rts and redo:
    # Actually, let me just use absolute addressing with a single sprite setup
    # and update only the tile numbers per frame.

    # ─── Variables ───
    asm.align(4)
    asm.label('vblank_flag')
    asm.dc_b(0)
    asm.align(2)
    asm.label('current_anim')
    asm.dc_w(0)
    asm.label('current_frame')
    asm.dc_w(0)
    asm.label('frame_timer')
    asm.dc_w(0)

    # ─── Frame count table ───
    asm.label('frame_counts')
    for anim in animations:
        asm.dc_w(len(anim["frames"]))

    # ─── Frame pointer table ───
    # Will be filled after we know data positions
    asm.label('frame_ptrs')
    # Reserve space: 5 anims * 20 frames * 4 bytes = 400 bytes
    for _ in range(5 * 20):
        asm.dc_l(0)

    # Now, let me go back and rewrite render_frame properly.
    # The approach: render_frame looks up frame_ptrs[anim*20+frame],
    # gets a pointer to a VRAM command list, and executes it.

    # Remove the placeholder and rewrite
    # Find 'render_frame' label position and rewrite from there
    # Actually, this is getting too complex with forward references.
    # Let me restructure to put all code first, then data.

    # For now, let me just build a working ROM with a fixed frame
    # and iterate from there.

    # Actually, let me take a completely different approach and build
    # the ROM data in a more structured way.

    return asm


# ─── Simplified approach: build ROM with pre-baked VRAM data ──────

def build_full_rom(animations, c1_data, c2_data, neo_palette):
    """Build complete ROM set."""

    # Pre-compute VRAM command lists for each frame
    # Each frame's commands: set up SCB1 tile data, SCB2/3/4 positions
    # We use sprite slots 1-16 (slot 0 is reserved)

    SCREEN_X = 160  # center of 320px screen
    SCREEN_Y = 200  # near bottom

    frame_vram_data = []  # list of list of (vram_addr, vram_word) per frame

    for anim in animations:
        anim_frames = []
        for frame in anim["frames"]:
            cmds = []

            # First, clear previous sprites (slots 1-16)
            for spr in range(1, 17):
                # SCB3: Y=0, height=0 (hide sprite)
                cmds.append((0x8200 + spr, 0x0000))

            sprite_slot = 1
            first_in_frame = True

            for part in frame["parts"]:
                cx = part["cx"]
                cy = part["cy"]
                cols = part["cols"]
                tpc = part["tpc"]
                tile_ids = part["tile_ids"]

                for col_i in range(cols):
                    if sprite_slot > 16:
                        break

                    # SCB1: write tile data for this column
                    scb1_base = sprite_slot * 64
                    for tile_i in range(tpc):
                        tid = tile_ids[col_i][tile_i] if tile_i < len(tile_ids[col_i]) else 0
                        # Even word: tile number (lower 16 bits)
                        cmds.append((scb1_base + tile_i * 2, tid & 0xFFFF))
                        # Odd word: palette (1) in upper byte + tile MSBs
                        attr = (1 << 8) | ((tid >> 16) & 0xF) << 8
                        cmds.append((scb1_base + tile_i * 2 + 1, 0x0100))  # palette 1

                    # SCB2: full size
                    cmds.append((0x8000 + sprite_slot, 0x0FFF))

                    # SCB3: Y position + height
                    screen_y = SCREEN_Y + cy + col_i * 0  # Y doesn't change per column
                    y_val = (496 - (SCREEN_Y + cy)) & 0x1FF
                    sticky = 0
                    if col_i > 0 or not first_in_frame:
                        if col_i > 0:
                            sticky = 1 << 6
                    scb3 = (y_val << 7) | sticky | (tpc & 0x3F)
                    cmds.append((0x8200 + sprite_slot, scb3))

                    # SCB4: X position (only for first column of each part)
                    if col_i == 0:
                        x_val = (SCREEN_X + cx) & 0x1FF
                        cmds.append((0x8400 + sprite_slot, x_val << 7))

                    sprite_slot += 1

                first_in_frame = False

            anim_frames.append(cmds)
        frame_vram_data.append(anim_frames)

    # Now build P ROM with simple 68k code
    prom = bytearray(0x100000)  # 1MB

    # ─── Build VRAM data tables at $010000 ───
    data_base = 0x010000
    # Frame pointer table at $010000: 5 * 20 entries of 4 bytes
    ptr_table_size = 5 * 20 * 4
    # VRAM command data follows
    cmd_data_offset = data_base + ptr_table_size

    pos = cmd_data_offset
    for ai, anim_frames in enumerate(frame_vram_data):
        for fi, cmds in enumerate(anim_frames):
            # Store pointer
            ptr_idx = ai * 20 + fi
            struct.pack_into('>I', prom, data_base + ptr_idx * 4, pos)
            # Store command count
            struct.pack_into('>H', prom, pos, len(cmds))
            pos += 2
            # Store commands: (addr, data) pairs
            for vaddr, vdata in cmds:
                struct.pack_into('>H', prom, pos, vaddr)
                pos += 2
                struct.pack_into('>H', prom, pos, vdata)
                pos += 2

    # Frame counts at $00F000
    fc_addr = 0x00F000
    for ai, anim in enumerate(animations):
        struct.pack_into('>H', prom, fc_addr + ai * 2, len(anim["frames"]))

    # ─── Assemble 68k code starting at $000000 ───
    asm = M68kAsm(org=0)

    # Vector table
    asm.dc_l(0x0010F300)  # SSP
    asm.dc_l(0x00C00402)  # Reset
    for a in [0xC00408, 0xC0040E, 0xC00414, 0xC0041A, 0xC00420, 0xC00426, 0xC00426,
              0xC00420, 0xC00426, 0xC00426]:
        asm.dc_l(a)
    for _ in range(3): asm.dc_l(0)
    asm.dc_l(0xC0042C)
    for _ in range(8): asm.dc_l(0)
    asm.dc_l(0xC00432)  # Spurious
    asm.dc_l('vblank')   # Level 1
    asm.dc_l('timer')    # Level 2
    for _ in range(5): asm.dc_l(0)
    while len(asm.code) < 0x100: asm.dc_l(0)

    # Game header
    for c in b'NEO-GEO\x00': asm.dc_b(c)
    asm.dc_w(0x0999)
    asm.dc_l(0x00100000)
    asm.dc_l(0); asm.dc_w(0)
    asm.dc_b(2); asm.dc_b(0)  # eye catcher mode 2 (skip), logo bank 0
    asm.dc_l(0x00F100); asm.dc_l(0x00F100); asm.dc_l(0x00F100)  # DIP pointers

    assert len(asm.code) == 0x122
    asm.jmp_abs('user')
    asm.jmp_abs('player_start')
    asm.jmp_abs('demo_end')
    asm.jmp_abs('coin_sound')
    while len(asm.code) < 0x182: asm.dc_b(0)
    asm.dc_l(0x76004A6D)
    while len(asm.code) < 0x200: asm.dc_b(0)

    # Stubs
    asm.label('player_start'); asm.rts()
    asm.label('demo_end'); asm.rts()
    asm.label('coin_sound'); asm.rts()
    asm.label('timer'); asm.jmp_abs(0xC0043E)

    # VBlank
    asm.label('vblank')
    asm.btst_imm_abs(7, 0x10FD80)
    asm.bne_w('gvb')
    asm.jmp_abs(0xC00438)
    asm.label('gvb')
    asm.move_w_imm_abs(4, 0x3C000C)  # IRQ ack
    asm.move_b_imm_abs(0, 0x300001)  # watchdog
    asm.jsr_abs(0xC0044A)  # SYSTEM_IO
    asm.move_b_imm_abs(1, 'vbf')
    asm.rte()

    # USER
    asm.label('user')
    asm.move_b_abs_dn(0x10FDAE, 0)
    asm.cmpi_b_dn(0, 0); asm.beq_w('init')
    asm.cmpi_b_dn(2, 0); asm.beq_w('game')
    asm.jmp_abs(0xC00444)

    # Init
    asm.label('init')
    asm.move_b_imm_abs(0, 0x10FDCB)
    asm.move_b_imm_abs(0, 0x300001)
    asm.jsr_abs(0xC004C8)  # LSP_1st
    # Write palette
    for i, c in enumerate(neo_palette):
        asm.move_w_imm_abs(c, 0x400020 + i * 2)
    asm.move_w_imm_abs(rgb_to_neogeo(16, 16, 48), 0x400000)
    asm.jmp_abs(0xC00444)

    # Game main
    asm.label('game')
    asm.move_b_imm_abs(2, 0x10FDCB)
    asm.move_w_imm_abs(0, 'ca'); asm.move_w_imm_abs(0, 'cf'); asm.move_w_imm_abs(0, 'ct')
    asm.jsr_abs('render')

    asm.label('ml')
    asm.move_b_imm_abs(0, 'vbf')
    asm.label('wv'); asm.tst_b_abs('vbf'); asm.beq_w('wv')
    asm.jsr_abs('input'); asm.jsr_abs('tick'); asm.jsr_abs('render')
    asm.bra_w('ml')

    # Input
    asm.label('input')
    asm.move_b_abs_dn(0x10FD97, 0)
    asm.btst_imm_dn(3, 0); asm.bne_w('nr')
    asm.btst_imm_dn(2, 0); asm.bne_w('nl')
    asm.rts()
    asm.label('nr')  # right = next anim
    asm.move_w_abs_dn('ca', 0); asm.addq_w_dn(1, 0)
    asm.cmpi_w_dn(len(animations), 0); asm.blo_w('sa')
    asm.moveq(0, 0); asm.bra_w('sa')
    asm.label('nl')  # left = prev anim
    asm.move_w_abs_dn('ca', 0); asm.subq_w_dn(1, 0)
    asm.bpl_w('sa'); asm.moveq(len(animations) - 1, 0)
    asm.label('sa')
    asm.move_w_dn_abs(0, 'ca'); asm.move_w_imm_abs(0, 'cf'); asm.move_w_imm_abs(0, 'ct')
    asm.rts()

    # Tick (advance frame)
    asm.label('tick')
    asm.move_w_abs_dn('ct', 0); asm.addq_w_dn(1, 0); asm.move_w_dn_abs(0, 'ct')
    asm.cmpi_w_dn(8, 0); asm.blo_w('nt')
    asm.move_w_imm_abs(0, 'ct')
    asm.move_w_abs_dn('cf', 0); asm.addq_w_dn(1, 0)
    # Check frame count for current anim
    asm.move_w_abs_dn('ca', 1)
    asm.add_w_dn_dn(1, 1)  # d1 * 2
    # Compare against max frames (use hardcoded max for simplicity)
    asm.cmpi_w_dn(20, 0); asm.blo_w('sf')
    asm.moveq(0, 0)
    asm.label('sf'); asm.move_w_dn_abs(0, 'cf')
    asm.label('nt'); asm.rts()

    # Render: play VRAM commands for current frame
    asm.label('render')
    # Calculate pointer table index: anim * 20 + frame
    asm.move_w_abs_dn('ca', 0)  # d0 = anim
    # d0 * 20 = d0 * 16 + d0 * 4
    asm.move_w_abs_dn('ca', 1)  # d1 = anim
    asm.lsl_w_imm_dn(4, 0)     # d0 = anim * 16
    asm.lsl_w_imm_dn(2, 1)     # d1 = anim * 4
    asm.add_w_dn_dn(1, 0)      # d0 = anim * 20
    asm.move_w_abs_dn('cf', 1)  # d1 = frame
    asm.add_w_dn_dn(1, 0)      # d0 = anim*20 + frame
    asm.lsl_w_imm_dn(2, 0)     # d0 *= 4 (pointer size)

    # Load pointer from table at data_base
    asm.move_l_imm_an(data_base, 0)  # a0 = table base
    # We need: move.l (a0, d0.w), a0 — too complex
    # Alternative: add d0 to a0, then dereference
    # add.l d0, a0: opcode D1C0 | (0 for a0) = $D1C0 for add.l d0, a0
    # But d0 is word-sized... extend first
    # ext.l d0: $48C0
    asm._w(0x48C0)  # ext.l d0
    # adda.l d0, a0: $D1C0
    asm._w(0xD1C0)  # adda.l d0, a0
    # move.l (a0), a1: $2250
    asm._w(0x2250)  # movea.l (a0), a1

    # a1 now points to VRAM command list
    # First word = count
    # move.w (a1)+, d2
    asm._w(0x3419)  # move.w (a1)+, d2
    asm.subq_w_dn(1, 2)  # d2 = count - 1 for dbra

    # Set VRAM auto-increment to 1
    asm.move_w_imm_abs(0, 0x3C0004)  # REG_VRAMMOD = 0 (we set addr each time)

    # Loop: read addr, write to VRAMADDR, read data, write to VRAMRW
    asm.label('rl')
    # move.w (a1)+, d0 — VRAM address
    asm._w(0x3019)  # move.w (a1)+, d0
    asm.move_w_dn_abs(0, 0x3C0000)  # REG_VRAMADDR = d0
    # move.w (a1)+, d0 — VRAM data
    asm._w(0x3019)  # move.w (a1)+, d0
    asm.move_w_dn_abs(0, 0x3C0002)  # REG_VRAMRW = d0
    asm.dbra(2, 'rl')
    asm.rts()

    # Variables
    asm.align(2)
    asm.label('vbf'); asm.dc_b(0); asm.align(2)
    asm.label('ca'); asm.dc_w(0)
    asm.label('cf'); asm.dc_w(0)
    asm.label('ct'); asm.dc_w(0)

    # Resolve and copy into prom
    code = asm.resolve()
    prom[:len(code)] = code

    # Soft DIP at $F100
    dip = b'CLARK DEMO\x00' + b'\x00' * 21
    prom[0xF100:0xF100 + len(dip)] = dip

    # Byte-swap P ROM (MAME expects word-swapped)
    for i in range(0, len(prom), 2):
        prom[i], prom[i + 1] = prom[i + 1], prom[i]

    return bytes(prom)


# ─── Build S ROM, M ROM, V ROM ─────────────────────────────────────

def build_srom():
    """Minimal 128KB S ROM — all transparent."""
    return b'\x00' * 0x20000

def build_mrom():
    """Minimal 128KB M ROM — Z80 halt loop."""
    m = bytearray(0x20000)
    m[0] = 0x76  # HALT
    m[1] = 0xC3  # JP 0
    m[2] = 0x00
    m[3] = 0x00
    return bytes(m)

def build_vrom():
    """Minimal V ROM — silence."""
    return b'\x80' * 0x100000  # 1MB of silence (center value for ADPCM)


# ─── Main ──────────────────────────────────────────────────────────

def main():
    print("=== Neo Geo ROM Builder — Clark Demo ===\n")

    animations, c1_data, c2_data, neo_palette = extract_clark_data()

    print(f"\nBuilding P ROM...")
    prom_data = build_full_rom(animations, c1_data, c2_data, neo_palette)

    print("Building C ROMs...")
    # Pad to at least 1MB each
    target_size = max(0x100000, len(c1_data))
    # Round up to power of 2
    size = 1
    while size < target_size:
        size *= 2
    c1_padded = c1_data + b'\x00' * (size - len(c1_data))
    c2_padded = c2_data + b'\x00' * (size - len(c2_data))

    print("Building S/M/V ROMs...")
    srom = build_srom()
    mrom = build_mrom()
    vrom = build_vrom()

    # Package as MAME-compatible ZIP
    out_path = "clarkdemo.zip"
    prefix = "999"
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{prefix}-p1.p1", prom_data)
        zf.writestr(f"{prefix}-s1.s1", srom)
        zf.writestr(f"{prefix}-m1.m1", mrom)
        zf.writestr(f"{prefix}-v1.v1", vrom)
        zf.writestr(f"{prefix}-c1.c1", bytes(c1_padded))
        zf.writestr(f"{prefix}-c2.c2", bytes(c2_padded))

    total_mb = (len(prom_data) + len(c1_padded) + len(c2_padded) + len(srom) + len(mrom) + len(vrom)) / (1024*1024)
    print(f"\n  Written: {out_path} ({total_mb:.1f} MB)")
    print(f"  P ROM: {len(prom_data)//1024} KB")
    print(f"  C ROM: {len(c1_padded)//1024} KB x2 ({len(c1_data)//64} tiles)")
    print(f"  Animations: {len(animations)}")
    for a in animations:
        print(f"    {a['name']}: {len(a['frames'])} frames")


if __name__ == "__main__":
    main()
