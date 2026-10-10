#!/usr/bin/env python3
"""Full-screen GPU visuals for Cave Dashboard.

GLSL ES 1.0 fragment shaders rendered with EGL + OpenGL ES 2 on an X11 window,
through ctypes only (no pip packages, no libGLESv2 needed: every GL entry point
is resolved with eglGetProcAddress). Shaders render into a reduced-resolution
framebuffer that is then upscaled, so even a Raspberry Pi Zero W (VideoCore IV)
keeps a steady frame rate.

Usage: visuals.py [--scale 3] [--fps 30] [--speed 1] [--cycle SECONDS] NAME [NAME...]
"""

import argparse
import ctypes as C
import os
import signal
import socket
import struct
import sys
import time
import zlib

# Shown in the control panel. Order matters: it is the order of the grid.
CATALOG = {
    "plasma": ("Plasma", "Colores líquidos que se mezclan sin parar"),
    "tunel": ("Túnel", "Viaje infinito por un túnel de cuadros"),
    "aurora": ("Aurora", "Auroras boreales sobre un cielo estrellado"),
    "lava": ("Lámpara de lava", "Burbujas que se funden y se separan"),
    "hiperespacio": ("Hiperespacio", "Estrellas pasando a toda velocidad"),
    "matrix": ("Matrix", "Lluvia de símbolos verdes"),
    "fuego": ("Fuego", "Llamas a pantalla completa"),
    "caleidoscopio": ("Caleidoscopio", "Simetría hipnótica en movimiento"),
    "reloj": ("Reloj", "Hora, fecha y dirección del panel"),
}
SHUFFLE = "aleatorio"

# --------------------------------------------------------------------------
# Shaders
# --------------------------------------------------------------------------

VERT = """
attribute vec2 a_pos;
varying vec2 v_uv;
void main() { v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }
"""

HEADER = """
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform float u_time;
uniform vec2 u_res;
varying vec2 v_uv;
vec3 pal(float t, vec3 a, vec3 b, vec3 c, vec3 d) { return a + b * cos(6.28318 * (c * t + d)); }
float hash(vec2 p) {  /* sin-free hash, stable on low-precision GPUs */
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
vec2 centered() { return (v_uv * 2.0 - 1.0) * vec2(u_res.x / u_res.y, 1.0); }
"""

