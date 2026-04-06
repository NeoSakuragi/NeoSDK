#!/usr/bin/env python3
"""Build a Neo Geo ROM from Aseprite files.

Usage:
    python3 build_rom_aseprite.py [file1.aseprite file2.aseprite ...]

Each .aseprite file becomes one animation. Left/Right cycles animations,
Up/Down/A/B moves the sprite. Game logic is in homebrew/game.c.
"""

import struct, os, sys, json, zipfile, subprocess, zlib, hashlib, shutil, glob as globmod
import numpy as np

HOMEBREW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "homebrew")
sys.path.insert(0, HOMEBREW_DIR)
CC = "m68k-linux-gnu-gcc"
LD = "m68k-linux-gnu-ld"
OBJCOPY = "m68k-linux-gnu-objcopy"
BUILD_DIR = "/tmp/neogeo_build"


# ─── Aseprite parser ─────────────────────────────────────────────────

def parse_aseprite(path):
    """Parse an Aseprite file, return (width, height, palette_rgba, frames).
    Each frame is {'image': np.array(h,w,4 uint8), 'duration': int ms}.
    """
    with open(path, 'rb') as f:
        data = f.read()

    fsize, magic, nframes, w, h, bpp = struct.unpack_from('<IHHHHH', data, 0)
    assert magic == 0xA5E0, f"Not an Aseprite file: {path}"
    assert bpp == 32, f"Only RGBA (32bpp) Aseprite files supported, got {bpp}bpp"

    palette = []
    frames = []
    off = 128

    for fi in range(nframes):
        frame_size, _, old_chunks, duration, _, new_chunks = struct.unpack_from(
            '<IHHH2sI', data, off)
        nchunks = new_chunks if new_chunks else old_chunks
        canvas = np.zeros((h, w, 4), dtype=np.uint8)

        chunk_off = off + 16
        for ci in range(nchunks):
            csize, ctype = struct.unpack_from('<IH', data, chunk_off)

            if ctype == 0x2019 and not palette:
                ncolors, first_idx, last_idx = struct.unpack_from(
                    '<III', data, chunk_off + 6)
                col_off = chunk_off + 26
                for i in range(ncolors):
                    if col_off + 6 > chunk_off + csize:
                        break
                    flags_c = struct.unpack_from('<H', data, col_off)[0]
                    r, g, b, a = data[col_off+2:col_off+6]
                    palette.append((r, g, b, a))
                    col_off += 6
                    if flags_c & 1:
                        slen = struct.unpack_from('<H', data, col_off)[0]
                        col_off += 2 + slen

            elif ctype == 0x2005:
                cel_data = data[chunk_off+6:chunk_off+csize]
                cx, cy = struct.unpack_from('<hh', cel_data, 2)
                cel_type = struct.unpack_from('<H', cel_data, 7)[0]

                if cel_type == 2:
                    zlib_pos = -1
                    for sig in [b'\x78\x9c', b'\x78\x01', b'\x78\xda']:
                        p = cel_data.find(sig)
                        if p >= 0:
                            zlib_pos = p
                            break
                    if zlib_pos >= 0:
                        cw, ch = struct.unpack_from('<HH', cel_data, zlib_pos - 4)
                        pixels = zlib.decompress(cel_data[zlib_pos:])
                        img = np.frombuffer(pixels, dtype=np.uint8).reshape(ch, cw, 4)
                        y0 = max(0, cy); y1 = min(h, cy + ch)
                        x0 = max(0, cx); x1 = min(w, cx + cw)
                        src = img[y0-cy:y1-cy, x0-cx:x1-cx]
                        mask = src[:, :, 3] > 0
                        canvas[y0:y1, x0:x1][mask] = src[mask]

            chunk_off += csize

        frames.append({'image': canvas, 'duration': duration})
        off += frame_size

    return w, h, palette, frames


# ─── Tile encoding ───────────────────────────────────────────────────

