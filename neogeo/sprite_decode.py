"""Neo Geo tile decoding and color conversion."""

import struct
import numpy as np

ADDR_MASK = 0x03FFFFFF


def r16(rom, off):
    return struct.unpack(">H", rom[off:off + 2])[0]

def r32(rom, off):
    return struct.unpack(">I", rom[off:off + 4])[0]

def rs16(rom, off):
    return struct.unpack(">h", rom[off:off + 2])[0]


def decode_tile(spr_data, tile_code):
    """Decode a 16x16 4bpp Neo Geo tile to palette indices (0-15)."""
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
    """Quick check if a tile has any non-zero pixel data."""
    off = (tile_code * 128) & ADDR_MASK
    if off + 128 > len(spr_data):
        return False
    return any(spr_data[off + i] != 0 for i in range(128))


def decode_color(raw):
    """Decode a Neo Geo 16-bit color word to (R, G, B) 8-bit."""
    dark = (raw >> 15) & 1
    r = ((raw >> 14) & 1) | (((raw >> 8) & 0xF) << 1)
    g = ((raw >> 13) & 1) | (((raw >> 4) & 0xF) << 1)
    b = ((raw >> 12) & 1) | (((raw >> 0) & 0xF) << 1)
    r = (r << 3) | (r >> 2)
    g = (g << 3) | (g >> 2)
    b = (b << 3) | (b >> 2)
    if dark:
        r = max(0, r - 4)
        g = max(0, g - 4)
        b = max(0, b - 4)
    return (r, g, b)


def read_palette(prom, addr):
    """Read a 16-color palette from ROM address, return list of (R,G,B)."""
    return [decode_color(r16(prom, addr + i * 2)) for i in range(16)]
