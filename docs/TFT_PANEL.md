# Cyberdeck Pi4 — TFT Status Panel & Control Buttons
## Addon guide for the Cyberdeck Pi 4 (`cyberdeck-pi4`)

This document describes how to add a **local hardware control surface** to the existing
`cyberdeck-pi4` project: a 320×240 status panel drawn on the Adafruit 2.8" PiTFT, styled to the
Cyberdeck UI Style Guide, plus four hardware buttons that control the deck without a client
device, keyboard, or SSH session.

It assumes the `cyberdeck-pi4` stack (hostapd / dnsmasq / mosquitto / nginx / `cyberdeck-pi4.service`)
is already installed and working, and that the PiTFT already enumerates as a framebuffer —
see the companion document *"Adafruit 2.8" PiTFT — Button Daemon & Framebuffer Display"* for
the bring-up steps and the hardware-level gotchas that are **not** repeated here.

---

## 1. What this addon adds

### Part 1 — Status panel

A full-screen 320×240 panel rendered directly to `/dev/fb0`, refreshed every few seconds.
It is a visual translation of the Cyberdeck web UI style guide into a framebuffer, so the
deck's physical screen and its web dashboard read as the same product:

| Style guide element | Panel equivalent |
|---|---|
| 48px left tab rail, cyan right border | 56px left **button rail**, one slot per hardware button |
| Active tab (cyan text, tinted bg, magenta edge marker) | Button slot whose feature is currently **ON** |
| 32px header (magenta bottom border, status dot, mode badge, project title) | 28px header row |
| `.service-item` (colored left border, name + status) | 2×2 service grid |
| `.gauge-bar` / `.gauge-fill` with color thresholds | CPU temp, memory, disk gauges |
| `.badge` pills (`🔒 Isolated` / `🌐 Bridged`) | Mode badge in the header |
| `.battery-visual` | **Battery indicator — placeholder, hardware added later** |

The battery indicator is drawn in its final position and final visual form from day one.
Until battery hardware exists it renders as an empty outline reading `NO SENSOR`. Adding real
hardware later requires editing **one function in one file** (`battery.py`) — no layout work.

### Part 2 — Button actions

| Physical position | BCM GPIO | Action |
|---|---|---|
| Top | 27 | **Backlight** — toggle TFT backlight off/on |
| Second | 23 | **Power mode** — toggle WiFi AP off + enter low-power mode / restore |
| Third | 22 | **AUX** — reserved, runs a user hook script if present (to be defined later) |
| Bottom | 17 | **Shutdown** — safe halt of the Pi (requires a 3-second hold) |

Design rules baked into the daemon:

- **The shutdown button cannot fire on a single press.** It requires a deliberate hold, and a
  short press only shows a hint on screen. An accidental brush against a bottom button on a
  portable deck must never halt the machine.
- **The button daemon never depends on the network.** Low-power mode takes down `wlan0`; if
  the daemon needed the AP to recover, the deck would be bricked until it was opened up.
  Recovery is always another press of the same button.
- **Button callbacks never block.** Every action is queued to a worker thread, because
  `systemctl stop hostapd` can take several seconds and gpiozero's callback thread is shared.

---

## 2. Repository changes

New and modified files, relative to the `cyberdeck-pi4` repo root:

```
cyberdeck-pi4/
├── config/
│   ├── tft.conf                     # NEW  installed to /etc/cyberdeck-pi4/tft.conf
│   ├── cyberdeck-panel.service      # NEW  systemd unit (button daemon + panel refresh)
│   └── button-aux.sh.example        # NEW  hook stub for the undefined 4th button
├── scripts/
│   ├── lowpower.sh                  # NEW  enable|disable|status — AP off + low power
│   ├── install-tft.sh               # NEW  installer (deps, files, service, self-check)
│   ├── uninstall-tft.sh             # NEW  clean removal, restores the stack + backlight
│   └── tft/
│       ├── cyberdeck_panel.py       # NEW  framebuffer renderer (the status panel)
│       ├── button_daemon.py         # NEW  button watcher + refresh loop
│       └── battery.py               # NEW  battery provider — PLACEHOLDER
├── docs/
│   └── TFT_PANEL.md                 # NEW  this document
├── .github/workflows/ci-tft.yml     # NEW  standalone addon CI (no edit to base ci.yml)
├── Makefile.tft                     # NEW  make -f Makefile.tft install|uninstall|preview
├── setup.sh                         # MOD  + optional --with-tft flag
└── README.md                        # MOD  + "Hardware Panel" section
```

The addon is designed to drop into the existing repo without touching the base project's
files: it adds a *separate* CI workflow (`ci-tft.yml`) rather than editing `ci.yml`, and a
*separate* `Makefile.tft` rather than editing the main `Makefile`. The only base file it
asks you to modify is `setup.sh`, and only to add one optional `--with-tft` line (§6.6).

Installed layout on the Pi:

| Source | Installed to |
|---|---|
| `scripts/tft/*.py` | `/opt/cyberdeck-pi4/tft/` |
| `scripts/lowpower.sh` | `/opt/cyberdeck-pi4/scripts/lowpower.sh` |
| `config/tft.conf` | `/etc/cyberdeck-pi4/tft.conf` |
| `config/cyberdeck-panel.service` | `/etc/systemd/system/` |
| `config/button-aux.sh.example` | `/etc/cyberdeck-pi4/button-aux.sh.example` |

> If your `setup.sh` installs the project somewhere other than `/opt/cyberdeck-pi4`, change
> `INSTALL_ROOT` in `install-tft.sh` and `CYBERDECK_SCRIPTS` in `tft.conf`. Nothing else is
> path-dependent.

---

## 3. Prerequisites and pre-flight checks

Run these **before** installing anything. Each one has bitten this build at least once.

### 3.1 The framebuffer exists and is the size you think it is

```bash
ls /dev/fb*
cat /sys/class/graphics/fb0/virtual_size      # expect: 320,240
cat /sys/class/graphics/fb0/bits_per_pixel    # expect: 16
```

**Gotcha — fb0 vs fb1.** On Bookworm/Trixie the KMS driver often claims `/dev/fb0` for HDMI,
which pushes the PiTFT to `/dev/fb1`. Check *both* nodes' `virtual_size`; the one reporting
`320,240` is the PiTFT. If it is `fb1`, set `TFT_FB=/dev/fb1` in `/etc/cyberdeck-pi4/tft.conf`.
Writing 320×240 RGB565 into a 1920×1080 HDMI framebuffer produces a garbled stripe in the
corner and no error message, so confirm this first.

### 3.2 Nothing else is drawing to that framebuffer

```bash
cat /proc/cmdline | grep -o 'fbcon=map:[0-9]*'   # should return nothing for the TFT node
systemctl get-default                            # expect: multi-user.target
```

**Gotcha.** If the kernel console is mapped to the TFT (`fbcon=map:1`) or a desktop session
is running, the console cursor and getty text will fight the panel and you will see flicker
and torn frames. Boot to CLI (`sudo raspi-config` → System → Boot → Console) and leave the
console on HDMI or serial.

### 3.3 Dependencies

```bash
sudo apt install -y python3-pil python3-gpiozero python3-lgpio fonts-dejavu-core rfkill
python3 -c "from PIL import Image, ImageDraw, ImageFont; import gpiozero, lgpio; print('deps ok')"
```

**Gotcha.** Do not use `RPi.GPIO` for edge detection on this kernel — `add_event_detect`
raises `RuntimeError: Failed to add edge detection`. gpiozero with the lgpio backend is the
supported path.

### 3.4 The existing stack is healthy

```bash
systemctl is-active hostapd dnsmasq mosquitto nginx
systemctl is-enabled cyberdeck-pi4.service
cat /etc/cyberdeck-pi4/bridge.conf
```

The panel reads `bridge.conf` to render the Isolated/Bridged badge, and low-power mode
recovers by restarting `cyberdeck-pi4.service` — so that unit must be installed and enabled
before this addon is useful.

