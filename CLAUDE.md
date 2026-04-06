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

## KOF96 Engine (Working)
Engine massively revamped vs KOF94/95. Extraction producing ~1900 frames across 29 characters (body-only, grayscale). Majority of frames render correctly.

### Architecture
- **P2 ROM** ($200000+): Contains fragment data, sdef tables, and sdef records (byte-swapped, NOT bank-swapped)
- **Animation bytecodes**: 6 bytes each, same format as KOF95 but uses **frame indices** instead of direct fragment pointers
  - `$FD $04/$05`: sets body fragment base index (fd_body)
  - `$FD $09/$0A`: sets accessory fragment base index (fd_acc)
  - Many more $FD sub-commands exist ($00-$D1) — mostly unexplored
- **Global fragment base**: $23CB86 — shared across all characters, 6 bytes per record: `[Y:s16][X:s16][sdef_word:16]`
- **Per-character sdef tables**: Pointer array at P2 offset $06C000 (33 × 4-byte pointers)
  - Each character has its OWN sdef pointer table in P2 ROM
  - Same sdef_idx resolves to different tile data per character
  - CRITICAL: sdef tile base is 32-bit: `(w2 << 16) | w3` — w2 contains upper tile bits (C ROM bank 0-3)

### Sdef Record Format (11 encoding modes, tpc 0-10)
- Header: `[cols:8|tpc:8] [b2:8|b3:8] [w2:16] [w3:16]`
- Grid: b2 rows × b3 columns, rendering: row→X (sprite columns), col→Y (tile rows)
- **tpc 0/1**: tile_base = (w2<<16)|w3, sequential numbering from base. Bitmask at off+8, bpr=ceil(b3/8), padded to even.
- **tpc 2/3**: like tpc 7/8 (individual 16-bit tile codes). C ROM bank = (w2>>4)&3. Bitmask at off+6, padded to even.
- **tpc 4/5**: like tpc 9/10 (base + 8-bit offsets). Bank = (w2>>12)&3, base = w2&0xFFF. Bitmask at off+6.
- **tpc 6**: placeholder (no tiles)
- **tpc 7/8**: individual 16-bit tile codes after bitmask at off+6. C ROM bank = (w2>>4)&3 applied to each code. Bitmask padded to even.
- **tpc 9/10**: base (w2) + 8-bit offsets after bitmask at off+6. Bank = (w2>>12)&3, base = w2&0xFFF.

### C ROM Bank Encoding
KOF96 has 32MB C ROM = 4 pairs (c1/c2, c3/c4, c5/c6, c7/c8), each 8MB = 65536 tiles.
- Bank 0: tiles $00000-$0FFFF (c1/c2)
- Bank 1: tiles $10000-$1FFFF (c3/c4)
- Bank 2: tiles $20000-$2FFFF (c5/c6)
- Bank 3: tiles $30000-$3FFFF (c7/c8)
- Bank is encoded differently per tpc mode (see above)

### Character ID Mapping (CHAR_ID_TO_TABLE_IDX_96)
State table (P1 ROM $080000) and sdef tables (P2 ROM $06C000) use different orderings:
- chars 0-8 (Japan/FF/AOF teams): direct mapping (char_id = table_idx)
- chars 9-11 (Iori/Mature/Vice): table indices 21-23
- chars 12-14 (Leona/Ralf/Clark): table indices 9-11
- chars 15-17 (Athena/Kensou/Chin): table indices 12-14
- chars 18-20 (Chizuru/Mai/King): table indices 15-17
- chars 21-23 (Kim/Chang/Choi): table indices 18-20
- chars 24+ (bosses): mapping needs verification (Goenitz shows Chizuru — wrong)

### Known Issues
- **tpc 2 and 10 bank encoding**: ~2-3% of frames still scrambled (bank extraction not fully correct for these modes)
- **Accessories disabled**: body+acc compositing produces double-layered frames (both parts are full character halves, not body+small accessory like KOF95)
- **Special effects mixed in**: projectiles/flames extracted as character frames alongside body frames
- **Low frame counts**: Mai(14), King(4), Kim(16), Shermie(28) — broken fd_body values (>60000)
- **Boss character mapping**: chars 24-32 need correct table index mapping
- **No palette mapping**: all output is grayscale
- **Characters 29-32 missing**: Geese, Krauser, Kasumi, Boss Team
- **MAME save states**: saved under `~/.mame/sta/neogeo/` (not `kof96/`)

## MAME Integration
- **Version**: MAME 0.285 installed, run with `mame neogeo kof96 -window -rompath roms`
- **Headless**: `xvfb-run -a mame neogeo kof96 ...` — same binary, virtual X display
- **Lua plugins**: `plugins/autoscreenshot/` for VRAM probing and screenshots
  - Plugin path: must use `-pluginspath /home/bruno/NeoGeo/plugins` (system has its own autoscreenshot)
  - `plugin.json` needs `"start": "true"` to auto-enable
  - Lua console: pipe commands via `(echo "dofile('/tmp/script.lua')"; sleep N) | mame ... -console`
  - Use `emu.register_frame_done()` callback for VRAM reads (direct reads crash before state loads)
