#!/bin/sh
# Launch Clark Demo homebrew in MAME
# Uses custom softlist entry via -hashpath
mame neogeo clarkdemo \
    -hashpath "./hash;/usr/share/games/mame/hash" \
    -rompath "./roms" \
    -noautosave -skip_gameinfo "$@"