> **Note — this addon assumes the base project's rename is complete.** It references the
> base install paths (`/opt/cyberdeck-pi4`, `/etc/cyberdeck-pi4`), the boot-persistence unit
> `cyberdeck-pi4.service`, and the broadcast SSID `Cyberdeck Pi4`. These follow from renaming
> the base project; make sure the base `setup.sh` actually installs under those names and
> that `config/hostapd.conf` sets `ssid=Cyberdeck Pi4`. If your base project kept the old
> `mqtt-hub` paths or SSID, adjust the values in `/etc/cyberdeck-pi4/tft.conf` and the two
> `systemctl` references in `lowpower.sh` to match — nothing else is name-dependent. Note
> that "MQTT" elsewhere in this document (the broker, the dashboard, pub/sub, the `:9001/mqtt`
> WebSocket) is the *protocol* and is unaffected by the rename.

### 3.5 Free GPIO check

This addon claims **GPIO 17, 22, 23, 27** (buttons) and **GPIO 18** (backlight), plus the SPI
pins already used by the display. None of these are touched by hostapd, dnsmasq, mosquitto,
or nginx, so there is no conflict with the base project.

```bash
sudo systemctl stop cyberdeck-panel 2>/dev/null || true   # release pins before manual tests
```

---

## 4. Configuration file

`config/tft.conf` → installed to `/etc/cyberdeck-pi4/tft.conf`. Sourced by `lowpower.sh` (as
shell) and parsed by the Python daemon (simple `KEY=value`), so keep it to plain key/value
lines with no shell expansion.

```ini
# /etc/cyberdeck-pi4/tft.conf — Cyberdeck TFT panel configuration

# --- display ---------------------------------------------------------------
TFT_FB=/dev/fb0             # /dev/fb1 on KMS systems — see docs 3.1
TFT_ROTATE=0                # 0 or 180 if the panel is mounted upside-down
REFRESH_SEC=5               # panel redraw interval
SCANLINES=0                 # 1 = emulate the web UI scanline overlay (noisy at 240p)

# --- interfaces ------------------------------------------------------------
AP_IFACE=wlan0
WAN_IFACE=eth0

# --- buttons (BCM numbering, physical top to bottom) -----------------------
BTN_BACKLIGHT=27
BTN_POWER=23
BTN_AUX=22
BTN_SHUTDOWN=17
BACKLIGHT_GPIO=18
BOUNCE_SEC=0.3              # hardware debounce window
HOLD_SEC=3                  # hold time required for the shutdown button

# --- actions ---------------------------------------------------------------
CYBERDECK_SCRIPTS=/opt/cyberdeck-pi4/scripts
AUX_CMD=/etc/cyberdeck-pi4/button-aux.sh          # runs only if present + executable
SHUTDOWN_CMD="/sbin/shutdown -h now"         # override for testing — see docs 9.7

# --- low power mode --------------------------------------------------------
LOWPOWER_SERVICES="hostapd dnsmasq mosquitto nginx"
LOWPOWER_RFKILL=1           # 1 = rfkill block wifi (biggest single power saving)
LOWPOWER_GOVERNOR=powersave # governor while in low power mode
NORMAL_GOVERNOR=ondemand    # governor to restore
LOWPOWER_LEDS=1             # 1 = turn off the ACT/PWR LEDs
LOWPOWER_BACKLIGHT=0        # 1 = also kill the backlight when entering low power
```

> **Gotcha — quote every multi-word value.** `lowpower.sh` sources this file with `.`, so
> `SHUTDOWN_CMD=/sbin/shutdown -h now` (unquoted) makes bash try to execute `-h` and, under
> `set -e`, aborts the script. The Python parser strips the quotes, so quoting is safe on
> both sides. For the same reason, avoid `#` inside values — the parser treats it as a
> comment.

---

## 5. Part 1 — The status panel

### 5.1 Layout

```
 0        56                                                        320
 ┌────────┬───────────────────────────────────────────────────────┐ 0
 │ #27    │ ● CYBERDECK PI4          [ISOLATED]            12:34:56    │
 │ BACK   ├───────────────────────────────────────────────────────┤ 28   magenta rule
 │ LIGHT  │ ▌hostapd    ACTIVE   ▌mosquitto  ACTIVE                │
 ├────────┤ ▌dnsmasq    ACTIVE   ▌nginx      ACTIVE                │      service grid
 │ #23    │ ──────────────────────────────────────────────────────│
 │ AP     │ AP IP                                    192.168.4.1  │
 │ POWER  │ CLIENTS                                 3 connected   │      net rows
 ├────────┤ WAN ETH0                                        DOWN  │
 │ #22    │ ──────────────────────────────────────────────────────│
 │ AUX    │ CPU TEMP                                       52.1C  │
 │        │ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
 ├────────┤ MEMORY                                  233/1844 MB   │      gauges
 │ #17    │ ▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
 │ SHUT   │ DISK                                             68%  │
 │ DOWN   │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░ │
 │        │ ──────────────────────────────────────────────────────│
 │        │ ┌────────┐                                    UPTIME  │      battery block
 │        │ │▓▓▓▓░░░░│┤ NO SENSOR                        3h 12m   │      (PLACEHOLDER)
 └────────┴───────────────────────────────────────────────────────┘ 240
```

Fixed geometry, all derived from the style guide's structure section:

| Region | Coordinates |
|---|---|
| Button rail | `x 0–55`, four 60px slots, 1px cyan right border |
| Header | `x 56–320`, `y 0–28`, 1px magenta bottom rule |
| Content | `x 64–314`, `y 34–234` |

### 5.2 Palette translation

RGB565 has **no alpha channel and no antialiasing budget at 240p**, so the web palette needs
two adjustments: translucent values are pre-flattened over `--bg-primary`, and the glow
effects (`text-shadow`, `box-shadow`) are dropped in favour of 1px accent borders. Colors
are otherwise identical to the style guide, which is what makes the physical panel and the
web dashboard read as one product.

| Style guide token | Hex | Panel constant |
|---|---|---|
| `--bg-primary` | `#0a0e27` | `C_BG` |
| `--bg-secondary` | `#1a1f3a` | `C_BG2` |
| `--bg-tertiary` (cards) | `#0d3b66` | `C_CARD` |
| `--bg-input` | `#0a1628` | `C_INPUT` |
| `--accent-cyan` | `#00ffff` | `C_CYAN` |
| `--accent-magenta` | `#ff00ff` | `C_MAGENTA` |
| `--accent-green` | `#00ff41` | `C_GREEN` |
| `--accent-amber` | `#ffaa00` | `C_AMBER` |
| `--accent-red` | `#ff3366` | `C_RED` |
| `--text-primary` | `#a0aec0` | `C_TEXT` |
| `--text-secondary` | `#6a7a8c` | `C_TEXT_DIM` |
| `--text-bright` | `#e2e8f0` | `C_TEXT_BRIGHT` |
| `--border-subtle` | `rgba(0,255,255,.15)` over bg | `C_BORDER` = `#093247` |
| `.tab.active` background | `rgba(0,255,255,.10)` over bg | `C_ACTIVE_BG` = `#092640` |

### 5.3 `scripts/tft/battery.py` — the placeholder

This is the **only** file that needs to change when real battery hardware arrives. It has one
contract: return a dict or `None`.

