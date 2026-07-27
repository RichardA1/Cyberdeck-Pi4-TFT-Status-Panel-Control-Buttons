# Cyberdeck Pi4 — TFT Status Panel & Control Buttons

![CI](https://github.com/RichardA1/Cyberdeck-Pi4-TFT-Status-Panel-Control-Buttons/actions/workflows/ci-tft.yml/badge.svg)

A local hardware control surface for the **Cyberdeck Pi4**: a 320×240 status panel drawn on
an Adafruit 2.8" PiTFT, plus four hardware buttons — no client device, keyboard, or SSH
session needed to see status or control the deck.

<img width="320" height="240" alt="panel_preview" src="https://github.com/user-attachments/assets/2ca8fc4f-a763-4448-8352-b557edfbf8d7" />

The panel is styled to match the Cyberdeck web UI, so the physical screen and the browser
dashboard read as the same product.

<!-- Optional: drop a photo of the running panel here -->
<!-- ![panel](docs/panel.jpg) -->

---

## Features

**Status panel** (refreshes every few seconds):

- Service health for `hostapd`, `dnsmasq`, `mosquitto`, `nginx`
- AP IP, connected client count, WAN/Ethernet IP
- CPU temperature, memory, and disk gauges with color thresholds
- Isolated / Bridged network-mode badge
- Battery indicator — **placeholder** until UPS hardware is fitted (shows `NO SENSOR`)

**Control buttons** (physical top to bottom):

| Position | GPIO (BCM) | Action |
|---|---|---|
| Top | 27 | Toggle TFT backlight off/on |
| Second | 23 | Toggle WiFi AP off + low-power mode / restore |
| Third | 22 | AUX — runs a user hook script if present (otherwise `UNASSIGNED`) |
| Bottom | 17 | Safe shutdown — **requires a 3-second hold** |

The shutdown button cannot fire on a single press: a tap only shows a `HOLD 3s TO CONFIRM`
hint, so a knock against the deck never halts it. Low-power mode and shutdown are both
recoverable — low power by pressing the button again, and the daemon never depends on the
network to function.

---

## Hardware requirements

- Raspberry Pi 4 Model B running Raspberry Pi OS / Debian **Trixie**
- Adafruit 2.8" PiTFT (320×240, ILI9340/9341, SPI) enumerating as a framebuffer
- Four tactile buttons wired to GPIO **17, 22, 23, 27** (active-low, internal pull-ups)
- Backlight on GPIO **18**

This project is an **addon**. It expects the base Cyberdeck Pi4 stack (WiFi AP / MQTT broker /
captive portal, installed as `cyberdeck-pi4.service`, config under `/etc/cyberdeck-pi4/`) to
be present. The panel reads `/etc/cyberdeck-pi4/bridge.conf`, and low-power recovery restarts
`cyberdeck-pi4.service`. The panel and buttons will still install and run without the base
stack, but the AP button (#23) has nothing to bring back until the base project is installed.

---

## Install on the Pi

SSH in (PuTTY or terminal), then:

```bash
cd ~
git clone https://github.com/RichardA1/Cyberdeck-Pi4-TFT-Status-Panel-Control-Buttons.git
cd Cyberdeck-Pi4-TFT-Status-Panel-Control-Buttons
sudo ./scripts/install-tft.sh
```

The installer is idempotent and does everything in one pass:

- installs dependencies (`python3-pil`, `python3-gpiozero`, `python3-lgpio`,
  `fonts-dejavu-core`, `rfkill`)
- copies the daemon and panel to `/opt/cyberdeck-pi4/tft/` and scripts to
  `/opt/cyberdeck-pi4/scripts/`
- installs config to `/etc/cyberdeck-pi4/tft.conf` (never overwrites an existing one)
- **auto-detects the PiTFT framebuffer node** and writes `TFT_FB=/dev/fb1` if the panel is
  not on `fb0` (common when the KMS HDMI driver claims `fb0`)
- installs, enables, and starts `cyberdeck-panel.service`
- runs a self-check

### Verify

```bash
sudo ./scripts/install-tft.sh --check     # framebuffer, deps, service — re-runnable
```

A healthy install prints:

```
==> self-check
    [ok] PiTFT framebuffer at /dev/fb0 (320x240)
    [ok] python deps present (PIL, gpiozero, lgpio)
    [ok] cyberdeck-panel.service is active
==> self-check passed
```

If the scripts refuse to run with `command not found`, they lost their executable bit in
transit — run `chmod +x scripts/*.sh scripts/tft/*.py` once, or fix it at the source (see
Maintenance).

### Convenience targets

```bash
make -f Makefile.tft tft-install     # install
make -f Makefile.tft tft-check       # self-check
make -f Makefile.tft tft-preview     # render the panel to /tmp/panel.png (no hardware)
make -f Makefile.tft tft-uninstall   # remove the addon
```

---

## Configuration

All settings live in `/etc/cyberdeck-pi4/tft.conf` (installed from `config/tft.conf`). It is a
plain `KEY=value` file shared by the shell scripts and the Python daemon. Common edits:

| Key | Purpose |
|---|---|
| `TFT_FB` | Framebuffer node (`/dev/fb0` or `/dev/fb1`) |
| `TFT_ROTATE` | `0` or `180` if the panel is mounted upside-down |
| `REFRESH_SEC` | Panel redraw interval |
| `HOLD_SEC` | Seconds to hold the shutdown button (default `3`) |
| `SHUTDOWN_CMD` | Command run on confirmed shutdown (override for testing) |
| `AUX_CMD` | Hook run by button #22 if present and executable |
| `LOWPOWER_*` | What low-power mode stops, and CPU governor / LED behavior |

After editing, apply changes with:

```bash
sudo systemctl restart cyberdeck-panel
```

### Assigning the AUX button (#22)

Drop an executable script at the path in `AUX_CMD` (default `/etc/cyberdeck-pi4/button-aux.sh`);
no daemon restart needed. A template is installed at
`/etc/cyberdeck-pi4/button-aux.sh.example`.

---

## Service management

```bash
sudo systemctl status  cyberdeck-panel      # state
sudo systemctl restart cyberdeck-panel      # after config or file changes
sudo journalctl -u cyberdeck-panel -f       # live log
```

---

## Maintaining the files

Development happens on your workstation and is published to GitHub; the Pi is a **deployment
target** you never edit directly. Keep that direction of flow and updates stay painless.

### The update loop

1. **Edit on your workstation**, commit, and push to GitHub (from Git Bash on Windows):

   ```bash
   git add -A
   git commit -m "describe the change"
   git push
   ```

2. **Update the Pi to match GitHub.** On the Pi, use a hard reset rather than `git pull`:

   ```bash
   cd ~/Cyberdeck-Pi4-TFT-Status-Panel-Control-Buttons
   git fetch origin
   git reset --hard origin/main
   ```

   > **Why `reset --hard`, not `pull`:** installing on the Pi changes file permissions and
   > can touch line endings, which git sees as "local changes." A `git pull` then aborts with
   > *"Your local changes would be overwritten by merge."* Since you never edit on the Pi,
   > discarding those changes with `reset --hard origin/main` is the correct, friction-free
   > way to update — it makes the checkout byte-for-byte match GitHub.

3. **Reinstall so the running copy updates.** The daemon runs from `/opt/cyberdeck-pi4/tft/`,
   **not** from the repo checkout. Pulling the repo alone does **not** change what's running —
   you must recopy:

   ```bash
   sudo ./scripts/install-tft.sh
   ```

   The installer copies the checkout into `/opt` and restarts the service. (Editing a file in
   the repo folder without reinstalling is the single most common "my fix didn't take"
   mistake.)

### Confirming which version is actually running

Commit messages can be misleading; file **contents** are the truth. When a change doesn't seem
to take effect, grep the file in all three places to find where the chain broke:

```bash
# in the repo checkout on the Pi
grep -n "<something-from-your-change>" scripts/tft/button_daemon.py

# what GitHub actually has
git show origin/main:scripts/tft/button_daemon.py | grep -n "<something-from-your-change>"

# what is actually running
grep -n "<something-from-your-change>" /opt/cyberdeck-pi4/tft/button_daemon.py
```

If the repo and GitHub agree but `/opt` differs, you skipped step 3. If GitHub differs from
your workstation, your push didn't land.

### Executable bits

Shell and Python entry-point files must be executable, and that bit should live in the repo so
every fresh clone gets runnable scripts. If GitHub shows a script as mode `100644`, fix it once
at the source (from your workstation):

```bash
git update-index --chmod=+x scripts/*.sh scripts/tft/*.py
git commit -m "Set executable bits"
git push
```

### Line endings (Windows)

Set this once on your workstation so Windows editors don't rewrite scripts with CRLF, which
breaks them on Linux:

```bash
git config --global core.autocrlf input
```

### A clean redeploy from scratch

If a Pi's checkout ever gets into a confusing state, the safest reset is to remove and
re-clone:

```bash
sudo ./scripts/uninstall-tft.sh
cd ~ && rm -rf Cyberdeck-Pi4-TFT-Status-Panel-Control-Buttons
git clone https://github.com/RichardA1/Cyberdeck-Pi4-TFT-Status-Panel-Control-Buttons.git
cd Cyberdeck-Pi4-TFT-Status-Panel-Control-Buttons && sudo ./scripts/install-tft.sh
```

---

## Adding the battery indicator later

The panel already draws the battery block; only the data source is missing. When you fit a
fuel gauge or UPS HAT, edit the single function `read_battery()` in
`scripts/tft/battery.py` (templates for MAX17048 / INA219 / PiSugar are in the comments),
push, then update the Pi with the loop above. No layout changes required. Full details in
[docs/TFT_PANEL.md §14](docs/TFT_PANEL.md).

---

## Uninstall

```bash
sudo ./scripts/uninstall-tft.sh              # remove addon, restore backlight
sudo KEEP_CONF=1 ./scripts/uninstall-tft.sh  # keep /etc/cyberdeck-pi4/tft.conf
```

The base project's services are left untouched. If the deck was in low-power mode, the
uninstaller restores the stack before removing itself.

---

## Repository layout

```
config/
  tft.conf                  installed to /etc/cyberdeck-pi4/tft.conf
  cyberdeck-panel.service   systemd unit
  button-aux.sh.example     template for the AUX (#22) hook
scripts/
  install-tft.sh            installer (deps, files, framebuffer detect, self-check)
  uninstall-tft.sh          clean removal
  lowpower.sh               AP off + low-power enable|disable|status
  tft/
    cyberdeck_panel.py      framebuffer renderer (the status panel)
    button_daemon.py        button watcher + refresh loop (the service)
    battery.py              battery provider — PLACEHOLDER
docs/
  TFT_PANEL.md              full setup, per-component tests, troubleshooting
Makefile.tft                convenience targets
.github/workflows/ci-tft.yml  CI: shellcheck, python lint, headless render
```

---

## Documentation

Full installation notes, a per-component test procedure, and a troubleshooting table are in
**[docs/TFT_PANEL.md](docs/TFT_PANEL.md)**.

## License

See [LICENSE](LICENSE).
