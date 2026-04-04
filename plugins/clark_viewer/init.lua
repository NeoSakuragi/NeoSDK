local exports = {
    name = "clark_viewer",
    version = "3.0",
    description = "test",
    license = "MIT",
    author = "demo"
}
function exports.startplugin()
    local fc = 0
    emu.register_frame_done(function()
        fc = fc + 1
        if fc == 300 then
            manager.machine.video:snapshot()
            print("[V] shot 1")
        end
        if fc == 350 then
            manager.machine:exit()
        end
    end)
    print("[V] loaded")
end
return exports