```python
#!/usr/bin/env python3
"""
battery.py — battery/UPS provider for the Cyberdeck panel.

PLACEHOLDER. No battery hardware is fitted yet.

Contract:
    read_battery() -> None                       when no battery is present
                   -> {"percent": 0..100,        required
                       "charging": bool,         required
                       "volts": float | None}    optional

Everything else in the panel is written against this contract, so adding real
hardware later means editing this file only — no layout or daemon changes.

The default implementation reads /run/cyberdeck-pi4/battery.json if something else
publishes it (an MQTT bridge, a UPS HAT daemon, a cron job). That also makes the
indicator testable today with no hardware at all:

    sudo mkdir -p /run/cyberdeck-pi4
    echo '{"percent":62,"charging":false,"volts":3.91}' | sudo tee /run/cyberdeck-pi4/battery.json
"""

import json

BATTERY_FILE = "/run/cyberdeck-pi4/battery.json"


def read_battery():
    try:
        with open(BATTERY_FILE) as fh:
            data = json.load(fh)
        pct = int(data["percent"])
        return {
            "percent": max(0, min(100, pct)),
            "charging": bool(data.get("charging", False)),
            "volts": data.get("volts"),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# WHEN HARDWARE IS FITTED, replace the body of read_battery() with one of these.
#
# MAX17048 / LC709203F fuel gauge (I2C, Adafruit LiPo backpacks):
#     from adafruit_max1704x import MAX17048
#     import board
#     _gauge = MAX17048(board.I2C())
#     def read_battery():
#         return {"percent": int(_gauge.cell_percent),
#                 "charging": _gauge.charge_rate > 0,
#                 "volts": round(_gauge.cell_voltage, 2)}
#
# INA219 current/voltage monitor (I2C) — derive percent from a voltage curve:
#     percent = _curve(bus_voltage); charging = current_ma > 0
#
# PiSugar / X728-style UPS HAT — read its own I2C registers or its daemon socket.
#
# Whichever you use: return None on any read error. A dead sensor must degrade to
# "NO SENSOR" on screen, never crash the refresh loop.
# ---------------------------------------------------------------------------
```

### 5.4 `scripts/tft/cyberdeck_panel.py` — the renderer

```python
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
```

---

## 6. Part 2 — The buttons

### 6.1 Behaviour summary

| GPIO | Press | Hold | On-screen feedback |
|---|---|---|---|
| 27 | Toggle backlight | — | `BACKLIGHT / ON` card (nothing to show when off) |
| 23 | Toggle AP + low power | — | `POWER MODE / AP OFF · LOW POWER` then result |
| 22 | Run `AUX_CMD` if present | — | `AUX / RUNNING` or `AUX / UNASSIGNED` |
| 17 | Show hold hint | **3 s → halt** | `SHUTDOWN / HOLD 3s TO CONFIRM` → `SHUTTING DOWN` |

Rail slots light up cyan with a magenta edge marker when their feature is ON, exactly like
`.tab.active` in the web UI: backlight on, AP up. The shutdown slot is always red.

### 6.2 `scripts/lowpower.sh`

What "low power" means on a Pi 4 — set expectations before wiring a battery to this: the
BCM2711 has **no true suspend state**. This script gets the deck from roughly 3.4–4 W (AP
serving clients) down to roughly 2.2–2.6 W idle. It is "quiet, cool and stretched battery
life", not "sleep". The radio is the single biggest win, which is why `rfkill` is the
default.

```bash
#!/bin/bash
# lowpower.sh — take the Cyberdeck Pi4 AP down and drop the Pi 4 into a low power state.
#
#   sudo ./lowpower.sh enable | disable | status
#   DRY_RUN=1 sudo ./lowpower.sh enable      # print actions, change nothing
#
# NOTE: enable blocks the WiFi radio. If you are connected over the AP or over
# wlan0 SSH, you WILL lose that session. Recovery is the front-panel button, the
# Ethernet port, or a keyboard — never the WiFi.

set -euo pipefail

CONF=/etc/cyberdeck-pi4/tft.conf
STATE_DIR=/run/cyberdeck-pi4
STATE="$STATE_DIR/lowpower.state"

if [ -r "$CONF" ]; then
    # shellcheck source=/dev/null
    . "$CONF"
fi

SERVICES=${LOWPOWER_SERVICES:-"hostapd dnsmasq mosquitto nginx"}
USE_RFKILL=${LOWPOWER_RFKILL:-1}
LP_GOV=${LOWPOWER_GOVERNOR:-powersave}
NORM_GOV=${NORMAL_GOVERNOR:-ondemand}
LP_LEDS=${LOWPOWER_LEDS:-1}
LP_BACKLIGHT=${LOWPOWER_BACKLIGHT:-0}
BACKLIGHT_GPIO=${BACKLIGHT_GPIO:-18}
DRY_RUN=${DRY_RUN:-0}

log()  { logger -t cyberdeck-lowpower -- "$*" 2>/dev/null || true; echo "[lowpower] $*" >&2; }
step() { if [ "$DRY_RUN" = "1" ]; then echo "DRY-RUN: $*"; else "$@" || log "WARN: failed: $*"; fi; }

set_governor() {
    local gov=$1
    if [ "$DRY_RUN" = "1" ]; then echo "DRY-RUN: governor -> $gov"; return; fi
    for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        if [ -w "$f" ]; then echo "$gov" > "$f" 2>/dev/null || true; fi
    done
}

set_leds() {                       # $1 = 0 (off) | 1 (restore default triggers)
    if [ "$DRY_RUN" = "1" ]; then echo "DRY-RUN: leds -> $1"; return; fi
    for led in /sys/class/leds/ACT /sys/class/leds/led0 \
               /sys/class/leds/PWR /sys/class/leds/led1; do
        [ -d "$led" ] || continue
        if [ "$1" = "0" ]; then
            echo none    > "$led/trigger"   2>/dev/null || true
            echo 0       > "$led/brightness" 2>/dev/null || true
        else
            echo default-on > "$led/trigger" 2>/dev/null || true
        fi
    done
}

enable_lowpower() {
    log "entering low power mode"
    for svc in $SERVICES; do
        step systemctl stop "$svc"
    done
    if [ "$USE_RFKILL" = "1" ]; then
        step rfkill block wifi
    fi
    set_governor "$LP_GOV"
    if [ "$LP_LEDS" = "1" ]; then set_leds 0; fi
    if [ "$LP_BACKLIGHT" = "1" ]; then
        step pinctrl set "$BACKLIGHT_GPIO" op dl
    fi
    if [ "$DRY_RUN" != "1" ]; then
        mkdir -p "$STATE_DIR"
        date -Is > "$STATE"
    fi
    log "low power mode active"
}

disable_lowpower() {
    log "leaving low power mode"
    if [ "$LP_LEDS" = "1" ]; then set_leds 1; fi
    set_governor "$NORM_GOV"
    # only undo what we did — GPIO 18 otherwise belongs to the button daemon
    if [ "$LP_BACKLIGHT" = "1" ]; then
        step pinctrl set "$BACKLIGHT_GPIO" op dh
    fi
    if [ "$USE_RFKILL" = "1" ]; then
        step rfkill unblock wifi
        sleep 2                     # let wlan0 reappear before hostapd claims it
    fi
    # cyberdeck-pi4.service is the single source of truth: it reapplies the static IP,
    # the iptables rules for the current bridge mode, and starts every service.
    if systemctl cat cyberdeck-pi4.service >/dev/null 2>&1; then
        step systemctl restart cyberdeck-pi4.service
    else
        for svc in $SERVICES; do
            step systemctl start "$svc"
        done
    fi
    [ "$DRY_RUN" = "1" ] || rm -f "$STATE"
    log "normal mode restored"
}

status_lowpower() {
    if [ -f "$STATE" ]; then
        echo "mode:      LOW POWER (since $(cat "$STATE"))"
    else
        echo "mode:      NORMAL"
    fi
    echo "governor:  $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo '?')"
    if rfkill list wifi | grep -q 'Soft blocked: yes'; then
        echo "wifi:      blocked"
    else
        echo "wifi:      unblocked"
    fi
    for svc in $SERVICES; do
        printf '%-11s %s\n' "$svc:" "$(systemctl is-active "$svc" 2>/dev/null || true)"
    done
}

case "${1:-}" in
    enable)  enable_lowpower ;;
    disable) disable_lowpower ;;
    status)  status_lowpower ;;
    *) echo "usage: $0 enable|disable|status" >&2; exit 2 ;;
esac
```

**Gotcha — do not restart `hostapd` directly to recover.** On a NetworkManager system the
static `192.168.4.1` and the captive-portal iptables rules are not restored by starting
hostapd alone; `dnsmasq` then binds only to the IPv6 link-local address and DNS never comes
up on `192.168.4.1:53`. Always recover through `cyberdeck-pi4.service`, which is exactly the
ordering `start-ap.sh` already gets right at boot.

