"""Neo Geo ADPCM-A audio encoder and V ROM builder.

Converts WAV files to YM2610 ADPCM-A format for the Neo Geo V ROM.
Sample rate must be 18500 Hz (or will be resampled).
"""

import struct, wave, os
import numpy as np


# ─── ADPCM-A codec (Yamaha/OKI) ─────────────────────────────────────

# Step size table (49 entries)
STEP_TABLE = [
    16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66,
    73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253,
    279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876,
    963, 1060, 1166, 1282, 1411, 1552
]

# Index adjustment per nibble value
INDEX_ADJ = [-1, -1, -1, -1, 2, 5, 7, 9]


def encode_adpcma(pcm_data):
    """Encode 16-bit signed PCM samples to ADPCM-A bytes.

    pcm_data: numpy array of int16 samples
    Returns: bytes of ADPCM-A encoded data (2 samples per byte)
    """
    signal = pcm_data.astype(np.int32)
    predicted = 0
    step_idx = 0
    nibbles = []

    for sample in signal:
        step = STEP_TABLE[step_idx]
        diff = sample - predicted

        # Encode
        nibble = 0
        if diff < 0:
            nibble = 8
            diff = -diff

        # Quantize
        if diff >= step:
            nibble |= 4
            diff -= step
        if diff >= (step >> 1):
            nibble |= 2
            diff -= (step >> 1)
        if diff >= (step >> 2):
            nibble |= 1

        # Decode (to track prediction)
        delta = (step >> 3)
        if nibble & 4:
            delta += step
        if nibble & 2:
            delta += (step >> 1)
        if nibble & 1:
            delta += (step >> 2)
        if nibble & 8:
            predicted -= delta
        else:
            predicted += delta

        # Clamp
        predicted = max(-2048, min(2047, predicted))

        # Update step index
        step_idx += INDEX_ADJ[nibble & 7]
        step_idx = max(0, min(48, step_idx))

        nibbles.append(nibble & 0xF)

    # Pack nibbles into bytes (high nibble first)
    result = bytearray()
    for i in range(0, len(nibbles), 2):
        hi = nibbles[i]
        lo = nibbles[i + 1] if i + 1 < len(nibbles) else 0
        result.append((hi << 4) | lo)

    return bytes(result)


def load_wav(path, target_rate=18500):
    """Load a WAV file and return 16-bit mono PCM at target sample rate."""
    with wave.open(path, 'rb') as w:
        nch = w.getnchannels()
        sampwidth = w.getsampwidth()
        rate = w.getframerate()
        nframes = w.getnframes()
        raw = w.readframes(nframes)

    # Decode to int16
    if sampwidth == 1:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128
        samples *= 256
    elif sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16)
    elif sampwidth == 4:
        samples = (np.frombuffer(raw, dtype=np.int32) >> 16).astype(np.int16)
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    # Mix to mono
    if nch > 1:
        samples = samples.reshape(-1, nch).mean(axis=1).astype(np.int16)

    # Resample if needed
    if rate != target_rate:
        n_out = int(len(samples) * target_rate / rate)
        indices = np.linspace(0, len(samples) - 1, n_out).astype(int)
        samples = samples[indices]

    return samples


def build_vrom(wav_paths, vrom_size=0x80000):
    """Build a V ROM from WAV files.

    Returns (vrom_bytes, sample_table) where sample_table is a list of
    (start_addr_256, end_addr_256) for each sample (256-byte granularity).
    """
    vrom = bytearray(vrom_size)
    offset = 0
    sample_table = []

    for path in wav_paths:
        name = os.path.basename(path)
        print(f"  Encoding {name}...")

        pcm = load_wav(path)
        adpcm = encode_adpcma(pcm)

        # Align to 256-byte boundary
        offset = (offset + 255) & ~255

        if offset + len(adpcm) > vrom_size:
            print(f"    WARNING: V ROM full, skipping {name}")
            continue

        # Record sample position (in 256-byte units)
        start_256 = offset // 256
        end_offset = offset + len(adpcm) - 1
        end_256 = end_offset // 256

        # Check 1MB page boundary
        if (start_256 >> 12) != (end_256 >> 12):
            print(f"    WARNING: Sample crosses 1MB boundary!")

        vrom[offset:offset + len(adpcm)] = adpcm
        sample_table.append((start_256, end_256))

        duration_ms = len(pcm) * 1000 // 18500
        print(f"    {len(pcm)} samples ({duration_ms}ms), "
              f"ADPCM {len(adpcm)} bytes, "
              f"offset ${offset:06X}-${end_offset:06X}")

        offset = end_offset + 1

    return bytes(vrom), sample_table


