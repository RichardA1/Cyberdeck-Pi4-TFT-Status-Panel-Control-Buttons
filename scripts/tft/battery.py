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
