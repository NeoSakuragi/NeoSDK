local exports = {name="autoscreenshot",version="1.0",description="detail scan",license="MIT",author="d"}
function exports.startplugin()
    local fc = 0
    emu.register_frame_done(function()
        fc = fc + 1
        if fc == 30 then manager.machine:load("k") end
        if fc == 90 then
            local mem = manager.machine.devices[":maincpu"].spaces["program"]
            
            -- Show sprites using pal 220 (0xDC) = P1, and 208 (0xD0)
            -- Include position info to identify body vs accessory
            print("[P] === P1 sprites (pal 0xDC = 220) ===")
            for spr = 0, 380 do
                mem:write_u16(0x3C0000, 0x8200 + spr)
                local scb3 = mem:read_u16(0x3C0002)
                local height = scb3 & 0x3F
                if height == 0 then goto next end
                
                mem:write_u16(0x3C0000, spr * 64 + 1)
                local attr = mem:read_u16(0x3C0002)
                local pal = (attr >> 8) & 0xFF
                
                if pal == 0xDC or pal == 0xD0 or pal == 0xC3 or pal == 0xBC then
                    mem:write_u16(0x3C0000, spr * 64)
                    local tile = mem:read_u16(0x3C0002)
                    local sticky = (scb3 >> 6) & 1
                    local y = (scb3 >> 7) & 0x1FF
                    mem:write_u16(0x3C0000, 0x8400 + spr)
                    local scb4 = mem:read_u16(0x3C0002)
                    local x = (scb4 >> 7) & 0x1FF
                    
                    print(string.format("[P]   spr %3d: pal=0x%02X tile=$%04X h=%d Y=%d X=%d sticky=%d",
                        spr, pal, tile, height, 496-y, x, sticky))
                end
                ::next::
            end
            
            -- Also dump the actual palette data at slots 220 and 208
            print("\n[P] === Palette 220 (0xDC) ===")
            local s = ""
            for i = 0, 15 do
                s = s .. string.format("%04X ", mem:read_u16(0x400000 + 220*32 + i*2))
            end
            print("[P]   " .. s)
            
            print("[P] === Palette 208 (0xD0) ===")
            s = ""
            for i = 0, 15 do
                s = s .. string.format("%04X ", mem:read_u16(0x400000 + 208*32 + i*2))
            end
            print("[P]   " .. s)
            
            print("[P] === Palette 195 (0xC3) ===")
            s = ""
            for i = 0, 15 do
                s = s .. string.format("%04X ", mem:read_u16(0x400000 + 195*32 + i*2))
            end
            print("[P]   " .. s)
            
            manager.machine:exit()
        end
    end)
end
return exports
