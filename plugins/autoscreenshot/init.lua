local exports = {name="autoscreenshot",version="1.0",description="boot test",license="MIT",author="d"}
function exports.startplugin()
    local fc = 0
    emu.register_frame_done(function()
        fc = fc + 1
        if fc % 60 == 0 and fc <= 600 then
            local mem = manager.machine.devices[":maincpu"].spaces["program"]
            local ram = mem:read_u32(0x100000)
            local sys = mem:read_u8(0x10FD80)
            local req = mem:read_u8(0x10FDAE)
            local mode = mem:read_u8(0x10FDCB)
            local bg = mem:read_u16(0x400000)
            print(string.format("[V] f=%d RAM=%08X SYS=%02X REQ=%02X MODE=%02X bg=%04X",
                fc, ram, sys, req, mode, bg))
            manager.machine.video:snapshot()
        end
        if fc >= 650 then manager.machine:exit() end
    end)
end
return exports