def rgba_to_palette_index(pixel_rgba, palette_rgb_map):
    if pixel_rgba[3] == 0:
        return 0
    key = (pixel_rgba[0], pixel_rgba[1], pixel_rgba[2])
    if key in palette_rgb_map:
        return palette_rgb_map[key]
    best_idx, best_dist = 1, float('inf')
    for rgb, idx in palette_rgb_map.items():
        d = sum((int(a) - int(b)) ** 2 for a, b in zip(key, rgb))
        if d < best_dist:
            best_dist = d
            best_idx = idx
    return best_idx


def encode_crom_tile(pixels_16x16):
    c1 = bytearray(64)
    c2 = bytearray(64)
    for half_idx, x_start in enumerate([8, 0]):
        base_off = half_idx * 32
        for y in range(16):
            bp0 = bp1 = bp2 = bp3 = 0
            for x in range(8):
                ci = int(pixels_16x16[y, x_start + x]) & 0xF
                bp0 |= ((ci >> 0) & 1) << x
                bp1 |= ((ci >> 1) & 1) << x
                bp2 |= ((ci >> 2) & 1) << x
                bp3 |= ((ci >> 3) & 1) << x
            off = base_off + y * 2
            c1[off] = bp0; c1[off + 1] = bp2
            c2[off] = bp1; c2[off + 1] = bp3
    return bytes(c1), bytes(c2)


def rgb_to_neogeo(r, g, b):
    r5 = r >> 3; g5 = g >> 3; b5 = b >> 3
    return (((r5 & 1) << 14) | ((r5 >> 1) << 8) |
            ((g5 & 1) << 13) | ((g5 >> 1) << 4) |
            ((b5 & 1) << 12) | ((b5 >> 1) << 0))


# ─── Frame → VRAM commands ──────────────────────────────────────────

