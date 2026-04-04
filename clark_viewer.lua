-- Clark Animation Viewer - Lua-based sprite renderer
-- Runs on KOF95 ROM, hijacks VRAM to display Clark animations
-- Left/Right to cycle animations
--
-- Usage: mame neogeo kof95 -plugin clark_viewer -skip_gameinfo -window

-- Animation data: Clark's fragment chains from the KOF95 ROM
-- Pre-extracted: state_id, sdef entries with tile codes and positions

local viewer = {}

-- Clark = char 2, tables at $080A00 (state) and $088A00 (sdef)
local CHAR_STATE_TABLE = 0x080A00
local CHAR_SDEF_TABLE  = 0x088A00
local TARGET_STATES = {0, 1, 2, 11, 14}  -- idle, walk, walk_back, guard_air, knockdown
local STATE_NAMES = {"idle", "walk_fwd", "walk_back", "guard_air", "knockdown"}

local cur_anim = 1
local cur_frame = 1
local frame_timer = 0
local animations = {}  -- parsed at startup
local mem = nil
local active = false

local function r16(addr) return mem:read_u16(addr) end
local function r32(addr) return mem:read_u32(addr) end
local function rs16(addr)
    local v = r16(addr)
    if v >= 0x8000 then return v - 0x10000 end
    return v
end

