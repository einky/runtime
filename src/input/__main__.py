"""Entry point: `python -m input`.

`EINKY_INPUT_BACKEND=gpio` (default) reads the on-device GPIO buttons.
`EINKY_INPUT_BACKEND=net` listens on TCP for button names from the ESP32
dev bridge (see ADR 0006); use this on WSL where no GPIO exists.
"""

from __future__ import annotations

import logging
import os
import sys


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("EINKY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    backend = os.environ.get("EINKY_INPUT_BACKEND", "gpio")
    if backend == "net":
        from input.net_handler import NetInputHandler

        host = os.environ.get("EINKY_INPUT_HOST", "0.0.0.0")
        port = int(os.environ.get("EINKY_INPUT_PORT", "5334"))
        NetInputHandler(host=host, port=port).serve_forever()
        return 0
    if backend == "gpio":
        from input.handler import InputHandler

        InputHandler().run_forever()
        return 0
    raise ValueError(f"unknown EINKY_INPUT_BACKEND={backend!r} (expected 'gpio' or 'net')")


if __name__ == "__main__":
    sys.exit(main())
