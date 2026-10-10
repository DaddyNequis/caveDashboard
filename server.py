#!/usr/bin/env python3
"""Cave Dashboard: kiosk display controller for Raspberry Pi.

Serves a small control panel over HTTP and shows the configured URL full
screen: video URLs (YouTube, mp4, streams...) are played with mpv, GPU visuals
(visual:<name>) run with visuals.py, everything else is opened in a kiosk
browser. Python standard library only.
"""

import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import visuals

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("CAVE_DATA", Path.home() / ".local/share/cavedashboard"))
STATE_FILE = DATA_DIR / "state.json"
PORT = int(os.environ.get("CAVE_PORT", "8080"))
YTDLP = os.environ.get("CAVE_YTDLP") or str(APP_DIR / "bin/yt-dlp")
MPV_EXTRA = shlex.split(os.environ.get("CAVE_MPV_ARGS", ""))
BROWSER_EXTRA = shlex.split(os.environ.get("CAVE_BROWSER_ARGS", ""))
HISTORY_MAX = 15
VISUAL_PREFIX = "visual:"
IDLE_URL = f"http://127.0.0.1:{PORT}/idle"

VIDEO_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "vimeo.com",
    "twitch.tv", "dailymotion.com",
)
VIDEO_EXT = (
    ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".ts", ".m3u8", ".mpd",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav",
)
STREAM_SCHEMES = ("rtsp", "rtsps", "rtmp", "rtmps", "srt", "udp")

DEFAULT_SETTINGS = {"max_height": 720, "volume": 100, "visual_quality": 3, "visual_speed": 1.0, "cycle_minutes": 5}
VISUAL_SETTINGS = ("visual_quality", "visual_speed", "cycle_minutes")


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            return s.getsockname()[0]
    except OSError:
        return ""


def detect_mode(url):
    if url.startswith(VISUAL_PREFIX):
        return "visual"
    p = urlparse(url)
    if p.scheme in STREAM_SCHEMES:
        return "video"
    host = (p.hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
        return "video"
    if p.path.lower().endswith(VIDEO_EXT):
        return "video"
    return "web"


def item_mode(item):
    return item["mode"] if item["mode"] != "auto" else detect_mode(item["url"])


def normalize_url(url):
    url = url.strip()
    if url.lower().startswith(VISUAL_PREFIX):
        return url.lower()
    if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        if re.match(r"^(javascript|data|file|about|chrome|view-source|blob):", url, re.I):
            raise ValueError("URL no válida (usa http:// o https://)")
        host = re.split(r"[:/?#]", url, maxsplit=1)[0].lower()
        is_local = (host == "localhost" or host.endswith(".local")
                    or re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host) is not None)
        url = ("http://" if is_local else "https://") + url
    return url


def validate_url(url, mode):
    if mode == "visual":
        name = url[len(VISUAL_PREFIX):]
        if not url.startswith(VISUAL_PREFIX) or (name not in visuals.CATALOG and name != visuals.SHUFFLE):
            raise ValueError("Visual desconocido")
        return
    p = urlparse(url)
    allowed = ("http", "https") + (STREAM_SCHEMES if mode != "web" else ())
    try:
        p.port  # raises on a malformed port
    except ValueError:
        raise ValueError("URL no válida: puerto incorrecto")
    if p.scheme not in allowed or not p.hostname:
        raise ValueError("URL no válida (usa http:// o https://)")


def cpu_has_neon():
    """Chromium and Firefox need NEON; the Pi Zero/1 (ARMv6) lacks it."""
    machine = os.uname().machine
    if not machine.startswith("arm"):
        return True  # aarch64 always has it; x86 dev machines don't need it
    try:
        return bool(re.search(r"^Features\s*:.*\bneon\b", Path("/proc/cpuinfo").read_text(), re.M))
    except OSError:
        return True


def find_browser():
    custom = os.environ.get("CAVE_BROWSER")
    if custom:
        return custom
    if not cpu_has_neon():
        return None
    for name in ("chromium-browser", "chromium", "firefox-esr", "firefox"):
        path = shutil.which(name)
        if path:
            return path
    return None


def screen_size():
    try:
        out = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"current (\d+) x (\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except (OSError, subprocess.SubprocessError):
        pass
    return None


