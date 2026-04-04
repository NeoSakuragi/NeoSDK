#!/usr/bin/env python3
"""
KOF95 Complete Character Sprite Extractor - Direct from ROM
============================================================
Uses the fully reverse-engineered animation bytecode system.

Correct data flow (from 68k disassembly of renderer at $4174):
  1. Bytecode interpreter stores frag_ptr (24-bit) in obj+$28
  2. Renderer reads from fragment list at frag_ptr:
     - Bytes 0-1: Y position offset (signed 16-bit)
     - Bytes 2-3: X position offset (signed 16-bit)
     - Bytes 4-5: sprite_def_index (9-bit) + flags
  3. Sprite def (from $080080 per-char table) contains:
     - Byte 0: number of sprite columns (outer loop)
     - Byte 1: tiles per column (inner loop)
     - Bytes 2-3: palette config
     - Bytes 4-5: C ROM base tile code (16-bit)
     - Bytes 6+: one bitmask byte per column (MSB first = top tile)
  4. Tile code = base_tile + (sequential counter, increments per visible tile)
  5. Bitmask bit = 1 means tile is visible, 0 means transparent
"""

import zipfile, struct, os, sys
import numpy as np
from PIL import Image
from collections import defaultdict

ROM_PATH = "kof94.zip"
PROM_CACHE = "/tmp/kof94_prom.bin"
OUTPUT_DIR = "kof94_sprites"

NAMES = [
    "Kyo Kusanagi", "Benimaru Nikaido", "Goro Daimon",        # 0-2   Japan
    "Terry Bogard", "Andy Bogard", "Joe Higashi",              # 3-5   Fatal Fury
    "Ryo Sakazaki", "Robert Garcia", "Takuma Sakazaki",        # 6-8   Art of Fighting
    "Heidern", "Ralf Jones", "Clark Still",                    # 9-11  Ikari Warriors
    "Athena Asamiya", "Sie Kensou", "Chin Gentsai",            # 12-14 Psycho Soldier
    "Kim Kaphwan", "Chang Koehan", "Choi Bounge",             # 15-17 Kim Team
    "Mai Shiranui", "King", "Yuri Sakazaki",                   # 18-20 Women
    "Heavy D!", "Lucky Glauber", "Brian Battler",              # 21-23 Sports
    "Rugal Bernstein", "Char 25",                              # 24-25
    "Char 26", "Char 27", "Char 28", "Char 29",               # 26-29
]

STATE_NAMES = {
    0: "idle", 1: "walk_fwd", 2: "walk_back",
    3: "jump_up", 4: "jump_fwd", 5: "jump_back",
    6: "crouch", 7: "stand2crouch", 8: "crouch2stand",
    9: "guard_stand", 10: "guard_crouch", 11: "guard_air",
    12: "hit_stand", 13: "hit_crouch", 14: "knockdown", 15: "getup",
}

C_ROM_LAYOUT = [
    ("055-c1.c1", "055-c2.c2", 0x000000),
    ("055-c3.c3", "055-c4.c4", 0x400000),
    ("055-c5.c5", "055-c6.c6", 0x800000),
    ("055-c7.c7", "055-c8.c8", 0xC00000),
]
TOTAL_SPRITE_REGION = 0x1000000
ADDR_MASK = 0x03FFFFFF


def load_prom():
    if os.path.exists(PROM_CACHE):
        with open(PROM_CACHE, "rb") as f:
            return f.read()
    with zipfile.ZipFile(ROM_PATH, "r") as zf:
        raw = bytearray(zf.read("055-p1.p1"))
    for i in range(0, len(raw), 2):
        raw[i], raw[i + 1] = raw[i + 1], raw[i]
    prom = bytearray(0x200000)
    prom[0x100000:0x200000] = raw[0x000000:0x100000]
    prom[0x000000:0x100000] = raw[0x100000:0x200000]
    result = bytes(prom)
    with open(PROM_CACHE, "wb") as f:
        f.write(result)
    return result


