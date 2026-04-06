"""Neo Geo ROM loading — parameterized by game config."""

import zipfile, struct, os
import numpy as np


def load_prom(config):
    """Load and decode P ROM from a game config dict.
    Handles byte-swap and bank-swap for all Neo Geo P ROMs.
    """
    rom_zip = config["rom_zip"]
    p_rom_name = config["p_rom"]

    with zipfile.ZipFile(rom_zip, "r") as zf:
        raw = bytearray(zf.read(p_rom_name))

    # Byte-swap within each 16-bit word (Neo Geo standard)
    for i in range(0, len(raw), 2):
        raw[i], raw[i + 1] = raw[i + 1], raw[i]

    # Bank-swap for 2MB P ROMs: first 1MB maps to $100000, second to $000000
    if len(raw) > 0x100000:
        prom = bytearray(len(raw))
        half = len(raw) // 2
        prom[half:] = raw[:half]
        prom[:half] = raw[half:]
    else:
        prom = raw

    return bytes(prom)


def load_p2rom(config):
    """Load P2 ROM for KOF96+ — byte-swapped but NOT bank-swapped."""
    rom_zip = config["rom_zip"]
    p2_name = config["p2_rom"]
    with zipfile.ZipFile(rom_zip, "r") as zf:
        raw = bytearray(zf.read(p2_name))
    # Byte-swap within each 16-bit word
    for i in range(0, len(raw), 2):
        raw[i], raw[i + 1] = raw[i + 1], raw[i]
    return bytes(raw)


def load_sprite_rom(config):
    """Load and interleave C ROM pairs from a game config dict."""
    rom_zip = config["rom_zip"]
    c_layout = config["c_rom_layout"]
    total = int(config["total_sprite_region"], 16)

    data = bytearray(total)
    with zipfile.ZipFile(rom_zip, "r") as zf:
        for odd_name, even_name, offset_str in c_layout:
            offset = int(offset_str, 16)
            odd = np.frombuffer(zf.read(odd_name), dtype=np.uint8)
            even = np.frombuffer(zf.read(even_name), dtype=np.uint8)
            size = min(len(odd), len(even))
            inter = np.empty(size * 2, dtype=np.uint8)
            inter[0::2] = odd[:size]
            inter[1::2] = even[:size]
            data[offset:offset + len(inter)] = inter.tobytes()

    return bytes(data)
