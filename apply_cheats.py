#!/usr/bin/env python3
"""Apply KOF95 infinite juggle hack — 2 patches, 4 bytes."""
import zipfile, shutil, os

ROM_PATH = "roms/kof95.zip"
BACKUP_PATH = "roms/kof95_original.zip"

if not os.path.exists(BACKUP_PATH):
    shutil.copy2(ROM_PATH, BACKUP_PATH)
    print(f"Backed up original to {BACKUP_PATH}")

with zipfile.ZipFile(BACKUP_PATH, 'r') as zf:
    raw = bytearray(zf.read('084-p1.p1'))
    other_files = {n: zf.read(n) for n in zf.namelist() if n != '084-p1.p1'}

swapped = bytearray(raw)
for i in range(0, len(swapped), 2):
    swapped[i], swapped[i+1] = swapped[i+1], swapped[i]
prom = bytearray(0x200000)
prom[0x100000:0x200000] = swapped[0x000000:0x100000]
prom[0x000000:0x100000] = swapped[0x100000:0x200000]

PATCHES = [
    (0x23C6, [0x4E, 0x71], "Defender always hittable (box check bypass)"),
    (0x245A, [0x4E, 0x71], "Allow re-hit during hit reaction ($AC bypass)"),
]

for addr, new_bytes, desc in PATCHES:
    for i, b in enumerate(new_bytes):
        prom[addr + i] = b
    print(f"  ${addr:06X}: {desc}")

new_swapped = bytearray(0x200000)
new_swapped[0x000000:0x100000] = prom[0x100000:0x200000]
new_swapped[0x100000:0x200000] = prom[0x000000:0x100000]
new_raw = bytearray(new_swapped)
for i in range(0, len(new_raw), 2):
    new_raw[i], new_raw[i+1] = new_raw[i+1], new_raw[i]

with zipfile.ZipFile(ROM_PATH, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('084-p1.p1', bytes(new_raw))
    for name, data in other_files.items():
        zf.writestr(name, data)

print(f"\nPatched ROM saved to {ROM_PATH}")
print("Run: QT_QPA_PLATFORM=xcb mame -rompath ./roms kof95")