def load_sprite_rom():
    data = bytearray(TOTAL_SPRITE_REGION)
    with zipfile.ZipFile(ROM_PATH, "r") as zf:
        for odd_name, even_name, offset in C_ROM_LAYOUT:
            odd = np.frombuffer(zf.read(odd_name), dtype=np.uint8)
            even = np.frombuffer(zf.read(even_name), dtype=np.uint8)
            size = min(len(odd), len(even))
            inter = np.empty(size * 2, dtype=np.uint8)
            inter[0::2] = odd[:size]
            inter[1::2] = even[:size]
            data[offset:offset + len(inter)] = inter.tobytes()
    return bytes(data)


def r16(rom, off):
    return struct.unpack(">H", rom[off:off + 2])[0]

def r32(rom, off):
    return struct.unpack(">I", rom[off:off + 4])[0]

def rs16(rom, off):
    return struct.unpack(">h", rom[off:off + 2])[0]


def load_palettes():
    import re
    palettes = {}
    for f in sorted(os.listdir(".")):
        if (f.startswith("fight_dump_") or f.startswith("demodump_") or
                f.startswith("sprite_dump_")) and f.endswith(".txt"):
            with open(f) as fh:
                for line in fh:
                    m = re.match(r"\s+pal\[\s*(\d+)\]:\s*(.*)", line)
                    if m:
                        idx = int(m.group(1))
                        colors = m.group(2).split()
                        if len(colors) == 16:
                            palettes[idx] = [decode_color(int(c, 16)) for c in colors]
    return palettes


def decode_color(raw):
    dark = (raw >> 15) & 1
    r = ((raw >> 14) & 1) | (((raw >> 8) & 0xF) << 1)
    g = ((raw >> 13) & 1) | (((raw >> 4) & 0xF) << 1)
    b = ((raw >> 12) & 1) | (((raw >> 0) & 0xF) << 1)
    r = (r << 3) | (r >> 2); g = (g << 3) | (g >> 2); b = (b << 3) | (b >> 2)
    if dark:
        r = max(0, r - 4); g = max(0, g - 4); b = max(0, b - 4)
    return (r, g, b)


def decode_tile(spr_data, tile_code):
    tile = np.zeros((16, 16), dtype=np.uint8)
    off = (tile_code * 128) & ADDR_MASK
    if off + 128 > len(spr_data):
        return tile
    for y in range(16):
        for half_off, x_base in [(0x40, 0), (0x00, 8)]:
            bp0 = spr_data[off + half_off + y * 4]
            bp2 = spr_data[off + half_off + y * 4 + 1]
            bp1 = spr_data[off + half_off + y * 4 + 2]
            bp3 = spr_data[off + half_off + y * 4 + 3]
            for x in range(8):
                tile[y, x_base + x] = (
                    (((bp3 >> x) & 1) << 3) | (((bp2 >> x) & 1) << 2) |
                    (((bp1 >> x) & 1) << 1) | ((bp0 >> x) & 1)
                )
    return tile


def tile_has_pixels(spr_data, tile_code):
    off = (tile_code * 128) & ADDR_MASK
    if off + 128 > len(spr_data):
        return False
    return any(spr_data[off + i] != 0 for i in range(128))


# ─── Animation parsing ───────────────────────────────────────────────

def parse_animation(prom, anim_addr, max_entries=100):
    """Parse animation bytecodes. Returns list of (duration, frag_addr_24bit, anim_flags)."""
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
        # 24-bit fragment list address (MSB of 32-bit = duration, masked out)
        frag_addr = (prom[pos + 1] << 16) | r16(prom, pos + 2)
        anim_flags = r16(prom, pos + 4)
        frames.append((duration, frag_addr, anim_flags))
        pos += 6
    return frames