**Gotcha — `set -e` and bare `[ x ] && cmd`.** Every conditional in this script is written as
`if … then … fi` rather than `[ cond ] && action`. Under `set -euo pipefail`, a standalone
`[ cond ] && action` whose test fails returns 1 and silently aborts the whole script — so a
deck with `LOWPOWER_LEDS=0` would stop halfway through, leaving services stopped but the
radio still on. Same reason the config is sourced inside an `if`, not with `[ -r … ] && .`.

**Gotcha — GPIO 18 belongs to the button daemon.** `disable_lowpower` only touches the
backlight if *it* was the thing that turned it off. If the script forced the backlight on
every time, gpiozero's `LED` object would still believe the panel is dark: the rail would
render dim and the refresh loop would skip redraws on a lit screen. Leave
`LOWPOWER_BACKLIGHT=0` and use the top button unless you have a reason not to.

**Gotcha — `rfkill unblock` is not instant.** `wlan0` takes a second or two to reappear.
Without the `sleep 2`, `hostapd` starts before the interface exists and fails; systemd's
`Restart=` may mask it, or it may just sit dead. The sleep is cheap insurance.

### 6.3 `config/button-aux.sh.example` — the fourth button

The third slot (GPIO 22) is deliberately undefined. Rather than editing the daemon later, it
runs an external hook if one exists, so defining it is a file drop and no restart:

```bash
#!/bin/bash
# Copy to /etc/cyberdeck-pi4/button-aux.sh and chmod +x to activate the AUX button.
# Runs as root, from the button daemon's worker thread. Keep it under ~10 seconds.
#
# Ideas: toggle bridge mode, publish a retained MQTT message, cycle WLED presets,
# rotate the panel, capture a screenshot, restart mosquitto.
#
# Example — toggle the internet bridge:
# /opt/cyberdeck-pi4/scripts/bridge-mode.sh status | grep -q bridged \
#     && /opt/cyberdeck-pi4/scripts/bridge-mode.sh disable \
#     || /opt/cyberdeck-pi4/scripts/bridge-mode.sh enable

echo "AUX button pressed"
```

### 6.4 `scripts/tft/button_daemon.py`

```python
#!/usr/bin/env python3
"""
button_daemon.py — Cyberdeck hardware buttons + panel refresh loop.

Owns GPIO 17/22/23/27 (buttons), GPIO 18 (backlight) and the framebuffer.
Run it as root under systemd; see config/cyberdeck-panel.service.
"""

import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")   # never RPi.GPIO on this kernel

from gpiozero import LED, Button          # noqa: E402  (must follow the env default)

import cyberdeck_panel as panel           # noqa: E402
from cyberdeck_panel import cfg           # noqa: E402

logging.basicConfig(
    level=logging.INFO, stream=sys.stdout,
    format="%(asctime)s [cyberdeck] %(levelname)s: %(message)s")
log = logging.getLogger("cyberdeck")

BTN_BACKLIGHT = int(cfg("BTN_BACKLIGHT", "27"))
BTN_POWER     = int(cfg("BTN_POWER", "23"))
BTN_AUX       = int(cfg("BTN_AUX", "22"))
BTN_SHUTDOWN  = int(cfg("BTN_SHUTDOWN", "17"))
BACKLIGHT_PIN = int(cfg("BACKLIGHT_GPIO", "18"))
BOUNCE        = float(cfg("BOUNCE_SEC", "0.3"))
HOLD_SEC      = float(cfg("HOLD_SEC", "3"))
REFRESH_SEC   = float(cfg("REFRESH_SEC", "5"))
SCRIPTS       = cfg("CYBERDECK_SCRIPTS", "/opt/cyberdeck-pi4/scripts")
AUX_CMD       = cfg("AUX_CMD", "/etc/cyberdeck-pi4/button-aux.sh")
SHUTDOWN_CMD  = cfg("SHUTDOWN_CMD", "/sbin/shutdown -h now")
LOWPOWER      = os.path.join(SCRIPTS, "lowpower.sh")
LOWPOWER_STATE = "/run/cyberdeck-pi4/lowpower.state"

OVERLAY_SEC = 3.0

_stop = threading.Event()
_jobs = queue.Queue()
_state_lock = threading.Lock()
_last_state = None
_overlay_until = 0.0
_halting = False


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def run(cmd, timeout=90):
    log.info("run: %s", cmd)
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True,
                             text=True, timeout=timeout)
        if res.returncode:
            log.error("exit %s: %s", res.returncode,
                      (res.stderr or res.stdout).strip()[:400])
        return res.returncode == 0
    except Exception as exc:
        log.error("command failed: %s", exc)
        return False


def overlay(title, lines, color=None, secs=OVERLAY_SEC):
    """Show a card over the panel using the cached state (never blocks on gather)."""
    global _overlay_until
    _overlay_until = time.monotonic() + secs
    with _state_lock:
        state = _last_state
    try:
        panel.draw_overlay(title, lines, color or panel.C_CYAN, state)
    except Exception:
        log.exception("overlay failed")


def lowpower_active():
    return os.path.exists(LOWPOWER_STATE)


# --------------------------------------------------------------------------
# actions — all run on the worker thread, never in a gpiozero callback
# --------------------------------------------------------------------------
def act_backlight():
    backlight.toggle()
    state = "ON" if backlight.is_lit else "OFF"
    log.info("backlight %s", state)
    if backlight.is_lit:
        overlay("BACKLIGHT", ["ON"], panel.C_CYAN, 1.5)


def act_power():
    if lowpower_active():
        overlay("POWER MODE", ["RESUMING", "AP + BROKER"], panel.C_CYAN, 120)
        ok = run("%s disable" % LOWPOWER)
        overlay("POWER MODE",
                ["AP ONLINE"] if ok else ["RESUME", "FAILED"],
                panel.C_GREEN if ok else panel.C_RED)
    else:
        overlay("POWER MODE", ["AP OFF", "LOW POWER"], panel.C_AMBER, 120)
        ok = run("%s enable" % LOWPOWER)
        overlay("POWER MODE",
                ["LOW POWER"] if ok else ["FAILED"],
                panel.C_AMBER if ok else panel.C_RED)


def act_aux():
    if os.path.isfile(AUX_CMD) and os.access(AUX_CMD, os.X_OK):
        overlay("AUX", ["RUNNING"], panel.C_CYAN, 60)
        ok = run(AUX_CMD, timeout=30)
        overlay("AUX", ["DONE"] if ok else ["FAILED"],
                panel.C_GREEN if ok else panel.C_RED)
    else:
        log.info("aux button pressed — no hook at %s", AUX_CMD)
        overlay("AUX", ["UNASSIGNED"], panel.C_TEXT_DIM, 2)


def act_shutdown_hint():
    if not _halting:
        overlay("SHUTDOWN", ["HOLD %ds" % int(HOLD_SEC), "TO CONFIRM"],
                panel.C_RED, HOLD_SEC + 0.5)


def act_shutdown():
    global _halting
    _halting = True
    log.warning("shutdown confirmed via GPIO %d", BTN_SHUTDOWN)
    _stop.set()                                  # stop the refresh loop overdrawing
    with _state_lock:
        state = _last_state
    try:
        panel.draw_overlay("SYSTEM", ["SHUTTING", "DOWN"], panel.C_RED, state)
    except Exception:
        log.exception("could not draw shutdown screen")
    time.sleep(1.0)
    subprocess.Popen(SHUTDOWN_CMD, shell=True)


# --------------------------------------------------------------------------
# threads
# --------------------------------------------------------------------------
def worker():
    while True:
        job = _jobs.get()
        if job is None:
            return
        try:
            job()
        except Exception:
            log.exception("action failed")
        finally:
            _jobs.task_done()


def enqueue(fn):
    """gpiozero callback target: return immediately, do the work elsewhere."""
    def _cb():
        _jobs.put(fn)
    return _cb


def refresh_loop():
    global _last_state
    while not _stop.is_set():
        try:
            state = panel.gather()
            state["backlight"] = backlight.is_lit
            with _state_lock:
                _last_state = state
            if time.monotonic() >= _overlay_until and backlight.is_lit:
                panel.draw_panel(state)
        except Exception:
            log.exception("refresh failed")
        _stop.wait(REFRESH_SEC)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
backlight = LED(BACKLIGHT_PIN, initial_value=True)
btn_backlight = Button(BTN_BACKLIGHT, pull_up=True, bounce_time=BOUNCE)
btn_power     = Button(BTN_POWER, pull_up=True, bounce_time=BOUNCE)
btn_aux       = Button(BTN_AUX, pull_up=True, bounce_time=BOUNCE)
btn_shutdown  = Button(BTN_SHUTDOWN, pull_up=True, bounce_time=BOUNCE,
                       hold_time=HOLD_SEC, hold_repeat=False)

btn_backlight.when_pressed = enqueue(act_backlight)
btn_power.when_pressed     = enqueue(act_power)
btn_aux.when_pressed       = enqueue(act_aux)
btn_shutdown.when_pressed  = enqueue(act_shutdown_hint)
btn_shutdown.when_held     = enqueue(act_shutdown)


def _terminate(signum, _frame):
    log.info("signal %s — stopping", signum)
    _stop.set()


def main():
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    threading.Thread(target=worker, daemon=True, name="actions").start()
    refresher = threading.Thread(target=refresh_loop, daemon=True, name="refresh")
    refresher.start()

    log.info("cyberdeck panel daemon started (fb=%s refresh=%ss)",
             panel.FB_DEV, REFRESH_SEC)
    log.info("  GPIO %2d -> backlight toggle", BTN_BACKLIGHT)
    log.info("  GPIO %2d -> AP off / low power toggle", BTN_POWER)
    log.info("  GPIO %2d -> aux hook (%s)", BTN_AUX, AUX_CMD)
    log.info("  GPIO %2d -> shutdown (hold %.0fs)", BTN_SHUTDOWN, HOLD_SEC)

    _stop.wait()
    if not _halting:
        try:
            panel.draw_overlay("PANEL", ["OFFLINE"], panel.C_TEXT_DIM, _last_state)
        except Exception:
            pass
    _jobs.put(None)
    log.info("stopped")


if __name__ == "__main__":
    main()
```

