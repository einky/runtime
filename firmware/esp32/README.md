# einky-esp32 dev bridge

ESP32 firmware that stands in for a broken Pi during a demo: receive 800×480
1-bit frames over TCP from the runtime on WSL and forward them to a
Waveshare 7.5" V2 e-paper, then read 7 buttons and send their names back over
TCP. **This is a temporary bring-up hack — see `meta/adr/0006-esp32-dev-bridge.md`.**

## Wiring

| Signal | ESP32 pin |
|---|---|
| EPD CS    | 5 |
| EPD DC    | 17 |
| EPD RST   | 16 |
| EPD BUSY  | 4 |
| EPD SCK   | 18 (VSPI default) |
| EPD MOSI  | 23 (VSPI default) |
| Up / Down / Left / Right | 32 / 33 / 25 / 26 |
| A / B / Start            | 27 / 14 / 12 |

Buttons go to GND when pressed; firmware uses `INPUT_PULLUP`.

## Build & flash

1. `cp include/config.h.example include/config.h` and fill in your hotspot
   SSID/password and the server IP/ports.
2. `pio run -t upload` (PlatformIO must be installed; VS Code or `pip install
   platformio`).
3. `pio device monitor` to watch logs at 115200.

## WSL networking

WSL2 is NAT'd off the laptop's network by default, so the ESP32 cannot reach
the WSL listener directly. Two options:

- **Mirrored networking (easiest, modern WSL):** put `[wsl2]\nnetworkingMode=mirrored`
  in `%USERPROFILE%\.wslconfig`, restart WSL. The ESP32 then connects to the
  laptop's hotspot IP directly.
- **Port-proxy (older WSL):** forward the two ports from the Windows host to
  the WSL VM:
  ```powershell
  $wsl = (wsl hostname -I).Trim().Split(' ')[0]
  netsh interface portproxy add v4tov4 listenport=5333 connectaddress=$wsl connectport=5333
  netsh interface portproxy add v4tov4 listenport=5334 connectaddress=$wsl connectport=5334
  netsh advfirewall firewall add rule name="einky-frame"  dir=in action=allow protocol=TCP localport=5333
  netsh advfirewall firewall add rule name="einky-input"  dir=in action=allow protocol=TCP localport=5334
  ```

## Run the WSL side

From `runtime/`:

```bash
EINKY_BACKEND=tcp EINKY_TCP_PORT=5333 \
  .venv/bin/python -m frame_processor &
EINKY_INPUT_BACKEND=net EINKY_INPUT_PORT=5334 \
  .venv/bin/python -m input &
```

Then power up the ESP32. Connection order doesn't matter — both sides
reconnect on drop.
