<!-- Paste this into the base project's README.md, e.g. after "Network Modes". -->

## Hardware Panel (Adafruit 2.8" PiTFT)

The optional TFT addon turns the deck into a self-contained control surface: a 320×240 status
panel styled to the Cyberdeck UI, plus four hardware buttons — no client, keyboard, or SSH
needed.

- **Panel** — live services, AP/WAN IPs, client count, CPU temp / memory / disk gauges,
  Isolated/Bridged badge, and a battery indicator (placeholder until UPS hardware is fitted).
- **Buttons** — backlight toggle · AP-off + low-power toggle · a user-defined AUX hook ·
  safe shutdown (3-second hold).

```bash
# on the Pi, in the repo:
sudo ./scripts/install-tft.sh          # install + enable + self-check
sudo ./scripts/install-tft.sh --check  # re-runnable health check
```

Full setup, per-component test procedure, and troubleshooting: **[docs/TFT_PANEL.md](docs/TFT_PANEL.md)**.
