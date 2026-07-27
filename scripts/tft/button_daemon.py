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
