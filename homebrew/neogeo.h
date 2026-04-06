/* neogeo.h — Neo Geo hardware definitions for C homebrew */
#ifndef NEOGEO_H
#define NEOGEO_H

#include <stdint.h>

/* ─── Hardware registers ─────────────────────────────────────────── */
#define REG_VRAMADDR   (*(volatile uint16_t *)0x3C0000)
#define REG_VRAMRW     (*(volatile uint16_t *)0x3C0002)
#define REG_VRAMMOD    (*(volatile uint16_t *)0x3C0004)
#define REG_IRQACK     (*(volatile uint16_t *)0x3C000C)
#define REG_WATCHDOG   (*(volatile uint8_t  *)0x300001)

/* ─── Palette RAM ────────────────────────────────────────────────── */
#define PALRAM         ((volatile uint16_t *)0x400000)

/* ─── BIOS variables ─────────────────────────────────────────────── */
#define BIOS_SYSTEM_MODE (*(volatile uint8_t *)0x10FD80)
#define BIOS_USER_REQUEST (*(volatile uint8_t *)0x10FDAE)
#define BIOS_USER_MODE   (*(volatile uint8_t *)0x10FDAF)
#define BIOS_P1CURRENT   (*(volatile uint8_t *)0x10FD96)
#define BIOS_P1CHANGE    (*(volatile uint8_t *)0x10FD97)

/* ─── Input bits ─────────────────────────────────────────────────── */
#define INPUT_UP     (1 << 0)
#define INPUT_DOWN   (1 << 1)
#define INPUT_LEFT   (1 << 2)
#define INPUT_RIGHT  (1 << 3)
#define INPUT_A      (1 << 4)
#define INPUT_B      (1 << 5)
#define INPUT_C      (1 << 6)
#define INPUT_D      (1 << 7)

/* ─── VRAM helpers ───────────────────────────────────────────────── */
static inline void vram_write(uint16_t addr, uint16_t data) {
    REG_VRAMADDR = addr;
    REG_VRAMRW = data;
}

static inline uint16_t vram_read(uint16_t addr) {
    REG_VRAMADDR = addr;
    return REG_VRAMRW;
}

/* ─── BIOS call addresses ────────────────────────────────────────── */
#define BIOS_SYSTEM_INT1   0xC00438
#define BIOS_SYSTEM_RETURN 0xC00444
#define BIOS_SYSTEM_IO     0xC0044A
#define BIOS_FIX_CLEAR     0xC004C2
#define BIOS_LSP_1ST       0xC004C8

/* Call a BIOS routine by address */
static inline void bios_call(uint32_t addr) {
    ((void (*)(void))addr)();
}

/* ─── Animation data structures (filled by build script) ─────────── */

/* A VRAM command: write 'data' to VRAM address 'addr' */
typedef struct {
    uint16_t addr;
    uint16_t data;
} vram_cmd_t;

/* A single animation frame: array of VRAM commands */
typedef struct {
    uint16_t n_cmds;
    const vram_cmd_t *cmds;
} anim_frame_t;

/* An animation: array of frames */
typedef struct {
    uint16_t n_frames;
    const anim_frame_t *frames;
} animation_t;

/* ─── Sound ──────────────────────────────────────────────────────── */
#define REG_SOUND      (*(volatile uint8_t *)0x320000)

/* Send a command to the Z80 sound driver */
static inline void sound_play(uint8_t cmd) {
    REG_SOUND = cmd;
}

/* Sound commands (matched to Z80 driver):
 *   0x01-0x06 = play ADPCM-A sample 0-5
 *   0x10      = stop all
 */
#define SND_STOP_ALL  0x10

#endif /* NEOGEO_H */