local function parse_animations()
    for _, state_id in ipairs(TARGET_STATES) do
        local state_addr = r32(CHAR_STATE_TABLE + state_id * 4)
        if state_addr == 0 or state_addr < 0x080000 then goto next_state end

        local frames = {}
        local pos = state_addr
        for _ = 1, 100 do
            if pos + 6 > 0x200000 then break end
            local w = r16(pos)
            if w == 0xFF00 or w == 0xFE00 then break end
            local b0 = mem:read_u8(pos)
            if b0 >= 0x80 then pos = pos + 6; goto next_entry end

            local duration = b0
            local frag_addr = (mem:read_u8(pos + 1) * 65536) + r16(pos + 2)
            pos = pos + 6

            if frag_addr < 0x080000 or frag_addr >= 0x200000 then goto next_entry end

            -- Follow fragment chain
            local parts = {}
            local fp = frag_addr
            for _ = 1, 8 do
                if fp + 6 > 0x200000 then break end
                local y_off = rs16(fp)
                local x_off = rs16(fp + 2)
                local sdef_word = r16(fp + 4)
                local sdef_idx = sdef_word & 0x01FF
                local chain = (mem:read_u8(fp + 4) >> 5) & 1

                -- Read sdef
                local sdef_ptr = r32(CHAR_SDEF_TABLE + sdef_idx * 4)
                if sdef_ptr >= 0x080000 and sdef_ptr < 0x200000 then
                    local cols = mem:read_u8(sdef_ptr)
                    local tpc = mem:read_u8(sdef_ptr + 1)
                    local tile_lo = r16(sdef_ptr + 4)
                    local tile_cfg = r16(sdef_ptr + 2)
                    local upper = tile_cfg & 0xF
                    local base_tile = (upper * 65536) + tile_lo

                    -- Read bitmasks
                    local bitmasks = {}
                    for c = 0, cols - 1 do
                        table.insert(bitmasks, mem:read_u8(sdef_ptr + 6 + c))
                    end

                    if cols > 0 and cols <= 20 and tpc > 0 and tpc <= 16 then
                        table.insert(parts, {
                            y = y_off, x = x_off,
                            cols = cols, tpc = tpc,
                            base_tile = base_tile,
                            bitmasks = bitmasks
                        })
                    end
                end

                if chain == 0 then break end
                fp = fp + 6
            end

            if #parts > 0 then
                table.insert(frames, {duration = math.max(1, duration), parts = parts})
            end
            if #frames >= 20 then break end

            ::next_entry::
        end

        table.insert(animations, {name = STATE_NAMES[#animations + 1] or "?", frames = frames})
        ::next_state::
    end
end

local function render_frame()
    if #animations == 0 then return end
    local anim = animations[cur_anim]
    if not anim or #anim.frames == 0 then return end
    local frame = anim.frames[cur_frame]
    if not frame then return end

    local SCREEN_X = 160
    local SCREEN_Y = 200
    local sprite_slot = 1

    -- Clear sprites 1-20
    for s = 1, 20 do
        mem:write_u16(0x3C0000, 0x8200 + s)  -- SCB3
        mem:write_u16(0x3C0002, 0)            -- hide
    end

    for pi, part in ipairs(frame.parts) do
        -- swapXY: canvas_y = x_off, canvas_x = y_off
        local cy = part.x
        local cx = part.y

        local tc = part.base_tile
        for col = 0, part.cols - 1 do
            if sprite_slot > 20 then break end
            local bm = part.bitmasks[col + 1] or 0xFF

            -- SCB1: write tile data
            local scb1_base = sprite_slot * 64
            mem:write_u16(0x3C0004, 1)  -- VRAMMOD = 1
            mem:write_u16(0x3C0000, scb1_base)

            for tile = 0, part.tpc - 1 do
                local visible = 0
                if tile < 8 then
                    visible = (bm >> (7 - tile)) & 1
                end
                local tid = 0
                if visible == 1 then
                    tid = tc
                    tc = tc + 1
                end
                mem:write_u16(0x3C0002, tid & 0xFFFF)  -- tile number
                -- Attributes: palette 23 (KOF95 character palette) in upper byte
                mem:write_u16(0x3C0002, 0x1700 | ((tid >> 16) & 0xF))
            end

            -- SCB2: full size
            mem:write_u16(0x3C0000, 0x8000 + sprite_slot)
            mem:write_u16(0x3C0002, 0x0FFF)

            -- SCB3: Y + sticky + height
            local y_screen = SCREEN_Y + cy
            local y_val = (496 - y_screen) & 0x1FF
            local sticky = 0
            if col > 0 then sticky = 64 end  -- bit 6
            mem:write_u16(0x3C0000, 0x8200 + sprite_slot)
            mem:write_u16(0x3C0002, (y_val * 128) + sticky + part.tpc)

            -- SCB4: X (only for first column of first part)
            if col == 0 and pi == 1 then
                local x_screen = (SCREEN_X + cx) & 0x1FF
                mem:write_u16(0x3C0000, 0x8400 + sprite_slot)
                mem:write_u16(0x3C0002, x_screen * 128)
            end

            sprite_slot = sprite_slot + 1
        end
    end
end

-- Export as MAME plugin
local exports = {
    name = "clark_viewer",
    version = "1.0",
    description = "Clark Animation Viewer",
    license = "MIT",
    author = "NeoGeoAnalyzer"
}

function exports.startplugin()
    local fc = 0

    emu.register_frame_done(function()
        fc = fc + 1

        -- Initialize on first frame after boot (frame 900 = ~15 seconds, past eye catcher)
        if fc == 900 and not active then
            mem = manager.machine.devices[":maincpu"].spaces["program"]
            print("[VIEWER] Parsing Clark animations from ROM...")
            parse_animations()
            for i, a in ipairs(animations) do
                print(string.format("[VIEWER]  %d: %s (%d frames)", i, a.name, #a.frames))
            end
            active = true
            cur_anim = 1
            cur_frame = 1
            frame_timer = 0
            render_frame()
            print("[VIEWER] Active! Left/Right to cycle animations")
        end

        if not active then return end

        -- Handle input
        local change = mem:read_u8(0x10FD97)  -- P1 change (edge)
        if (change & 8) ~= 0 then  -- Right
            cur_anim = cur_anim + 1
            if cur_anim > #animations then cur_anim = 1 end
            cur_frame = 1; frame_timer = 0
            print("[VIEWER] Animation: " .. animations[cur_anim].name)
        elseif (change & 4) ~= 0 then  -- Left
            cur_anim = cur_anim - 1
            if cur_anim < 1 then cur_anim = #animations end
            cur_frame = 1; frame_timer = 0
            print("[VIEWER] Animation: " .. animations[cur_anim].name)
        end

        -- Advance frame
        frame_timer = frame_timer + 1
        if frame_timer >= 8 then
            frame_timer = 0
            cur_frame = cur_frame + 1
            local anim = animations[cur_anim]
            if anim and cur_frame > #anim.frames then
                cur_frame = 1
            end
        end

        render_frame()
    end)
    print("[VIEWER] Clark Viewer plugin loaded")
end

return exports
