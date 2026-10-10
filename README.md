# Cave Dashboard

Convierte una Raspberry Pi (pensado para la **Pi Zero W**) en una pantalla a pantalla completa
que se controla desde el navegador de cualquier dispositivo de la red.

- **Vídeos** (YouTube, Vimeo, Twitch, `.mp4`, `.webm`, `.m3u8`, cámaras `rtsp://`…) → `mpv` con
  decodificación por hardware y `yt-dlp`.
- **Visuales** (plasma, túnel, aurora, lámpara de lava, hiperespacio, matrix, fuego, caleidoscopio,
  reloj) → shaders GLSL en la GPU, a 30 FPS incluso en una Pi Zero W.
- **Cualquier otra URL** (dashboards, Home Assistant, Grafana, imágenes…) → navegador en modo kiosko
  (no disponible en Pi Zero W / Pi 1, ver abajo).
- Sin escritorio, sin barras, sin cursor. Si no hay nada configurado, la pantalla muestra el visual
  **Reloj** con la hora, la fecha y la dirección del panel.
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
| **Visuales** | Toca una tarjeta para mostrarlo; las miniaturas son los mismos shaders en vivo |
| **Aleatorio** | Rota entre todos los visuales con un fundido (intervalo en Ajustes) |

## API

```bash
curl -X POST http://<ip>/api/show -H 'Content-Type: application/json' \
  -d '{"url":"https://youtu.be/dQw4w9WgXcQ","mode":"auto","loop":true}'
curl -X POST http://<ip>/api/show -d '{"url":"visual:aurora"}'      # o visual:aleatorio
curl -X POST http://<ip>/api/settings -d '{"visual_quality":3,"visual_speed":1.5,"cycle_minutes":10}'
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

## Visuales

`visuals.py` dibuja shaders GLSL ES 1.0 con EGL + OpenGL ES 2 usando solo `ctypes` (sin pip ni
`libGLESv2`). Renderiza a resolución reducida (1/3 de la pantalla en calidad Normal) y escala con la GPU.

Medido en una Pi Zero W con pantalla 1080p, calidad Normal: todos van a 29–30 FPS y usan ~20 % de CPU.
La calidad Alta (1/2) baja a 21–27 FPS en la Zero W; úsala en placas más potentes.

```bash
DISPLAY=:0 python3 /opt/cavedashboard/visuals.py --stats plasma        # FPS cada 5 s
DISPLAY=:0 python3 /opt/cavedashboard/visuals.py --snapshot /tmp/a.png aurora   # captura un fotograma
```

Para añadir uno nuevo: añade su shader a `SHADERS` y su nombre a `CATALOG` en `visuals.py`.
Recibe `u_time` (segundos) y `u_res` (resolución). Aparece solo en el panel, con miniatura.

## Notas sobre la Pi Zero W

- **Sin páginas web**: Chromium y Firefox exigen NEON, que el ARMv6 de la Zero W / Pi 1 no tiene.
  El instalador lo detecta y no instala el navegador. Vídeos y visuales sí funcionan. Una Pi Zero 2 W
  u Orange Pi Zero 3 sí pueden abrir webs.

- **Vídeo**: hasta 720p en H.264 va fluido. El panel limita la calidad de YouTube y prefiere H.264
  (ajustable a 360p, 480p, 720p o 1080p).
- **YouTube tarda ~15–30 s en empezar**, porque `yt-dlp` es lento en esta CPU. Se actualiza solo una vez al día.
- **YouTube y JavaScript**: las versiones recientes de `yt-dlp` necesitan un runtime de JS (deno o node)
  para tener todos los formatos de YouTube. Deno no existe para ARMv6. Si hay `node` en el sistema,
  se usa automáticamente. Sin él, YouTube suele funcionar igualmente, pero con menos formatos.

## Problemas comunes

```bash
journalctl -u cavedashboard -f                       # logs del servidor
cat ~/.local/share/cavedashboard/mpv.log             # último error de mpv
cat ~/.local/share/cavedashboard/browser.log         # último error del navegador
cat ~/.local/share/cavedashboard/visuals.log         # último error de los visuales
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
visuals.py             visuales GPU (shaders GLSL vía EGL/GLES2 con ctypes)
static/index.html      panel de control
static/idle.html       pantalla de espera en navegador (si la GPU no está disponible)
kiosk/session.sh       sesión X mínima (sin escritorio, sin cursor, sin salvapantallas)
kiosk/cavedashboard.service   servicio systemd que arranca X en tty1
install.sh             instalador para Raspberry Pi OS Lite
```

## Desarrollo en local

```bash
CAVE_PORT=8080 python3 server.py   # en macOS/Linux; lanza el navegador/mpv que encuentre en el PATH
```