def frame_to_vram_cmds(indexed_img, pal_map, bbox, canvas_anchor, tile_cache, c1_data, c2_data, next_tile_id):
    """Convert indexed frame to VRAM commands.

    pal_map: per-pixel palette ID (1 or 2).
    canvas_anchor: (anchor_x, anchor_y) — the canvas position that maps to screen center.
    """
    x0, y0, x1, y1 = bbox
    content = indexed_img[y0:y1+1, x0:x1+1]
    content_pal = pal_map[y0:y1+1, x0:x1+1]
    ch, cw = content.shape

    pad_w = ((cw + 15) // 16) * 16
    pad_h = ((ch + 15) // 16) * 16
    padded = np.zeros((pad_h, pad_w), dtype=np.uint8)
    padded[:ch, :cw] = content
    padded_pal = np.ones((pad_h, pad_w), dtype=np.uint8)
    padded_pal[:ch, :cw] = content_pal

    n_cols = pad_w // 16
    n_rows = pad_h // 16

    # Screen position: anchor maps to fixed screen point
    SCREEN_AX = 160  # screen X for the canvas anchor
    SCREEN_AY = 200  # screen Y for the canvas anchor (feet level)
    ca_x, ca_y = canvas_anchor

    # Content bbox top-left in canvas coords → screen coords
    anchor_x = SCREEN_AX + (x0 - ca_x)
    anchor_y = SCREEN_AY + (y0 - ca_y)

    cmds = []
    for spr in range(1, 33):
        cmds.append((0x8200 + spr, 0x0000))

    sprite_slot = 1
    for col in range(n_cols):
        if sprite_slot > 32:
            break

        scb1_base = sprite_slot * 64
        for row in range(n_rows):
            tile_pixels = padded[row*16:(row+1)*16, col*16:(col+1)*16]
            tile_pals = padded_pal[row*16:(row+1)*16, col*16:(col+1)*16]
            key = tile_pixels.tobytes()

            if np.all(tile_pixels == 0):
                tid = 0
                pal_id = 1
            else:
                if key in tile_cache:
                    tid = tile_cache[key]
                else:
                    tid = next_tile_id
                    tile_cache[key] = tid
                    tc1, tc2 = encode_crom_tile(tile_pixels)
                    c1_data.extend(tc1)
                    c2_data.extend(tc2)
                    next_tile_id += 1
                # Majority palette for this tile
                visible = tile_pals[tile_pixels > 0]
                if len(visible) > 0:
                    pal_id = 2 if np.sum(visible == 2) > np.sum(visible == 1) else 1
                else:
                    pal_id = 1

            cmds.append((scb1_base + row * 2, tid & 0xFFFF))
            cmds.append((scb1_base + row * 2 + 1, pal_id << 8))

        for row in range(n_rows, 32):
            cmds.append((scb1_base + row * 2, 0))
            cmds.append((scb1_base + row * 2 + 1, 0))

        cmds.append((0x8000 + sprite_slot, 0x0FFF))

        y_screen = anchor_y
        y_val = (496 - y_screen) & 0x1FF
        sticky = (1 << 6) if col > 0 else 0
        cmds.append((0x8200 + sprite_slot, (y_val << 7) | sticky | (n_rows & 0x3F)))

        if col == 0:
            cmds.append((0x8400 + sprite_slot, (anchor_x & 0x1FF) << 7))

        sprite_slot += 1

    return cmds, next_tile_id


# ─── Process Aseprite files ─────────────────────────────────────────

def _load_dual_palettes(aseprite_paths):
    """Try to load body + accessory palettes from the game config.

    Looks at the Aseprite path to find the character, then reads both
    palettes from the KOF95 ROM via the game config JSON.
    Falls back to Aseprite-embedded palette if config not found.
    """
    # Try to detect character from path (e.g. "09_iori_yagami")
    import re
    for path in aseprite_paths:
        m = re.search(r'(\d{2})_', os.path.basename(os.path.dirname(path)))
        if m:
            char_id = int(m.group(1))
            break
    else:
        return None, None

    # Try loading from game config
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", "kof95.json")
    if not os.path.exists(config_path):
        return None, None

    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "neogeo"))
        from sprite_decode import read_palette
        from rom_loader import load_prom
        import json as _json

        with open(config_path) as f:
            cfg = _json.load(f)
        prom = load_prom(cfg)

        for ch in cfg['characters']:
            if ch['char_id'] == char_id:
                pals = ch.get('palettes', {})
                body = read_palette(prom, int(pals['v0_body'], 16)) if 'v0_body' in pals else None
                acc = read_palette(prom, int(pals['v1_acc'], 16)) if 'v1_acc' in pals else None
                if body and acc:
                    print(f"  Loaded ROM palettes for char {char_id} (body + acc)")
                    return body, acc
    except Exception as e:
        print(f"  Warning: couldn't load ROM palettes: {e}")

    return None, None


