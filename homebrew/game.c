/* game.c — Neo Geo game logic in C
 *
 * The build script generates anim_data.h with animation tables.
 * This file handles input, sprite movement, and animation playback.
 */

#include "neogeo.h"
#include "anim_data.h"

/* ─── Game state ─────────────────────────────────────────────────── */
static uint16_t cur_anim;
static uint16_t cur_frame;
static uint16_t frame_timer;
static int16_t  sprite_x;
static int16_t  sprite_y;

#define FRAME_DELAY  8   /* VBlanks per animation frame */
#define MOVE_SPEED   2   /* Pixels per VBlank */

/* ─── Render current frame ───────────────────────────────────────── */
static void render_frame(void) {
    const anim_frame_t *f = &ANIMATIONS[cur_anim].frames[cur_frame];

    for (uint16_t i = 0; i < f->n_cmds; i++) {
        const vram_cmd_t *cmd = &f->cmds[i];
        uint16_t addr = cmd->addr;
        uint16_t data = cmd->data;

        /* Patch X position for sprite movement */
        if ((addr & 0xFC00) == 0x8400) {
            /* SCB4: X position — shift by sprite_x offset */
            int16_t base_x = (int16_t)(data >> 7);
            base_x += sprite_x;
            data = ((uint16_t)(base_x & 0x1FF)) << 7;
        }
        /* Patch Y position for sprite movement */
        else if ((addr & 0xFC00) == 0x8200 && data != 0) {
            /* SCB3: Y position is bits 15-7, sticky is bit 6, height is bits 5-0 */
            uint16_t y_val = (data >> 7) & 0x1FF;
            uint16_t sticky = data & 0x40;
            uint16_t height = data & 0x3F;
            int16_t y_pos = (int16_t)y_val;
            y_pos -= sprite_y;  /* subtract because Neo Geo Y is inverted */
            data = (((uint16_t)(y_pos & 0x1FF)) << 7) | sticky | height;
        }

        vram_write(addr, data);
    }
}

/* ─── Advance animation timer ────────────────────────────────────── */
static void advance_animation(void) {
    frame_timer++;
    if (frame_timer >= FRAME_DELAY) {
        frame_timer = 0;
        cur_frame++;
        if (cur_frame >= ANIMATIONS[cur_anim].n_frames) {
            cur_frame = 0;
        }
    }
}

/* ─── Handle input ───────────────────────────────────────────────── */
static void handle_input(void) {
    uint8_t pressed = BIOS_P1CHANGE;
    uint8_t held = BIOS_P1CURRENT;

    /* Left/Right (pressed): cycle animation */
    if (pressed & INPUT_RIGHT) {
        cur_anim++;
        if (cur_anim >= NUM_ANIMATIONS)
            cur_anim = 0;
        cur_frame = 0;
        frame_timer = 0;
        sound_play(0x01);  /* play sample 0 on anim change */
    }
    if (pressed & INPUT_LEFT) {
        if (cur_anim == 0)
            cur_anim = NUM_ANIMATIONS - 1;
        else
            cur_anim--;
        cur_frame = 0;
        frame_timer = 0;
        sound_play(0x01);  /* play sample 0 on anim change */
    }

    /* D-pad (held): move sprite */
    if (held & INPUT_UP)
        sprite_y -= MOVE_SPEED;
    if (held & INPUT_DOWN)
        sprite_y += MOVE_SPEED;
    /* A/B: also move horizontally (since left/right cycles anims) */
    if (held & INPUT_A)
        sprite_x -= MOVE_SPEED;
    if (held & INPUT_B)
        sprite_x += MOVE_SPEED;
}

/* ─── Called once at init and again when game starts ─────────────── */
void game_init(void) {
    /* Load palettes: body = palette 1, accessory = palette 2 */
    for (uint16_t i = 0; i < 16; i++) {
        PALRAM[16 + i] = PALETTE[i];      /* Palette 1 (body) at $400020 */
        PALRAM[32 + i] = PALETTE_ACC[i];   /* Palette 2 (acc)  at $400040 */
    }
    /* Background color = dark blue */
    PALRAM[0] = 0x1008;

    cur_anim = 0;
    cur_frame = 0;
    frame_timer = 0;
    sprite_x = 0;
    sprite_y = 0;

    render_frame();
}

/* ─── Called every VBlank from the main loop ─────────────────────── */
void game_tick(void) {
    REG_WATCHDOG = 0;  /* Kick watchdog */
    handle_input();
    advance_animation();
    render_frame();
}
