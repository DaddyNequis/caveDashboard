# Cave Dashboard

Convierte una Raspberry Pi (pensado para la **Pi Zero W**) en una pantalla a pantalla completa
que se controla desde el navegador de cualquier dispositivo de la red.

- **Vídeos** (YouTube, Vimeo, Twitch, `.mp4`, `.webm`, `.m3u8`, cámaras `rtsp://`…) → `mpv` con
  decodificación por hardware y `yt-dlp`.
- **Cualquier otra URL** (dashboards, Home Assistant, Grafana, imágenes…) → navegador en modo kiosko.
- Sin escritorio, sin barras, sin cursor. Si no hay nada configurado, la pantalla muestra un reloj y
  la dirección del panel.
- Recuerda la última URL y la vuelve a abrir al arrancar.

## Instalación

1. Graba **Raspberry Pi OS Lite (32-bit)** con Raspberry Pi Imager. Configura ahí la Wi-Fi, el usuario y SSH.
2. En la Pi:

```bash
sudo apt-get install -y git
git clone <este-repo> caveDashboard && cd caveDashboard
sudo ./install.sh
```

3. Abre `http://<ip-de-la-pi>/` desde el móvil o el PC. La IP también aparece en la pantalla.

> Si no usas git: `scp -r caveDashboard pi@<ip>:~` y luego `sudo ./install.sh`.

## Uso

| Campo | Qué hace |
|---|---|
| **Auto** | Detecta si la URL es un vídeo o una web |
| **Página web / Vídeo** | Fuerza el modo, por ejemplo para ver la web de YouTube en lugar del vídeo |
| **Repetir vídeo** | Reproduce el vídeo en bucle; si no está marcado, al terminar vuelve a la pantalla de espera |
| **Recargar** | Vuelve a abrir lo que se está mostrando |
| **Detener** | Muestra la pantalla de espera y olvida la URL |

## API

```bash
curl -X POST http://<ip>/api/show -H 'Content-Type: application/json' \
  -d '{"url":"https://youtu.be/dQw4w9WgXcQ","mode":"auto","loop":true}'
curl -X POST http://<ip>/api/stop -d '{}'
curl http://<ip>/api/status
```

## Configuración

`/etc/default/cavedashboard` (después ejecuta `sudo systemctl restart cavedashboard`):

| Variable | Por defecto | |
|---|---|---|
| `CAVE_PORT` | `80` | Puerto del panel |
| `CAVE_BROWSER` | autodetectado | Ruta al navegador (chromium o firefox) |
| `CAVE_MPV_ARGS` | — | Argumentos extra para mpv, p. ej. `--hwdec=v4l2m2m-copy` |
| `CAVE_BROWSER_ARGS` | — | Argumentos extra para el navegador, p. ej. `--force-device-scale-factor=1.5` |

## Notas sobre la Pi Zero W

- **Vídeo**: hasta 720p en H.264 va fluido. El panel limita la calidad de YouTube y prefiere H.264
  (ajustable a 360p, 480p, 720p o 1080p).
- **YouTube tarda ~15–30 s en empezar**, porque `yt-dlp` es lento en esta CPU. Se actualiza solo una vez al día.
- **Páginas web**: Chromium funciona, pero en una Zero W es lento (~20–30 s en abrir). Los dashboards
  estáticos o ligeros van bien; las webs pesadas, no. Una Pi Zero 2 W cambia mucho la experiencia con el mismo software.
- **YouTube y JavaScript**: las versiones recientes de `yt-dlp` necesitan un runtime de JS (deno o node)
  para tener todos los formatos de YouTube. Deno no existe para ARMv6. Si hay `node` en el sistema,
  se usa automáticamente. Sin él, YouTube suele funcionar igualmente, pero con menos formatos.

## Problemas comunes

```bash
journalctl -u cavedashboard -f                       # logs del servidor
cat ~/.local/share/cavedashboard/mpv.log             # último error de mpv
cat ~/.local/share/cavedashboard/browser.log         # último error del navegador
sudo systemctl restart cavedashboard
```

- **Pantalla negra si la Pi arranca sin el monitor encendido**: añade `video=HDMI-A-1:1920x1080@60D`
  al final de `/boot/firmware/cmdline.txt`.
- **Vídeo a saltos**: baja la calidad a 480p o prueba `CAVE_MPV_ARGS="--hwdec=v4l2m2m-copy"`.
- **La web sale con bordes o zoom raro**: ajusta `CAVE_BROWSER_ARGS="--force-device-scale-factor=1"`.

## Desinstalar

```bash
sudo systemctl disable --now cavedashboard
sudo rm -rf /opt/cavedashboard /etc/systemd/system/cavedashboard.service \
  /etc/default/cavedashboard /etc/sudoers.d/cavedashboard
```

## Estructura

```
server.py              servidor HTTP + gestor de procesos (Python stdlib, sin dependencias)
static/index.html      panel de control
static/idle.html       pantalla de espera (reloj + IP)
kiosk/session.sh       sesión X mínima (sin escritorio, sin cursor, sin salvapantallas)
kiosk/cavedashboard.service   servicio systemd que arranca X en tty1
install.sh             instalador para Raspberry Pi OS Lite
```

## Desarrollo en local

```bash
CAVE_PORT=8080 python3 server.py   # en macOS/Linux; lanza el navegador/mpv que encuentre en el PATH
```
