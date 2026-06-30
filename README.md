# runtime

User-space code that runs on the Einky Pi handheld at boot, **except** the
launcher (in `einky/launcher`) and the Ren'Py SDK (installed by `einky/os`).

The runtime has two responsibilities:

1. **Pump frames** from the launcher's Xvfb display to the GDEM0397T81P
   e-paper panel — capture, resize, greyscale, dither, dispatch.
2. **Pump inputs** from the seven GPIO buttons into Ren'Py as keypresses.

The launcher renders. The runtime does not know what is on the screen; it
only knows how to get pixels off Xvfb and onto e-ink, and how to feed buttons
back the other way.

**`buildroot_os` consumes this repo as a Buildroot package** (ADR 0008): it builds
the wheel and runs the three console-scripts it exposes —

- `inky-frame` — the Xvfb capture → dither → dispatch pipeline (`frame_processor`),
- `inky-input` — the GPIO / TCP button reader → keypress injector (`input`),
- `inky-eink-receiver` — the in-engine path: decode PNGs pushed by Ren'Py's
  `eink_push_callback` over the engine-capture socket and feed them through the
  *same* dither/dispatch pipeline (no second dither).

The frame/input/SPI/ESP32 contracts those tools share are owned here and generated
from `meta/shared/hardware.toml` (see "GPIO pin map"), so the package stays cleanly
self-describing for downstream consumers.

## Architecture

```
+---------------------+      +---------------------+
|  launcher (Ren'Py)  | <--- |  input/             | <--- 7x GPIO buttons
|  draws into Xvfb    |      |  gpiozero -> xdotool|
+----------+----------+      +---------------------+
           |
           v Xvfb root window
+----------+----------+      +---------------------+      +-------------+
|  frame_processor/   | ---> |  spi_driver/ (C)    | ---> | e-paper HW  |
|  capture -> resize  |      |  init/refresh/sleep |      | GDEM0397T81P|
|  -> grey -> dither  |      +---------------------+      +-------------+
+----------+----------+
           |  EINKY_BACKEND=socket (dev)
           v
+---------------------+
|  Unix socket        |  ---> tools/preview.py on a workstation
|  /tmp/einky-…sock   |
+---------------------+
```

Two systemd units in `systemd/` glue this onto boot. Both depend on
`inky-launcher.service` from the launcher repo.

## Layout

| Path                  | What                                             |
| --------------------- | ------------------------------------------------ |
| `src/frame_processor` | Xvfb capture, dither, dispatch (Python + Pillow + numpy) |
| `src/input`           | GPIO button reader + keypress injector (Python)  |
| `src/spi_driver`      | C SPI driver + CFFI binding for the e-paper panel |
| `tests/unit`          | Mocked GPIO / mocked framebuffer                 |
| `tests/integration`   | Socket-backed end-to-end against golden frames   |
| `tests/golden`        | Golden hashes pinning dither output              |
| `scripts/`            | `install-renpy-sdk.sh` (symlinked from `meta/`)  |
| `systemd/`            | Service units consumed by `os/stage-runtime/`    |

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

## Cooperation with the launcher

The launcher is a Ren'Py game running under Xvfb on `:0`. It draws normally;
it does not know e-paper exists. The runtime:

- captures the Xvfb root every `1 / EINKY_TARGET_FPS` seconds (default 2 fps),
- pushes a partial refresh to the panel each frame,
- triggers a full refresh every 30 frames to clear ghosting,
- deep-sleeps the panel on shutdown.

For inputs: `xdotool` posts keysyms to the focused window on `:0`, which is
always the Ren'Py game (the launcher locks focus). Ren'Py's standard
`config.keymap` consumes them like any keyboard event.

## Setup

```bash
make setup         # venv + dev deps + pre-commit hooks
make build-c       # compile the SPI driver C extension
make test          # unit + integration (no hardware)
make lint          # ruff + mypy + clang-format
make run-dev       # EINKY_BACKEND=socket, talks to tools/preview.py
make run-prod      # EINKY_BACKEND=spi, on the Pi
```

The Ren'Py SDK is **not** installed here. On the Pi it lives at
`/opt/renpy-sdk` (placed by `os/stage-runtime/`). On a dev workstation:

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