**Why the queue.** `systemctl stop hostapd` can take 5+ seconds. gpiozero runs callbacks on
its own internal thread; blocking there delays or drops every other button. Callbacks here do
nothing but `put()` a function on a queue.

**Why the cached state.** Building an overlay needs a background panel to draw on top of.
Calling `gather()` inside a button action would add ~120 ms of `systemctl` shelling before the
user sees any feedback, and would report a stale mid-transition state anyway. The refresh
thread keeps `_last_state` warm.

**Why redraw is skipped when the backlight is off.** No point pushing 150 kB to a dark panel
every 5 seconds; it also means the backlight button is a real power-saving control.

### 6.5 `config/cyberdeck-panel.service`

```ini
[Unit]
Description=Cyberdeck TFT panel and button daemon
Documentation=file:/opt/cyberdeck-pi4/docs/TFT_PANEL.md
After=multi-user.target cyberdeck-pi4.service
Wants=cyberdeck-pi4.service

[Service]
Type=simple
WorkingDirectory=/opt/cyberdeck-pi4/tft
Environment=PYTHONUNBUFFERED=1
Environment=GPIOZERO_PIN_FACTORY=lgpio
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/python3 /opt/cyberdeck-pi4/tft/button_daemon.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

Three deliberate choices:

- **`Wants=`, not `Requires=`.** If `cyberdeck-pi4.service` fails, the panel must still come up —
  that is precisely when you need to read the screen and press buttons.
- **`WorkingDirectory=/opt/cyberdeck-pi4/tft`** so `import cyberdeck_panel` and `import battery`
  resolve without packaging anything.
- **`ExecStartPre=/bin/sleep 2`** gives the kernel time to release the GPIO lines after a
  restart. Without it you get `lgpio.error: 'GPIO busy'` and a crash loop throttled by
  `RestartSec`.

The daemon runs as root because it writes `/dev/fb0`, calls `systemctl`, and halts the
machine. Dropping privileges would require a `video` group membership plus three polkit
rules for marginal benefit on a single-purpose appliance.

### 6.6 `scripts/install-tft.sh`

```bash
#!/bin/bash
# install-tft.sh — install the Cyberdeck TFT panel addon. Idempotent.
set -euo pipefail

INSTALL_ROOT=${INSTALL_ROOT:-/opt/cyberdeck-pi4}
REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

echo "==> dependencies"
apt-get update
apt-get install -y python3-pil python3-gpiozero python3-lgpio fonts-dejavu-core rfkill

echo "==> directories"
install -d "$INSTALL_ROOT/tft" "$INSTALL_ROOT/scripts" /etc/cyberdeck-pi4 /run/cyberdeck-pi4

echo "==> files"
install -m 0755 "$REPO_DIR/scripts/tft/cyberdeck_panel.py" "$INSTALL_ROOT/tft/"
install -m 0755 "$REPO_DIR/scripts/tft/button_daemon.py"   "$INSTALL_ROOT/tft/"
install -m 0644 "$REPO_DIR/scripts/tft/battery.py"         "$INSTALL_ROOT/tft/"
install -m 0755 "$REPO_DIR/scripts/lowpower.sh"            "$INSTALL_ROOT/scripts/"
install -m 0644 "$REPO_DIR/config/button-aux.sh.example"   /etc/cyberdeck-pi4/

# never clobber a customised config
if [ ! -f /etc/cyberdeck-pi4/tft.conf ]; then
    install -m 0644 "$REPO_DIR/config/tft.conf" /etc/cyberdeck-pi4/tft.conf
else
    echo "    /etc/cyberdeck-pi4/tft.conf exists — left untouched"
fi

echo "==> systemd"
install -m 0644 "$REPO_DIR/config/cyberdeck-panel.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable cyberdeck-panel.service
systemctl restart cyberdeck-panel.service

echo "==> done"
systemctl --no-pager --lines=5 status cyberdeck-panel.service || true
```

Hook it into the existing installer by appending to `setup.sh`:

```bash
# --- optional TFT panel addon --------------------------------------------
if [ "${WITH_TFT:-no}" = "yes" ] || [ "${1:-}" = "--with-tft" ]; then
    "$(dirname "$0")/scripts/install-tft.sh"
fi
```

---

## 7. Installation

### Existing deck (addon added to a repo already on the Pi)

```bash
cd ~/cyberdeck-pi4
git pull
sudo ./scripts/install-tft.sh
```

That single command installs dependencies, copies the files, auto-detects the PiTFT
framebuffer node (writing `TFT_FB=/dev/fb1` into the config if the panel is not on `fb0`),
enables and starts the service, and runs a self-check. On a re-run it never overwrites an
existing `/etc/cyberdeck-pi4/tft.conf`.

### Fresh build (base project + addon in one go)

```bash
git clone <your-repo-url> ~/cyberdeck-pi4      # HTTPS needs a Personal Access Token
cd ~/cyberdeck-pi4
sudo ./setup.sh --with-tft
```

### Verify

```bash
sudo ./scripts/install-tft.sh --check          # framebuffer, deps, service — re-runnable
sudo journalctl -u cyberdeck-panel -f
```

Expected first-run log:

```
cyberdeck panel daemon started (fb=/dev/fb0 refresh=5.0s)
  GPIO 27 -> backlight toggle
  GPIO 23 -> AP off / low power toggle
  GPIO 22 -> aux hook (/etc/cyberdeck-pi4/button-aux.sh)
  GPIO 17 -> shutdown (hold 3s)
```

And the self-check prints:

```
==> self-check
    [ok] PiTFT framebuffer at /dev/fb0 (320x240)
    [ok] python deps present (PIL, gpiozero, lgpio)
    [ok] cyberdeck-panel.service is active
