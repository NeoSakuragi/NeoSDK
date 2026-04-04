#!/usr/bin/env python3
"""Generate a LibreSprite/Aseprite .aseprite file for a KOF95 character.

Usage: .venv/bin/python3 generate_aseprite.py [char_id]
Default: char_id=2 (Clark Still)
"""

import struct, zlib, sys, os, json
import numpy as np
from extract_sprites_rom import (
    load_prom, load_sprite_rom, r16, r32, rs16,
    read_sprite_def, render_sdef, parse_animation,
    NAMES, STATE_NAMES,
)

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
        16 + len(chunk_data),  # frame size
        0xF1FA,                # magic
        n,                     # old chunk count
        duration_ms,
        0, 0,                  # reserved
        0,                     # new chunk count (0 = use old field)
    ) + chunk_data


def make_layer_chunk(name, flags=3, opacity=255):
    """flags: 1=visible, 2=editable, 3=both"""
    data = struct.pack('<HHHHHHBbbb',
        flags,     # flags
        0,         # type: normal
        0,         # child level
        0, 0,      # default w/h (ignored)
        0,         # blend mode: normal
        opacity,   # opacity
        0, 0, 0,   # reserved
    )
    data += ase_string(name)
    return ase_chunk(0x2004, data)


def make_cel_chunk(layer_idx, x, y, width, height, rgba_pixels, opacity=255):
    """Create compressed image cel. Aseprite 1.3 format."""
    header = struct.pack('<HhhBH',
        layer_idx, x, y, opacity,
        2,  # cel type: compressed image
    )
    header += struct.pack('<h', 0)  # z-index
    header += b'\x00' * 5          # reserved
    compressed = zlib.compress(bytes(rgba_pixels), 6)
    return ase_chunk(0x2005, header + struct.pack('<HH', width, height) + compressed)


def make_tags_chunk(tags):
    """tags: list of (name, from_frame, to_frame, loop_dir)
    loop_dir: 0=forward, 1=reverse, 2=ping-pong"""
    data = struct.pack('<H', len(tags))
    data += b'\x00' * 8  # reserved
    for name, fr, to, loop_dir in tags:
        data += struct.pack('<HHB', fr, to, loop_dir)
        data += struct.pack('<H', 0)  # repeat (0=infinite)
        data += b'\x00' * 6  # reserved
        data += bytes([0x80, 0x80, 0x80])  # tag color (gray)
        data += b'\x00'  # extra
        data += ase_string(name)
    return ase_chunk(0x2018, data)


def make_slice_chunk(name, pivot_x, pivot_y, canvas_w, canvas_h):
    """Single slice covering full canvas with pivot point."""
    data = struct.pack('<I', 1)   # 1 slice key
    data += struct.pack('<I', 2)  # flags: has pivot
    data += struct.pack('<I', 0)  # reserved
    data += ase_string(name)
    # Slice key for frame 0
    data += struct.pack('<I', 0)          # frame 0
    data += struct.pack('<ii', 0, 0)      # slice origin x, y
    data += struct.pack('<II', canvas_w, canvas_h)  # slice size
    data += struct.pack('<ii', pivot_x, pivot_y)     # pivot
    return ase_chunk(0x2022, data)


def make_palette_chunk(palette):
    """palette: list of (r,g,b) tuples, up to 256 entries."""
    n = len(palette)
    data = struct.pack('<III', n, 0, n - 1)
    data += b'\x00' * 8
    for r, g, b in palette:
        data += struct.pack('<HBBBB', 0, r, g, b, 255)  # no name, RGBA
    return ase_chunk(0x2019, data)


def make_color_profile_chunk():
    data = struct.pack('<HH', 1, 0)  # sRGB, no flags
    data += struct.pack('<I', 0)     # fixed gamma (unused)
    data += b'\x00' * 8
    return ase_chunk(0x2007, data)


# ─── Character data extraction ──────────────────────────────────────

def extract_char_frames(prom, spr_data, palette, char_id):
    """Extract all animation frames for a character.
    Returns: list of (state_id, state_name, frames)
      where frames = list of (duration_ms, parts)
      and parts = list of (canvas_y, canvas_x, rgba_image)
    """
    char_state_table = r32(prom, 0x080000 + char_id * 4)
    sdef_table = r32(prom, 0x080080 + char_id * 4)

    if char_state_table < 0x080000 or sdef_table < 0x080000:
        return []

    states = []  # (state_id, state_name, [(duration_ms, [(cy, cx, img), ...])])
    seen_sigs = set()

    for state_id in range(256):
        state_addr = r32(prom, char_state_table + state_id * 4)
        if state_addr == 0 or state_addr < 0x080000 or state_addr >= 0x200000:
            continue

        anim_frames = parse_animation(prom, state_addr)
        if not anim_frames:
            continue

        # Detect loop type from terminator word after the animation data
        # 0xFF00 = loop forever, 0xFE00 = play once
        loops = False
        pos = state_addr
        for _ in range(200):
            if pos + 2 > len(prom):
                break
            w = r16(prom, pos)
            if w == 0xFF00:
                loops = True
                break
            if w == 0xFE00:
                loops = False
                break
            pos += 6

        frames = []
        for duration, frag_addr, anim_flags in anim_frames:
            if frag_addr < 0x080000 or frag_addr >= 0x200000:
                continue
            if frag_addr + 6 > len(prom):
                continue

            # Follow fragment chain
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
                if sdef is not None:
                    raw_parts.append((y_off, x_off, sdef))

                if not chain:
                    break
                frag_pos += 6

            if not raw_parts:
                continue

            # Dedup
            sig = tuple((p[0], p[1], p[2]["base_tile"], p[2]["cols"],
                         p[2]["tiles_per_col"]) for p in raw_parts)
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)

            # Render parts and apply swapXY transform
            rendered_parts = []
            for y_off, x_off, sdef in raw_parts:
                # Skip parts with absurd offsets (bad data)
                if abs(y_off) > 512 or abs(x_off) > 512:
                    continue
                img = render_sdef(spr_data, sdef, palette)
                if img is not None:
                    # swapXY: canvas_y = x_off, canvas_x = y_off
                    rendered_parts.append((x_off, y_off, img))

            if not rendered_parts:
                continue

            # Duration: bytecode units are frames at 60fps
            duration_ms = max(16, int(duration * 1000 / 60))
            frames.append((duration_ms, rendered_parts))

        if frames:
            sname = STATE_NAMES.get(state_id, f"state_{state_id:03d}")
            states.append((state_id, sname, frames, loops))

    return states