SHADERS = {
    "plasma": """
void main() {
  vec2 p = centered() * 2.0;
  float t = u_time * 0.6;
  float v = sin(p.x * 1.5 + t) + sin((p.y * 1.3 + t) * 1.1) + sin((p.x + p.y) * 1.2 + t * 1.3);
  vec2 c = p + vec2(sin(t * 0.7) * 2.0, cos(t * 0.5) * 1.5);
  v += sin(length(c) * 2.0 - t);
  vec3 col = pal(v * 0.25 + t * 0.05, vec3(0.5), vec3(0.5), vec3(1.0), vec3(0.0, 0.33, 0.67));
  gl_FragColor = vec4(col, 1.0);
}""",
    "tunel": """
void main() {
  vec2 p = centered();
  p += 0.15 * vec2(sin(u_time * 0.5), cos(u_time * 0.37));
  float r = length(p);
  float a = atan(p.y, p.x);
  vec2 uv = vec2(0.5 / max(r, 0.001) + u_time * 0.9, a / 3.14159 * 4.0 + u_time * 0.08);
  float check = mod(floor(uv.x * 2.0) + floor(uv.y * 2.0), 2.0);
  vec3 c1 = pal(uv.x * 0.05, vec3(0.5), vec3(0.5), vec3(1.0), vec3(0.0, 0.1, 0.2));
  vec3 c2 = pal(uv.x * 0.05 + 0.5, vec3(0.5), vec3(0.5), vec3(1.0), vec3(0.0, 0.1, 0.2)) * 0.35;
  vec3 col = mix(c2, c1, check);
  col *= smoothstep(0.0, 0.7, r);
  col += vec3(1.0, 0.8, 0.6) * 0.05 / (r + 0.05);
  gl_FragColor = vec4(col, 1.0);
}""",
    "aurora": """
void main() {
  vec2 uv = v_uv;
  float t = u_time * 0.3;
  vec3 col = mix(vec3(0.0, 0.01, 0.04), vec3(0.02, 0.04, 0.12), uv.y);
  float rayMod = 0.6 + 0.4 * sin(uv.x * 60.0 + t * 3.0);
  float hue = 0.5 + 0.5 * sin(uv.x * 2.5 + t);
  for (int i = 0; i < 3; i++) {
    float fi = float(i);
    float y = 0.58 + 0.1 * sin(uv.x * (2.0 + fi) * 1.3 + t * (1.0 + fi * 0.3) + fi * 1.7)
                   + 0.04 * sin(uv.x * 7.0 - t * 2.0 + fi);
    float d = uv.y - y;
    float glow = exp(-d * d * (180.0 + fi * 60.0)) * 0.75 + exp(-abs(d) * 9.0) * rayMod * 0.22;
    col += mix(vec3(0.1, 0.95, 0.55), vec3(0.55, 0.25, 0.95), clamp(fi * 0.35 + hue * 0.4, 0.0, 1.0)) * glow;
  }
  float s = hash(floor(v_uv * u_res));
  col += step(0.996, s) * (0.4 + 0.4 * sin(u_time * 2.0 + s * 300.0)) * smoothstep(0.25, 0.9, uv.y);
  gl_FragColor = vec4(col, 1.0);
}""",
    "lava": """
void main() {
  vec2 p = centered();
  float t = u_time * 0.35;
  float f = 0.0;
  for (int i = 0; i < 6; i++) {
    float fi = float(i);
    vec2 c = vec2(sin(t * (0.7 + fi * 0.13) + fi * 2.1) * 1.25, cos(t * (0.5 + fi * 0.17) + fi * 1.3) * 0.75);
    vec2 d = p - c;
    f += (0.07 + 0.025 * sin(fi * 1.7)) / dot(d, d);
  }
  float m = smoothstep(0.95, 1.05, f);
  float rim = smoothstep(0.55, 1.0, f) - m;
  vec3 bg = mix(vec3(0.06, 0.0, 0.08), vec3(0.18, 0.02, 0.1), v_uv.y);
  vec3 lava = pal(min(f, 4.0) * 0.08 + t * 0.05, vec3(0.85, 0.35, 0.2), vec3(0.2, 0.25, 0.1),
                  vec3(1.0), vec3(0.0, 0.1, 0.2));
  vec3 col = mix(bg, lava, m) + rim * vec3(1.0, 0.45, 0.15) * 0.5;
  gl_FragColor = vec4(col, 1.0);
}""",
    "hiperespacio": """
void main() {
  vec2 p = centered();
  vec3 col = vec3(0.0, 0.0, 0.02);
  float t = u_time * 0.25;
  for (int i = 0; i < 3; i++) {
    float fi = float(i);
    float z = fract(fi * 0.3333 + t);
    float scale = mix(14.0, 0.6, z);
    float fade = smoothstep(0.0, 0.35, z) * smoothstep(1.0, 0.85, z);
    vec2 q = p * scale + fi * 17.3;
    vec2 id = floor(q);
    vec2 f = fract(q) - 0.5;
    float h = hash(id + fi * 31.0);
    vec2 off = vec2(h, fract(h * 13.7)) - 0.5;
    float d = length(f - off * 0.7);
    float star = smoothstep(0.09, 0.0, d) + exp(-d * 18.0) * 0.25;
    col += star * step(0.45, h) * fade * mix(vec3(0.6, 0.7, 1.0), vec3(1.0, 0.85, 0.7), h) * (0.4 + z);
  }
  gl_FragColor = vec4(col, 1.0);
}""",
    "matrix": """
void main() {
  float cell = u_res.x / 64.0;
  vec2 frag = v_uv * u_res;
  vec2 g = floor(frag / cell);
  vec2 f = fract(frag / cell);
  float rows = u_res.y / cell;
  float seed = hash(vec2(g.x, 7.0));
  float speed = 5.0 + seed * 12.0;
  float row = rows - g.y;
  float head = mod(u_time * speed + seed * 200.0, rows + 40.0);
  float dist = head - row;
  float trail = dist > 0.0 ? exp(-dist * 0.11) : 0.0;
  vec2 sub = floor(f * 5.0);
  float glyph = floor(u_time * (1.5 + seed * 3.0) + hash(g) * 10.0);
  float bit = step(0.45, hash(g * 1.7 + sub * 0.37 + glyph * 3.1));
  float inside = step(0.1, f.x) * step(f.x, 0.9) * step(0.1, f.y) * step(f.y, 0.9);
  float lum = bit * inside * trail;
  float lead = step(0.0, dist) * step(dist, 1.0);
  vec3 col = mix(vec3(0.1, 0.95, 0.35), vec3(0.85, 1.0, 0.9), lead) * lum;
  gl_FragColor = vec4(col, 1.0);
}""",
    "fuego": """
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
void main() {
  vec2 uv = v_uv;
  vec2 p = vec2(uv.x * u_res.x / u_res.y * 3.0, uv.y * 2.2);
  vec2 q = p - vec2(0.0, u_time * 1.6);
  float n = 0.5 * noise(q) + 0.25 * noise(q * 2.03 + 1.7) + 0.125 * noise(q * 4.01 - u_time * 0.5);
  n /= 0.875;
  float f = clamp((n - uv.y * 0.55) * 1.7 - uv.y * 0.3, 0.0, 1.0);
  vec3 col = vec3(smoothstep(0.0, 0.35, f), smoothstep(0.25, 0.8, f) * 0.85, smoothstep(0.7, 1.0, f) * 0.45);
  gl_FragColor = vec4(col, 1.0);
}""",
    "caleidoscopio": """
void main() {
  vec2 p = centered();
  float t = u_time * 0.2;
  float r = length(p);
  float a = atan(p.y, p.x) + t;
  float seg = 6.28318 / 8.0;
  a = abs(mod(a, seg) - seg * 0.5);
  vec2 q = vec2(cos(a), sin(a)) * r * 3.0;
  float v = sin(q.x * 3.0 + t * 3.0) * cos(q.y * 4.0 - t * 2.0) + sin(length(q - vec2(1.0, 0.5)) * 5.0 - t * 4.0);
  vec3 col = pal(v * 0.3 + r * 0.4 - t * 0.5, vec3(0.5), vec3(0.5), vec3(1.0, 1.0, 0.5), vec3(0.8, 0.9, 0.3));
  col *= 0.25 + 0.75 * smoothstep(1.7, 0.1, r);
  gl_FragColor = vec4(col, 1.0);
}""",
    "reloj": """
uniform sampler2D u_mask;
uniform vec2 u_mask_px;
uniform float u_sec;
uniform vec4 u_bar;
void main() {
  vec2 uv = vec2(v_uv.x, 1.0 - v_uv.y);
  float t = u_time * 0.04;
  vec3 col = vec3(0.015, 0.015, 0.025)
           + 0.05 * pal(v_uv.x * 0.5 + t, vec3(0.5), vec3(0.5), vec3(1.0), vec3(0.0, 0.33, 0.67)) * (1.0 - v_uv.y);
  vec3 m = texture2D(u_mask, uv).rgb;
  vec2 o = u_mask_px * 2.5;
  float glow = (texture2D(u_mask, uv + vec2(o.x, 0.0)).r + texture2D(u_mask, uv - vec2(o.x, 0.0)).r
              + texture2D(u_mask, uv + vec2(0.0, o.y)).r + texture2D(u_mask, uv - vec2(0.0, o.y)).r
              + texture2D(u_mask, uv + o * 1.6).r + texture2D(u_mask, uv - o * 1.6).r) / 6.0;
  col += glow * vec3(0.95, 0.55, 0.15) * 0.35;
  col = mix(col, vec3(0.94), smoothstep(0.3, 0.6, m.r));
  col = mix(col, vec3(0.55, 0.57, 0.6), smoothstep(0.3, 0.6, m.g));
  col = mix(col, vec3(0.95, 0.64, 0.23), smoothstep(0.3, 0.6, m.b));
  float inBar = step(u_bar.x, uv.x) * step(uv.x, u_bar.z) * step(u_bar.y, uv.y) * step(uv.y, u_bar.w);
  float filled = step(uv.x, mix(u_bar.x, u_bar.z, u_sec));
  col = mix(col, mix(vec3(0.12), vec3(0.95, 0.64, 0.23), filled), inBar);
  gl_FragColor = vec4(col, 1.0);
}""",
}