def process_aseprite_files(aseprite_paths):
    print("Loading Aseprite files...")

    tile_cache = {}
    c1_data = bytearray(64)  # tile 0 = transparent
    c2_data = bytearray(64)
    next_tile_id = 1

    # Try loading dual palettes from ROM
    body_pal_rgb, acc_pal_rgb = _load_dual_palettes(aseprite_paths)

    # Build color→(palette_id, index) maps
    body_rgb_map = {}  # RGB → index (palette 1)
    acc_rgb_map = {}   # RGB → index (palette 2)
    has_dual = body_pal_rgb is not None and acc_pal_rgb is not None

    if has_dual:
        for i, (r, g, b) in enumerate(body_pal_rgb):
            if i > 0:
                body_rgb_map[(r, g, b)] = i
        for i, (r, g, b) in enumerate(acc_pal_rgb):
            if i > 0:
                acc_rgb_map[(r, g, b)] = i
    else:
        # Fallback: use Aseprite palette
        first_pal = None

    animations = []

    for path in aseprite_paths:
        name = os.path.splitext(os.path.basename(path))[0]
        print(f"  Processing {name}...")

        w, h, pal, frames = parse_aseprite(path)

        if not has_dual and not body_rgb_map:
            # Fallback: single palette from Aseprite
            for i, (r, g, b, a) in enumerate(pal):
                if i > 0:
                    body_rgb_map[(r, g, b)] = i

        # Detect scale factor: Aseprite files from extraction use scale=2
        test_img = frames[0]['image']
        test_alpha = test_img[:, :, 3]
        tr = np.any(test_alpha > 0, axis=1)
        tc = np.any(test_alpha > 0, axis=0)
        if tr.any():
            content_h = np.where(tr)[0][-1] - np.where(tr)[0][0] + 1
            scale = 2 if content_h > 150 else 1
        else:
            scale = 1
        if scale > 1:
            print(f"    Detected scale={scale}, downsampling...")

        # Canvas anchor: the point on the (downscaled) canvas that represents
        # the character's feet/ground position. We use the center-bottom of
        # the canvas content across all frames for consistency.
        all_bottoms = []
        all_centers_x = []
        for frame in frames:
            fimg = frame['image']
            if scale > 1:
                fimg = fimg[::scale, ::scale]
            fa = fimg[:, :, 3]
            fr = np.any(fa > 0, axis=1)
            fc = np.any(fa > 0, axis=0)
            if fr.any():
                all_bottoms.append(np.where(fr)[0][-1])
                cx0, cx1 = np.where(fc)[0][[0, -1]]
                all_centers_x.append((cx0 + cx1) // 2)
        # Anchor = center X across frames, max bottom Y (feet)
        canvas_anchor = (
            int(np.median(all_centers_x)) if all_centers_x else w // (2 * scale),
            max(all_bottoms) if all_bottoms else h // scale
        )

        anim_frames = []
        for fi, frame in enumerate(frames):
            img = frame['image']

            # Downsample if needed (take every Nth pixel)
            if scale > 1:
                img = img[::scale, ::scale]

            sh, sw = img.shape[:2]
            alpha = img[:, :, 3]

            rows_mask = np.any(alpha > 0, axis=1)
            cols_mask = np.any(alpha > 0, axis=0)
            if not rows_mask.any():
                continue

            r0, r1 = np.where(rows_mask)[0][[0, -1]]
            c0, c1 = np.where(cols_mask)[0][[0, -1]]

            indexed = np.zeros((sh, sw), dtype=np.uint8)
            pal_map = np.ones((sh, sw), dtype=np.uint8)  # 1=body, 2=acc

            for y in range(r0, r1 + 1):
                for x in range(c0, c1 + 1):
                    px = img[y, x]
                    if px[3] == 0:
                        continue
                    key = (int(px[0]), int(px[1]), int(px[2]))

                    if key in body_rgb_map:
                        indexed[y, x] = body_rgb_map[key]
                        pal_map[y, x] = 1
                    elif has_dual and key in acc_rgb_map:
                        indexed[y, x] = acc_rgb_map[key]
                        pal_map[y, x] = 2
                    else:
                        indexed[y, x] = rgba_to_palette_index(px, body_rgb_map)
                        pal_map[y, x] = 1

            cmds, next_tile_id = frame_to_vram_cmds(
                indexed, pal_map, (c0, r0, c1, r1), canvas_anchor,
                tile_cache, c1_data, c2_data, next_tile_id)
            anim_frames.append(cmds)

        animations.append({'name': name, 'frames': anim_frames})
        print(f"    {len(anim_frames)} frames")

    print(f"  Total unique tiles: {next_tile_id}")

    # Build Neo Geo palettes — use raw ROM values when available for exact colors
    neo_body_pal = [0x0000]
    neo_acc_pal = [0x0000]
    if has_dual:
        # Try to get raw Neo Geo color words directly from ROM
        raw_body = _load_raw_palette(aseprite_paths)
        if raw_body:
            neo_body_pal = [0x0000] + raw_body[0][1:16]
            neo_acc_pal = [0x0000] + raw_body[1][1:16]
        else:
            for i in range(1, 16):
                r, g, b = body_pal_rgb[i]
                neo_body_pal.append(rgb_to_neogeo(r, g, b))
                r, g, b = acc_pal_rgb[i]
                neo_acc_pal.append(rgb_to_neogeo(r, g, b))
    else:
        w2, h2, pal, _ = parse_aseprite(aseprite_paths[0])
        for i in range(1, 16):
            if i < len(pal):
                r, g, b, a = pal[i]
                neo_body_pal.append(rgb_to_neogeo(r, g, b))
            else:
                neo_body_pal.append(0x0000)
        neo_acc_pal = list(neo_body_pal)

    return animations, bytes(c1_data), bytes(c2_data), neo_body_pal, neo_acc_pal


def _load_raw_palette(aseprite_paths):
    """Load raw 16-bit Neo Geo color words from ROM for exact palette reproduction."""
    import re
    for path in aseprite_paths:
        m = re.search(r'(\d{2})_', os.path.basename(os.path.dirname(path)))
        if m:
            char_id = int(m.group(1))
            break
    else:
        return None

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", "kof95.json")
    if not os.path.exists(config_path):
        return None

    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "neogeo"))
        from sprite_decode import r16
        from rom_loader import load_prom
        import json as _json

        with open(config_path) as f:
            cfg = _json.load(f)
        prom = load_prom(cfg)

        for ch in cfg['characters']:
            if ch['char_id'] == char_id:
                pals = ch.get('palettes', {})
                body_addr = int(pals['v0_body'], 16)
                acc_addr = int(pals['v1_acc'], 16)
                # Read raw 16-bit words directly
                body_raw = [r16(prom, body_addr + i * 2) for i in range(16)]
                acc_raw = [r16(prom, acc_addr + i * 2) for i in range(16)]
                return body_raw, acc_raw
    except Exception:
        pass
    return None