==> self-check passed
```

> **Convenience targets.** `make -f Makefile.tft tft-install`, `tft-check`, `tft-preview`
> (renders the panel to `/tmp/panel.png` with no hardware), `tft-lint`, and `tft-uninstall`
> wrap the same scripts. Merge `Makefile.tft` into your top-level `Makefile` if you prefer
> plain `make tft-install`.

---

## 8. Testing Part 1 — the panel

Every test below is independent and non-destructive unless marked otherwise.

### 8.1 Render preview off-device (no Pi required)

The fastest iteration loop. Works on any machine with Python and Pillow — laptop, CI runner,
container. `TFT_STUB=1` substitutes synthetic system data so nothing shells out to
`systemctl`, and `TFT_PNG` writes a PNG instead of a framebuffer.

```bash
cd scripts/tft
TFT_STUB=1 TFT_PNG=/tmp/panel.png python3 cyberdeck_panel.py
python3 -c "from PIL import Image; im=Image.open('/tmp/panel.png'); print(im.size, im.mode)"
```

**Pass:** `(320, 240) RGB`, and the PNG shows the layout from §5.1 — rail, header with an
`ISOLATED` badge, four green service items, three gauges, empty battery reading `NO SENSOR`.

**Fail → check:** a `FileNotFoundError` on fonts means `fonts-dejavu-core` is missing; the
panel falls back to a bitmap font and text will be tiny and misaligned.

### 8.2 Render on the device

```bash
sudo systemctl stop cyberdeck-panel
sudo python3 /opt/cyberdeck-pi4/tft/cyberdeck_panel.py
```

**Pass:** the real panel appears on the TFT with live values, and the command prints
`panel written to /dev/fb0`.

**Fail → garbled diagonal stripe or nothing:** you are writing to the wrong framebuffer node.
Re-check §3.1 and set `TFT_FB=/dev/fb1`.

### 8.3 Colour channel order

Pillow's `BGR;16` packer is correct on every PiTFT tested, but verify once per new panel:

```bash
sudo python3 - <<'PY'
import sys; sys.path.insert(0, "/opt/cyberdeck-pi4/tft")
from PIL import Image, ImageDraw
import cyberdeck_panel as p
img = Image.new("RGB", (320, 240))
d = ImageDraw.Draw(img)
for i, (name, col) in enumerate([("CYAN", p.C_CYAN), ("MAGENTA", p.C_MAGENTA),
                                 ("GREEN", p.C_GREEN), ("RED", p.C_RED)]):
    d.rectangle([0, i*60, 320, i*60+59], fill=col)
    d.text((10, i*60+20), name, fill=(0, 0, 0))
p.show(img)
PY
```

**Pass:** each bar matches its label. **Fail:** if cyan looks yellow and red looks blue, the
channels are swapped — change `"BGR;16"` to `"RGB;16"` in `_pack565()`.

### 8.4 Orientation

If the panel is mounted upside-down in the enclosure, the rail will appear on the right and
the button order inverted.

```bash
sudo TFT_ROTATE=180 python3 /opt/cyberdeck-pi4/tft/cyberdeck_panel.py
```

**Pass:** the rail lines up with the physical buttons, top slot (`#27`) next to the top
button. Persist it with `TFT_ROTATE=180` in `/etc/cyberdeck-pi4/tft.conf`.

### 8.5 Data accuracy

Cross-check each field against its source. Run with the panel visible.

| Panel field | Verify with |
|---|---|
| Service grid | `systemctl is-active hostapd dnsmasq mosquitto nginx` |
| AP IP | `ip -4 addr show wlan0` |
| CLIENTS | `wc -l < /var/lib/misc/dnsmasq.leases` |
| WAN | `ip -4 addr show eth0` |
| CPU TEMP | `vcgencmd measure_temp` |
| MEMORY | `free -m` (used = total − available) |
| DISK | `df -h /` |
| UPTIME | `uptime -p` |
| Mode badge | `cat /etc/cyberdeck-pi4/bridge.conf` |

### 8.6 Degraded and alternate states

Each of these should change the panel within one refresh interval (≤5 s):

```bash
sudo systemctl stop mosquitto                 # mosquitto item -> red DOWN, header dot amber
sudo systemctl start mosquitto                # back to green

sudo /opt/cyberdeck-pi4/scripts/bridge-mode.sh enable    # badge -> amber BRIDGED, WAN shows an IP
sudo /opt/cyberdeck-pi4/scripts/bridge-mode.sh disable   # badge -> cyan ISOLATED

sudo stress-ng --cpu 4 --timeout 120s &       # CPU TEMP gauge climbs green -> amber
```

**Pass:** gauge colours cross their thresholds (green <70%, amber <85–88%, red above) and no
text overflows the 250px content column at any value.

### 8.7 Battery placeholder

The indicator ships with no hardware, but it is fully testable today because `battery.py`
reads a JSON file:

```bash
sudo mkdir -p /run/cyberdeck-pi4

echo '{"percent":90,"charging":false,"volts":4.11}' | sudo tee /run/cyberdeck-pi4/battery.json
echo '{"percent":45,"charging":false,"volts":3.78}' | sudo tee /run/cyberdeck-pi4/battery.json
echo '{"percent":12,"charging":false,"volts":3.55}' | sudo tee /run/cyberdeck-pi4/battery.json
echo '{"percent":66,"charging":true}'               | sudo tee /run/cyberdeck-pi4/battery.json

sudo rm /run/cyberdeck-pi4/battery.json            # back to the placeholder
```

**Pass:** fill bar green at 90, amber at 45, red at 12, cyan + `CHARGING` at 66, and
`NO SENSOR` once the file is removed.

**Also test the failure path** — a broken sensor must never take down the panel:

```bash
echo 'not json at all' | sudo tee /run/cyberdeck-pi4/battery.json
```

**Pass:** panel keeps refreshing, indicator falls back to `NO SENSOR`, no traceback in the
journal.

### 8.8 Refresh loop and tearing

```bash
sudo systemctl restart cyberdeck-panel
watch -n1 'journalctl -u cyberdeck-panel -n1 --no-pager'
```

**Pass:** the header clock advances every `REFRESH_SEC`, the frame appears in one piece, and
CPU use stays low:

```bash
top -b -n1 | grep button_daemon      # expect low single-digit %CPU
```

**Fail → visible tearing or 40%+ CPU:** you are on the pure-Python RGB565 fallback (very old
Pillow) or something else is writing the same framebuffer (see §3.2).

---

## 9. Testing Part 2 — the buttons

### 9.1 Wiring check (daemon stopped)

```bash
sudo systemctl stop cyberdeck-panel
python3 - <<'PY'
from gpiozero import Button
from signal import pause
for pin in (17, 22, 23, 27):
    b = Button(pin, pull_up=True)
    b.when_pressed = lambda btn=b: print("pressed GPIO", btn.pin.number)
print("press each button; Ctrl+C to quit")
pause()
PY
```

**Pass:** top→bottom presses print 27, 23, 22, 17 in that order.
**Fail → `GPIO busy`:** something still holds the pins; confirm the daemon stopped.

### 9.2 Backlight (GPIO 27)

```bash
sudo systemctl start cyberdeck-panel
# press the top button twice
sudo journalctl -u cyberdeck-panel -n5 --no-pager
```

**Pass:** screen goes dark, log shows `backlight OFF`; second press restores it and shows the
`BACKLIGHT / ON` card briefly before the panel returns. The `#27` rail slot is cyan with a
magenta marker while lit, dim while off.

Independent verification that the pin is doing the work:

```bash
sudo pinctrl get 18       # expect: op dh (on) / op dl (off)
```

### 9.3 Low power mode — dry run first

Never test this live on a deck you are SSH'd into over WiFi.

```bash
sudo DRY_RUN=1 /opt/cyberdeck-pi4/scripts/lowpower.sh enable
sudo DRY_RUN=1 /opt/cyberdeck-pi4/scripts/lowpower.sh disable
```

**Pass:** every action prints with a `DRY-RUN:` prefix, in this order — stop the four
services, `rfkill block wifi`, governor, LEDs — and nothing actually changes:

```bash
systemctl is-active hostapd     # still active
/opt/cyberdeck-pi4/scripts/lowpower.sh status
```

### 9.4 Low power mode — live, from the command line

```bash
sudo /opt/cyberdeck-pi4/scripts/lowpower.sh enable
/opt/cyberdeck-pi4/scripts/lowpower.sh status
```

