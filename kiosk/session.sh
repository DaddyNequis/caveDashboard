#!/bin/sh
# Minimal X session: no desktop, no cursor, no screen blanking.
# matchbox forces every window (browser / mpv) to full screen.
xset s off
xset s noblank
xset -dpms
xsetroot -solid black 2>/dev/null
matchbox-window-manager -use_titlebar no -use_cursor no &
# Leftovers from a previous session would keep the GPU busy.
pkill -u "$(id -u)" -f "cavedashboard/visuals.py"
pkill -u "$(id -u)" -x mpv
exec python3 "$(dirname "$0")/../server.py"
