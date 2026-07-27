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