**Pass:**
- `mode: LOW POWER`, all four services `inactive`, governor `powersave`
- the `Cyberdeck Pi4` SSID disappears from a phone's WiFi list
- `rfkill list wifi` shows `Soft blocked: yes`
- the ACT/PWR LEDs are dark
- the panel keeps updating: badge turns red `LOW PWR`, header dot amber, all four service
  items red, `AP IP` shows `--`, the `#23` rail slot goes dim

```bash
sudo /opt/cyberdeck-pi4/scripts/lowpower.sh disable
```

**Pass — and this is the test that matters most, because it exercises the recovery path that
the base project's boot ordering gotchas live in:**

```bash
ip -4 addr show wlan0 | grep 192.168.4.1        # static IP reapplied
sudo ss -lunp | grep ':53 '                     # dnsmasq bound to 192.168.4.1:53, not just [::]
sudo iptables -t nat -L PREROUTING -n | head    # captive portal (or NAT) rules restored
systemctl is-active hostapd dnsmasq mosquitto nginx
```

Then, from a phone: join `Cyberdeck Pi4`, open `http://192.168.4.1/`, connect the dashboard, and
publish a test message. Full-stack recovery, not just "the services say active".

### 9.5 Low power mode — from the button (GPIO 23)

Press the second button. **Pass:** `AP OFF / LOW POWER` card appears immediately (not after
the services finish stopping — that would mean the queue is not working), the log shows the
script running, and the panel settles into the low-power state within one refresh. Press
again to restore, then repeat the §9.4 verification block.

Time it: the whole enable path should complete in under ~10 s, restore in under ~20 s.

### 9.6 AUX button (GPIO 22)

```bash
# unassigned
# press the third button
```
**Pass:** `AUX / UNASSIGNED` card, log line `aux button pressed — no hook at ...`, nothing
else happens.

```bash
sudo cp /etc/cyberdeck-pi4/button-aux.sh.example /etc/cyberdeck-pi4/button-aux.sh
sudo chmod +x /etc/cyberdeck-pi4/button-aux.sh
# press the third button again
```
**Pass:** `AUX / RUNNING` then `AUX / DONE`, and the hook's output appears in the journal. No
daemon restart was required.

### 9.7 Shutdown button — hold semantics (safe, no halt)

Override the shutdown command so the full path can be exercised without powering off:

```bash
sudo sed -i 's|^SHUTDOWN_CMD=.*|SHUTDOWN_CMD="logger -t cyberdeck FAKE-SHUTDOWN"|' \
    /etc/cyberdeck-pi4/tft.conf
sudo systemctl restart cyberdeck-panel
```

Now run these three cases:

| Case | Action | Expected |
|---|---|---|
| Tap | press and release quickly | `SHUTDOWN / HOLD 3s TO CONFIRM` card, **no** halt, nothing in the journal beyond the hint |
| Short hold | hold ~1.5 s, release | same as tap — still no halt |
| Full hold | hold >3 s | `SYSTEM / SHUTTING DOWN` card, journal shows `shutdown confirmed via GPIO 17` and `FAKE-SHUTDOWN` |

**This is the single most important test in the document.** A deck that halts on a knock is
worse than one with no shutdown button at all. Also bump the enclosure a few times and
confirm nothing fires.

Restore the real command afterwards:

```bash
sudo sed -i 's|^SHUTDOWN_CMD=.*|SHUTDOWN_CMD="/sbin/shutdown -h now"|' /etc/cyberdeck-pi4/tft.conf
sudo systemctl restart cyberdeck-panel
```

### 9.8 Shutdown button — the real thing (destructive)

Hold the bottom button for 3 seconds.

**Pass:** the `SHUTTING DOWN` card appears, the green ACT LED blinks its 10-flash halt
pattern, and the Pi powers down. After booting again:

```bash
journalctl -b -1 -n 30 --no-pager                     # clean shutdown sequence
journalctl -b -1 | grep -iE 'ext4-fs error|unclean|recovery'   # expect nothing
```

**Pass:** no filesystem recovery on the next boot — that is what distinguishes a safe halt
from pulling power, and it is the whole reason this button exists.

---

## 10. Integration tests

### 10.1 Reboot persistence

```bash
sudo reboot
```

After boot, with no login:

**Pass:** the panel is drawn within ~30 s of power-on, all services green, buttons respond,
and `systemctl is-enabled cyberdeck-panel` returns `enabled`.

### 10.2 Panel survives a broken stack

```bash
sudo systemctl stop cyberdeck-pi4.service hostapd dnsmasq mosquitto nginx
```

**Pass:** the panel still refreshes and shows four red service items — because the unit uses
`Wants=`, not `Requires=`. A panel that dies with the stack is useless.

Recover with the power button (press twice) or `sudo systemctl restart cyberdeck-pi4.service`.

### 10.3 Crash recovery

```bash
sudo pkill -9 -f button_daemon.py
sleep 8
systemctl is-active cyberdeck-panel      # expect: active
```

**Pass:** systemd restarts it and no `GPIO busy` appears in the journal — that is what the
`ExecStartPre` sleep buys.

### 10.4 Config robustness

```bash
sudo mv /etc/cyberdeck-pi4/tft.conf /tmp/tft.conf.bak
sudo systemctl restart cyberdeck-panel   # must start on built-in defaults
sudo mv /tmp/tft.conf.bak /etc/cyberdeck-pi4/tft.conf
sudo systemctl restart cyberdeck-panel
```

**Pass:** the daemon starts either way. A missing or malformed config degrades to defaults;
it never crash-loops.

### 10.5 Bridge mode interaction

With bridge mode enabled, run the §9.4 low-power cycle again.

**Pass:** after `lowpower.sh disable`, the deck comes back **bridged** — badge amber, WAN IP
shown, `ping -c1 1.1.1.1` succeeds from a WiFi client. Recovery goes through
`cyberdeck-pi4.service`, which reads `bridge.conf`, so the mode survives a low-power cycle.

---

## 11. CI (`.github/workflows/ci-tft.yml`)

The addon ships its own workflow file so you don't have to merge YAML into the base
project's `ci.yml`. It is path-filtered — it only runs when addon files change. The render
job is the valuable one: it catches layout regressions and Python errors on every push, with
no hardware, and uploads preview PNGs to each run.

The complete file is already in the repo at `.github/workflows/ci-tft.yml`; its three jobs
are:

```yaml
  tft-shellcheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: ShellCheck TFT scripts
        run: |
          sudo apt-get update && sudo apt-get install -y shellcheck
          shellcheck scripts/lowpower.sh scripts/install-tft.sh \
                     config/button-aux.sh.example

  tft-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install pyflakes
      - run: python -m compileall -q scripts/tft
      - run: pyflakes scripts/tft/*.py

  tft-render:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install render deps
        run: |
          sudo apt-get update
          sudo apt-get install -y python3-pil fonts-dejavu-core
      - name: Render panel variants
        run: |
          cd scripts/tft
          TFT_STUB=1 TFT_PNG=/tmp/panel-nobatt.png python3 cyberdeck_panel.py
          TFT_STUB=1 TFT_STUB_BATTERY='{"percent":62,"charging":false,"volts":3.9}' \
            TFT_PNG=/tmp/panel-batt.png python3 cyberdeck_panel.py
          TFT_STUB=1 TFT_STUB_BATTERY='{"percent":8,"charging":true}' \
            TFT_PNG=/tmp/panel-low.png python3 cyberdeck_panel.py
      - name: Assert dimensions
        run: |
          python3 - <<'PY'
          from PIL import Image
          for f in ("nobatt", "batt", "low"):
              im = Image.open(f"/tmp/panel-{f}.png")
              assert im.size == (320, 240), (f, im.size)
              assert im.convert("RGB").getcolors(maxcolors=1) is None, f"{f} is a flat frame"
          print("panel renders OK")
          PY
      - uses: actions/upload-artifact@v4
        with:
          name: tft-panel-previews
          path: /tmp/panel-*.png
```

