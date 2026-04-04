-- KOF95 Special Move Command Tracer v2
-- Scans for active character objects and logs ALL state changes with input history.

local cpu = manager.machine.devices[":maincpu"]
local mem = cpu.spaces["program"]
local frame = 0
local out = io.open("command_trace.txt", "w")

local A5 = 0x108000
local P1_CURRENT = A5 + 0x57B6

-- Extended input history (we record every frame)
local p1_history = {}
local p2_history = {}

-- Track state per object slot
local prev_states = {}

local function numpad(bits)
    local d = bits & 0x0F
    local map = {
        [0]="5",[1]="8",[2]="2",[3]="?",[4]="4",[5]="7",
        [6]="1",[7]="?",[8]="6",[9]="9",[10]="3",[11]="?",
        [12]="?",[13]="?",[14]="?",[15]="?"
    }
    local s = map[d] or "?"
    if bits & 0x10 ~= 0 then s = s .. "A" end
    if bits & 0x20 ~= 0 then s = s .. "B" end
    if bits & 0x40 ~= 0 then s = s .. "C" end
    if bits & 0x80 ~= 0 then s = s .. "D" end
    return s
end

local logged = 0

emu.register_frame_done(function()
    frame = frame + 1

    -- Record raw input every frame
    local p1 = mem:read_u8(P1_CURRENT)
    local p2 = mem:read_u8(P1_CURRENT + 1)
    table.insert(p1_history, p1)
    table.insert(p2_history, p2)
    if #p1_history > 60 then table.remove(p1_history, 1) end
    if #p2_history > 60 then table.remove(p2_history, 1) end

    -- Scan character object slots for state changes
    -- Objects are at $108100, $108300, and at $10B0A8+ (8 slots, $100 apart)
    local slots = {0x108100, 0x108300}
    for i = 0, 7 do
        table.insert(slots, 0x10B0A8 + i * 0x100)
    end

    for _, addr in ipairs(slots) do
        local handler = mem:read_u32(addr)
        -- Check if this looks like a valid handler pointer (in code area)
        if handler >= 0x7000 and handler < 0x40000 then
            local state72 = mem:read_u16(addr + 0x72)
            local key = string.format("%06X", addr)

            if prev_states[key] == nil then
                prev_states[key] = {state = state72, handler = handler}
            end

            local ps = prev_states[key]
            if state72 ~= ps.state then
                -- State changed! Log it with input history
                local facing = mem:read_u8(addr + 0x31) & 1
                local e0 = mem:read_u16(addr + 0xE0)

                -- Build input history string (last 20 frames)
                local hist = p1_history
                if addr == 0x108300 then hist = p2_history end
                local hist_str = ""
                for i = math.max(1, #hist - 19), #hist do
                    hist_str = hist_str .. " " .. numpad(hist[i])
                end

                local line = string.format(
                    "[%05d] obj=$%06X state %3d->%3d handler=$%06X face=%s E0=$%04X\n" ..
                    "  input:%s\n",
                    frame, addr, ps.state, state72, handler,
                    facing == 0 and "R" or "L", e0, hist_str)

                out:write(line)
                out:flush()
                logged = logged + 1

                if logged % 10 == 0 then
                    print(string.format("[%d] %d transitions logged", frame, logged))
                end

                ps.state = state72
                ps.handler = handler
            end
        end
    end
end)

out:write("KOF95 Command Tracer v2\n")
out:write("Scanning object slots for state transitions with input history.\n\n")
print("Command tracer v2 active - scanning all object slots")
