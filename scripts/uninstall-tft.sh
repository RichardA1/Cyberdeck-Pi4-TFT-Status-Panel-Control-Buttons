#!/bin/bash
# uninstall-tft.sh — remove the Cyberdeck Pi4 TFT panel addon.
# Leaves the base project (hostapd/dnsmasq/mosquitto/nginx/cyberdeck-pi4.service) untouched.
#
#   sudo ./scripts/uninstall-tft.sh              # remove everything, restore backlight
#   sudo KEEP_CONF=1 ./scripts/uninstall-tft.sh  # keep /etc/cyberdeck-pi4/tft.conf
set -euo pipefail

INSTALL_ROOT=${INSTALL_ROOT:-/opt/cyberdeck-pi4}
KEEP_CONF=${KEEP_CONF:-0}

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

echo "==> stopping service"
systemctl disable --now cyberdeck-panel.service 2>/dev/null || true
rm -f /etc/systemd/system/cyberdeck-panel.service
systemctl daemon-reload

# If the deck was left in low power mode, recover the stack BEFORE we delete the
# script that knows how to recover it.
if [ -f /run/cyberdeck-pi4/lowpower.state ] && [ -x "$INSTALL_ROOT/scripts/lowpower.sh" ]; then
    echo "==> deck is in low power mode — restoring before removal"
    "$INSTALL_ROOT/scripts/lowpower.sh" disable || true
fi

echo "==> removing files"
rm -rf "$INSTALL_ROOT/tft"
rm -f "$INSTALL_ROOT/scripts/lowpower.sh"

if [ "$KEEP_CONF" = "1" ]; then
    echo "    keeping /etc/cyberdeck-pi4/tft.conf (KEEP_CONF=1)"
    rm -f /etc/cyberdeck-pi4/button-aux.sh.example
else
    rm -f /etc/cyberdeck-pi4/tft.conf \
          /etc/cyberdeck-pi4/button-aux.sh \
          /etc/cyberdeck-pi4/button-aux.sh.example
fi

echo "==> leaving the backlight on"
pinctrl set 18 op dh 2>/dev/null || true

echo "==> done. Base project services were not touched."
