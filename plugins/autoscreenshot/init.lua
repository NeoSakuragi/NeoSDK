local exports = {name="autoscreenshot",version="1.0",description="live pal",license="MIT",author="d"}
function exports.startplugin()
    local fc = 0
    local saves = {"k", "s", "r"}
    local names = {"Kim", "Saisyu", "Rugal"}
    local si = 1
    local phase = "idle"  -- idle, loading, running, capturing
    local run_frames = 0

    emu.register_frame_done(function()
        fc = fc + 1
        
        if phase == "idle" and fc >= 30 and si <= #saves then
            manager.machine:load(saves[si])
            phase = "loading"
            run_frames = 0
        end
        
        if phase == "loading" then
            run_frames = run_frames + 1
            if run_frames >= 60 then  -- let game run 60 frames to DMA palettes
                phase = "capturing"
            end
        end
        
        if phase == "capturing" then
            local mem = manager.machine.devices[":maincpu"].spaces["program"]
            print(string.format("[P] === %s ===", names[si]))
            manager.machine.video:snapshot()
            
            -- Now read the ACTUAL palette colors from BOTH banks
            -- The game swaps banks each frame, so read both
            for bank_name, bank_base in pairs({bank1=0x400000, bank2=0x600000}) do
                for slot = 16, 31 do
                    local nz = 0
                    local s = ""
                    for i = 0, 15 do
                        local v = mem:read_u16(bank_base + slot*32 + i*2)
                        if v ~= 0 then nz = nz + 1 end
                        s = s .. string.format("%04X ", v)
                    end
                    if nz >= 8 then
                        print(string.format("[P]   %s slot %2d: %s", bank_name, slot, s))
                    end
                end
            end
            
            si = si + 1
            phase = "idle"
        end
        
        if si > #saves and phase == "idle" then
            manager.machine:exit()
        end
    end)
end
return exports
