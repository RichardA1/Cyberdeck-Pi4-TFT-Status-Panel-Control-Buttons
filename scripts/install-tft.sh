#!/bin/bash
# install-tft.sh — install the Cyberdeck Pi4 TFT panel addon. Idempotent.
#
#   sudo ./scripts/install-tft.sh          # install + enable + start + self-check
#   sudo ./scripts/install-tft.sh --check  # self-check only, change nothing
#
# Safe to re-run: it never clobbers an existing /etc/cyberdeck-pi4/tft.conf.
set -euo pipefail

INSTALL_ROOT=${INSTALL_ROOT:-/opt/cyberdeck-pi4}
REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
CONF=/etc/cyberdeck-pi4/tft.conf

need_root() { [ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }; }

# -- framebuffer sanity: find the 320x240 node and warn if the config disagrees --
detect_fb() {
    local found=""
    for fb in /dev/fb0 /dev/fb1; do
        [ -e "$fb" ] || continue
        local n; n=${fb##*/fb}
        local size; size=$(cat "/sys/class/graphics/fb${n}/virtual_size" 2>/dev/null || echo "")
        if [ "$size" = "320,240" ]; then found="$fb"; fi
    done
    echo "$found"
}

self_check() {
    echo "==> self-check"
    local ok=0

    local fb; fb=$(detect_fb)
    if [ -n "$fb" ]; then
        echo "    [ok] PiTFT framebuffer at $fb (320x240)"
        if [ -f "$CONF" ]; then
            local want; want=$(grep -E '^TFT_FB=' "$CONF" | cut -d= -f2 | tr -d ' ')
            if [ -n "$want" ] && [ "$want" != "$fb" ]; then
                echo "    [WARN] tft.conf has TFT_FB=$want but the 320x240 panel is $fb"
                echo "           edit $CONF and set TFT_FB=$fb"
                ok=1
            fi
        fi
    else
        echo "    [WARN] no 320x240 framebuffer found — is the PiTFT overlay loaded?"
        ok=1
    fi

    python3 - <<'PY' || ok=1
try:
    from PIL import Image, ImageDraw, ImageFont  # noqa
    import gpiozero, lgpio  # noqa
    print("    [ok] python deps present (PIL, gpiozero, lgpio)")
except Exception as e:
    print("    [WARN] missing python dep:", e)
    raise SystemExit(1)
PY

    if systemctl is-active --quiet cyberdeck-panel.service 2>/dev/null; then
        echo "    [ok] cyberdeck-panel.service is active"
    else
        echo "    [WARN] cyberdeck-panel.service is not active — see: journalctl -u cyberdeck-panel -n30"
        ok=1
    fi

    if [ "$ok" -eq 0 ]; then
        echo "==> self-check passed"
    else
        echo "==> self-check found issues (see [WARN] above)"
    fi
    return "$ok"
}

if [ "${1:-}" = "--check" ]; then
    self_check
    exit $?
fi

need_root

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
if [ ! -f "$CONF" ]; then
    install -m 0644 "$REPO_DIR/config/tft.conf" "$CONF"
    # auto-set the framebuffer node if it is not the default
    fb=$(detect_fb)
    if [ -n "$fb" ] && [ "$fb" != "/dev/fb0" ]; then
        sed -i "s#^TFT_FB=.*#TFT_FB=$fb#" "$CONF"
        echo "    detected PiTFT at $fb — wrote it to tft.conf"
    fi
else
    echo "    $CONF exists — left untouched"
fi

echo "==> systemd"
install -m 0644 "$REPO_DIR/config/cyberdeck-panel.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable cyberdeck-panel.service
systemctl restart cyberdeck-panel.service
sleep 3      # let it settle so the self-check reflects reality

self_check || true

echo "==> done"
systemctl --no-pager --lines=5 status cyberdeck-panel.service || true