# ─── Main ───────────────────────────────────────────────────────────

def generate_char(char_id, prom, spr_data, pd):
    name = NAMES[char_id] if char_id < len(NAMES) else f"Char_{char_id}"
    safe_name = name.replace(" ", "_").lower()

    palette = [tuple(c) for c in pd[str(char_id)]["p1"]["rgb"]]
    states = extract_char_frames(prom, spr_data, palette, char_id)
    if not states:
        print("  No frames found!")
        return

    total_frames = sum(len(frames) for _, _, frames, _ in states)
    max_parts = max(len(parts) for _, _, frames, _ in states for _, parts in frames)
    print(f"  {len(states)} states, {total_frames} frames, max {max_parts} layers")

    # ── Global bounding box (shared canvas size across all files) ──
    global_min_y = 0
    global_min_x = 0
    global_max_y = 0
    global_max_x = 0

    for _, _, frames, _ in states:
        for _, parts in frames:
            for cy, cx, img in parts:
                h, w = img.shape[:2]
                global_min_y = min(global_min_y, cy)
                global_min_x = min(global_min_x, cx)
                global_max_y = max(global_max_y, cy + h)
                global_max_x = max(global_max_x, cx + w)

    canvas_w = global_max_x - global_min_x
    canvas_h = global_max_y - global_min_y
    pivot_x = -global_min_x
    pivot_y = -global_min_y

    print(f"  Canvas: {canvas_w}x{canvas_h}, pivot: ({pivot_x}, {pivot_y})")

    # ── Write one .aseprite per animation state ──
    char_dir = os.path.join("rom_sprites_final", f"{char_id:02d}_{safe_name}")
    os.makedirs(char_dir, exist_ok=True)

    for state_id, sname, frames, loops in states:
        n_frames = len(frames)
        n_parts = max(len(parts) for _, parts in frames)
        layer_names = [f"Part {i}" for i in range(n_parts)]

        # Loop direction: 0=forward (play once), 0=forward with repeat for looping anims
        # Aseprite: 0=forward, 2=ping-pong
        loop_dir = 0  # forward

        # Frame 0 metadata chunks
        meta_chunks = [make_color_profile_chunk(), make_palette_chunk(palette)]
        for ln in layer_names:
            meta_chunks.append(make_layer_chunk(ln))
        meta_chunks.append(make_tags_chunk([(sname, 0, n_frames - 1, loop_dir)]))
        meta_chunks.append(make_slice_chunk("origin", pivot_x, pivot_y, canvas_w, canvas_h))

        all_frame_data = []
        for fi, (duration_ms, parts) in enumerate(frames):
            chunks = list(meta_chunks) if fi == 0 else []
            for layer_idx, (cy, cx, img) in enumerate(parts):
                cel_x = cx - global_min_x
                cel_y = cy - global_min_y
                h, w = img.shape[:2]
                chunks.append(make_cel_chunk(layer_idx, cel_x, cel_y, w, h, img.tobytes()))
            all_frame_data.append(ase_frame(duration_ms, chunks))

        # Write file
        header = bytearray(128)
        frame_data = b''.join(all_frame_data)
        file_size = 128 + len(frame_data)

        struct.pack_into('<I', header, 0, file_size)
        struct.pack_into('<H', header, 4, 0xA5E0)
        struct.pack_into('<H', header, 6, n_frames)
        struct.pack_into('<H', header, 8, canvas_w)
        struct.pack_into('<H', header, 10, canvas_h)
        struct.pack_into('<H', header, 12, 32)       # RGBA
        struct.pack_into('<I', header, 14, 1)         # layer opacity valid
        struct.pack_into('<H', header, 18, 100)
        struct.pack_into('<H', header, 32, len(palette))

        out_path = os.path.join(char_dir, f"{sname}.aseprite")
        with open(out_path, "wb") as f:
            f.write(bytes(header))
            f.write(frame_data)

    print(f"  [{char_id:2d}] {name:20s}: {len(states)} anims, {total_frames} frames → {char_dir}/")


def main():
    print("Loading ROMs...")
    prom = load_prom()
    spr_data = load_sprite_rom()

    with open("char_palettes_all.json") as f:
        pd = json.load(f)

    if len(sys.argv) > 1 and sys.argv[1] != "all":
        char_ids = [int(sys.argv[1])]
    else:
        char_ids = [i for i in range(30) if str(i) in pd]

    print(f"Generating .aseprite for {len(char_ids)} characters...\n")
    for char_id in char_ids:
        generate_char(char_id, prom, spr_data, pd)

    print("\nDone!")


if __name__ == "__main__":
    main()
