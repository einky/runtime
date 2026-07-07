# runtime

The **shared on-device library** of the Einky Pi handheld: the single owner
(ADR 0008) of the e-ink frame pipeline, the contract keymap, and the C SPI
panel driver. It is consumed two ways:

- **imported by the launcher** (`einky/launcher`, ADR 0009) — the app the
  device boots into uses `spi_driver.open_panel()`, the
  `frame_processor` dither/pack pipeline, `input.keymap`, and
  `input.net_sender` for the launcher↔game bridge;
- **packaged into InkyOS** by `buildroot_os` as the `inky-runtime` Buildroot
  package (which also cross-compiles the CFFI SPI extension on the Pi target).

The runtime does not know what is on the screen; it knows how to turn RGB/PNG
pixels into packed 1-bit panel frames, how to talk to the GDEM0397T81P, and
what the seven buttons are called.

## Architecture

```
producers                       process (one impl)                 dispatch
launcher Canvas / game PNGs --> frame_processor/                -> spi_driver/ (C, libgpiod +
(or Xvfb capture, legacy)       grey -> Floyd-Steinberg            /dev/spidev0.0) -> GDEM0397T81P
                                dither -> pack 1-bit (MSB)      -> SocketSink/TcpFrameSink (dev preview)

input                           input/
7x GPIO (gpiozero) / TCP    --> keymap.py (generated from the contract)
                                net_sender.py -> a game's input socket
```

Console scripts (installed by the wheel; useful for debugging — since ADR 0009
the launcher performs these roles in-process and none are in the boot path):

- `inky-frame` — standalone Xvfb capture → dither → dispatch loop,
- `inky-input` — standalone GPIO/TCP button reader → keysym injector,
- `inky-eink-receiver` — standalone engine-capture (PNG socket) receiver.

The frame/input/SPI contracts are owned here and generated from
`meta/shared/hardware.toml` (see "GPIO pin map"), so the package stays cleanly
self-describing for downstream consumers. (The `systemd/` units and the ESP32
firmware under `firmware/esp32` are retired pre-Buildroot/pre-ADR-0009
artifacts kept for reference.)

## Layout

| Path                  | What                                             |
| --------------------- | ------------------------------------------------ |
| `src/frame_processor` | Xvfb capture, dither, dispatch (Python + Pillow + numpy) |
| `src/input`           | GPIO button reader + keypress injector (Python)  |
| `src/spi_driver`      | C SPI driver + CFFI binding for the e-paper panel |
| `tests/unit`          | Mocked GPIO / mocked framebuffer                 |
| `tests/integration`   | Socket-backed end-to-end against golden frames   |
| `tests/golden`        | Golden hashes pinning dither output              |
| `scripts/`            | `gen_from_contract.py` (`make gen`) + `install-renpy-sdk.sh` (symlinked from `meta/`) |
| `systemd/`            | Retired pre-Buildroot service units (kept for reference) |
| `firmware/esp32`      | Retired ESP32 dev bridge (ADR 0006; kept for reference) |

## GPIO pin map

**Pins come from the shared hardware contract, `meta/shared/hardware.toml`** (the
single source of truth; see ADR 0008), and are rendered for humans in
`docs/hardware/wiring.md`. The runtime does not hand-maintain them: they are
generated into `src/input/keymap.py`, `src/spi_driver/contract.h`, and
`src/frame_processor/constants.py` by `scripts/gen_from_contract.py` (`make gen`),
and the `contract-parity` CI check fails if a committed copy drifts. The table
below is a convenience copy.