BLIT = """
precision mediump float;
uniform sampler2D u_tex;
uniform float u_fade;
varying vec2 v_uv;
void main() { gl_FragColor = vec4(texture2D(u_tex, v_uv).rgb * u_fade, 1.0); }
"""

# --------------------------------------------------------------------------
# Tiny 5x7 bitmap font for the clock (rows top to bottom, bit 4 = left)
# --------------------------------------------------------------------------

FONT = {
    "0": (14, 17, 19, 21, 25, 17, 14), "1": (4, 12, 4, 4, 4, 4, 14), "2": (14, 17, 1, 2, 4, 8, 31),
    "3": (31, 2, 4, 2, 1, 17, 14), "4": (2, 6, 10, 18, 31, 2, 2), "5": (31, 16, 30, 1, 1, 17, 14),
    "6": (6, 8, 16, 30, 17, 17, 14), "7": (31, 1, 2, 4, 8, 8, 8), "8": (14, 17, 17, 14, 17, 17, 14),
    "9": (14, 17, 17, 15, 1, 2, 12), ":": (0, 12, 12, 0, 12, 12, 0), ".": (0, 0, 0, 0, 0, 12, 12),
    "/": (0, 1, 2, 4, 8, 16, 0), "-": (0, 0, 0, 31, 0, 0, 0), " ": (0,) * 7,
    "A": (14, 17, 17, 31, 17, 17, 17), "B": (30, 17, 17, 30, 17, 17, 30), "C": (14, 17, 16, 16, 16, 17, 14),
    "D": (28, 18, 17, 17, 17, 18, 28), "E": (31, 16, 16, 30, 16, 16, 31), "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 15), "H": (17, 17, 17, 31, 17, 17, 17), "I": (14, 4, 4, 4, 4, 4, 14),
    "J": (7, 2, 2, 2, 2, 18, 12), "K": (17, 18, 20, 24, 20, 18, 17), "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17), "N": (17, 17, 25, 21, 19, 17, 17), "O": (14, 17, 17, 17, 17, 17, 14),
    "P": (30, 17, 17, 30, 16, 16, 16), "Q": (14, 17, 17, 17, 21, 18, 13), "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30), "T": (31, 4, 4, 4, 4, 4, 4), "U": (17, 17, 17, 17, 17, 17, 14),
    "V": (17, 17, 17, 17, 17, 10, 4), "W": (17, 17, 17, 21, 21, 21, 10), "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 17, 10, 4, 4, 4), "Z": (31, 1, 2, 4, 8, 16, 31),
}
DAYS = ("LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM")
MONTHS = ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC")
MASK_W, MASK_H = 480, 270