def read_sprite_def(prom, sdef_table_addr, idx):
    """Read sprite definition record. Returns (cols, tiles_per_col, base_tile, bitmasks)."""
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
    base_tile_lo = r16(prom, sdef_addr + 4)  # 16-bit base tile (word0 for VRAM)
    tile_config = r16(prom, sdef_addr + 2)    # 68k does: LSL.W #4 then ANDI #$F0
    # After LSL.W #4 on tile_config, bits 7-4 = lowest nibble of tile_config
    upper_nibble = tile_config & 0xF          # this becomes VRAM word1 bits 7-4
    base_tile = (upper_nibble << 16) | base_tile_lo  # 20-bit C ROM tile code

    if cols == 0 or cols > 20 or tiles_per_col == 0 or tiles_per_col > 16:
        return None

    # KOF94 always uses WORD (16-bit) bitmasks, unlike KOF95 which uses
    # BYTE for tpc<=8. Both are big-endian, MSB = first tile.
    bitmasks = []
    for c in range(cols):
        bm_off = sdef_addr + 6 + c * 2
        if bm_off + 1 < len(prom):
            bitmasks.append(r16(prom, bm_off))
        else:
            bitmasks.append(0xFFFF)

    # Palette sub-index: byte 2 >> 4 selects which of the 16 sub-palettes
    pal_sub = (prom[sdef_addr + 2] >> 4) & 0xF

    return {
        "cols": cols,
        "tiles_per_col": tiles_per_col,
        "base_tile": base_tile,
        "bitmasks": bitmasks,
        "pal_sub": pal_sub,
    }


def render_sdef(spr_data, sdef, palette):
    """Render a single sprite definition, return (image, height, width) or None."""
    cols = sdef["cols"]
    tpc = sdef["tiles_per_col"]
    base = sdef["base_tile"]
    bitmasks = sdef["bitmasks"]

    w = cols * 16
    h = tpc * 16
    img = np.zeros((h, w, 4), dtype=np.uint8)

    tile_code = base
    for col in range(cols):
        bm = bitmasks[col] if col < len(bitmasks) else 0xFFFF
        bm_bits = 16
        for tile in range(tpc):
            if tile < bm_bits:
                visible = (bm >> (bm_bits - 1 - tile)) & 1
            else:
                visible = 1
            if visible:
                if tile_has_pixels(spr_data, tile_code):
                    pixels = decode_tile(spr_data, tile_code)
                    y0 = tile * 16
                    x0 = col * 16
                    for py in range(16):
                        for px in range(16):
                            ci = pixels[py, px]
                            if ci == 0:
                                continue
                            r, g, b = palette[ci] if ci < len(palette) else (255, 255, 255)
                            img[y0 + py, x0 + px] = [r, g, b, 255]
                tile_code += 1

    if not img[:, :, 3].any():
        return None
    return img


def render_frame(spr_data, parts, palette_set, scale=2):
    """Render a composite frame from multiple sprite parts.
    parts: list of (y_off, x_off, sdef) tuples from the fragment chain.
    Each part is rendered and composited using its Y/X offset."""
    vram_pal = 23
    palette = palette_set.get(vram_pal, palette_set.get(1, [(0,0,0)]*16))

    # Render each part and find bounding box
    # Fragment offsets are swapped: stored Y becomes screen X, stored X becomes screen Y
    rendered_parts = []
    for y_off, x_off, sdef in parts:
        img = render_sdef(spr_data, sdef, palette)
        if img is not None:
            rendered_parts.append((x_off, y_off, img))

    if not rendered_parts:
        return None

    # Compute bounding box across all parts
    min_y = min(y for y, x, img in rendered_parts)
    min_x = min(x for y, x, img in rendered_parts)
    max_y = max(y + img.shape[0] for y, x, img in rendered_parts)
    max_x = max(x + img.shape[1] for y, x, img in rendered_parts)

    w = max_x - min_x
    h = max_y - min_y
    if w <= 0 or h <= 0 or w > 512 or h > 512:
        return None

    # Composite all parts onto canvas
    canvas = np.zeros((h * scale, w * scale, 4), dtype=np.uint8)
    for y_off, x_off, part_img in rendered_parts:
        py = (y_off - min_y) * scale
        px = (x_off - min_x) * scale
        ph, pw = part_img.shape[:2]
        # Scale up
        for sy in range(ph):
            for sx in range(pw):
                if part_img[sy, sx, 3] > 0:
                    for ds in range(scale):
                        for dr in range(scale):
                            ty = py + sy * scale + ds
                            tx = px + sx * scale + dr
                            if 0 <= ty < h * scale and 0 <= tx < w * scale:
                                canvas[ty, tx] = part_img[sy, sx]

    if not canvas[:, :, 3].any():
        return None
    return canvas


