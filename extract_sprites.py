#!/usr/bin/env python3
"""Unified Neo Geo sprite extractor.

Usage:
    python extract_sprites.py kof95          # extract all characters
    python extract_sprites.py kof94 --char 2 # extract one character
    python extract_sprites.py kof95 --no-aseprite  # PNG only

Reads game config from games/{game_id}.json.
Outputs to output/{game_id}/sprites/
"""

import argparse, json, os, sys, struct, zlib
import numpy as np
from PIL import Image
from collections import defaultdict

from neogeo.rom_loader import load_prom, load_sprite_rom
from neogeo.sprite_decode import r16, r32, rs16, read_palette
from neogeo.animation import parse_animation, follow_fragment_chain
from neogeo.renderer import render_frame, render_sdef

# ─── Aseprite format helpers ────────────────────────────────────────

def ase_string(s):
    encoded = s.encode('utf-8')
    return struct.pack('<H', len(encoded)) + encoded

def ase_chunk(chunk_type, data):
    return struct.pack('<IH', 6 + len(data), chunk_type) + data

def ase_frame(duration_ms, chunks):
    chunk_data = b''.join(chunks)
    n = len(chunks)
    return struct.pack('<IHHHBBI',
        16 + len(chunk_data), 0xF1FA, n, duration_ms, 0, 0, 0
    ) + chunk_data

def make_layer_chunk(name, flags=3, opacity=255):
    data = struct.pack('<HHHHHHBbbb', flags, 0, 0, 0, 0, 0, opacity, 0, 0, 0)
    data += ase_string(name)
    return ase_chunk(0x2004, data)

def make_cel_chunk(layer_idx, x, y, width, height, pixel_data, opacity=255):
    header = struct.pack('<HhhBH', layer_idx, x, y, opacity, 2)
    header += struct.pack('<h', 0) + b'\x00' * 5  # z-index + reserved (v1.3)
    compressed = zlib.compress(bytes(pixel_data), 6)
    return ase_chunk(0x2005, header + struct.pack('<HH', width, height) + compressed)

def make_tags_chunk(tags):
    data = struct.pack('<H', len(tags)) + b'\x00' * 8
    for name, fr, to, loop_dir in tags:
        data += struct.pack('<HHB', fr, to, loop_dir)
        data += struct.pack('<H', 0) + b'\x00' * 6
        data += bytes([0x80, 0x80, 0x80, 0x00])
        data += ase_string(name)
    return ase_chunk(0x2018, data)

def make_palette_chunk(palette_rgb):
    n = len(palette_rgb)
    data = struct.pack('<III', n, 0, n - 1) + b'\x00' * 8
    for r, g, b in palette_rgb:
        data += struct.pack('<HBBBB', 0, r, g, b, 255)
    return ase_chunk(0x2019, data)

def make_color_profile_chunk():
    data = struct.pack('<HH', 1, 0) + struct.pack('<I', 0) + b'\x00' * 8
    return ase_chunk(0x2007, data)

# ─── Main extraction ───────────────────────────────────────────────