class ClockMask:
    """RGB mask texture: R = time, G = secondary text, B = accent text."""

    def __init__(self):
        self.key = None
        self.buf = None
        self.bar = (0.0, 0.0, 0.0, 0.0)

    def _text(self, text, scale, cy, channel):
        adv = 6 * scale
        x0 = (MASK_W - (len(text) * adv - scale)) // 2
        for i, ch in enumerate(text):
            rows = FONT.get(ch, FONT[" "])
            for ry, bits in enumerate(rows):
                for rx in range(5):
                    if bits & (16 >> rx):
                        x = x0 + i * adv + rx * scale
                        for yy in range(cy + ry * scale, cy + (ry + 1) * scale):
                            start = (yy * MASK_W + x) * 3 + channel
                            self.buf[start:start + scale * 3:3] = b"\xff" * scale
        return x0, x0 + len(text) * adv - scale

    def update(self, now, ip):
        hhmm = time.strftime("%H:%M", now)
        date = f"{DAYS[now.tm_wday]} {now.tm_mday:02d} {MONTHS[now.tm_mon - 1]}"
        key = (hhmm, date, ip)
        if key == self.key:
            return False
        self.key = key
        self.buf = bytearray(MASK_W * MASK_H * 3)
        self._text("CAVE DASHBOARD", 2, 26, 2)
        x0, x1 = self._text(hhmm, 11, 62, 0)
        self.bar = (x0 / MASK_W, 154 / MASK_H, x1 / MASK_W, 157 / MASK_H)
        self._text(date, 3, 172, 1)
        self._text(f"HTTP://{ip}" if ip else "SIN RED", 2, 232, 1)
        return True


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return ""


# --------------------------------------------------------------------------
# EGL / GLES2 through ctypes
# --------------------------------------------------------------------------