class State:
    """Persisted configuration: what to show and past URLs."""

    def __init__(self):
        self.current = None  # {"url", "mode", "loop", "mute"}
        self.history = []
        self.settings = dict(DEFAULT_SETTINGS)
        self.lock = threading.Lock()
        try:
            data = json.loads(STATE_FILE.read_text())
            self.current = data.get("current")
            self.history = data.get("history", [])
            self.settings.update(data.get("settings", {}))
        except (OSError, ValueError):
            pass

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"current": self.current, "history": self.history, "settings": self.settings}, indent=2))
        tmp.replace(STATE_FILE)

    def add_history(self, item):
        self.history = [h for h in self.history if h["url"] != item["url"]]
        self.history.insert(0, {k: item[k] for k in ("url", "mode", "loop", "mute")})
        del self.history[HISTORY_MAX:]


class Player:
    """Owns the single process (browser, mpv or visuals) currently on screen."""

    def __init__(self, state):
        self.state = state
        self.proc = None
        self.kind = None  # what is shown: "web" | "video" | "visual" | "idle"
        self.backend = None  # what shows it: "browser" | "mpv" | "visuals"
        self.status = "starting"
        self.error = None
        self.since = time.time()
        self.lock = threading.RLock()
        self.browser = find_browser()
        self.visuals_ok = True
        self.crashes = 0

    # --- public -----------------------------------------------------------

    def show(self, item):
        with self.lock:
            self._stop_proc()
            self.error = None
            self.crashes = 0
            self._launch(item)

    def idle(self, error=None):
        with self.lock:
            self._stop_proc()
            self.error = error
            self._start_idle()

    def restore(self):
        if self.state.current:
            self.show(self.state.current)
        else:
            self.idle()

    def refresh_visual(self):
        """Apply changed visual settings to whatever visual is on screen."""
        with self.lock:
            if self.backend != "visuals" or not self.proc:
                return
            kind = self.kind
            self._stop_proc()
            if kind == "idle" or not self.state.current:
                self._start_idle()
            else:
                self._launch(self.state.current)

    def shutdown(self):
        with self.lock:
            self._stop_proc()

    def info(self):
        return {"kind": self.kind, "backend": self.backend, "status": self.status, "error": self.error,
                "since": self.since}

    # --- internals --------------------------------------------------------

    def _launch(self, item):
        mode = item_mode(item)
        if mode == "video":
            self._start_video(item)
        elif mode == "visual":
            self._start_visual(item["url"][len(VISUAL_PREFIX):], "visual")
        else:
            self._start_browser(item["url"], "web")

    def _start_idle(self):
        if self.visuals_ok:
            self._start_visual("reloj", "idle")
        else:
            self._start_browser(IDLE_URL, "idle")

    def _spawn(self, cmd, kind, backend, logname):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logf = open(DATA_DIR / logname, "w")
        log("exec:", " ".join(shlex.quote(c) for c in cmd))
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as e:
            logf.close()
            self.kind, self.backend, self.status = kind, backend, "error"
            self.error = f"No se pudo lanzar {cmd[0]}: {e}"
            return
        logf.close()
        self.proc, self.kind, self.backend, self.status, self.since = proc, kind, backend, "playing", time.time()
        threading.Thread(target=self._watch, args=(proc, kind, backend, logname, time.monotonic()),
                         daemon=True).start()

    def _start_visual(self, name, kind):
        s = self.state.settings
        cmd = [sys.executable, str(APP_DIR / "visuals.py"),
               "--scale", str(int(s["visual_quality"])), "--speed", str(float(s["visual_speed"])),
               "--cycle", str(int(s["cycle_minutes"]) * 60), name]
        self._spawn(cmd, kind, "visuals", "visuals.log")

    def _start_video(self, item):
        s = self.state.settings
        h = int(s.get("max_height", 720))
        fmt = f"bv*[height<={h}][vcodec^=avc1]+ba/b[height<={h}]/bv*[height<={h}]+ba/b"
        cmd = [
            "mpv", "--fs", "--quiet", "--input-terminal=no", "--msg-level=all=warn", "--no-osc", "--osd-level=0",
            "--no-input-default-bindings", "--input-vo-keyboard=no", "--cursor-autohide=always",
            "--hwdec=auto-safe", "--keep-open=no", f"--volume={int(s.get('volume', 100))}",
            f"--ytdl-format={fmt}",
        ]
        if os.path.exists(YTDLP):
            cmd.append(f"--script-opts=ytdl_hook-ytdl_path={YTDLP}")
        if shutil.which("node"):
            cmd.append("--ytdl-raw-options-append=js-runtimes=node")
        if item.get("loop"):
            cmd.append("--loop-playlist=inf" if "list=" in item["url"] else "--loop-file=inf")
        if item.get("mute"):
            cmd.append("--mute=yes")
        cmd += MPV_EXTRA + ["--", item["url"]]
        self._spawn(cmd, "video", "mpv", "mpv.log")

    def _start_browser(self, url, kind):
        if not self.browser:
            self.kind, self.backend, self.status = kind, "browser", "error"
            self.error = ("Este equipo no puede abrir páginas web (su CPU no es compatible con los navegadores "
                          "actuales). Usa vídeos o visuales." if not cpu_has_neon()
                          else "No hay navegador instalado (chromium o firefox)")
            return
        profile = DATA_DIR / "browser"
        profile.mkdir(parents=True, exist_ok=True)
        if "firefox" in os.path.basename(self.browser):
            cmd = [self.browser, "--kiosk", "--profile", str(profile), "--no-remote"]
        else:
            self._clear_chromium_crash_flag(profile)
            cmd = [
                self.browser, "--kiosk", "--start-fullscreen", "--noerrdialogs", "--disable-infobars",
                "--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble",
                "--disable-features=Translate,TranslateUI,MediaRouter", "--disable-pinch",
                "--overscroll-history-navigation=0", "--autoplay-policy=no-user-gesture-required",
                "--check-for-update-interval=31536000", "--password-store=basic",
                "--enable-low-end-device-mode", f"--user-data-dir={profile}",
                "--window-position=0,0",
            ]
            size = screen_size()
            if size:
                cmd.append(f"--window-size={size[0]},{size[1]}")
        cmd += BROWSER_EXTRA + [url]
        self._spawn(cmd, kind, "browser", "browser.log")

    @staticmethod
    def _clear_chromium_crash_flag(profile):
        prefs = profile / "Default" / "Preferences"
        try:
            text = prefs.read_text()
            text = text.replace('"exited_cleanly":false', '"exited_cleanly":true')
            text = text.replace('"exit_type":"Crashed"', '"exit_type":"Normal"')
            prefs.write_text(text)
        except OSError:
            pass

    def _stop_proc(self):
        proc, self.proc = self.proc, None
        if not proc or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        except ProcessLookupError:
            pass

    def _watch(self, proc, kind, backend, logname, started):
        code = proc.wait()
        with self.lock:
            if proc is not self.proc:
                return  # stopped on purpose
            self.proc = None
            log(f"{backend} ({kind}) exited with code {code}")
            if backend == "mpv":
                if code == 0:
                    self.idle()
                else:
                    self.idle(error=f"No se pudo reproducir el vídeo: {self._tail(logname)}")
                return
            if backend == "visuals" and code != 0 and time.monotonic() - started < 15:
                # Failed right away: no usable GPU/display for visuals.
                msg = f"No se pudieron iniciar los visuales: {self._tail(logname)}"
                if kind == "idle":
                    self.visuals_ok = False
                    self.error = msg
                    self._start_idle()
                else:
                    self.idle(error=msg)
                return
            # Died after running for a while: restart with backoff.
            self.crashes += 1
            self.status = "error"
            what = "Los visuales se cerraron" if backend == "visuals" else "El navegador se cerró"
            self.error = f"{what} (código {code}), reiniciando…"
        time.sleep(min(60, 3 * self.crashes))
        with self.lock:
            if self.proc is None and self.status == "error" and self.kind == kind:
                if kind == "idle" or not self.state.current:
                    self._start_idle()
                else:
                    self._launch(self.state.current)

    @staticmethod
    def _tail(logname):
        try:
            lines = [l.strip() for l in (DATA_DIR / logname).read_text(errors="replace").splitlines() if l.strip()]
            errors = [l for l in lines if "error" in l.lower() and not l.startswith("Exiting")]
            return (errors or lines or ["error desconocido"])[-1][:300]
        except OSError:
            return "error desconocido"