# ─── Generate anim_data.h ───────────────────────────────────────────

def _c_ident(name):
    """Convert a filename to a valid C identifier (uppercase)."""
    import re
    ident = re.sub(r'[^a-zA-Z0-9]', '_', name).upper()
    if ident[0].isdigit():
        ident = '_' + ident
    return ident


def generate_anim_header(animations, neo_body_pal, neo_acc_pal, out_path):
    """Generate C header with animation data and named references."""
    with open(out_path, 'w') as f:
        f.write("/* Auto-generated animation data — do not edit */\n")
        f.write("#ifndef ANIM_DATA_H\n#define ANIM_DATA_H\n\n")
        f.write("#include \"neogeo.h\"\n\n")

        f.write(f"#define NUM_ANIMATIONS {len(animations)}\n\n")

        # Named animation indices
        f.write("/* Animation indices — use with ANIMATIONS[] */\n")
        for ai, anim in enumerate(animations):
            ident = _c_ident(anim['name'])
            f.write(f"#define ANIM_{ident}  {ai}\n")
        f.write("\n")

        # Palettes (body = palette 1, accessory = palette 2)
        f.write("static const uint16_t PALETTE[16] = {\n    ")
        f.write(", ".join(f"0x{c:04X}" for c in neo_body_pal))
        f.write("\n};\n\n")
        f.write("static const uint16_t PALETTE_ACC[16] = {\n    ")
        f.write(", ".join(f"0x{c:04X}" for c in neo_acc_pal))
        f.write("\n};\n\n")

        # Per-frame VRAM command arrays
        for ai, anim in enumerate(animations):
            for fi, cmds in enumerate(anim['frames']):
                f.write(f"static const vram_cmd_t anim{ai}_frame{fi}_cmds[] = {{\n")
                for j, (addr, data) in enumerate(cmds):
                    comma = "," if j < len(cmds) - 1 else ""
                    f.write(f"    {{0x{addr:04X}, 0x{data:04X}}}{comma}\n")
                f.write("};\n")

        # Per-animation frame arrays
        for ai, anim in enumerate(animations):
            f.write(f"\nstatic const anim_frame_t anim{ai}_frames[] = {{\n")
            for fi, cmds in enumerate(anim['frames']):
                comma = "," if fi < len(anim['frames']) - 1 else ""
                f.write(f"    {{{len(cmds)}, anim{ai}_frame{fi}_cmds}}{comma}\n")
            f.write("};\n")

        # Top-level animation table
        f.write("\nstatic const animation_t ANIMATIONS[] = {\n")
        for ai, anim in enumerate(animations):
            comma = "," if ai < len(animations) - 1 else ""
            f.write(f"    {{{len(anim['frames'])}, anim{ai}_frames}}{comma}  /* {anim['name']} */\n")
        f.write("};\n\n")

        # Frame count helpers
        f.write("/* Frame counts per animation */\n")
        for ai, anim in enumerate(animations):
            ident = _c_ident(anim['name'])
            f.write(f"#define ANIM_{ident}_FRAMES  {len(anim['frames'])}\n")
        f.write("\n")

        f.write("#endif /* ANIM_DATA_H */\n")