def build_sound_driver(sample_table):
    """Build a Z80 M ROM with ADPCM-A sample playback.

    Commands sent from 68k via $320000:
      $00 = no-op
      $01-$06 = play sample 0-5 on ADPCM-A channel 0
      $10 = stop all
    """
    PORT_FROM_68K = 0x00
    PORT_YM_B_ADDR = 0x06    # YM2610 port B address
    PORT_YM_B_DATA = 0x07    # YM2610 port B data
    PORT_YM_A_ADDR = 0x04    # YM2610 port A address (for Timer B)
    PORT_YM_A_VAL = 0x05
    PORT_ENABLE_NMI = 0x08
    PORT_TO_68K = 0x0C

    mrom = bytearray(0x20000)

    # Build the sample table in ROM at $0200
    TABLE_ADDR = 0x200
    for i, (start, end) in enumerate(sample_table):
        mrom[TABLE_ADDR + i * 4 + 0] = start & 0xFF          # start lo
        mrom[TABLE_ADDR + i * 4 + 1] = (start >> 8) & 0xFF   # start hi
        mrom[TABLE_ADDR + i * 4 + 2] = end & 0xFF             # end lo
        mrom[TABLE_ADDR + i * 4 + 3] = (end >> 8) & 0xFF      # end hi

    NUM_SAMPLES = len(sample_table)

    # === Entry point $0000 ===
    pc = 0
    mrom[pc] = 0xF3; pc += 1          # DI
    mrom[pc] = 0xC3; pc += 1          # JP $0100
    mrom[pc] = 0x00; pc += 1
    mrom[pc] = 0x01; pc += 1

    # === INT handler $0038 (IM 1) — Timer B IRQ ===
    pc = 0x38
    mrom[pc] = 0xF3; pc += 1          # DI
    mrom[pc] = 0xF5; pc += 1          # PUSH AF
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x27; pc += 1    # LD A, $27
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_A_ADDR; pc += 1  # OUT ($04), A
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x3A; pc += 1    # LD A, $3A
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_A_VAL; pc += 1   # OUT ($05), A
    mrom[pc] = 0xF1; pc += 1          # POP AF
    mrom[pc] = 0xFB; pc += 1          # EI
    mrom[pc] = 0xED; pc += 1; mrom[pc] = 0x4D; pc += 1    # RETI

    # === NMI handler $0066 — 68k command dispatch ===
    nmi_start = 0x66
    pc = nmi_start
    mrom[pc] = 0xF5; pc += 1          # PUSH AF
    mrom[pc] = 0xC5; pc += 1          # PUSH BC
    mrom[pc] = 0xD5; pc += 1          # PUSH DE
    mrom[pc] = 0xE5; pc += 1          # PUSH HL
    mrom[pc] = 0xDB; pc += 1; mrom[pc] = PORT_FROM_68K; pc += 1  # IN A, ($00)
    mrom[pc] = 0x47; pc += 1          # LD B, A  (save command)
    mrom[pc] = 0xF6; pc += 1; mrom[pc] = 0x80; pc += 1    # OR $80
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_TO_68K; pc += 1    # OUT ($0C), A  (ack)

    # Check command: $10 = stop all
    mrom[pc] = 0x78; pc += 1          # LD A, B
    mrom[pc] = 0xFE; pc += 1; mrom[pc] = 0x10; pc += 1    # CP $10
    stop_jr_addr = pc  # will patch
    mrom[pc] = 0x28; pc += 1; mrom[pc] = 0x00; pc += 1    # JR Z, stop_handler

    # Check command: $01-$06 = play sample
    mrom[pc] = 0x78; pc += 1          # LD A, B
    mrom[pc] = 0xFE; pc += 1; mrom[pc] = 0x01; pc += 1    # CP $01
    nop_jr_addr = pc
    mrom[pc] = 0x38; pc += 1; mrom[pc] = 0x00; pc += 1    # JR C, nmi_done (cmd < 1)

    mrom[pc] = 0xFE; pc += 1; mrom[pc] = NUM_SAMPLES + 1; pc += 1  # CP NUM_SAMPLES+1
    over_jr_addr = pc
    mrom[pc] = 0x30; pc += 1; mrom[pc] = 0x00; pc += 1    # JR NC, nmi_done (cmd > NUM)

    # Valid sample command: A = cmd (1-based), look up table
    mrom[pc] = 0x3D; pc += 1          # DEC A  (0-based index)
    # HL = TABLE_ADDR + A*4
    mrom[pc] = 0x87; pc += 1          # ADD A, A  (A*2)
    mrom[pc] = 0x87; pc += 1          # ADD A, A  (A*4)
    mrom[pc] = 0x6F; pc += 1          # LD L, A
    mrom[pc] = 0x26; pc += 1; mrom[pc] = (TABLE_ADDR >> 8) & 0xFF; pc += 1  # LD H, hi(TABLE)

    # Set master volume
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x08; pc += 1    # LD A, $08 (master vol reg)
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_ADDR; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x3F; pc += 1    # LD A, $3F (max volume)
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_DATA; pc += 1

    # Set channel 0 volume
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x01; pc += 1    # LD A, $01 (ch0 vol)
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_ADDR; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x1F; pc += 1    # LD A, $1F (max)
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_DATA; pc += 1

    # Start address low (reg $10)
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x10; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_ADDR; pc += 1
    mrom[pc] = 0x7E; pc += 1          # LD A, (HL)  — start_lo
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_DATA; pc += 1

    # Start address high (reg $18)
    mrom[pc] = 0x23; pc += 1          # INC HL
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x18; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_ADDR; pc += 1
    mrom[pc] = 0x7E; pc += 1          # LD A, (HL)  — start_hi
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_DATA; pc += 1

    # End address low (reg $20)
    mrom[pc] = 0x23; pc += 1          # INC HL
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x20; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_ADDR; pc += 1
    mrom[pc] = 0x7E; pc += 1          # LD A, (HL)  — end_lo
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_DATA; pc += 1

    # End address high (reg $28)
    mrom[pc] = 0x23; pc += 1          # INC HL
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x28; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_ADDR; pc += 1
    mrom[pc] = 0x7E; pc += 1          # LD A, (HL)  — end_hi
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_DATA; pc += 1

    # Key-on channel 0 (reg $00, bit 0)
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x00; pc += 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_ADDR; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x01; pc += 1    # key-on ch0
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_B_DATA; pc += 1

    nmi_done = pc
    mrom[pc] = 0xE1; pc += 1          # POP HL
    mrom[pc] = 0xD1; pc += 1          # POP DE
    mrom[pc] = 0xC1; pc += 1          # POP BC
    mrom[pc] = 0xF1; pc += 1          # POP AF
    mrom[pc] = 0xED; pc += 1; mrom[pc] = 0x45; pc += 1    # RETN

    # Patch JR targets
    mrom[stop_jr_addr + 1] = (nmi_done - stop_jr_addr - 2) & 0xFF  # temp: stop = done (TODO: actual stop)
    mrom[nop_jr_addr + 1] = (nmi_done - nop_jr_addr - 2) & 0xFF
    mrom[over_jr_addr + 1] = (nmi_done - over_jr_addr - 2) & 0xFF

    # Stop handler: dump all ADPCM-A channels
    # For now, stop_jr just goes to nmi_done. We can add key-off later.

    # === Init code $0100 ===
    pc = 0x100
    mrom[pc] = 0x31; pc += 1; mrom[pc] = 0xFF; pc += 1; mrom[pc] = 0xFF; pc += 1  # LD SP, $FFFF
    mrom[pc] = 0xED; pc += 1; mrom[pc] = 0x56; pc += 1    # IM 1
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_ENABLE_NMI; pc += 1  # OUT ($08), A

    # Program Timer B
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x26; pc += 1    # LD A, $26
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_A_ADDR; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0xFF; pc += 1    # LD A, $FF
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_A_VAL; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x27; pc += 1    # LD A, $27
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_A_ADDR; pc += 1
    mrom[pc] = 0x3E; pc += 1; mrom[pc] = 0x3A; pc += 1    # LD A, $3A
    mrom[pc] = 0xD3; pc += 1; mrom[pc] = PORT_YM_A_VAL; pc += 1

    mrom[pc] = 0xFB; pc += 1          # EI
    mainloop = pc
    mrom[pc] = 0x76; pc += 1          # HALT
    mrom[pc] = 0xC3; pc += 1          # JP mainloop
    mrom[pc] = mainloop & 0xFF; pc += 1
    mrom[pc] = (mainloop >> 8) & 0xFF; pc += 1

    return bytes(mrom)
