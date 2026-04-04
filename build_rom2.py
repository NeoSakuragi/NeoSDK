#!/usr/bin/env python3
"""Build a minimal Neo Geo ROM — Clark Animation Viewer.
Uses vasm for 68k assembly, patches in sprite data from KOF95.
"""

import struct, os, json, zipfile, subprocess
import numpy as np
from extract_sprites_rom import (
    load_prom, load_sprite_rom, r16, r32, rs16,
    read_sprite_def, parse_animation, decode_tile,
)


def rgb_to_neogeo(r, g, b):
    r5 = r >> 3; g5 = g >> 3; b5 = b >> 3
    return (((r5 & 1) << 14) | ((r5 >> 1) << 8) |
            ((g5 & 1) << 13) | ((g5 >> 1) << 4) |
            ((b5 & 1) << 12) | ((b5 >> 1) << 0))


def encode_crom_tile(pixels_16x16):
    """Encode 16x16 4bpp pixels to Neo Geo C ROM (c1, c2) format."""
    c1 = bytearray(64)
    c2 = bytearray(64)
    for half_idx, x_start in enumerate([8, 0]):
        base_off = half_idx * 32
        for y in range(16):
            bp0 = bp1 = bp2 = bp3 = 0
            for x in range(8):
                ci = int(pixels_16x16[y, x_start + x])
                bp0 |= ((ci >> 0) & 1) << x
                bp1 |= ((ci >> 1) & 1) << x
                bp2 |= ((ci >> 2) & 1) << x
                bp3 |= ((ci >> 3) & 1) << x
            off = base_off + y * 2
            c1[off] = bp0; c1[off + 1] = bp2
            c2[off] = bp1; c2[off + 1] = bp3
    return bytes(c1), bytes(c2)


def extract_clark_data():
    """Extract 5 Clark animations, encode tiles, build VRAM command lists."""
    print("Loading KOF95 ROM...")
    prom = load_prom()
    spr_data = load_sprite_rom()
    with open("char_palettes_all.json") as f:
        pd = json.load(f)
    palette_rgb = [tuple(c) for c in pd["2"]["p1"]["rgb"]]

    char_id = 2
    char_state_table = r32(prom, 0x080000 + char_id * 4)
    sdef_table = r32(prom, 0x080080 + char_id * 4)

    target_states = [0, 1, 2, 11, 14]
    state_names = ["idle", "walk_fwd", "walk_back", "guard_air", "knockdown"]

    tile_cache = {}
    c1_data = bytearray(64)  # tile 0 = transparent
    c2_data = bytearray(64)
    next_tile_id = 1

    SCREEN_X = 160
    SCREEN_Y = 200

    animations = []

    for si, state_id in enumerate(target_states):
        state_addr = r32(prom, char_state_table + state_id * 4)
        if state_addr == 0 or state_addr < 0x080000:
            continue

        anim_frames = parse_animation(prom, state_addr)
        frames_vram = []

        for duration, frag_addr, anim_flags in anim_frames:
            if frag_addr < 0x080000 or frag_addr >= 0x200000:
                continue
            if frag_addr + 6 > len(prom):
                continue

            raw_parts = []
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
                    raw_parts.append((y_off, x_off, sdef))
                if not chain:
                    break
                frag_pos += 6

            if not raw_parts:
                continue

            # Build VRAM commands for this frame
            cmds = []
            # Clear sprites 1-16
            for spr in range(1, 17):
                cmds.append((0x8200 + spr, 0x0000))  # SCB3: hide

            sprite_slot = 1
            for part_i, (y_off, x_off, sdef) in enumerate(raw_parts):
                # swapXY
                cy, cx = x_off, y_off
                cols = sdef["cols"]
                tpc = sdef["tiles_per_col"]
                bitmasks = sdef["bitmasks"]
                base_tile = sdef["base_tile"]

                src_tc = base_tile
                for col_i in range(cols):
                    if sprite_slot > 16:
                        break
                    bm = bitmasks[col_i] if col_i < len(bitmasks) else 0xFF
                    bm_bits = 8

                    # SCB1: tile data
                    scb1_base = sprite_slot * 64
                    for tile_i in range(tpc):
                        visible = (bm >> (bm_bits - 1 - tile_i)) & 1 if tile_i < bm_bits else 1
                        if visible:
                            pixels = decode_tile(spr_data, src_tc)
                            key = pixels.tobytes()
                            if key not in tile_cache:
                                tile_cache[key] = next_tile_id
                                tc1, tc2 = encode_crom_tile(pixels)
                                c1_data += tc1
                                c2_data += tc2
                                next_tile_id += 1
                            tid = tile_cache[key]
                            src_tc += 1
                        else:
                            tid = 0

                        cmds.append((scb1_base + tile_i * 2, tid & 0xFFFF))
                        # Attr: palette 1 in bits 15-8
                        cmds.append((scb1_base + tile_i * 2 + 1, 0x0100))

                    # Blank remaining tile slots
                    for tile_i in range(tpc, 32):
                        cmds.append((scb1_base + tile_i * 2, 0))
                        cmds.append((scb1_base + tile_i * 2 + 1, 0))

                    # SCB2: full size
                    cmds.append((0x8000 + sprite_slot, 0x0FFF))

                    # SCB3: Y + sticky + height
                    y_screen = SCREEN_Y + cy
                    y_val = (496 - y_screen) & 0x1FF
                    sticky = (1 << 6) if col_i > 0 else 0
                    cmds.append((0x8200 + sprite_slot, (y_val << 7) | sticky | (tpc & 0x3F)))

                    # SCB4: X position (only first column per part)
                    if col_i == 0 and part_i == 0:
                        x_screen = SCREEN_X + cx
                        cmds.append((0x8400 + sprite_slot, (x_screen & 0x1FF) << 7))

                    sprite_slot += 1

            frames_vram.append(cmds)
            if len(frames_vram) >= 20:
                break

        animations.append({"name": state_names[si], "frames": frames_vram})
        print(f"  {state_names[si]}: {len(frames_vram)} frames")

    print(f"  Total unique tiles: {next_tile_id}")
    neo_palette = [rgb_to_neogeo(r, g, b) for r, g, b in palette_rgb]
    return animations, c1_data, c2_data, neo_palette