EGL_NONE, EGL_RED, EGL_GREEN, EGL_BLUE = 0x3038, 0x3024, 0x3023, 0x3022
EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_ES2_BIT = 0x3033, 4, 0x3040, 4
EGL_CONTEXT_CLIENT_VERSION, EGL_OPENGL_ES_API, EGL_WIDTH, EGL_HEIGHT = 0x3098, 0x30A0, 0x3057, 0x3056
GL_FRAGMENT_SHADER, GL_VERTEX_SHADER, GL_COMPILE_STATUS, GL_LINK_STATUS = 0x8B30, 0x8B31, 0x8B81, 0x8B82
GL_ARRAY_BUFFER, GL_STATIC_DRAW, GL_FLOAT, GL_TRIANGLE_STRIP = 0x8892, 0x88E4, 0x1406, 5
GL_TEXTURE_2D, GL_RGB, GL_RGBA, GL_UNSIGNED_BYTE = 0x0DE1, 0x1907, 0x1908, 0x1401
GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR = 0x2801, 0x2800, 0x2601
GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE = 0x2802, 0x2803, 0x812F
GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_FRAMEBUFFER_COMPLETE = 0x8D40, 0x8CE0, 0x8CD5
GL_TEXTURE0, GL_COLOR_BUFFER_BIT, GL_UNPACK_ALIGNMENT = 0x84C0, 0x4000, 0x0CF5

vp, u32, i32, f32 = C.c_void_p, C.c_uint, C.c_int, C.c_float

GL_PROTOS = {
    "glCreateShader": (u32, u32), "glShaderSource": (None, u32, i32, C.POINTER(C.c_char_p), vp),
    "glCompileShader": (None, u32), "glGetShaderiv": (None, u32, u32, C.POINTER(i32)),
    "glGetShaderInfoLog": (None, u32, i32, vp, vp), "glCreateProgram": (u32,),
    "glAttachShader": (None, u32, u32), "glBindAttribLocation": (None, u32, u32, C.c_char_p),
    "glLinkProgram": (None, u32), "glGetProgramiv": (None, u32, u32, C.POINTER(i32)),
    "glGetProgramInfoLog": (None, u32, i32, vp, vp), "glUseProgram": (None, u32),
    "glGetUniformLocation": (i32, u32, C.c_char_p), "glUniform1f": (None, i32, f32),
    "glUniform2f": (None, i32, f32, f32), "glUniform4f": (None, i32, f32, f32, f32, f32),
    "glUniform1i": (None, i32, i32), "glGenBuffers": (None, i32, C.POINTER(u32)),
    "glBindBuffer": (None, u32, u32), "glBufferData": (None, u32, C.c_ssize_t, vp, u32),
    "glVertexAttribPointer": (None, u32, i32, u32, C.c_ubyte, i32, vp),
    "glEnableVertexAttribArray": (None, u32), "glDrawArrays": (None, u32, i32, i32),
    "glViewport": (None, i32, i32, i32, i32), "glGenTextures": (None, i32, C.POINTER(u32)),
    "glDeleteTextures": (None, i32, C.POINTER(u32)), "glBindTexture": (None, u32, u32),
    "glTexImage2D": (None, u32, i32, i32, i32, i32, i32, u32, u32, vp),
    "glTexParameteri": (None, u32, u32, i32), "glPixelStorei": (None, u32, i32),
    "glGenFramebuffers": (None, i32, C.POINTER(u32)), "glDeleteFramebuffers": (None, i32, C.POINTER(u32)),
    "glBindFramebuffer": (None, u32, u32), "glFramebufferTexture2D": (None, u32, u32, u32, u32, i32),
    "glCheckFramebufferStatus": (u32, u32), "glActiveTexture": (None, u32),
    "glClearColor": (None, f32, f32, f32, f32), "glClear": (None, u32),
    "glReadPixels": (None, i32, i32, i32, i32, u32, u32, vp),
}


class GL:
    def __init__(self, egl):
        egl.eglGetProcAddress.restype = vp
        egl.eglGetProcAddress.argtypes = [C.c_char_p]
        for name, (res, *args) in GL_PROTOS.items():
            addr = egl.eglGetProcAddress(name.encode())
            if not addr:
                raise RuntimeError(f"OpenGL ES sin {name}")
            setattr(self, name[2:], C.CFUNCTYPE(res, *args)(addr))


