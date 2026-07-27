#!/usr/bin/env python3
"""
cyberdeck_panel.py — Cyberdeck Pi4 status panel for the Adafruit 2.8" PiTFT.

Renders a 320x240 RGB565 frame styled to the Cyberdeck UI style guide and writes
it straight to the framebuffer. No X server, no console, no touch input.

    sudo python3 cyberdeck_panel.py                          # draw once on the Pi
    TFT_STUB=1 TFT_PNG=/tmp/panel.png python3 cyberdeck_panel.py   # preview anywhere
"""

import os
import struct
import subprocess
import threading
from datetime import datetime

from PIL import Image, ImageChops, ImageDraw, ImageFont

from battery import read_battery

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
CONF_PATH = os.environ.get("TFT_CONF", "/etc/cyberdeck-pi4/tft.conf")


def load_conf(path=CONF_PATH):
    """Parse the KEY=value config. Shared with button_daemon.py."""
    conf = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                conf[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return conf


CONF = load_conf()


def cfg(key, default):
    """Environment overrides config file overrides default."""
    return os.environ.get(key, CONF.get(key, default))


WIDTH, HEIGHT = 320, 240
FB_DEV = cfg("TFT_FB", "/dev/fb0")
ROTATE = int(cfg("TFT_ROTATE", "0"))
SCANLINES = cfg("SCANLINES", "0") == "1"
AP_IFACE = cfg("AP_IFACE", "wlan0")
WAN_IFACE = cfg("WAN_IFACE", "eth0")
PNG_OUT = os.environ.get("TFT_PNG")          # preview mode: write a PNG, not the fb
STUB = os.environ.get("TFT_STUB") == "1"     # synthetic data, for CI and design work

LEASES = "/var/lib/misc/dnsmasq.leases"
BRIDGE_CONF = "/etc/cyberdeck-pi4/bridge.conf"
LOWPOWER_STATE = "/run/cyberdeck-pi4/lowpower.state"
SERVICES = ("hostapd", "dnsmasq", "mosquitto", "nginx")

# --------------------------------------------------------------------------
# palette — Cyberdeck UI style guide, alpha pre-flattened over --bg-primary
# --------------------------------------------------------------------------
C_BG          = (10, 14, 39)
C_BG2         = (26, 31, 58)
C_CARD        = (13, 59, 102)
C_INPUT       = (10, 22, 40)
C_CYAN        = (0, 255, 255)
C_MAGENTA     = (255, 0, 255)
C_GREEN       = (0, 255, 65)
C_AMBER       = (255, 170, 0)
C_RED         = (255, 51, 102)
C_TEXT        = (160, 174, 192)
C_TEXT_DIM    = (106, 122, 140)
C_TEXT_BRIGHT = (226, 232, 240)
C_BORDER      = (9, 50, 71)
C_ACTIVE_BG   = (9, 38, 64)

# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
RAIL_W = 56
HEADER_H = 28
SLOT_H = HEIGHT // 4
CX0 = RAIL_W + 8          # content left
CX1 = WIDTH - 6           # content right
CW = CX1 - CX0

# rail slots, physical top to bottom: (gpio label, line 1, line 2, kind)
SLOTS = [
    ("27", "BACK", "LIGHT", "backlight"),
    ("23", "AP", "POWER", "ap"),
    ("22", "AUX", "", "aux"),
    ("17", "SHUT", "DOWN", "shutdown"),
]

_FONTS = {}
_FB_LOCK = threading.Lock()
_SCANLINE_MASK = None


def _font(size, bold=True):
    key = (size, bold)
    if key in _FONTS:
        return _FONTS[key]
    names = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"] if bold else []
    ) + [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    ]
    font = ImageFont.load_default()
    for path in names:
        if os.path.exists(path):
            font = ImageFont.truetype(path, size)
            break
    _FONTS[key] = font
    return font


def _tw(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


# --------------------------------------------------------------------------
# framebuffer output
# --------------------------------------------------------------------------
def _pack565(img):
    """RGB -> RGB565 little-endian.

    Pillow's 'BGR;16' rawmode is a 5-6-5 packer and is ~200x faster than a
    Python loop over 76,800 pixels. If your panel shows red and blue swapped,
    change it to 'RGB;16'.
    """
    try:
        return img.convert("RGB").tobytes("raw", "BGR;16")
    except (KeyError, ValueError):                       # very old Pillow
        px = img.convert("RGB").tobytes("raw", "RGB")
        out = bytearray(WIDTH * HEIGHT * 2)
        for i in range(WIDTH * HEIGHT):
            r, g, b = px[i * 3], px[i * 3 + 1], px[i * 3 + 2]
            struct.pack_into("<H", out, i * 2,
                             ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3))
        return bytes(out)


