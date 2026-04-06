"""Neo Geo sprite rendering — composites multi-part frames.

Supports both RGBA rendering (for PNG atlases) and indexed rendering
(for Aseprite indexed color mode).

KOF96 adds render_kof96_sdef() which handles 2D bitmask grids with
auto-orientation (b2 vs b3 determines transpose).
"""

import numpy as np
from .sprite_decode import decode_tile, tile_has_pixels


def render_sdef(spr_data, sdef, palette):
    """Render a single sprite definition to RGBA image."""
    cols = sdef["cols"]
    tpc = sdef["tiles_per_col"]
    base = sdef["base_tile"]
    bitmasks = sdef["bitmasks"]

    w = cols * 16
    h = tpc * 16
    img = np.zeros((h, w, 4), dtype=np.uint8)

    # Determine bitmask width from the bitmask values
    bm_bits = 16 if any(bm > 0xFF for bm in bitmasks) else 8

    tile_code = base
    for col in range(cols):
        bm = bitmasks[col] if col < len(bitmasks) else (0xFFFF if bm_bits == 16 else 0xFF)
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


def render_sdef_indexed(spr_data, sdef):
    """Render a single sprite definition to palette index image (uint8).
    Returns None if all pixels are transparent (index 0)."""
    cols = sdef["cols"]
    tpc = sdef["tiles_per_col"]
    base = sdef["base_tile"]
    bitmasks = sdef["bitmasks"]

    w = cols * 16
    h = tpc * 16
    img = np.zeros((h, w), dtype=np.uint8)

    bm_bits = 16 if any(bm > 0xFF for bm in bitmasks) else 8

    tile_code = base
    for col in range(cols):
        bm = bitmasks[col] if col < len(bitmasks) else (0xFFFF if bm_bits == 16 else 0xFF)
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
                    img[y0:y0 + 16, x0:x0 + 16] = pixels
                tile_code += 1

    if not img.any():
        return None
    return img


def render_frame(spr_data, parts, body_pal, acc_pal=None, scale=2, return_origin=False):
    """Render a composite frame from multiple sprite parts.

    Uses body_pal for the largest part (body) and acc_pal for smaller
    parts (accessories like hats, balls, bandanas).
    If acc_pal is None, body_pal is used for all parts.

    parts: list of (y_off, x_off, sdef) tuples from the fragment chain.

    If return_origin=True, returns (image, origin_x, origin_y) where
    origin is where game coordinate (0,0) maps on the canvas.
    This is needed for consistent Aseprite positioning.
    """
    if acc_pal is None:
        acc_pal = body_pal

    # Find largest part = body
    sizes = [sdef["cols"] * sdef["tiles_per_col"] for _, _, sdef in parts]
    body_idx = sizes.index(max(sizes)) if sizes else 0

    # Render each part with swapXY transform and appropriate palette
    rendered_parts = []
    for pi, (y_off, x_off, sdef) in enumerate(parts):
        pal = body_pal if pi == body_idx else acc_pal
        img = render_sdef(spr_data, sdef, pal)
        if img is not None:
            # swapXY: canvas_y = x_off, canvas_x = y_off
            rendered_parts.append((x_off, y_off, img))

    if not rendered_parts:
        return None

    min_y = min(y for y, x, img in rendered_parts)
    min_x = min(x for y, x, img in rendered_parts)
    max_y = max(y + img.shape[0] for y, x, img in rendered_parts)
    max_x = max(x + img.shape[1] for y, x, img in rendered_parts)

    w = max_x - min_x
    h = max_y - min_y
    if w <= 0 or h <= 0 or w > 512 or h > 512:
        return (None, 0, 0) if return_origin else None

    # Origin (game coordinate 0,0) position on canvas
    origin_x = (-min_x) * scale
    origin_y = (-min_y) * scale

    canvas = np.zeros((h * scale, w * scale, 4), dtype=np.uint8)
    for y_off, x_off, part_img in rendered_parts:
        py = (y_off - min_y) * scale
        px = (x_off - min_x) * scale
        ph, pw = part_img.shape[:2]
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
        return (None, 0, 0) if return_origin else None
    if return_origin:
        return canvas, origin_x, origin_y
    return canvas