class Window:
    """X11 window + EGL context. matchbox (or any WM) makes it full screen."""

    def __init__(self, swap_interval):
        os.environ.setdefault("EGL_PLATFORM", "x11")
        x = self.x = C.CDLL("libX11.so.6")
        egl = self.egl = C.CDLL("libEGL.so.1")
        x.XOpenDisplay.restype = vp; x.XOpenDisplay.argtypes = [C.c_char_p]
        x.XDefaultScreen.argtypes = [vp]
        x.XRootWindow.restype = C.c_ulong; x.XRootWindow.argtypes = [vp, i32]
        x.XDisplayWidth.argtypes = x.XDisplayHeight.argtypes = [vp, i32]
        x.XCreateSimpleWindow.restype = C.c_ulong
        x.XCreateSimpleWindow.argtypes = [vp, C.c_ulong, i32, i32, u32, u32, u32, C.c_ulong, C.c_ulong]
        x.XInternAtom.restype = C.c_ulong; x.XInternAtom.argtypes = [vp, C.c_char_p, i32]
        x.XChangeProperty.argtypes = [vp, C.c_ulong, C.c_ulong, C.c_ulong, i32, i32, vp, i32]
        x.XStoreName.argtypes = [vp, C.c_ulong, C.c_char_p]
        x.XMapWindow.argtypes = [vp, C.c_ulong]
        x.XFlush.argtypes = [vp]

        self.dpy = x.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError("No se puede abrir el display X (¿DISPLAY?)")
        scr = x.XDefaultScreen(self.dpy)
        w, h = x.XDisplayWidth(self.dpy, scr), x.XDisplayHeight(self.dpy, scr)
        self.win = x.XCreateSimpleWindow(self.dpy, x.XRootWindow(self.dpy, scr), 0, 0, w, h, 0, 0, 0)
        x.XStoreName(self.dpy, self.win, b"cave-visuals")
        fs = C.c_ulong(x.XInternAtom(self.dpy, b"_NET_WM_STATE_FULLSCREEN", 0))
        x.XChangeProperty(self.dpy, self.win, x.XInternAtom(self.dpy, b"_NET_WM_STATE", 0), 4, 32, 0,
                          C.byref(fs), 1)
        x.XMapWindow(self.dpy, self.win)
        x.XFlush(self.dpy)

        egl.eglGetDisplay.restype = vp; egl.eglGetDisplay.argtypes = [vp]
        egl.eglInitialize.argtypes = [vp, C.POINTER(i32), C.POINTER(i32)]
        egl.eglChooseConfig.argtypes = [vp, C.POINTER(i32), C.POINTER(vp), i32, C.POINTER(i32)]
        egl.eglCreateWindowSurface.restype = vp
        egl.eglCreateWindowSurface.argtypes = [vp, vp, C.c_ulong, vp]
        egl.eglCreateContext.restype = vp
        egl.eglCreateContext.argtypes = [vp, vp, vp, C.POINTER(i32)]
        egl.eglMakeCurrent.argtypes = [vp, vp, vp, vp]
        egl.eglSwapInterval.argtypes = [vp, i32]
        egl.eglSwapBuffers.argtypes = [vp, vp]
        egl.eglQuerySurface.argtypes = [vp, vp, i32, C.POINTER(i32)]

        self.edpy = egl.eglGetDisplay(self.dpy)
        major, minor = i32(), i32()
        if not self.edpy or not egl.eglInitialize(self.edpy, C.byref(major), C.byref(minor)):
            raise RuntimeError("No se pudo inicializar EGL")
        egl.eglBindAPI(EGL_OPENGL_ES_API)
        attrs = (i32 * 11)(EGL_RED, 8, EGL_GREEN, 8, EGL_BLUE, 8, EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
                           EGL_RENDERABLE_TYPE, EGL_ES2_BIT, EGL_NONE)
        cfg, n = vp(), i32()
        if not egl.eglChooseConfig(self.edpy, attrs, C.byref(cfg), 1, C.byref(n)) or n.value < 1:
            raise RuntimeError("EGL: no hay configuración compatible con OpenGL ES 2")
        self.surf = egl.eglCreateWindowSurface(self.edpy, cfg, self.win, None)
        ctx = egl.eglCreateContext(self.edpy, cfg, None, (i32 * 3)(EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE))
        if not self.surf or not ctx or not egl.eglMakeCurrent(self.edpy, self.surf, self.surf, ctx):
            raise RuntimeError("EGL: no se pudo crear el contexto OpenGL ES 2")
        egl.eglSwapInterval(self.edpy, swap_interval)

    def size(self):
        w, h = i32(), i32()
        self.egl.eglQuerySurface(self.edpy, self.surf, EGL_WIDTH, C.byref(w))
        self.egl.eglQuerySurface(self.edpy, self.surf, EGL_HEIGHT, C.byref(h))
        return w.value, h.value

    def swap(self):
        self.egl.eglSwapBuffers(self.edpy, self.surf)