| Function     | BCM pin | Pi header | Notes                       |
| ------------ | ------- | --------- | --------------------------- |
| Button: Up   | 5       | 29        | pull-up, active-low         |
| Button: Down | 6       | 31        | pull-up, active-low         |
| Button: Left | 13      | 33        | pull-up, active-low         |
| Button: Right| 19      | 35        | pull-up, active-low         |
| Button: A    | 16      | 36        | pull-up, active-low → Space |
| Button: B    | 20      | 38        | pull-up, active-low → Esc   |
| Button: Start| 21      | 40        | pull-up, active-low → Enter |
| SPI MOSI     | 10      | 19        | to panel DIN                |
| SPI SCLK     | 11      | 23        | to panel CLK                |
| SPI CS0      | 8       | 24        | to panel CS                 |
| Panel DC     | 25      | 22        | data/command select         |
| Panel RST    | 17      | 11        | hardware reset              |
| Panel BUSY   | 24      | 18        | active-high                 |

### Button → keypress map

```
D-pad  → Up / Down / Left / Right
A      → space     (advance dialogue)
B      → Escape    (back / menu)
Start  → Return    (confirm)
```

## Dev-mode socket protocol

Set `EINKY_BACKEND=socket` and the runtime streams frames to a Unix socket
instead of SPI. The receiver is `tools/preview.py` on a developer
workstation (Mac / Linux) which renders frames in a window so you can iterate
on Ren'Py screens without flashing a Pi.

Per-frame wire format, little-endian:

```
| 4 bytes | 4 bytes | 4 bytes | N bytes |
| 'EINK'  | width   | height  | packed  |
```

`packed` is MSB-first 1-bit, length = `height * (width / 8)` =
`480 * 100 = 48000` bytes for the production panel. One frame per send;
no streaming framing beyond that. The connection stays open across frames.

Override paths via env:

| Var                  | Default                       |
| -------------------- | ----------------------------- |
| `EINKY_BACKEND`      | `spi`                         |
| `EINKY_SOCKET_PATH`  | `/tmp/einky-preview.sock`     |
| `EINKY_TARGET_FPS`   | `2.0`                         |
| `EINKY_SPI_DEV`      | `/dev/spidev0.0`              |
| `EINKY_LOG_LEVEL`    | `INFO`                        |
| `DISPLAY`            | `:0`                          |

## Cooperation with the launcher (ADR 0009)

The launcher — a native Python app, not a Ren'Py game — owns the panel and the
buttons for the whole uptime and imports this repo directly:

- **menu frames:** the launcher packs its own Pillow canvas and calls the panel
  driver (`open_panel().partial_refresh/full_refresh`), deciding partial vs
  full itself;
- **game frames:** a running Ren'Py game pushes PNGs over the engine-capture
  socket; the launcher decodes them through this repo's
  `to_panel_grey -> floyd_steinberg -> pack_1bit` (one dither implementation,
  ADR 0008) and drives the panel;
- **input:** the launcher reads GPIO via the generated keymap and forwards
  button *names* to the game's input socket via `input.net_sender`; the
  in-game hook queues the mapped Ren'Py events. No X-level key injection is in
  the shipping path;
- the panel is deep-slept before power-off.

## Setup

```bash
make setup         # venv + dev deps + pre-commit hooks
make build-c       # compile the SPI driver C extension
make test          # unit + integration (no hardware)
make lint          # ruff + mypy + clang-format
make run-dev       # EINKY_BACKEND=socket, talks to tools/preview.py
make run-prod      # EINKY_BACKEND=spi, on the Pi
```

The Ren'Py SDK is **not** installed here. On the device the engine is built
from source by `buildroot_os` and lives at `/opt/renpy`. On a dev workstation:

```bash
./scripts/install-renpy-sdk.sh ~/renpy
```

(That script is a symlink to `../meta/scripts/install-renpy-sdk.sh`. The CI
job `install-script-parity` enforces that the two stay byte-identical.)

## Tooling

- **Python**: `ruff` (lint + format + import sort, replaces black/flake8/isort),
  `mypy --strict`, `pytest`.
- **C**: `clang-format`, `cppcheck`, `make`.
- **Pre-commit**: `.pre-commit-config.yaml` runs ruff + ruff-format +
  clang-format on every commit.

## License

MIT — see `LICENSE`.