def build_prom(animations, neo_palette):
    """Assemble 68k code with vasm, then patch in data tables."""
    # Assemble
    print("Assembling 68k code...")
    result = subprocess.run(
        ["./vasmm68k_mot", "-Fbin", "-o", "/tmp/demo.bin", "-m68000", "demo.s"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Assembly failed:\n{result.stderr}")
        return None

    with open("/tmp/demo.bin", "rb") as f:
        code = f.read()

    # Create 2MB P ROM (like KOF95)
    # Neo Geo 2MB layout: code at $000000-$0FFFFF, data can go to $100000+
    prom = bytearray(0x100000)
    prom[:len(code)] = code

    # Patch palette at $8000
    for i, c in enumerate(neo_palette):
        struct.pack_into(">H", prom, 0x8000 + i * 2, c)

    # Patch anim_frame_counts at $8020
    for i, anim in enumerate(animations):
        struct.pack_into(">H", prom, 0x8020 + i * 2, len(anim["frames"]))

    # Patch frame_ptrs at $9000 and vram_cmd_data at $A000
    data_pos = 0xA000
    for ai, anim in enumerate(animations):
        for fi, cmds in enumerate(anim["frames"]):
            ptr_idx = ai * 20 + fi
            struct.pack_into(">I", prom, 0x9000 + ptr_idx * 4, data_pos)
            # Write command count
            struct.pack_into(">H", prom, data_pos, len(cmds))
            data_pos += 2
            # Write command pairs
            for vaddr, vdata in cmds:
                struct.pack_into(">H", prom, data_pos, vaddr & 0xFFFF)
                data_pos += 2
                struct.pack_into(">H", prom, data_pos, vdata & 0xFFFF)
                data_pos += 2

    print(f"  Code: {len(code)} bytes, Data ends at ${data_pos:06X}")

    # Byte-swap within each word for MAME load16_word_swap
    for i in range(0, len(prom), 2):
        prom[i], prom[i + 1] = prom[i + 1], prom[i]

    return bytes(prom)


def main():
    print("=== Neo Geo Clark Viewer ROM Builder ===\n")

    animations, c1_data, c2_data, neo_palette = extract_clark_data()

    prom = build_prom(animations, neo_palette)
    if prom is None:
        return

    # Pad C ROMs to 1MB
    size = 0x100000
    c1 = c1_data + b'\x00' * (size - len(c1_data))
    c2 = c2_data + b'\x00' * (size - len(c2_data))

    # S ROM: 128KB empty
    srom = b'\x00' * 0x20000
    # M ROM: 128KB halt loop
    mrom = bytearray(0x20000)
    mrom[0] = 0x76; mrom[1] = 0xC3; mrom[2] = 0; mrom[3] = 0
    # V ROM: 1MB silence
    vrom = b'\x80' * 0x100000

    # Package
    out = "clarkdemo.zip"
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("999-p1.p1", prom)
        zf.writestr("999-s1.s1", srom)
        zf.writestr("999-m1.m1", bytes(mrom))
        zf.writestr("999-v1.v1", vrom)
        zf.writestr("999-c1.c1", bytes(c1))
        zf.writestr("999-c2.c2", bytes(c2))

    import shutil
    shutil.copy(out, "roms/clarkdemo.zip")

    print(f"\n  Built: {out}")
    print(f"  {len(animations)} animations, {sum(len(a['frames']) for a in animations)} frames")
    for a in animations:
        print(f"    {a['name']}: {len(a['frames'])} frames")


if __name__ == "__main__":
    main()
