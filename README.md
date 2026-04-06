# NeoSDK

Homebrew development toolkit for the Neo Geo. Takes Aseprite sprite files, C game code, and WAV audio samples, and produces a bootable Neo Geo ROM.

## What It Does

```
Aseprite files (.aseprite) ─┐
C game logic (game.c)       ├──► build_rom_aseprite.py ──► clarkdemo.zip (bootable ROM)
WAV audio (.wav)            ─┘
```

- Parses Aseprite files directly (RGBA 32bpp, zlib-compressed cels)
- Compiles C game code with `m68k-linux-gnu-gcc` for the 68000
- Encodes WAV audio to YM2610 ADPCM-A format
- Generates MAME-compatible ROM with softlist XML

## Quick Start

```bash
# Install m68k cross compiler
sudo apt install gcc-m68k-linux-gnu binutils-m68k-linux-gnu

# Build ROM (defaults to Iori Yagami from KOF95 extraction)
python3 build_rom_aseprite.py

# Or specify custom Aseprite files + audio
python3 build_rom_aseprite.py sprites/idle.aseprite sprites/walk.aseprite sounds/hit.wav

# Launch in MAME
./launch_clarkdemo.sh
```

## Project Structure

```
homebrew/
  crt0.s          # 68k bootstrap: vector table, BIOS integration, VBlank handler
  neogeo.h        # Hardware registers, VRAM helpers, input, sound, animation structs
  neogeo.ld       # Linker script for Neo Geo memory map
  game.c          # Game logic in C (edit this for your game)
  audio.py        # ADPCM-A encoder + Z80 sound driver generator
  sounds/         # Drop .wav files here for auto-inclusion
build_rom_aseprite.py  # Main build script
```

## Writing Game Code

Edit `homebrew/game.c`. Two entry points:

```c
void game_init(void);  // Called at startup
void game_tick(void);  // Called every VBlank (60fps)
```

The build script auto-generates `anim_data.h` with:
```c
#define ANIM_IDLE      0
#define ANIM_WALK_FWD  1
// ...
#define NUM_ANIMATIONS 5

// Access animation data:
ANIMATIONS[ANIM_IDLE].n_frames   // frame count
ANIMATIONS[ANIM_IDLE].frames[0]  // VRAM command list for frame 0
```

Hardware access via `neogeo.h`:
```c
vram_write(addr, data);          // Write to VRAM
sound_play(0x01);                // Play ADPCM-A sample
uint8_t input = BIOS_P1CHANGE;  // Read joystick (pressed this frame)
uint8_t held  = BIOS_P1CURRENT; // Read joystick (currently held)
```

## ROM Output

| ROM | Size | Contents |
|-----|------|----------|
| P1 | 512KB | 68k code + animation data |
| C1/C2 | 2x1MB | Sprite tiles (4bpp planar) |
| S1 | 128KB | Fix layer (empty) |
| M1 | 128KB | Z80 sound driver |
| V1 | 512KB | ADPCM-A audio samples |

## Requirements

- Python 3 + NumPy
- `m68k-linux-gnu-gcc` (cross compiler)
- MAME 0.285+ (for testing)
- Aseprite files (32bpp RGBA)

## Status

Working: sprite rendering, animation playback, joystick input, sprite movement, sound playback.

TODO: palette accuracy (body/accessory tile assignment), green BIOS boot screen (needs S ROM), multi-sample audio.
