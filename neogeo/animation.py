"""Neo Geo animation bytecode parsing and sprite definition reading.

The animation system is shared across KOF94/95/96 with minor variations:
- Bitmask format: KOF94 always uses 16-bit words; KOF95+ uses 8-bit for tpc<=8
- Table locations: configured per game in the JSON config
- Fragment chain format: identical across all games

KOF96 engine differences:
- Animation bytecodes use $FD control bytes to set body/accessory frame bases
- Frames use relative indices instead of direct fragment addresses
- Sdef records use 2D bitmasks with 7 encoding modes (tpc 0-10)
- Fragment/sdef data lives in P2 ROM, not P1
"""

import math
from .sprite_decode import r16, r32, rs16


def parse_animation(prom, anim_addr, max_entries=100):
    """Parse animation bytecodes. Returns list of (duration, frag_addr, anim_flags)."""
    if anim_addr < 0x080000 or anim_addr >= 0x200000:
        return []
    frames = []
    pos = anim_addr
    for _ in range(max_entries):
        if pos + 2 > len(prom):
            break
        w = r16(prom, pos)
        if w == 0xFF00 or w == 0xFE00:
            break
        b0 = prom[pos]
        if b0 >= 0x80:
            pos += 6
            continue
        if pos + 6 > len(prom):
            break
        duration = b0
        frag_addr = (prom[pos + 1] << 16) | r16(prom, pos + 2)
        anim_flags = r16(prom, pos + 4)
        frames.append((duration, frag_addr, anim_flags))
        pos += 6
    return frames


def read_sprite_def(prom, sdef_table_addr, idx, bitmask_mode="auto"):
    """Read a sprite definition record.

    bitmask_mode:
        "auto"  — KOF95 style: byte bitmask if tpc<=8, word if >8
        "word"  — KOF94 style: always 16-bit word bitmasks
    """
    ptr_off = sdef_table_addr + idx * 4
    if ptr_off + 4 > len(prom):
        return None
    sdef_addr = r32(prom, ptr_off)
    if sdef_addr < 0x080000 or sdef_addr >= 0x200000:
        return None
    if sdef_addr + 6 > len(prom):
        return None

    cols = prom[sdef_addr]
    tiles_per_col = prom[sdef_addr + 1]
    base_tile_lo = r16(prom, sdef_addr + 4)
    tile_config = r16(prom, sdef_addr + 2)
    upper_nibble = tile_config & 0xF
    base_tile = (upper_nibble << 16) | base_tile_lo

    if cols == 0 or cols > 20 or tiles_per_col == 0 or tiles_per_col > 16:
        return None

    # Read bitmasks — format depends on game
    bitmasks = []
    if bitmask_mode == "word":
        # KOF94: always 16-bit word bitmasks
        for c in range(cols):
            bm_off = sdef_addr + 6 + c * 2
            if bm_off + 1 < len(prom):
                bitmasks.append(r16(prom, bm_off))
            else:
                bitmasks.append(0xFFFF)
    else:
        # KOF95 "auto": byte if tpc<=8, word if >8
        if tiles_per_col <= 8:
            for c in range(cols):
                bm_off = sdef_addr + 6 + c
                if bm_off < len(prom):
                    bitmasks.append(prom[bm_off])
                else:
                    bitmasks.append(0xFF)
        else:
            for c in range(cols):
                bm_off = sdef_addr + 6 + c * 2
                if bm_off + 1 < len(prom):
                    bitmasks.append(r16(prom, bm_off))
                else:
                    bitmasks.append(0xFFFF)

    pal_sub = (prom[sdef_addr + 2] >> 4) & 0xF

    return {
        "cols": cols,
        "tiles_per_col": tiles_per_col,
        "base_tile": base_tile,
        "bitmasks": bitmasks,
        "pal_sub": pal_sub,
    }