def find_palette(spr_data, sdefs, palettes):
    """Find best palette for a set of sprite defs.
    Prioritizes palettes with skin tones and diverse hue spread."""
    sample_tiles = []
    for sdef in sdefs[:10]:
        tc = sdef["base_tile"]
        for i in range(min(8, sdef["cols"] * sdef["tiles_per_col"])):
            if tile_has_pixels(spr_data, tc + i):
                sample_tiles.append(tc + i)
                if len(sample_tiles) >= 12:
                    break
        if len(sample_tiles) >= 12:
            break

    if not sample_tiles:
        return 16

    # Pre-decode sample tile pixels
    tile_pixels = []
    for tc in sample_tiles:
        pixels = decode_tile(spr_data, tc)
        indices = []
        for y in range(0, 16, 2):
            for x in range(0, 16, 2):
                ci = pixels[y, x]
                if 0 < ci < 16:
                    indices.append(ci)
        tile_pixels.append(indices)

    best_pal = 91  # default to first skin-tone palette
    best_score = -1
    for pal_idx, pal in palettes.items():
        if pal_idx < 16:
            continue
        score = 0
        hues = set()
        has_skin = False
        for indices in tile_pixels:
            for ci in indices:
                r, g, b = pal[ci]
                # Reward skin tones (warm colors typical in character sprites)
                if r > 150 and g > 80 and b < 150 and r > g > b:
                    has_skin = True
                    score += 50
                # Compute rough hue bucket
                mx = max(r, g, b)
                mn = min(r, g, b)
                sat = mx - mn
                if sat > 30:
                    if mx == r:
                        hue = ((g - b) * 6 // sat) % 6
                    elif mx == g:
                        hue = 2 + (b - r) * 2 // sat
                    else:
                        hue = 4 + (r - g) * 2 // sat
                    hues.add(hue)
                score += sat
        # Reward hue diversity heavily
        score += len(hues) * 500
        # Bonus for skin tones
        if has_skin:
            score += 2000
        if score > best_score:
            best_score = score
            best_pal = pal_idx
    return best_pal


# ─── Main extraction ─────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Loading P ROM...")
    prom = load_prom()
    print("Loading C ROM...")
    spr_data = load_sprite_rom()
    print("Loading palettes...")
    palettes = load_palettes()
    grayscale_pal = [(0, 0, 0)] + [(int(i * 255 / 15),) * 3 for i in range(1, 16)]

    # Load per-character palette from char_palettes_all.json
    # The key palette is index 23 (the character body palette in VRAM)
    import json
    grayscale_pal = [(0, 0, 0)] + [(int(i * 255 / 15),) * 3 for i in range(1, 16)]

    char_palette_sets = {}  # char_id → {23: [(r,g,b)×16]}
    pal_json = os.path.join(os.path.dirname(__file__) or ".", "char_palettes_all.json")
    if os.path.exists(pal_json):
        with open(pal_json) as f:
            pd = json.load(f)
        for cid_str, variants in pd.items():
            cid = int(cid_str)
            if "p1" in variants:
                rgb = [tuple(c) for c in variants["p1"]["rgb"]]
                char_palette_sets[cid] = {23: rgb}
        print(f"  {len(char_palette_sets)} character palettes loaded")
    else:
        print("  No palette file found, using grayscale")

    print()

    total_frames = 0
    total_chars = 0

    for char_id in range(30):
        name = NAMES[char_id] if char_id < len(NAMES) else f"Char_{char_id}"
        safe_name = name.replace(" ", "_").lower()

        # Read character tables
        char_state_table = r32(prom, 0x080000 + char_id * 4)
        sdef_table = r32(prom, 0x080080 + char_id * 4)

        if char_state_table < 0x080000 or char_state_table >= 0x200000:
            continue
        if sdef_table < 0x080000 or sdef_table >= 0x200000:
            continue

        # Collect all unique frames
        all_rendered = []  # (state_id, duration, img)
        all_sdefs = []
        seen_sigs = set()
        state_frame_counts = {}

        for state_id in range(256):
            state_addr = r32(prom, char_state_table + state_id * 4)
            if state_addr == 0 or state_addr < 0x080000 or state_addr >= 0x200000:
                continue

            anim_frames = parse_animation(prom, state_addr)
            if not anim_frames:
                continue

            state_count = 0
            for duration, frag_addr, anim_flags in anim_frames:
                if frag_addr < 0x080000 or frag_addr >= 0x200000:
                    continue
                if frag_addr + 6 > len(prom):
                    continue

                # Follow the fragment chain: collect ALL parts with positions
                parts = []  # list of (y_off, x_off, sdef)
                frag_pos = frag_addr
                for _ in range(8):  # max chain depth
                    if frag_pos + 6 > len(prom):
                        break
                    y_off = rs16(prom, frag_pos)
                    x_off = rs16(prom, frag_pos + 2)
                    sdef_word = r16(prom, frag_pos + 4)
                    sdef_idx = sdef_word & 0x01FF
                    chain = (prom[frag_pos + 4] >> 5) & 1

                    sdef = read_sprite_def(prom, sdef_table, sdef_idx)
                    if sdef is not None:
                        parts.append((y_off, x_off, sdef))

                    if not chain:
                        break
                    frag_pos += 6

                if not parts:
                    continue

                # Dedup by combined signature of ALL parts
                sig = tuple((p[0], p[1], p[2]["base_tile"], p[2]["cols"],
                             p[2]["tiles_per_col"]) for p in parts)
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)

                all_rendered.append((state_id, duration, parts))
                state_count += 1

            if state_count:
                state_frame_counts[state_id] = state_count

        if not all_rendered:
            print(f"  [{char_id:2d}] {name:20s}: no valid frames")
            continue

        # Get this character's palette set (or fallback to char 6 = Kyo)
        pal_set = char_palette_sets.get(char_id,
                  char_palette_sets.get(6, {23: grayscale_pal}))

        # Render all frames (each frame is a list of parts to composite)
        rendered_images = []
        for state_id, duration, parts in all_rendered:
            img = render_frame(spr_data, parts, pal_set)
            if img is not None:
                rendered_images.append((state_id, duration, img))

        if not rendered_images:
            print(f"  [{char_id:2d}] {name:20s}: all frames blank")
            continue

        total_chars += 1
        total_frames += len(rendered_images)

        # Save per-state sprite sheets
        char_dir = os.path.join(OUTPUT_DIR, f"{char_id:02d}_{safe_name}")
        os.makedirs(char_dir, exist_ok=True)

        by_state = defaultdict(list)
        for state_id, dur, img in rendered_images:
            by_state[state_id].append((dur, img))

        for state_id, frame_list in sorted(by_state.items()):
            sname = STATE_NAMES.get(state_id, f"state_{state_id:03d}")
            images = [img for _, img in frame_list]
            max_h = max(im.shape[0] for im in images)
            gap = 2
            total_w = sum(im.shape[1] for im in images) + gap * (len(images) - 1)
            strip = np.zeros((max_h, total_w, 4), dtype=np.uint8)
            x = 0
            for im in images:
                h_im, w_im = im.shape[:2]
                y_off = max_h - h_im
                strip[y_off:y_off + h_im, x:x + w_im] = im
                x += w_im + gap
            Image.fromarray(strip, "RGBA").save(os.path.join(char_dir, f"{sname}.png"))

        # Combined atlas
        all_imgs = [img for _, _, img in rendered_images]
        cols_atlas = min(10, len(all_imgs))
        rows_atlas = (len(all_imgs) + cols_atlas - 1) // cols_atlas
        max_fw = max(im.shape[1] for im in all_imgs)
        max_fh = max(im.shape[0] for im in all_imgs)
        gap = 4
        atlas = np.zeros((rows_atlas * (max_fh + gap), cols_atlas * (max_fw + gap), 4), dtype=np.uint8)

        for i, im in enumerate(all_imgs):
            c = i % cols_atlas
            r = i // cols_atlas
            fh, fw = im.shape[:2]
            atlas[r * (max_fh + gap):r * (max_fh + gap) + fh,
                  c * (max_fw + gap):c * (max_fw + gap) + fw] = im

        Image.fromarray(atlas, "RGBA").save(
            os.path.join(OUTPUT_DIR, f"{char_id:02d}_{safe_name}_atlas.png"))

        print(f"  [{char_id:2d}] {name:20s}: {len(rendered_images):4d} frames, "
              f"{len(state_frame_counts):3d} states")

    print(f"\nDone! {total_chars} characters, {total_frames} total frames")
    print(f"Output: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
