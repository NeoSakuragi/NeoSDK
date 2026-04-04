# NeoGeo Analyzer — Project Context

## What This Is
A Neo Geo ROM reverse engineering and sprite extraction toolkit, focused on the King of Fighters series. Includes a working Neo Geo homebrew ROM prototype.

## Project Structure
```
NeoGeo/
  extract_sprites.py     # Unified extractor: python extract_sprites.py kof95
  games/                 # Per-game JSON configs (ROM layout, palettes, character names)
    kof94.json
    kof95.json
    kof96_analysis.md    # KOF96 RE notes (engine revamped, WIP)
  neogeo/                # Reusable library
    rom_loader.py        # P ROM + C ROM loading, parameterized by config
    sprite_decode.py     # Tile decoding, color conversion
    animation.py         # Bytecode parsing, sdef reading, fragment chains
    renderer.py          # Multi-part compositing with swapXY + body/accessory palettes
  output/{game}/sprites/ # Extracted PNG atlases + Aseprite files
  plugins/autoscreenshot/ # MAME Lua plugin for live testing
  demo.s                 # 68k assembly for homebrew ROM
  build_rom2.py          # ROM builder (P ROM + C ROM encoder + Z80 sound driver)
  hash/neogeo.xml        # MAME softlist for homebrew ROM
  todo.txt               # Known issues
```

## Neo Geo Sprite Engine (KOF94/95)
- **Tables at $080000**: `char_state_table[char_id]` → state pointer table
- **Tables at $080080**: `sdef_table[char_id]` → sprite definition pointer table
- **Animation bytecodes**: 6 bytes each: `[duration:8][frag_addr:24][flags:16]`
  - Control bytes: b0 >= $80. Terminators: $FF00 (loop), $FE00 (once)
- **Fragment chains**: `[Y:s16][X:s16][sdef_word:16]`, chain bit at sdef_word bit 13
- **Sprite compositing**: swapXY transform (canvas_y = frag_x, canvas_x = frag_y)
- **Body = largest part** in fragment chain, accessories = smaller parts
- **Bitmask modes**: KOF94 = always 16-bit word; KOF95 = 8-bit when tpc≤8

## Palette System (KOF95)
- ROM palette table at $1D7020, 512 entries of 16 colors × 2 bytes
- Per-character: V0 = body standard, V1 = accessories, V16 = body alt, V17 = acc alt
- V14 = what VRAM captures during gameplay (has highlight overrides — NOT the clean palette)
- Characters 0-11: table at $080000 base, spacing $20 per char, base_idx = $0100 + cid*$20
- Characters 12-14 (Kim team): separate region at $1C0400/$1C0800/$1C0C00
- Characters 15-23: base_idx = $02E0 + (cid-15)*$20
- Characters 24-25 (bosses): $1DF000/$1DF400

## KOF96 Status (WIP)
Engine massively revamped. Key difference: animation bytecodes now use **frame indices** (0,1,2...) instead of direct ROM fragment pointers. Needs RE of the indirection/lookup system. See `games/kof96_analysis.md`.

## MAME Integration
- **Headless**: `xvfb-run -a mame neogeo kof95 ...` (requires xvfb package)
- **Lua plugins**: `plugins/autoscreenshot/` for live VRAM probing and screenshots
- **Save states**: at `~/.mame/sta/kof95/` — used for palette capture
- **Homebrew ROM**: `clarkdemo.zip` boots via `hash/neogeo.xml` softlist entry
  - CRITICAL: MAME requires CRC/SHA1 checksums in softlist or CPU won't execute
  - Z80 M ROM must program YM2610 Timer B for BIOS handshake
  - 68k must enable interrupts (`move #$2000,sr`) in game_main

## Key Decisions & Lessons
- Palette color 0 in each Neo Geo palette = transparent (index 0 never drawn)
- Neo Geo color format: bit15=dark, bits14/8-11=R, bits13/4-7=G, bits12/0-3=B (5-bit per channel, scattered)
- The `decode_color` / `rgb_to_neogeo` round-trip is lossless when dark bit = 0
- ROM P1 files use `load16_word_swap` in MAME (byte pairs swapped)
- 2MB P ROMs also bank-swap the two 1MB halves in the file
- C ROM pairs are interleaved: odd bytes from c1, even from c2

## Running Extractions
```bash
# KOF95 full extraction (PNG + Aseprite):
python extract_sprites.py kof95

# Single character:
python extract_sprites.py kof95 --char 2

# KOF94 (palettes not mapped yet, grayscale):
python extract_sprites.py kof94 --no-aseprite
```