# ─── Build P ROM (C + ASM) ──────────────────────────────────────────

def build_prom(animations, neo_body_pal, neo_acc_pal):
    """Compile C game code + ASM bootstrap, link into P ROM binary."""
    os.makedirs(BUILD_DIR, exist_ok=True)

    # Generate animation data header
    anim_h_path = os.path.join(BUILD_DIR, "anim_data.h")
    generate_anim_header(animations, neo_body_pal, neo_acc_pal, anim_h_path)

    # Compile crt0.s
    print("Assembling crt0.s...")
    crt0_o = os.path.join(BUILD_DIR, "crt0.o")
    r = subprocess.run([
        CC, "-m68000", "-nostdlib", "-c",
        os.path.join(HOMEBREW_DIR, "crt0.s"),
        "-o", crt0_o
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"crt0.s assembly failed:\n{r.stderr}")
        return None

    # Compile game.c
    print("Compiling game.c...")
    game_o = os.path.join(BUILD_DIR, "game.o")
    r = subprocess.run([
        CC, "-m68000", "-O2", "-ffreestanding", "-nostdlib",
        "-I", HOMEBREW_DIR,
        "-I", BUILD_DIR,  # for anim_data.h
        "-c", os.path.join(HOMEBREW_DIR, "game.c"),
        "-o", game_o
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"game.c compilation failed:\n{r.stderr}")
        return None

    # Link
    print("Linking...")
    elf_path = os.path.join(BUILD_DIR, "rom.elf")
    r = subprocess.run([
        LD, "-T", os.path.join(HOMEBREW_DIR, "neogeo.ld"),
        "-o", elf_path,
        crt0_o, game_o
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Linking failed:\n{r.stderr}")
        return None

    # Extract raw binary
    bin_path = os.path.join(BUILD_DIR, "rom.bin")
    r = subprocess.run([
        OBJCOPY, "-O", "binary", elf_path, bin_path
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"objcopy failed:\n{r.stderr}")
        return None

    with open(bin_path, "rb") as f:
        code = f.read()

    print(f"  Binary size: {len(code)} bytes")

    # Pad to 1MB P ROM
    prom = bytearray(0x100000)
    if len(code) > len(prom):
        print(f"  ERROR: Code too large ({len(code)} > {len(prom)})")
        return None
    prom[:len(code)] = code

    # Byte-swap for MAME load16_word_swap
    for i in range(0, len(prom), 2):
        prom[i], prom[i + 1] = prom[i + 1], prom[i]

    return bytes(prom)


# ─── Checksums & softlist ───────────────────────────────────────────

def compute_checksums(data):
    crc = "%08x" % (zlib.crc32(data) & 0xFFFFFFFF)
    sha1 = hashlib.sha1(data).hexdigest()
    return crc, sha1


def build_softlist_xml(rom_files):
    xml = '<?xml version="1.0"?>\n'
    xml += '<!DOCTYPE softwarelist SYSTEM "softwarelist.dtd">\n'
    xml += '<softwarelist name="neogeo" description="Neo Geo cartridges">\n'
    xml += '\t<software name="clarkdemo">\n'
    xml += '\t\t<description>Sprite Viewer</description>\n'
    xml += '\t\t<year>2025</year>\n'
    xml += '\t\t<publisher>Homebrew</publisher>\n'
    xml += '\t\t<part name="cart" interface="neo_cart">\n'

    for area_name, roms in rom_files.items():
        extra = roms.get('extra_attrs', '')
        xml += f'\t\t\t<dataarea name="{area_name}"{extra} size="{roms["size"]}">\n'
        for rom in roms['files']:
            loadflag = rom.get('loadflag', '')
            offset = rom.get('offset', '0x000000')
            lf_attr = f' loadflag="{loadflag}"' if loadflag else ''
            xml += f'\t\t\t\t<rom name="{rom["name"]}" size="{rom["fsize"]}" '
            xml += f'crc="{rom["crc"]}" sha1="{rom["sha1"]}" '
            xml += f'offset="{offset}"{lf_attr}/>\n'
        xml += '\t\t\t</dataarea>\n'

    xml += '\t\t</part>\n'
    xml += '\t</software>\n'
    xml += '</softwarelist>\n'
    return xml


# ─── Z80 M ROM builder ──────────────────────────────────────────────

def _build_mrom(mrom):
    PORT_FROM_68K = 0x00
    PORT_YM2610_A_ADDR = 0x04
    PORT_YM2610_A_VAL = 0x05
    PORT_ENABLE_NMI = 0x08
    PORT_TO_68K = 0x0C

    pc = 0
    mrom[pc] = 0xF3; pc += 1
    mrom[pc] = 0xC3; pc += 1; mrom[pc] = 0x00; pc += 1; mrom[pc] = 0x01; pc += 1

    pc = 0x38
    mrom[pc] = 0xF3; pc += 1; mrom[pc] = 0xF5; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x27; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM2610_A_ADDR; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x3A; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM2610_A_VAL; pc += 1
    mrom[pc] = 0xF1; pc += 1; mrom[pc] = 0xFB; pc += 1
    mrom[pc] = 0xED; pc += 1; mrom[pc] = 0x4D; pc += 1

    pc = 0x66
    mrom[pc] = 0xF5; pc += 1
    mrom[pc] = 0xDB; pc += 1; mrom[pc] = PORT_FROM_68K; pc += 1
    mrom[pc] = 0xF6; pc += 1; mrom[pc] = 0x80; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_TO_68K; pc += 1
    mrom[pc] = 0xF1; pc += 1
    mrom[pc] = 0xED; pc += 1; mrom[pc] = 0x45; pc += 1

    pc = 0x100
    mrom[pc] = 0x31; pc += 1; mrom[pc] = 0xFF; pc += 1; mrom[pc] = 0xFF; pc += 1
    mrom[pc] = 0xED; pc += 1; mrom[pc] = 0x56; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_ENABLE_NMI; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x26; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM2610_A_ADDR; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0xFF; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM2610_A_VAL; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x27; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM2610_A_ADDR; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x3A; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM2610_A_VAL; pc += 1
    mrom[pc] = 0xFB; pc += 1
    mainloop = pc
    mrom[pc] = 0x76; pc += 1
    mrom[pc] = 0xC3; pc += 1
    mrom[pc] = mainloop & 0xFF; pc += 1
    mrom[pc] = (mainloop >> 8) & 0xFF; pc += 1


# ─── Main ───────────────────────────────────────────────────────────

def main():
    # Parse arguments: .aseprite files and .wav files
    aseprite_files = []
    wav_files = []

    if len(sys.argv) < 2:
        base = "output/kof95/sprites/09_iori_yagami"
        aseprite_files = [
            os.path.join(base, "idle.aseprite"),
            os.path.join(base, "walk_fwd.aseprite"),
            os.path.join(base, "walk_back.aseprite"),
            os.path.join(base, "state_016.aseprite"),
            os.path.join(base, "state_028.aseprite"),
        ]
        # Auto-detect WAV files in homebrew/sounds/
        sounds_dir = os.path.join(HOMEBREW_DIR, "sounds")
        if os.path.isdir(sounds_dir):
            wav_files = sorted(globmod.glob(os.path.join(sounds_dir, "*.wav")))
    else:
        for f in sys.argv[1:]:
            if f.lower().endswith('.aseprite'):
                aseprite_files.append(f)
            elif f.lower().endswith('.wav'):
                wav_files.append(f)

    for f in aseprite_files + wav_files:
        if not os.path.exists(f):
            print(f"ERROR: File not found: {f}")
            return

    print("=== Neo Geo ROM Builder (Aseprite + C + Audio) ===\n")

    animations, c1_raw, c2_raw, neo_body_pal, neo_acc_pal = process_aseprite_files(aseprite_files)

    prom_data = build_prom(animations, neo_body_pal, neo_acc_pal)
    if prom_data is None:
        return

    # Pad C ROMs to 1MB
    crom_size = 0x100000
    c1 = c1_raw + b'\x00' * (crom_size - len(c1_raw))
    c2 = c2_raw + b'\x00' * (crom_size - len(c2_raw))

    srom = b'\x00' * 0x20000

    # Audio: build V ROM and sound driver
    from audio import build_vrom, build_sound_driver
    if wav_files:
        print("\nBuilding audio...")
        vrom_data, sample_table = build_vrom(wav_files)
        mrom = build_sound_driver(sample_table)
        vrom = vrom_data
        print(f"  {len(wav_files)} samples encoded, {len(sample_table)} in driver table")
    else:
        print("\n  No WAV files — using silent V ROM")
        mrom = bytearray(0x20000)
        _build_mrom(mrom)
        mrom = bytes(mrom)
        vrom = b'\x80' * 0x80000

    prom_512k = prom_data[:0x80000]

    rom_files = {
        'maincpu': {'extra_attrs': ' width="16" endianness="big"', 'size': '0x080000', 'files': [
            {'name': '999-p1.p1', 'fsize': len(prom_512k),
             'loadflag': 'load16_word_swap',
             **dict(zip(['crc','sha1'], compute_checksums(prom_512k)))}
        ]},
        'fixed': {'size': '0x020000', 'files': [
            {'name': '999-s1.s1', 'fsize': len(srom),
             **dict(zip(['crc','sha1'], compute_checksums(srom)))}
        ]},
        'audiocpu': {'size': '0x020000', 'files': [
            {'name': '999-m1.m1', 'fsize': len(mrom),
             **dict(zip(['crc','sha1'], compute_checksums(mrom)))}
        ]},
        'ymsnd:adpcma': {'size': '0x080000', 'files': [
            {'name': '999-v1.v1', 'fsize': len(vrom),
             **dict(zip(['crc','sha1'], compute_checksums(vrom)))}
        ]},
        'sprites': {'size': '0x200000', 'files': [
            {'name': '999-c1.c1', 'fsize': len(c1),
             'loadflag': 'load16_byte', 'offset': '0x000000',
             **dict(zip(['crc','sha1'], compute_checksums(c1)))},
            {'name': '999-c2.c2', 'fsize': len(c2),
             'loadflag': 'load16_byte', 'offset': '0x000001',
             **dict(zip(['crc','sha1'], compute_checksums(c2)))},
        ]},
    }

    xml = build_softlist_xml(rom_files)
    os.makedirs("hash", exist_ok=True)
    with open("hash/neogeo.xml", "w") as f:
        f.write(xml)

    out = "clarkdemo.zip"
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("999-p1.p1", prom_512k)
        zf.writestr("999-s1.s1", srom)
        zf.writestr("999-m1.m1", mrom)
        zf.writestr("999-v1.v1", vrom)
        zf.writestr("999-c1.c1", c1)
        zf.writestr("999-c2.c2", c2)

    os.makedirs("roms", exist_ok=True)
    shutil.copy(out, "roms/clarkdemo.zip")

    print(f"\n  Built: {out}")
    print(f"  Softlist: hash/neogeo.xml (checksums updated)")
    print(f"  {len(animations)} animations:")
    for a in animations:
        print(f"    {a['name']}: {len(a['frames'])} frames")
    print(f"\n  Run: ./launch_clarkdemo.sh")


if __name__ == "__main__":
    main()
