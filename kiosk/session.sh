#!/bin/sh
# Minimal X session: no desktop, no cursor, no screen blanking.
# matchbox forces every window (browser / mpv) to full screen.
xset s off
xset s noblank
xset -dpms
xsetroot -solid black 2>/dev/null
matchbox-window-manager -use_titlebar no -use_cursor no &
exec python3 "$(dirname "$0")/../server.py"