def update_ytdlp():
    """Keep yt-dlp fresh: YouTube breaks old versions often."""
    stamp = DATA_DIR / "ytdlp-updated"
    try:
        if time.time() - stamp.stat().st_mtime < 86400:
            return
    except OSError:
        pass
    if not os.access(YTDLP, os.W_OK):
        return
    log("updating yt-dlp…")
    try:
        r = subprocess.run([YTDLP, "-U"], capture_output=True, text=True, timeout=300)
        log("yt-dlp:", (r.stdout or r.stderr).strip().splitlines()[-1:] or "")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    except (OSError, subprocess.SubprocessError) as e:
        log("yt-dlp update failed:", e)


class Handler(BaseHTTPRequestHandler):
    server_version = "CaveDashboard/1.0"
    state: State = None
    player: Player = None

    def log_message(self, fmt, *args):
        pass

    # --- helpers ------------------------------------------------------------

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name):
        try:
            self._send(200, (STATIC_DIR / name).read_bytes(), "text/html; charset=utf-8")
        except OSError:
            self._send(404, {"error": "not found"})

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            raise ValueError("Petición demasiado grande")
        return json.loads(self.rfile.read(length) or b"{}")

    def _status(self):
        st, pl = self.state, self.player
        with st.lock:
            return {
                "current": st.current,
                "history": st.history,
                "settings": st.settings,
                "player": pl.info(),
                "browser": pl.browser,
                "web_ok": bool(pl.browser),
                "visuals": [{"id": k, "name": n, "desc": d} for k, (n, d) in visuals.CATALOG.items()],
                "ip": local_ip(),
                "port": PORT,
                "hostname": socket.gethostname(),
            }

    # --- routes -------------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._file("index.html")
        elif path == "/idle":
            self._file("idle.html")
        elif path == "/api/status":
            self._send(200, self._status())
        elif path == "/api/shaders":
            self._send(200, {"vert": visuals.VERT, "header": visuals.HEADER,
                             "shaders": {k: v for k, v in visuals.SHADERS.items() if k != "reloj"}})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._json_body()
            if path == "/api/show":
                self._show(body)
            elif path == "/api/stop":
                with self.state.lock:
                    self.state.current = None
                    self.state.save()
                self.player.idle()
            elif path == "/api/reload":
                self.player.restore()
            elif path == "/api/settings":
                self._settings(body)
            elif path == "/api/history/delete":
                with self.state.lock:
                    self.state.history = [h for h in self.state.history if h["url"] != body.get("url")]
                    self.state.save()
            elif path == "/api/system":
                self._system(body.get("action"))
            else:
                return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        self._send(200, self._status())

    def _show(self, body):
        url = normalize_url(str(body.get("url", "")))
        mode = body.get("mode", "auto")
        if mode not in ("auto", "web", "video", "visual"):
            raise ValueError("Modo no válido")
        if url.startswith(VISUAL_PREFIX):
            mode = "visual"
        validate_url(url, mode if mode != "auto" else detect_mode(url))
        item = {"url": url, "mode": mode, "loop": bool(body.get("loop", True)), "mute": bool(body.get("mute"))}
        with self.state.lock:
            self.state.current = item
            self.state.add_history(item)
            self.state.save()
        self.player.show(item)

    def _settings(self, body):
        with self.state.lock:
            if "max_height" in body:
                h = int(body["max_height"])
                if h not in (360, 480, 720, 1080):
                    raise ValueError("Calidad no válida")
                self.state.settings["max_height"] = h
            if "volume" in body:
                self.state.settings["volume"] = max(0, min(130, int(body["volume"])))
            if "visual_quality" in body:
                q = int(body["visual_quality"])
                if q not in (2, 3, 4):
                    raise ValueError("Calidad no válida")
                self.state.settings["visual_quality"] = q
            if "visual_speed" in body:
                self.state.settings["visual_speed"] = round(max(0.25, min(3.0, float(body["visual_speed"]))), 2)
            if "cycle_minutes" in body:
                self.state.settings["cycle_minutes"] = max(1, min(120, int(body["cycle_minutes"])))
            self.state.save()
        if any(k in body for k in VISUAL_SETTINGS):
            self.player.refresh_visual()

    def _system(self, action):
        cmds = {"reboot": ["sudo", "-n", "/usr/bin/systemctl", "reboot"],
                "poweroff": ["sudo", "-n", "/usr/bin/systemctl", "poweroff"]}
        if action not in cmds:
            raise ValueError("Acción no válida")
        r = subprocess.run(cmds[action], capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError(f"No se pudo ejecutar: {r.stderr.strip() or r.returncode}")


def main():
    state = State()
    player = Player(state)
    Handler.state, Handler.player = state, player

    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True

    def stop(*_):
        player.shutdown()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    # xinit sends SIGHUP when X goes away: clean up children in every case.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)

    log(f"Cave Dashboard on http://{local_ip() or 'localhost'}:{PORT}  (browser: {player.browser})")
    threading.Thread(target=update_ytdlp, daemon=True).start()
    # Small delay so the idle page can be served as soon as the browser asks for it.
    threading.Timer(0.5, player.restore).start()
    httpd.serve_forever()
    player.shutdown()


if __name__ == "__main__":
    main()
