"""Entry point: `python -m input`."""

from __future__ import annotations

import logging
import os
import sys


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("EINKY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from input.handler import InputHandler

    handler = InputHandler()
    handler.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