def render_frame_indexed(spr_data, parts, scale=2):
    """Render a composite frame as indexed pixels (uint8).

    All parts share the same palette index space. The largest part is
    body; pixel indices are written directly.

    Returns (canvas, body_idx) or (None, -1).
    body_idx indicates which part index is the body.
    """
    sizes = [sdef["cols"] * sdef["tiles_per_col"] for _, _, sdef in parts]
    body_idx = sizes.index(max(sizes)) if sizes else 0

    rendered_parts = []
    for pi, (y_off, x_off, sdef) in enumerate(parts):
        img = render_sdef_indexed(spr_data, sdef)
        if img is not None:
            rendered_parts.append((x_off, y_off, img, pi))

    if not rendered_parts:
        return None, -1

    min_y = min(y for y, x, _, _ in rendered_parts)
    min_x = min(x for y, x, _, _ in rendered_parts)
    max_y = max(y + img.shape[0] for y, x, img, _ in rendered_parts)
    max_x = max(x + img.shape[1] for y, x, img, _ in rendered_parts)

    w = max_x - min_x
    h = max_y - min_y
    if w <= 0 or h <= 0 or w > 512 or h > 512:
        return None, -1

    canvas = np.zeros((h * scale, w * scale), dtype=np.uint8)
    part_map = np.full((h * scale, w * scale), -1, dtype=np.int8)

    for y_off, x_off, part_img, pi in rendered_parts:
        py = (y_off - min_y) * scale
        px = (x_off - min_x) * scale
        ph, pw = part_img.shape[:2]
        for sy in range(ph):
            for sx in range(pw):
                if part_img[sy, sx] > 0:
                    for ds in range(scale):
                        for dr in range(scale):
                            ty = py + sy * scale + ds
                            tx = px + sx * scale + dr
                            if 0 <= ty < h * scale and 0 <= tx < w * scale:
                                canvas[ty, tx] = part_img[sy, sx]
                                part_map[ty, tx] = pi

    if not canvas.any():
        return None, -1
    return (canvas, part_map, body_idx)


# ─── KOF96 rendering ──────────────────────────────────────────────

def render_kof96_sdef(spr_data, sdef, palette):
    """Render a KOF96 sdef to RGBA image.

    The sdef bitmask grid always maps row→X (sprite column), col→Y (tile row).
    This matches Neo Geo hardware convention where each bitmask row corresponds
    to one hardware sprite column (vertical strip).
    """
    b2 = sdef["b2"]
    b3 = sdef["b3"]
    tiles = sdef["tiles"]

    # Always: row → X (hardware sprite columns), col → Y (tile rows)
    w = b2 * 16
    h = b3 * 16

    if w == 0 or h == 0:
        return None

    img = np.zeros((h, w, 4), dtype=np.uint8)

    for row, col, tile_code in tiles:
        if not tile_has_pixels(spr_data, tile_code):
            continue
        pixels = decode_tile(spr_data, tile_code)
        x0 = row * 16
        y0 = col * 16
        for py in range(16):
            for px in range(16):
                ci = pixels[py, px]
                if ci == 0:
                    continue
                r, g, b = palette[ci] if ci < len(palette) else (255, 255, 255)
                yy = y0 + py
                xx = x0 + px
                if 0 <= yy < h and 0 <= xx < w:
                    img[yy, xx] = [r, g, b, 255]

    if not img[:, :, 3].any():
        return None
    return img


def render_kof96_frame(spr_data, parts, body_pal, acc_pal=None, scale=2, return_origin=False):
    """Render a KOF96 composite frame from body + accessory parts.

    parts: list of (frag_y, frag_x, sdef) from fragment records.
    Uses swapXY: canvas_x = frag_y, canvas_y = frag_x.
    """
    if acc_pal is None:
        acc_pal = body_pal

    # Body = first part (index 0), accessory = rest
    rendered_parts = []
    for pi, (frag_y, frag_x, sdef) in enumerate(parts):
        pal = body_pal if pi == 0 else acc_pal
        img = render_kof96_sdef(spr_data, sdef, pal)
        if img is not None:
            # swapXY: canvas_x = frag_y, canvas_y = frag_x
            rendered_parts.append((frag_y, frag_x, img))

    if not rendered_parts:
        return (None, 0, 0) if return_origin else None

    min_cx = min(cy for cy, cx, _ in rendered_parts)
    min_cy = min(cx for cy, cx, _ in rendered_parts)
    max_cx = max(cy + img.shape[1] for cy, cx, img in rendered_parts)
    max_cy = max(cx + img.shape[0] for cy, cx, img in rendered_parts)

    w = max_cx - min_cx
    h = max_cy - min_cy
    if w <= 0 or h <= 0 or w > 512 or h > 512:
        return (None, 0, 0) if return_origin else None

    origin_x = (-min_cx) * scale
    origin_y = (-min_cy) * scale

    canvas = np.zeros((h * scale, w * scale, 4), dtype=np.uint8)
    for cy, cx, part_img in rendered_parts:
        px_off = (cy - min_cx) * scale
        py_off = (cx - min_cy) * scale
        ph, pw = part_img.shape[:2]
        for sy in range(ph):
            for sx in range(pw):
                if part_img[sy, sx, 3] > 0:
                    for ds in range(scale):
                        for dr in range(scale):
                            ty = py_off + sy * scale + ds
                            tx = px_off + sx * scale + dr
                            if 0 <= ty < h * scale and 0 <= tx < w * scale:
                                canvas[ty, tx] = part_img[sy, sx]

    if not canvas[:, :, 3].any():
        return (None, 0, 0) if return_origin else None
    if return_origin:
        return canvas, origin_x, origin_y
    return canvas