class Renderer:
    def __init__(self, gl):
        self.gl = gl
        self.programs = {}
        self.fbo = self.fbo_tex = None
        self.fbo_size = None
        self.mask_tex = None
        buf = u32()
        gl.GenBuffers(1, C.byref(buf))
        gl.BindBuffer(GL_ARRAY_BUFFER, buf.value)
        quad = (f32 * 8)(-1, -1, 1, -1, -1, 1, 1, 1)
        gl.BufferData(GL_ARRAY_BUFFER, C.sizeof(quad), quad, GL_STATIC_DRAW)
        gl.VertexAttribPointer(0, 2, GL_FLOAT, 0, 0, None)
        gl.EnableVertexAttribArray(0)
        gl.PixelStorei(GL_UNPACK_ALIGNMENT, 1)
        self.blit = self.program("__blit__", BLIT)

    def _shader(self, kind, src):
        gl = self.gl
        sh = gl.CreateShader(kind)
        gl.ShaderSource(sh, 1, C.byref(C.c_char_p(src.encode())), None)
        gl.CompileShader(sh)
        ok = i32()
        gl.GetShaderiv(sh, GL_COMPILE_STATUS, C.byref(ok))
        if not ok.value:
            log = C.create_string_buffer(4096)
            gl.GetShaderInfoLog(sh, 4096, None, log)
            raise RuntimeError("Error compilando shader: " + log.value.decode(errors="replace"))
        return sh

    def program(self, name, frag_src):
        if name in self.programs:
            return self.programs[name]
        gl = self.gl
        prog = gl.CreateProgram()
        gl.AttachShader(prog, self._shader(GL_VERTEX_SHADER, VERT))
        gl.AttachShader(prog, self._shader(GL_FRAGMENT_SHADER, frag_src))
        gl.BindAttribLocation(prog, 0, b"a_pos")
        gl.LinkProgram(prog)
        ok = i32()
        gl.GetProgramiv(prog, GL_LINK_STATUS, C.byref(ok))
        if not ok.value:
            log = C.create_string_buffer(4096)
            gl.GetProgramInfoLog(prog, 4096, None, log)
            raise RuntimeError(f"Error enlazando {name}: " + log.value.decode(errors="replace"))
        names = (b"u_time", b"u_res", b"u_tex", b"u_fade", b"u_mask", b"u_mask_px", b"u_sec", b"u_bar")
        uni = {n.decode(): gl.GetUniformLocation(prog, n) for n in names}
        self.programs[name] = (prog, uni)
        return self.programs[name]

    def _texture(self, w, h, fmt, data=None):
        gl = self.gl
        tex = u32()
        gl.GenTextures(1, C.byref(tex))
        gl.BindTexture(GL_TEXTURE_2D, tex.value)
        for p, v in ((GL_TEXTURE_MIN_FILTER, GL_LINEAR), (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                     (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE), (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)):
            gl.TexParameteri(GL_TEXTURE_2D, p, v)
        gl.TexImage2D(GL_TEXTURE_2D, 0, fmt, w, h, 0, fmt, GL_UNSIGNED_BYTE, data)
        return tex.value

    def ensure_fbo(self, w, h):
        if self.fbo_size == (w, h):
            return
        gl = self.gl
        if self.fbo:
            gl.DeleteFramebuffers(1, C.byref(u32(self.fbo)))
            gl.DeleteTextures(1, C.byref(u32(self.fbo_tex)))
        gl.ActiveTexture(GL_TEXTURE0)
        self.fbo_tex = self._texture(w, h, GL_RGBA)
        fbo = u32()
        gl.GenFramebuffers(1, C.byref(fbo))
        gl.BindFramebuffer(GL_FRAMEBUFFER, fbo.value)
        gl.FramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self.fbo_tex, 0)
        if gl.CheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("Framebuffer incompleto")
        self.fbo, self.fbo_size = fbo.value, (w, h)

    def upload_mask(self, data):
        gl = self.gl
        gl.ActiveTexture(GL_TEXTURE0 + 1)
        raw = (C.c_ubyte * len(data)).from_buffer(data)
        if self.mask_tex is None:
            self.mask_tex = self._texture(MASK_W, MASK_H, GL_RGB, raw)
        else:
            gl.BindTexture(GL_TEXTURE_2D, self.mask_tex)
            gl.TexImage2D(GL_TEXTURE_2D, 0, GL_RGB, MASK_W, MASK_H, 0, GL_RGB, GL_UNSIGNED_BYTE, raw)
        gl.ActiveTexture(GL_TEXTURE0)

    def snapshot(self, path):
        """Save the last rendered (reduced-resolution) frame as PNG, for debugging."""
        w, h = self.fbo_size
        buf = (C.c_ubyte * (w * h * 4))()
        self.gl.BindFramebuffer(GL_FRAMEBUFFER, self.fbo)
        self.gl.ReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, buf)
        self.gl.BindFramebuffer(GL_FRAMEBUFFER, 0)
        raw = bytes(buf)
        rows = b"".join(b"\0" + raw[y * w * 4:(y + 1) * w * 4] for y in range(h - 1, -1, -1))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    def frame(self, prog, win_size, scale, t, fade, extra=None):
        gl = self.gl
        W, H = win_size
        w, h = max(1, W // scale), max(1, H // scale)
        self.ensure_fbo(w, h)
        # Pass 1: the visual, at reduced resolution.
        p, u = prog
        gl.BindFramebuffer(GL_FRAMEBUFFER, self.fbo)
        gl.Viewport(0, 0, w, h)
        gl.UseProgram(p)
        gl.Uniform1f(u["u_time"], t)
        gl.Uniform2f(u["u_res"], w, h)
        if extra:
            extra(u)
        gl.DrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        # Pass 2: upscale to the window.
        bp, bu = self.blit
        gl.BindFramebuffer(GL_FRAMEBUFFER, 0)
        gl.Viewport(0, 0, W, H)
        gl.UseProgram(bp)
        gl.ActiveTexture(GL_TEXTURE0)
        gl.BindTexture(GL_TEXTURE_2D, self.fbo_tex)
        gl.Uniform1i(bu["u_tex"], 0)
        gl.Uniform1f(bu["u_fade"], fade)
        gl.DrawArrays(GL_TRIANGLE_STRIP, 0, 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("names", nargs="+", help=f"{', '.join(CATALOG)} o {SHUFFLE}")
    ap.add_argument("--scale", type=int, default=3, help="divisor de resolución (1 = nativa)")
    ap.add_argument("--fps", type=int, default=30, choices=(30, 60))
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--cycle", type=float, default=300, help="segundos por visual al rotar")
    ap.add_argument("--stats", action="store_true", help="imprime FPS cada 5 s")
    ap.add_argument("--snapshot", metavar="PNG", help="guarda un fotograma tras 3 s y sale")
    args = ap.parse_args()

    names = [n for n in CATALOG if n != "reloj"] if args.names == [SHUFFLE] else args.names
    unknown = [n for n in names if n not in SHADERS]
    if unknown:
        sys.exit(f"Visual desconocido: {', '.join(unknown)}")

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    win = Window(swap_interval=60 // args.fps)
    gl = GL(win.egl)
    r = Renderer(gl)
    progs = [r.program(n, HEADER + SHADERS[n]) for n in names]  # compile all up front: fail fast
    scale = max(1, min(8, args.scale))
    gl.ClearColor(0, 0, 0, 1)

    mask = ClockMask() if "reloj" in names else None
    ip, ip_checked = local_ip(), time.monotonic()

    start = time.monotonic()
    frames, stat_t = 0, start
    idx, idx_t = 0, start
    while True:
        now = time.monotonic()
        if len(progs) > 1 and now - idx_t >= args.cycle:
            idx, idx_t = (idx + 1) % len(progs), now
        since = now - idx_t
        fade = 1.0
        if len(progs) > 1:
            fade = min(1.0, since, max(0.0, args.cycle - since))  # 1 s fade in/out
        t = (since if len(progs) > 1 else now - start) * args.speed % 3600.0
        extra = None
        if names[idx] == "reloj":
            if now - ip_checked > 10:
                ip, ip_checked = local_ip(), now
            lt = time.localtime()
            if mask.update(lt, ip):
                r.upload_mask(mask.buf)
            sec = (lt.tm_sec + (time.time() % 1.0)) / 60.0

            def extra(u, sec=sec):
                gl.Uniform1i(u["u_mask"], 1)
                gl.Uniform2f(u["u_mask_px"], 1.0 / MASK_W, 1.0 / MASK_H)
                gl.Uniform1f(u["u_sec"], sec)
                gl.Uniform4f(u["u_bar"], *mask.bar)
        r.frame(progs[idx], win.size(), scale, t, fade, extra)
        win.swap()

        if args.snapshot and now - start >= 3:
            r.snapshot(args.snapshot)
            return
        frames += 1
        if args.stats and now - stat_t >= 5:
            print(f"{names[idx]}: {frames / (now - stat_t):.1f} fps", flush=True)
            frames, stat_t = 0, now


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as e:
        sys.exit(f"visuals: error: {e}")
