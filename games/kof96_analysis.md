# KOF96 Initial Analysis

## ROM Structure
- P ROM: 214-p1.p1 (1MB) + 214-p2.sp2 (2MB) = 3MB total
  - p1 at $000000, p2 at $100000 (no bank swap, just linear)
- C ROM: 8 × 4MB = 32MB (8 files, 4 pairs)
- Prefix: 214

## Major Engine Changes from KOF95
The animation system was significantly revamped:

### Table Layout at $080000
Multiple tables of 40 entries × 4 bytes each:
- $080000: char → state_table_ptr (512 states per char, $800 spacing)
- $0800A0: char → unknown ptr (closely spaced values)
- $080140: char → unknown ptr
- $0801E0: char → unknown ptr (some share same value)

### Animation Bytecodes (CHANGED)
Same 6-byte format but fragment field is now a **frame INDEX** not a ROM pointer:
```
byte0: duration (same as KOF95)
byte1: $00 (was MSB of 24-bit address in KOF95)
word2: frame_index (0, 1, 2, 3... sequential per character)
word4: flags ($0600 for idle/walk, $0200 for guard/knockdown)
```
- Frame indices are per-character: char 0 idle uses 0-9, walk uses 10-14, etc.
- Control bytes ($FDxx) still present
- Terminators ($FF00, $FE00) still present

### Indirection System
The frame_index must resolve through a per-character frame definition table:
- NOT at table2 ($0800A0) — those values are too closely spaced
- Probably stored in a separate data structure pointed to from one of the tables
- Need to trace the renderer code to find the resolution path

### Renderer Code (estimated)
- $004F5A: LEA $080000 — table initialization
- $005222-$006E14: VRAM write cluster — sprite column rendering
- $009A0E-$009BDC: another VRAM write cluster  
- $062000-$062C9E: dense VRAM writes — possibly the tile renderer

### Characters
40 character slots (vs 30 in KOF95)

### Bitmask Format
TBD — need to find sdef records first. Likely word bitmasks based on KOF94 pattern
(the engine was rewritten, may have kept word format or switched)

## Next Steps
1. Trace renderer at $005222 or $062000 to understand frame_index → sdef resolution
2. Find the sdef table / frame definition table in P2 ROM
3. Determine bitmask format
4. Map character names and palette locations
