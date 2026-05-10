"""Entry point: `python -m frame_processor`."""

from __future__ import annotations

import logging
import os
import sys

from frame_processor.capture import XCapture
from frame_processor.dispatch import make_sink
from frame_processor.processor import FrameProcessor


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("EINKY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    backend = os.environ.get("EINKY_BACKEND", "spi")
    socket_path = os.environ.get("EINKY_SOCKET_PATH", "/tmp/einky-preview.sock")
    tcp_host = os.environ.get("EINKY_TCP_HOST", "0.0.0.0")
    tcp_port = int(os.environ.get("EINKY_TCP_PORT", "5333"))
    fps = float(os.environ.get("EINKY_TARGET_FPS", "2.0"))

    sink = make_sink(backend, socket_path, tcp_host, tcp_port)
    proc = FrameProcessor(capture=XCapture(), sink=sink, target_fps=fps)
    proc.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