- **Save states**: at `~/.mame/sta/neogeo/` for KOF96 (game launched as `mame neogeo kof96`)
  - Available: k.sta (Kyo vs Kyo), t.sta (Terry vs Yuri, AOF stage), i.sta (Iori vs Benimaru, AOF stage)
- **VRAM access from Lua**: write address to $3C0000, read data from $3C0002
  - SCB1 (tiles): sprite N, row R → VRAM addr = N*64 + R*2 (tlo), N*64 + R*2 + 1 (attr)
  - SCB3 (Y/height): VRAM $8200 + N
  - SCB4 (X): VRAM $8400 + N
  - Tile number: tlo | ((attr & 3) << 16). Bits 2-3 of attr = auto-animation, NOT tile bits
  - Palette RAM at $400000 + pal*32 + color*2

## NeoSDK — Homebrew Development Pipeline
Build system that takes Aseprite files + C code + WAV audio → bootable Neo Geo ROM.

### Architecture
```
homebrew/
  crt0.s          # 68k assembly bootstrap: vector table, BIOS glue, VBlank handler
  neogeo.h        # C header: hardware registers, VRAM helpers, input, sound, animation structs
  neogeo.ld       # Linker script for Neo Geo memory map
  game.c          # Game logic in C: input handling, sprite movement, animation playback
  audio.py        # ADPCM-A encoder + V ROM builder + Z80 sound driver generator
  sounds/         # Drop .wav files here for auto-inclusion
build_rom_aseprite.py  # Main build script: Aseprite → tiles + C compile → ROM ZIP
```

### Build Pipeline
1. **Aseprite parser**: reads .aseprite files (RGBA 32bpp), extracts frames with zlib decompression
2. **Scale detection**: auto-detects 2x scaled Aseprite files (from KOF95 extraction), downsamples
3. **Dual palette**: loads body + accessory palettes from ROM config, maps each pixel to correct palette
4. **Tile encoder**: splits frames into 16x16 tiles, encodes to Neo Geo C ROM format (4bpp planar)
5. **C code generation**: produces `anim_data.h` with named animation indices (`ANIM_IDLE`, etc.) and VRAM command tables
6. **68k compilation**: `m68k-linux-gnu-gcc -m68000 -O2` compiles game.c, links with crt0.s
7. **Audio**: converts WAV → ADPCM-A (18.5kHz), builds Z80 sound driver with sample table
8. **ROM packaging**: builds P/S/M/V/C ROMs, generates MAME softlist XML with CRC/SHA1

### Build & Run
```bash
# Default (Iori Yagami, 5 animations):
python3 build_rom_aseprite.py

# Custom Aseprite files + WAV:
python3 build_rom_aseprite.py path/to/idle.aseprite path/to/walk.aseprite sfx.wav

# Launch in MAME:
./launch_clarkdemo.sh
```

### Key Technical Details
- **Softlist XML**: must have `loadflag="load16_word_swap"` for P ROM, `loadflag="load16_byte"` with offset 0/1 for C ROM pairs
- **GAS JMP encoding**: `jmp label` may emit PC-relative (4 bytes) — must use `.word 0x4EF9 / .long target` for 6-byte absolute JMP at fixed header offsets ($122-$139)
- **Struct alignment**: m68k GCC packs `{uint16_t, ptr}` as 6 bytes (no padding), not 8
- **Canvas anchor**: Aseprite frames use consistent canvas positioning; anchor = median center-X, max bottom-Y across all frames
- **Per-tile palette**: SCB1 attr word bits 15-8 select palette per tile (body=1, acc=2)
- **BIOS boot**: ~5 second green screen during eye catcher (empty S ROM), then game starts

### ROM Structure
- **P ROM**: 512KB — 68k code + const animation data (VRAM command tables)
- **C ROM**: 2×1MB — sprite tiles (c1=bitplanes 0,2; c2=bitplanes 1,3)
- **S ROM**: 128KB — empty (no fix layer text yet)
- **M ROM**: 128KB — Z80 sound driver (Timer B + ADPCM-A playback)
- **V ROM**: 512KB — ADPCM-A audio samples
- NGH $0999, security code at $186, eye catcher mode 2

### TODO
- **Palette accuracy**: body+acc palette assignment per-tile uses majority vote; tiles with mixed body/acc pixels show wrong colors for minority palette. Need pixel-level palette assignment or split compositing
- **Green BIOS boot**: 5-second green screen during eye catcher — need S ROM with font tiles or faster BIOS skip
- **Sound timing**: ADPCM-A beep plays on animation change but Z80 NMI dispatch needs testing for multi-sample support
- **Real hardware**: ROM header compatible; needs EPROM programmer (TL866II Plus ~$50) + 6 chips (~$20)
  - P1: 27C4096, S1/M1: 27C010, V1: 27C4096, C1/C2: 27C8000

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
python3 extract_sprites.py kof95

# Single character:
python3 extract_sprites.py kof95 --char 2

# KOF96 full extraction (grayscale, body-only):
python3 extract_sprites.py kof96 --no-aseprite

# KOF96 single character:
python3 extract_sprites.py kof96 --char 9 --no-aseprite

# KOF94 (palettes not mapped yet, grayscale):
python3 extract_sprites.py kof94 --no-aseprite
```