def follow_fragment_chain(prom, frag_addr, sdef_table, bitmask_mode="auto", max_parts=8):
    """Follow a fragment chain and return list of (y_off, x_off, sdef) tuples."""
    parts = []
    frag_pos = frag_addr
    for _ in range(max_parts):
        if frag_pos + 6 > len(prom):
            break
        y_off = rs16(prom, frag_pos)
        x_off = rs16(prom, frag_pos + 2)
        sdef_word = r16(prom, frag_pos + 4)
        sdef_idx = sdef_word & 0x01FF
        chain = (prom[frag_pos + 4] >> 5) & 1

        sdef = read_sprite_def(prom, sdef_table, sdef_idx, bitmask_mode)
        if sdef is not None and abs(y_off) <= 512 and abs(x_off) <= 512:
            parts.append((y_off, x_off, sdef))

        if not chain:
            break
        frag_pos += 6

    return parts


# ─── KOF96 engine ──────────────────────────────────────────────────

# Per-character tables in P2 ROM
CHAR_FRAG_TABLE_96 = 0x3C008   # P2 offset: 33 × 4-byte frag base pointers
CHAR_SDEF_TABLE_96 = 0x6C000   # P2 offset: 33 × 4-byte sdef table pointers
P2_BASE = 0x200000

# State table char_id → sdef/frag table index mapping
# State table groups Yagami team (Iori/Mature/Vice) at 9-11,
# but sdef/frag tables follow KOF96 team order where Yagami is at 21-23
CHAR_ID_TO_TABLE_IDX_96 = [
    0, 1, 2,        # Japan: Kyo, Benimaru, Goro
    3, 4, 5,        # Fatal Fury: Terry, Andy, Joe
    6, 7, 8,        # AOF: Ryo, Robert, Yuri
    21, 22, 23,     # Yagami: Iori, Mature, Vice
    9, 10, 11,      # Ikari: Leona, Ralf, Clark
    12, 13, 14,     # Psycho: Athena, Kensou, Chin
    15, 16, 17,     # Women: Chizuru, Mai, King
    18, 19, 20,     # Kim: Kim, Chang, Choi
    24, 25, 26,     # New Faces: char24, char25, char26
    27, 28,         # Goenitz, Mr. Big
    29, 30, 31, 32, # Geese, Krauser, Kasumi, Boss
]


def _p2(addr):
    return addr - P2_BASE


def _p2_byte(p2rom, offset):
    """Read a single byte from pre-byte-swapped P2 ROM.
    No XOR needed — load_p2rom already handles byte-swap."""
    return p2rom[offset]


def parse_kof96_animation(prom, anim_addr):
    """Parse KOF96 animation bytecodes.

    Returns (fd_body, fd_acc, frames) where:
      fd_body/fd_acc: frame base offsets for body/accessory (or None)
      frames: list of (duration, frame_index)
    """
    if anim_addr < 0x080000 or anim_addr >= 0x200000:
        return None, None, []
    fd_body = fd_acc = None
    frames = []
    pos = anim_addr
    for _ in range(200):
        if pos + 6 > len(prom):
            break
        b0 = prom[pos]
        b1 = prom[pos + 1]
        w2 = r16(prom, pos + 2)
        if b0 == 0xFF or b0 == 0xFE:
            break
        if b0 == 0xFD:
            if b1 in (0x04, 0x05):
                fd_body = w2
            elif b1 in (0x09, 0x0A):
                fd_acc = w2
            pos += 6
            continue
        if b0 >= 0x80:
            pos += 6
            continue
        # Regular frame: duration, flags, frame_index, anim_flags
        frames.append((b0, w2))
        pos += 6
    return fd_body, fd_acc, frames


FRAG_BASE_96 = 0x23CB86  # Global fragment base (shared across all characters)


def get_char_tables_96(p2rom, char_id):
    """Get per-character frag base and sdef table for KOF96.

    Fragment base is global (same for all characters).
    Sdef table is per-character (indexed by team ordering).
    """
    tbl_idx = CHAR_ID_TO_TABLE_IDX_96[char_id] if char_id < len(CHAR_ID_TO_TABLE_IDX_96) else char_id
    sdef_table = r32(p2rom, CHAR_SDEF_TABLE_96 + tbl_idx * 4)
    return FRAG_BASE_96, sdef_table