def _scanlines(img):
    global _SCANLINE_MASK
    if _SCANLINE_MASK is None:
        mask = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
        md = ImageDraw.Draw(mask)
        for yy in range(0, HEIGHT, 2):
            md.line([(0, yy), (WIDTH, yy)], fill=(214, 214, 214))
        _SCANLINE_MASK = mask
    return ImageChops.multiply(img, _SCANLINE_MASK)


def show(img):
    """Push a rendered image to the framebuffer (or a PNG in preview mode)."""
    if SCANLINES:
        img = _scanlines(img)
    if ROTATE:
        img = img.rotate(ROTATE)
    if PNG_OUT:
        img.save(PNG_OUT)
        return
    data = _pack565(img)
    with _FB_LOCK:                       # refresh thread and button overlays share this
        with open(FB_DEV, "wb") as fb:
            fb.write(data)


# --------------------------------------------------------------------------
# state collection
# --------------------------------------------------------------------------
def _run(cmd, timeout=4):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True,
                             text=True, timeout=timeout)
        return res.stdout.strip()
    except Exception:
        return ""


def _svc(name):
    return _run("systemctl is-active " + name) == "active"


def _ip(iface):
    return _run("ip -4 -o addr show %s 2>/dev/null | awk '{print $4}' | cut -d/ -f1"
                % iface)


def _mem():
    total = avail = 0
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) // 1024
    except OSError:
        pass
    return total - avail, total


def _disk():
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        return (total - free) / total if total else 0.0
    except OSError:
        return 0.0


def _temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            return int(fh.read()) / 1000.0
    except (OSError, ValueError):
        return 0.0


def _uptime():
    try:
        with open("/proc/uptime") as fh:
            secs = int(float(fh.read().split()[0]))
    except (OSError, ValueError):
        return "?"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return "%dd %dh" % (d, h)
    return "%dh %02dm" % (h, m) if h else "%dm" % m


def _clients():
    try:
        with open(LEASES) as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _bridged():
    try:
        with open(BRIDGE_CONF) as fh:
            return "BRIDGE_ENABLED=yes" in fh.read().replace(" ", "")
    except OSError:
        return False


def gather():
    """Collect everything the panel draws. ~120ms; runs off the refresh thread."""
    if STUB:
        return _stub_state()
    svc = {name: _svc(name) for name in SERVICES}
    used, total = _mem()
    lowpower = os.path.exists(LOWPOWER_STATE)
    return {
        "svc": svc,
        "ap_ip": _ip(AP_IFACE) or "--",
        "wan_ip": _ip(WAN_IFACE),
        "clients": _clients(),
        "temp": _temp(),
        "mem_used": used,
        "mem_total": total,
        "disk": _disk(),
        "uptime": _uptime(),
        "bridged": _bridged(),
        "lowpower": lowpower,
        "ap_up": svc["hostapd"] and not lowpower,
        "backlight": True,              # overridden by the daemon, which owns GPIO 18
        "battery": read_battery(),
        "clock": datetime.now().strftime("%H:%M:%S"),
    }


