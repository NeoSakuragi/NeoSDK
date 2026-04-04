local exports = {name="autoscreenshot",version="1.0",description="Clark FINAL v2",license="MIT",author="d"}

local clark_pal = {0x014E,0x4F00,0x7FD9,0x0FB7,0x3D73,0x3941,0x6410,0x1579,0x4358,0x5135,0x0014,0x100F,0x4442,0x5331,0x1220,0x0100}
local ST = 0x080A00
local SD = 0x088A00
local STATES = {0, 1, 2, 11, 14}
local SNAMES = {"idle", "walk_fwd", "walk_back", "guard_air", "knockdown"}

function exports.startplugin()
    local fc, active, mem = 0, false, nil
    local anims, ca, cf, ft, shots = {}, 1, 1, 0, 0

    local function parse()
        for _, sid in ipairs(STATES) do
            local sa = mem:read_u32(ST + sid * 4)
            if sa < 0x080000 then goto nx end
            local frs = {}
            local pos = sa
            for _ = 1, 100 do
                if pos + 6 > 0x200000 then break end
                local w = mem:read_u16(pos)
                if w == 0xFF00 or w == 0xFE00 then break end
                if mem:read_u8(pos) >= 0x80 then pos = pos + 6; goto co end
                local fa = mem:read_u8(pos+1)*65536 + mem:read_u16(pos+2)
                pos = pos + 6
                if fa < 0x080000 then goto co end
                local pts = {}
                local fp = fa
                for _ = 1, 8 do
                    if fp+6 > 0x200000 then break end
                    local yo = mem:read_u16(fp); if yo >= 0x8000 then yo=yo-0x10000 end
                    local xo = mem:read_u16(fp+2); if xo >= 0x8000 then xo=xo-0x10000 end
                    local sw = mem:read_u16(fp+4); local si = sw & 0x1FF
                    local ch = (mem:read_u8(fp+4)>>5) & 1
                    local sp = mem:read_u32(SD + si*4)
                    if sp >= 0x080000 and sp < 0x200000 then
                        local nc=mem:read_u8(sp); local tp=mem:read_u8(sp+1)
                        local bt=((mem:read_u16(sp+2)&0xF)*65536)+mem:read_u16(sp+4)
                        local bm={}
                        for c=0,nc-1 do bm[c+1]=mem:read_u8(sp+6+c) end
                        if nc>0 and nc<=20 and tp>0 and tp<=16 then
                            pts[#pts+1]={y=yo,x=xo,cols=nc,tpc=tp,bt=bt,bm=bm}
                        end
                    end
                    if ch==0 then break end
                    fp=fp+6
                end
                if #pts>0 then frs[#frs+1]={parts=pts} end
                if #frs>=20 then break end
                ::co::
            end
            anims[#anims+1]={name=SNAMES[#anims+1] or "?",frames=frs}
            ::nx::
        end
    end

    local function render()
        local frm = anims[ca] and anims[ca].frames[cf]
        if not frm then return end
        -- Clear our slots via SCB3
        mem:write_u16(0x3C0004, 1)
        mem:write_u16(0x3C0000, 0x8201)
        for s=1,20 do mem:write_u16(0x3C0002, 0) end
        -- Render
        local slot, AX, AY = 1, 160, 180
        for pi, p in ipairs(frm.parts) do
            local scr_y, scr_x = AY + p.x, AX + p.y
            local tc = p.bt
            for col = 0, p.cols - 1 do
                if slot > 20 then break end
                local cbm = p.bm[col+1] or 0xFF
                mem:write_u16(0x3C0004, 1)
                mem:write_u16(0x3C0000, slot * 64)
                for t = 0, p.tpc - 1 do
                    local vis = (t < 8) and ((cbm >> (7-t)) & 1) or 0
                    local tid = 0
                    if vis == 1 then tid = tc; tc = tc + 1 end
                    mem:write_u16(0x3C0002, tid & 0xFFFF)
                    mem:write_u16(0x3C0002, 0x1700 + ((tid >> 16) & 0xF))
                end
                mem:write_u16(0x3C0004, 0x200)
                mem:write_u16(0x3C0000, 0x8000 + slot)
                mem:write_u16(0x3C0002, 0x0FFF)
                local yv = (496 - scr_y) & 0x1FF
                local sticky = col > 0 and 64 or 0
                mem:write_u16(0x3C0002, yv * 128 + sticky + p.tpc)
                if col == 0 then
                    mem:write_u16(0x3C0002, (scr_x & 0x1FF) * 128)
                else
                    mem:write_u16(0x3C0002, 0)
                end
                slot = slot + 1
            end
        end
    end

    -- BEFORE frame: palette + render
    emu.register_frame(function()
        fc = fc + 1
        if not active or not mem then return end
        -- Write Clark palette to BOTH banks every frame
        for i=1,16 do
            mem:write_u16(0x400000+23*32+(i-1)*2, clark_pal[i])
            mem:write_u16(0x600000+23*32+(i-1)*2, clark_pal[i])
        end
        mem:write_u8(0x300001, 0)
        ft = ft + 1
        if ft >= 8 then ft = 0; cf = cf + 1
            if anims[ca] and cf > #anims[ca].frames then cf = 1 end
        end
        render()
    end, "clark")

    -- AFTER frame: also write palette (catch the other bank swap phase)
    emu.register_frame_done(function()
        if fc == 1200 and not active then
            mem = manager.machine.devices[":maincpu"].spaces["program"]
            parse()
            for i,a in ipairs(anims) do print(string.format("[V] %s: %d frames", a.name, #a.frames)) end
            active = true
            print("[V] Active!")
        end
        if not active then return end
        -- Write palette again after frame (covers bank swap)
        for i=1,16 do
            mem:write_u16(0x400000+23*32+(i-1)*2, clark_pal[i])
            mem:write_u16(0x600000+23*32+(i-1)*2, clark_pal[i])
        end
        if fc >= 1500 and fc % 90 == 0 and shots < 5 then
            shots = shots + 1
            manager.machine.video:snapshot()
            print(string.format("[V] Shot %d: %s f%d", shots, anims[ca].name, cf))
            ca = ca + 1; if ca > #anims then ca = 1 end
            cf = 1; ft = 0
        end
        if shots >= 5 then manager.machine:exit() end
    end)
    print("[V] loaded")
end
return exports