def read_kof96_fragment(p2rom, abs_idx, frag_base=None):
    """Read a KOF96 fragment record. Returns (y, x, sdef_idx) or None."""
    if frag_base is None:
        frag_base = FRAG_BASE_96
    addr = frag_base + abs_idx * 6
    off = _p2(addr)
    if off < 0 or off + 6 > len(p2rom):
        return None
    y = rs16(p2rom, off)
    x = rs16(p2rom, off + 2)
    sw = r16(p2rom, off + 4)
    sdef_idx = sw & 0x1FF
    if abs(y) > 300 or abs(x) > 300 or sdef_idx >= 584:
        return None
    return y, x, sdef_idx


def read_kof96_sdef(p2rom, sdef_idx, sdef_table=None):
    """Read a KOF96 sdef record. Returns dict with tiles list or None.

    The sdef dict contains:
      cols, tpc, b2, b3: header fields
      tiles: list of (row, col, tile_num) — grid positions and C ROM tile indices
    """
    if sdef_idx >= 584:
        return None
    if sdef_table is None:
        sdef_table = r32(p2rom, CHAR_SDEF_TABLE_96)  # default to char 0
    ptr_off = _p2(sdef_table + sdef_idx * 4)
    if ptr_off < 0 or ptr_off + 4 > len(p2rom):
        return None
    sdef_ptr = r32(p2rom, ptr_off)
    off = _p2(sdef_ptr)
    if off < 0 or off + 8 > len(p2rom):
        return None

    w0 = r16(p2rom, off)
    w1 = r16(p2rom, off + 2)
    w2 = r16(p2rom, off + 4)
    cols = (w0 >> 8) & 0xFF
    tpc = w0 & 0xFF
    b2 = (w1 >> 8) & 0xFF
    b3 = w1 & 0xFF

    if b2 == 0 or b3 == 0 or b2 > 20 or b3 > 20:
        return None

    tiles = []
    bpr = math.ceil(b3 / 8)

    if tpc in (0, 1):
        tb = (w2 << 16) | r16(p2rom, off + 6)
        raw = bpr * b2
        if raw % 2:
            raw += 1
        tc = 0
        for r in range(b2):
            for bc in range(bpr):
                bv = _p2_byte(p2rom, off + 8 + r * bpr + bc)
                for bit in range(8):
                    c = bc * 8 + bit
                    if c < b3 and (bv >> (7 - bit)) & 1:
                        tiles.append((r, c, tb + tc))
                        tc += 1
    elif tpc in (2, 3, 7, 8):
        # Individual 16-bit tile codes after bitmask
        # w2 encodes C ROM bank at bits 4-5: bank = (w2 >> 4) & 3
        tile_bank = ((w2 >> 4) & 0x3) << 16
        bm_sz = bpr * b2
        if bm_sz % 2:
            bm_sz += 1  # pad to even for word-aligned tile codes
        io = off + 6 + bm_sz
        for r in range(b2):
            for bc in range(bpr):
                bv = _p2_byte(p2rom, off + 6 + r * bpr + bc)
                for bit in range(8):
                    c = bc * 8 + bit
                    if c < b3 and (bv >> (7 - bit)) & 1:
                        if io + 2 <= len(p2rom):
                            tiles.append((r, c, tile_bank | r16(p2rom, io)))
                        io += 2
    elif tpc in (4, 5, 9, 10):
        # Base tile + 8-bit offsets after bitmask
        # w2 encodes bank at bits 12-13, base at bits 0-11
        tile_bank = ((w2 >> 12) & 0x3) << 16
        tile_base = w2 & 0x0FFF
        bm_sz = bpr * b2
        io = off + 6 + bm_sz
        for r in range(b2):
            for bc in range(bpr):
                bv = _p2_byte(p2rom, off + 6 + r * bpr + bc)
                for bit in range(8):
                    c = bc * 8 + bit
                    if c < b3 and (bv >> (7 - bit)) & 1:
                        if io < len(p2rom):
                            tiles.append((r, c, tile_bank | (tile_base + _p2_byte(p2rom, io))))
                        io += 1
    elif tpc == 6:
        pass  # placeholder, no tiles

    if not tiles:
        return None

    return {
        "cols": cols,
        "tpc": tpc,
        "b2": b2,
        "b3": b3,
        "tiles": tiles,
    }