def _stub_state():
    """Synthetic state so the layout can be rendered on a laptop or in CI."""
    import json
    return {
        "svc": {"hostapd": True, "dnsmasq": True, "mosquitto": True, "nginx": True},
        "ap_ip": "192.168.4.1", "wan_ip": "", "clients": 3,
        "temp": 52.1, "mem_used": 233, "mem_total": 1844, "disk": 0.68,
        "uptime": "3h 12m", "bridged": False, "lowpower": False,
        "ap_up": True, "backlight": True,
        "battery": json.loads(os.environ.get("TFT_STUB_BATTERY", "null")),
        "clock": "12:34:56",
    }


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------
def _draw_rail(d, st):
    for x in range(RAIL_W):                       # linear-gradient(to right, bg1, bg2)
        t = x / float(RAIL_W - 1)
        d.line([(x, 0), (x, HEIGHT)],
               fill=tuple(int(a + (b - a) * t) for a, b in zip(C_BG, C_BG2)))

    active_map = {"backlight": st["backlight"], "ap": st["ap_up"],
                  "aux": False, "shutdown": False}
    f8, f9 = _font(8), _font(9)

    for i, (gpio, l1, l2, kind) in enumerate(SLOTS):
        y0 = i * SLOT_H
        mid = y0 + SLOT_H // 2
        active = active_map[kind]
        if kind == "shutdown":
            color = C_RED
        elif kind == "aux":
            color = C_TEXT_DIM
        else:
            color = C_CYAN if active else C_TEXT_DIM
        if active:
            d.rectangle([0, y0 + 1, RAIL_W - 2, y0 + SLOT_H - 1], fill=C_ACTIVE_BG)
            d.rectangle([RAIL_W - 5, y0 + 6, RAIL_W - 3, y0 + SLOT_H - 6],
                        fill=C_MAGENTA)          # .tab.active::after
        if i:
            d.line([(0, y0), (RAIL_W - 2, y0)], fill=C_BORDER)
        d.text((4, y0 + 5), "#" + gpio, font=f8, fill=C_TEXT_DIM)
        if l2:
            d.text(((RAIL_W - _tw(d, l1, f9)) // 2, mid - 11), l1, font=f9, fill=color)
            d.text(((RAIL_W - _tw(d, l2, f9)) // 2, mid + 1), l2, font=f9, fill=color)
        else:
            d.text(((RAIL_W - _tw(d, l1, f9)) // 2, mid - 5), l1, font=f9, fill=color)

    d.line([(RAIL_W - 1, 0), (RAIL_W - 1, HEIGHT)], fill=C_CYAN)


def _draw_header(d, st):
    f8, f9, f11 = _font(8), _font(9), _font(11)
    d.rectangle([RAIL_W, 0, WIDTH, HEADER_H - 1], fill=C_BG2)
    d.line([(RAIL_W, HEADER_H), (WIDTH, HEADER_H)], fill=C_MAGENTA)

    all_up = all(st["svc"].values())
    dot = C_GREEN if all_up else (C_AMBER if any(st["svc"].values()) else C_RED)
    if st["lowpower"]:
        dot = C_AMBER
    d.ellipse([RAIL_W + 7, 11, RAIL_W + 13, 17], fill=dot)
    d.text((RAIL_W + 19, 9), "CYBERDECK PI4", font=f11, fill=C_MAGENTA)

    clock = st["clock"]
    cw = _tw(d, clock, f9)
    d.text((CX1 - cw, 10), clock, font=f9, fill=C_TEXT_DIM)

    if st["lowpower"]:
        label, bcol = "LOW PWR", C_RED
    elif st["bridged"]:
        label, bcol = "BRIDGED", C_AMBER
    else:
        label, bcol = "ISOLATED", C_CYAN
    lw = _tw(d, label, f8)
    bx1 = CX1 - cw - 8
    bx0 = bx1 - lw - 12
    d.rounded_rectangle([bx0, 7, bx1, 21], radius=7, outline=bcol)
    d.text((bx0 + 6, 10), label, font=f8, fill=bcol)


def _divider(d, y):
    d.line([(CX0, y), (CX1, y)], fill=C_BORDER)
    return y + 8


def _draw_services(d, y, st):
    f8, f9 = _font(8), _font(9)
    iw, ih = (CW - 6) // 2, 15
    order = ("hostapd", "mosquitto", "dnsmasq", "nginx")   # column-major on screen
    for i, name in enumerate(order):
        up = st["svc"].get(name, False)
        x = CX0 + (i % 2) * (iw + 6)
        yy = y + (i // 2) * (ih + 3)
        d.rectangle([x, yy, x + iw, yy + ih], fill=C_INPUT)
        d.rectangle([x, yy, x + 2, yy + ih], fill=C_GREEN if up else C_RED)
        d.text((x + 8, yy + 3), name, font=f9, fill=C_TEXT_BRIGHT)
        lbl = "ACTIVE" if up else "DOWN"
        d.text((x + iw - _tw(d, lbl, f8) - 5, yy + 4), lbl, font=f8,
               fill=C_GREEN if up else C_RED)
    return y + 2 * ih + 3 + 6


def _row(d, y, label, value, color=C_CYAN):
    f9, f10 = _font(9), _font(10)
    d.text((CX0, y + 1), label, font=f9, fill=C_TEXT_DIM)
    d.text((CX1 - _tw(d, value, f10), y), value, font=f10, fill=color)
    return y + 13


def _draw_netrows(d, y, st):
    y = _row(d, y, "AP IP", st["ap_ip"])
    y = _row(d, y, "CLIENTS", "%d connected" % st["clients"])
    wan = st["wan_ip"]
    y = _row(d, y, "WAN " + WAN_IFACE.upper(), wan or "DOWN",
             C_CYAN if wan else C_TEXT_DIM)
    return y


def _gauge(d, y, label, value, frac, color):
    f9 = _font(9)
    d.text((CX0, y), label, font=f9, fill=C_TEXT_DIM)
    d.text((CX1 - _tw(d, value, f9), y), value, font=f9, fill=color)
    by = y + 11
    d.rectangle([CX0, by, CX1, by + 5], fill=C_INPUT, outline=C_BORDER)
    frac = max(0.0, min(1.0, frac))
    fw = int((CX1 - CX0 - 2) * frac)
    if fw > 0:
        d.rectangle([CX0 + 1, by + 1, CX0 + 1 + fw, by + 4], fill=color)
    return y + 21


def _threshold(frac, warn=0.70, crit=0.85):
    return C_GREEN if frac < warn else (C_AMBER if frac < crit else C_RED)


def _draw_gauges(d, y, st):
    tfrac = st["temp"] / 85.0
    y = _gauge(d, y, "CPU TEMP", "%.1fC" % st["temp"], tfrac,
               _threshold(tfrac, 0.70, 0.88))
    mfrac = (st["mem_used"] / st["mem_total"]) if st["mem_total"] else 0
    y = _gauge(d, y, "MEMORY", "%d/%d MB" % (st["mem_used"], st["mem_total"]),
               mfrac, _threshold(mfrac))
    y = _gauge(d, y, "DISK", "%d%%" % round(st["disk"] * 100), st["disk"],
               _threshold(st["disk"], 0.80, 0.92))
    return y


def _draw_battery(d, y, st):
    """Battery indicator. PLACEHOLDER until hardware exists — see battery.py."""
    f8, f9, f13 = _font(8), _font(9), _font(13)
    bat = st["battery"]
    bx, by, bw, bh = CX0, y + 2, 46, 20

    if bat is None:
        d.rectangle([bx, by, bx + bw, by + bh], outline=C_TEXT_DIM)
        d.rectangle([bx + bw + 1, by + 6, bx + bw + 4, by + bh - 6], fill=C_TEXT_DIM)
        for i in range(3):                       # hollow "no data" cells
            sx = bx + 4 + i * 14
            d.rectangle([sx, by + 4, sx + 10, by + bh - 4], outline=C_BORDER)
        d.text((bx + bw + 12, by + 1), "NO SENSOR", font=f9, fill=C_TEXT_DIM)
        d.text((bx + bw + 12, by + 12), "batt tbd", font=f8, fill=C_BORDER)
    else:
        pct = bat["percent"]
        col = C_GREEN if pct > 50 else (C_AMBER if pct > 20 else C_RED)
        if bat["charging"]:
            col = C_CYAN
        d.rectangle([bx, by, bx + bw, by + bh], outline=C_CYAN)
        d.rectangle([bx + bw + 1, by + 6, bx + bw + 4, by + bh - 6], fill=C_CYAN)
        fw = int((bw - 4) * pct / 100.0)
        if fw > 0:
            d.rectangle([bx + 2, by + 2, bx + 2 + fw, by + bh - 2], fill=col)
        d.text((bx + bw + 12, by + 2), "%d%%" % pct, font=f13, fill=col)
        sub = "CHARGING" if bat["charging"] else (
            "%.2fV" % bat["volts"] if bat.get("volts") else "ON BATTERY")
        d.text((bx + bw + 12, by + 20), sub, font=f8, fill=C_TEXT_DIM)

    lbl = "UPTIME"
    d.text((CX1 - _tw(d, lbl, f8), by + 1), lbl, font=f8, fill=C_TEXT_DIM)
    d.text((CX1 - _tw(d, st["uptime"], f9), by + 12), st["uptime"], font=f9,
           fill=C_TEXT)
    return y + 30


def render(st=None):
    """Build the full panel image. Pure function — no I/O to the framebuffer."""
    if st is None:
        st = gather()
    img = Image.new("RGB", (WIDTH, HEIGHT), C_BG)
    d = ImageDraw.Draw(img)
    _draw_rail(d, st)
    _draw_header(d, st)
    y = HEADER_H + 6
    y = _draw_services(d, y, st)
    y = _divider(d, y)
    y = _draw_netrows(d, y, st)
    y = _divider(d, y)
    y = _draw_gauges(d, y, st)
    y = _divider(d, y)
    _draw_battery(d, y, st)
    return img


def render_overlay(title, lines, color=C_CYAN, st=None):
    """Draw a style-guide card over the live panel — used for button feedback."""
    img = render(st)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = RAIL_W + 14, 72, WIDTH - 14, 168
    d.rectangle([x0, y0, x1, y1], fill=C_CARD, outline=color)
    f10, f16 = _font(10), _font(16)
    d.text((x0 + 10, y0 + 8), title, font=f10, fill=color)
    d.line([(x0 + 10, y0 + 24), (x1 - 10, y0 + 24)], fill=color)
    yy = y0 + 34
    for line in lines:
        d.text(((WIDTH - _tw(d, line, f16)) // 2, yy), line, font=f16,
               fill=C_TEXT_BRIGHT)
        yy += 22
    return img


def draw_panel(st=None):
    show(render(st))


def draw_overlay(title, lines, color=C_CYAN, st=None):
    show(render_overlay(title, lines, color, st))


def clear(color=C_BG):
    show(Image.new("RGB", (WIDTH, HEIGHT), color))


if __name__ == "__main__":
    draw_panel()
    print("panel written to %s" % (PNG_OUT or FB_DEV))