def extract_game(config, char_filter=None, do_aseprite=True, do_png=True):
    game_id = config["game_id"]
    bitmask_mode = config.get("bitmask_mode", "auto")
    state_names = config.get("state_names", {})
    state_table_base = int(config["state_table_base"], 16)
    sdef_table_base = int(config["sdef_table_base"], 16)

    print(f"Loading {config['title']}...")
    prom = load_prom(config)
    spr_data = load_sprite_rom(config)

    out_base = os.path.join("output", game_id, "sprites")
    os.makedirs(out_base, exist_ok=True)

    total_frames = 0

    for char_info in config["characters"]:
        char_id = char_info["char_id"]
        name = char_info["name"]
        safe = name.replace(" ", "_").replace("!", "").lower()

        if char_filter is not None and char_id != char_filter:
            continue

        # Load palettes
        pal_data = char_info.get("palettes")
        if pal_data and isinstance(pal_data, dict):
            p1_body = read_palette(prom, int(pal_data["v0_body"], 16))
            p1_acc = read_palette(prom, int(pal_data["v1_acc"], 16))
            p2_body = read_palette(prom, int(pal_data["v16_body"], 16))
            p2_acc = read_palette(prom, int(pal_data["v17_acc"], 16))
        else:
            # Fallback: grayscale
            gray = [(0, 0, 0)] + [(int(i * 255 / 15),) * 3 for i in range(1, 16)]
            p1_body = p1_acc = p2_body = p2_acc = gray

        # Read character tables
        char_state_table = r32(prom, state_table_base + char_id * 4)
        sdef_table = r32(prom, sdef_table_base + char_id * 4)
        if char_state_table < 0x080000 or char_state_table >= 0x200000:
            continue
        if sdef_table < 0x080000 or sdef_table >= 0x200000:
            continue

        # Collect all unique frames
        all_frames = []  # (state_id, duration, parts)
        seen_sigs = set()
        state_frame_counts = {}

        for state_id in range(256):
            state_addr = r32(prom, char_state_table + state_id * 4)
            if state_addr == 0 or state_addr < 0x080000 or state_addr >= 0x200000:
                continue
            anim_frames = parse_animation(prom, state_addr)
            if not anim_frames:
                continue

            # Detect loop type from terminator
            loops = False
            pos = state_addr
            for _ in range(200):
                if pos + 2 > len(prom): break
                w = r16(prom, pos)
                if w == 0xFF00: loops = True; break
                if w == 0xFE00: loops = False; break
                pos += 6

            state_count = 0
            for duration, frag_addr, anim_flags in anim_frames:
                if frag_addr < 0x080000 or frag_addr >= 0x200000:
                    continue
                parts = follow_fragment_chain(prom, frag_addr, sdef_table, bitmask_mode)
                if not parts:
                    continue

                sig = tuple((p[0], p[1], p[2]["base_tile"], p[2]["cols"],
                             p[2]["tiles_per_col"]) for p in parts)
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)

                all_frames.append((state_id, duration, parts, loops))
                state_count += 1

            if state_count:
                state_frame_counts[state_id] = state_count

        if not all_frames:
            continue

        # Render all frames with P1 palette
        rendered = []
        for state_id, duration, parts, loops in all_frames:
            img = render_frame(spr_data, parts, p1_body, p1_acc)
            if img is not None:
                rendered.append((state_id, duration, img, parts, loops))

        if not rendered:
            continue

        total_frames += len(rendered)
        char_dir = os.path.join(out_base, f"{char_id:02d}_{safe}")
        os.makedirs(char_dir, exist_ok=True)

        # ── PNG Atlas ──
        if do_png:
            all_imgs = [img for _, _, img, _, _ in rendered]
            cols_atlas = min(10, len(all_imgs))
            rows_atlas = (len(all_imgs) + cols_atlas - 1) // cols_atlas
            max_fw = max(im.shape[1] for im in all_imgs)
            max_fh = max(im.shape[0] for im in all_imgs)
            gap = 4
            atlas = np.zeros((rows_atlas * (max_fh + gap), cols_atlas * (max_fw + gap), 4), dtype=np.uint8)
            for i, im in enumerate(all_imgs):
                c = i % cols_atlas; r = i // cols_atlas
                fh, fw = im.shape[:2]
                atlas[r * (max_fh + gap):r * (max_fh + gap) + fh,
                      c * (max_fw + gap):c * (max_fw + gap) + fw] = im
            Image.fromarray(atlas, "RGBA").save(os.path.join(out_base, f"{char_id:02d}_{safe}_atlas.png"))

            # P2 alt atlas
            if p2_body != p1_body or p2_acc != p1_acc:
                imgs2 = []
                for _, _, _, parts, _ in rendered:
                    img2 = render_frame(spr_data, parts, p2_body, p2_acc)
                    if img2 is not None:
                        imgs2.append(img2)
                if imgs2:
                    atlas2 = np.zeros_like(atlas)
                    for i, im in enumerate(imgs2):
                        c = i % cols_atlas; r = i // cols_atlas
                        fh, fw = im.shape[:2]
                        atlas2[r * (max_fh + gap):r * (max_fh + gap) + fh,
                              c * (max_fw + gap):c * (max_fw + gap) + fw] = im
                    Image.fromarray(atlas2, "RGBA").save(
                        os.path.join(out_base, f"{char_id:02d}_{safe}_atlas_alt.png"))

        # ── Aseprite files (per state) ──
        if do_aseprite:
            # Group frames by state
            by_state = defaultdict(list)
            for state_id, duration, img, parts, loops in rendered:
                duration_ms = max(16, int(duration * 1000 / 60))
                by_state[state_id].append((duration_ms, img, loops))

            # Global bounding box for consistent canvas across all states
            g_min_y = g_min_x = 0
            g_max_y = g_max_x = 0
            for _, _, img, _, _ in rendered:
                g_max_y = max(g_max_y, img.shape[0])
                g_max_x = max(g_max_x, img.shape[1])
            canvas_w = g_max_x
            canvas_h = g_max_y

            for state_id, frame_list in sorted(by_state.items()):
                sname = state_names.get(str(state_id), f"state_{state_id:03d}")
                n_frames = len(frame_list)
                loop_dir = 0  # forward

                # Metadata chunks (frame 0 only)
                meta = [make_color_profile_chunk(), make_palette_chunk(p1_body)]
                meta.append(make_layer_chunk("Sprite"))
                meta.append(make_tags_chunk([(sname, 0, n_frames - 1, loop_dir)]))

                all_frame_data = []
                for fi, (dur_ms, img, loops) in enumerate(frame_list):
                    chunks = list(meta) if fi == 0 else []
                    h, w = img.shape[:2]
                    rgba_bytes = img.tobytes()
                    chunks.append(make_cel_chunk(0, 0, canvas_h - h, w, h, rgba_bytes))
                    all_frame_data.append(ase_frame(dur_ms, chunks))

                # Write file
                header = bytearray(128)
                frame_data = b''.join(all_frame_data)
                file_size = 128 + len(frame_data)
                struct.pack_into('<I', header, 0, file_size)
                struct.pack_into('<H', header, 4, 0xA5E0)
                struct.pack_into('<H', header, 6, n_frames)
                struct.pack_into('<H', header, 8, canvas_w)
                struct.pack_into('<H', header, 10, canvas_h)
                struct.pack_into('<H', header, 12, 32)  # RGBA
                struct.pack_into('<I', header, 14, 1)
                struct.pack_into('<H', header, 18, 100)
                struct.pack_into('<H', header, 32, 16)

                out_path = os.path.join(char_dir, f"{sname}.aseprite")
                with open(out_path, "wb") as f:
                    f.write(bytes(header) + frame_data)

        print(f"  [{char_id:2d}] {name:20s}: {len(rendered):4d} frames")

    print(f"\nDone! {total_frames} total frames → output/{game_id}/sprites/")


def main():
    parser = argparse.ArgumentParser(description="Neo Geo sprite extractor")
    parser.add_argument("game", help="Game ID (e.g. kof95, kof94)")
    parser.add_argument("--char", type=int, default=None, help="Extract single character by ID")
    parser.add_argument("--no-aseprite", action="store_true", help="Skip Aseprite generation")
    parser.add_argument("--no-png", action="store_true", help="Skip PNG atlas generation")
    args = parser.parse_args()

    config_path = os.path.join("games", f"{args.game}.json")
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    extract_game(config, char_filter=args.char,
                 do_aseprite=not args.no_aseprite,
                 do_png=not args.no_png)


if __name__ == "__main__":
    main()
