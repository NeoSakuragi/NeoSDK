"""Neo Geo animation bytecode parsing and sprite definition reading.

The animation system is shared across KOF94/95/96 with minor variations:
- Bitmask format: KOF94 always uses 16-bit words; KOF95+ uses 8-bit for tpc<=8
- Table locations: configured per game in the JSON config
- Fragment chain format: identical across all games
"""

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
