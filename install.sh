#!/usr/bin/env bash
# Cave Dashboard installer for Raspberry Pi OS Lite (Pi Zero W and up).
# Usage: sudo ./install.sh [user]
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta con sudo: sudo ./install.sh"; exit 1; }

USER_NAME="${1:-${SUDO_USER:-pi}}"
APP=/opt/cavedashboard
SRC="$(cd "$(dirname "$0")" && pwd)"
id "$USER_NAME" >/dev/null 2>&1 || { echo "El usuario '$USER_NAME' no existe"; exit 1; }

echo "==> Instalando paquetes (tarda un rato en una Pi Zero)"
apt-get update
apt-get install -y --no-install-recommends \
  xserver-xorg xserver-xorg-legacy xinit x11-xserver-utils matchbox-window-manager \
  mpv python3 ca-certificates curl fonts-dejavu-core

echo "==> Instalando navegador"
BROWSER=""
for pkg in chromium chromium-browser firefox-esr; do
  if apt-cache show "$pkg" >/dev/null 2>&1 && apt-get install -y --no-install-recommends "$pkg"; then
    BROWSER="$pkg"; break
  fi
done
[ -n "$BROWSER" ] && echo "    navegador: $BROWSER" || echo "    AVISO: no se pudo instalar navegador; solo funcionarán los vídeos"

echo "==> Copiando aplicación a $APP"
install -d "$APP/bin"
cp -r "$SRC/server.py" "$SRC/static" "$SRC/kiosk" "$APP/"
chmod +x "$APP/server.py" "$APP/kiosk/session.sh"

echo "==> Descargando yt-dlp"
curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o "$APP/bin/yt-dlp"
chmod +x "$APP/bin/yt-dlp"
chown -R "$USER_NAME": "$APP"   # so the server can self-update yt-dlp

echo "==> Permisos de X y vídeo para $USER_NAME"
cat > /etc/X11/Xwrapper.config <<CFG
allowed_users=anybody
needs_root_rights=yes
CFG
for g in video input render tty audio; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$USER_NAME"
done

echo "==> Permitir reiniciar/apagar desde el panel"
cat > /etc/sudoers.d/cavedashboard <<CFG
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff
CFG
chmod 440 /etc/sudoers.d/cavedashboard
visudo -cf /etc/sudoers.d/cavedashboard >/dev/null

if [ ! -f /etc/default/cavedashboard ]; then
  cat > /etc/default/cavedashboard <<'CFG'
# Cave Dashboard settings (restart with: sudo systemctl restart cavedashboard)
CAVE_PORT=80
# CAVE_BROWSER=/usr/bin/chromium
# CAVE_MPV_ARGS="--hwdec=v4l2m2m-copy"
# CAVE_BROWSER_ARGS="--force-device-scale-factor=1.5"
CFG
fi

echo "==> Servicio systemd"
sed "s/__USER__/$USER_NAME/" "$SRC/kiosk/cavedashboard.service" > /etc/systemd/system/cavedashboard.service
systemctl daemon-reload
systemctl set-default multi-user.target >/dev/null   # no desktop
systemctl enable cavedashboard
systemctl restart cavedashboard

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Listo. Panel de control: http://${IP:-<ip-de-la-pi>}/"
echo "Logs: journalctl -u cavedashboard -f"