The uploaded PNGs mean every pull request that touches the panel carries a visual diff you
can eyeball without a Pi on the desk.

> **Note.** `config/tft.conf` should also be added to the existing ShellCheck job's
> exclusions or given a `# shellcheck disable` header if you lint config files — it is
> sourced, not executed.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Garbled stripe in a screen corner | Writing 320×240 into the HDMI framebuffer | `TFT_FB=/dev/fb1` (§3.1) |
| Cyan renders yellow, red renders blue | RGB565 channel order | `"RGB;16"` in `_pack565()` (§8.3) |
| Panel upside-down / rail on the wrong side | Enclosure mount | `TFT_ROTATE=180` |
| Flicker, console text bleeding through | Kernel console mapped to the TFT | Remove `fbcon=map:` , boot to CLI (§3.2) |
| `lgpio.error: 'GPIO busy'` | Two processes claiming the pins | `systemctl stop cyberdeck-panel` before manual tests |
| `RuntimeError: Failed to add edge detection` | RPi.GPIO on kernel 5.x+ | Use gpiozero + lgpio; check `GPIOZERO_PIN_FACTORY` |
| No buttons respond, no errors | Daemon crashed, systemd in `RestartSec` backoff | `journalctl -u cyberdeck-panel -n50` |
| Panel frozen on one frame | Refresh thread died, or an overlay with a long expiry is stuck | Restart the unit; check for a traceback |
| Panel frozen but clock in the journal moves | Backlight is off — redraw is intentionally skipped | Press the top button |
| Shutdown fires on a light tap | `hold_time` not applied (old config cached) | `HOLD_SEC=3`, restart the unit, retest §9.7 |
| AP never returns after low power | Recovery bypassed `cyberdeck-pi4.service` | `systemctl restart cyberdeck-pi4.service`; check the unit is installed and enabled |
| AP returns but clients get no DNS | dnsmasq started before `wlan0` had its IP | This is the base project's known gotcha; the `sleep 2` in `disable_lowpower` plus `cyberdeck-pi4.service` ordering fixes it |
| Refresh eats 40%+ CPU | Pure-Python RGB565 fallback | Upgrade Pillow (`python3-pil` ≥ 9) |
| Screen dark after entering low power | `LOWPOWER_BACKLIGHT=1` | Press the top button, or set it to 0 |

Diagnostic one-liners:

```bash
sudo journalctl -u cyberdeck-panel -f                    # live daemon log
sudo journalctl -t cyberdeck-lowpower --no-pager -n 30    # low power script log
/opt/cyberdeck-pi4/scripts/lowpower.sh status                 # power mode summary
sudo systemctl stop cyberdeck-panel && \
  sudo python3 /opt/cyberdeck-pi4/tft/cyberdeck_panel.py      # render once, by hand
```

---

## 13. Acceptance checklist

Sign-off list before calling the addon done.

**Panel**

- [ ] Preview renders 320×240 off-device (§8.1)
- [ ] Panel renders on the TFT with live data (§8.2)
- [ ] Colours match the style guide, no channel swap (§8.3)
- [ ] Orientation matches the physical buttons (§8.4)
- [ ] Every field cross-checks against its source command (§8.5)
- [ ] Service items, badge and gauges change state correctly (§8.6)
- [ ] Battery placeholder renders at 90 / 45 / 12 / charging / absent / corrupt (§8.7)
- [ ] Clock advances every refresh, no tearing, low CPU (§8.8)

**Buttons**

- [ ] All four buttons detected in the right physical order (§9.1)
- [ ] Backlight toggles, GPIO 18 confirmed with `pinctrl` (§9.2)
- [ ] `lowpower.sh` dry run prints the full plan and changes nothing (§9.3)
- [ ] Low power enable/disable verified including client reconnect (§9.4)
- [ ] Button path gives immediate feedback, completes within time budget (§9.5)
- [ ] AUX shows `UNASSIGNED` without a hook and runs one when present (§9.6)
- [ ] Tap and short hold do **not** shut down; 3 s hold does (§9.7)
- [ ] Real shutdown is clean, no filesystem recovery on next boot (§9.8)

**Integration**

- [ ] Panel and buttons come up automatically after reboot (§10.1)
- [ ] Panel survives the whole stack being stopped (§10.2)
- [ ] Daemon restarts cleanly after `kill -9`, no GPIO busy (§10.3)
- [ ] Missing/malformed config falls back to defaults (§10.4)
- [ ] Bridge mode survives a low-power cycle (§10.5)
- [ ] CI render job passes and uploads previews (§11)

---

## 14. Adding the battery later

The panel is already drawing the indicator; only the data source is missing. When hardware is
fitted:

1. Wire the gauge (I²C on GPIO 2/3 — **check it does not collide with the PiTFT's STMPE touch
   controller address** if you keep touch enabled; the display itself is SPI, so it does not
   conflict).
2. `sudo apt install python3-smbus` (or the vendor's CircuitPython library).
3. Replace the body of `read_battery()` in `/opt/cyberdeck-pi4/tft/battery.py` using one of the
   commented templates. Keep the return contract: dict or `None`, never an exception.
4. `sudo systemctl restart cyberdeck-panel`.
5. Re-run §8.7 with the real cell — charge and discharge it past each threshold and confirm
   the colours change at 50% and 20%.

Two further steps worth doing at the same time, both outside this addon's scope but enabled
by it:

- Publish the same dict to MQTT (`cyberdeck/battery`) so the web dashboard's
  `.battery-visual` component can show it too — the style guide already has the markup.
- Add a low-battery hook: below ~5%, have the daemon call `lowpower.sh enable`, and below
  ~3%, trigger the same safe shutdown path the bottom button uses. That path is already
  written and already tested by §9.7.

---

## 15. Uninstall

```bash
sudo ./scripts/uninstall-tft.sh              # remove everything, restore backlight
sudo KEEP_CONF=1 ./scripts/uninstall-tft.sh  # keep /etc/cyberdeck-pi4/tft.conf
```

The script stops and disables the service, removes the installed files, leaves the backlight
on, and — importantly — if the deck was left in low power mode it recovers the stack through
`lowpower.sh disable` *before* deleting that script, so you are never left with the AP down
and no easy way to bring it back. The base project's own services are never touched.

Equivalent manual steps, if you prefer:

```bash
sudo systemctl disable --now cyberdeck-panel
sudo rm -f /etc/systemd/system/cyberdeck-panel.service
sudo systemctl daemon-reload
sudo rm -rf /opt/cyberdeck-pi4/tft /opt/cyberdeck-pi4/scripts/lowpower.sh
sudo rm -f /etc/cyberdeck-pi4/tft.conf /etc/cyberdeck-pi4/button-aux.sh*
sudo pinctrl set 18 op dh        # leave the backlight on
# if it was in low power mode:
sudo systemctl restart cyberdeck-pi4.service && sudo rfkill unblock wifi
```

---

## 16. File summary

| File | Installed location | Purpose |
|---|---|---|
| `cyberdeck_panel.py` | `/opt/cyberdeck-pi4/tft/` | Style-guide renderer + framebuffer writer |
| `battery.py` | `/opt/cyberdeck-pi4/tft/` | Battery provider — **placeholder** |
| `button_daemon.py` | `/opt/cyberdeck-pi4/tft/` | Buttons, action queue, refresh loop |
| `lowpower.sh` | `/opt/cyberdeck-pi4/scripts/` | AP off + low power enable/disable/status |
| `install-tft.sh` | repo only | Installer: deps, files, service, framebuffer detect, self-check |
| `uninstall-tft.sh` | repo only | Clean removal + stack/backlight restore |
| `tft.conf` | `/etc/cyberdeck-pi4/` | Shared config (shell + Python) |
| `button-aux.sh` | `/etc/cyberdeck-pi4/` | Optional hook for the undefined button |
| `cyberdeck-panel.service` | `/etc/systemd/system/` | systemd unit |
| `ci-tft.yml` | `.github/workflows/` | Standalone addon CI (shellcheck, lint, render) |
| `Makefile.tft` | repo only | `make` convenience targets |
| `TFT_PANEL.md` | `docs/` | This document |
