"""Engine-capture receiver: PNG over a Unix socket -> the standard frame pipeline.

buildroot_os runs the capture *inside* Ren'Py (``config.eink_push_callback`` in
``eink_hook.rpy``) and ships one PNG per stable frame to this receiver over the
engine-capture Unix socket. The framing is ``[protocol.engine_capture]``::

    | 4 bytes         | M bytes |
    | u32 length (BE) | PNG     |

We decode the PNG and feed it into the SAME greyscale -> Floyd-Steinberg -> pack
-> FrameSink path as the external X-capture stack (``processor.to_panel_grey`` +
``dither``), so there is exactly one dither implementation. The output sink is
chosen by ``EINKY_BACKEND`` via ``make_sink`` — identical to ``python -m
frame_processor`` — so this is just a different *capture* feeding the one pipeline.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import struct
import sys
from io import BytesIO

import numpy as np
from PIL import Image

from frame_processor.constants import (
    EINK_SOCKET,
    FRAME_TCP_PORT,
    PANEL_HEIGHT,
    PANEL_WIDTH,
    PREVIEW_SOCKET,
)
from frame_processor.dispatch import FrameSink, make_sink
from frame_processor.dither import floyd_steinberg, pack_1bit
from frame_processor.processor import to_panel_grey

log = logging.getLogger(__name__)

# Reject a corrupt length prefix before it drives a huge allocation. A PNG of an
# 800x480 frame is far smaller than this; the cap only rejects garbage.
_MAX_PNG_BYTES = 8 * 1024 * 1024


class EinkReceiver:
    """Accept PNG frames on a Unix socket and dispatch them through the pipeline."""

    def __init__(self, sink: FrameSink, socket_path: str = EINK_SOCKET) -> None:
        self._sink = sink
        self._path = socket_path

    def serve_forever(self) -> None:
        self._unlink_stale()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
            srv.bind(self._path)
            srv.listen(1)
            log.info("eink receiver on %s", self._path)
            try:
                while True:
                    conn, _ = srv.accept()
                    log.info("engine capture connected")
                    try:
                        self._handle(conn)
                    except OSError as e:
                        log.warning("engine capture error: %s", e)
                    finally:
                        conn.close()
                        log.info("engine capture disconnected; awaiting reconnect")
            finally:
                self._sink.close()
                self._unlink_stale()

    def _handle(self, conn: socket.socket) -> None:
        while True:
            header = self._recv_exact(conn, 4)
            if header is None:
                return
            (length,) = struct.unpack(">I", header)
            if length == 0 or length > _MAX_PNG_BYTES:
                log.warning("bad PNG length %d; dropping connection", length)
                return
            png = self._recv_exact(conn, length)
            if png is None:
                return
            self.process_png(png)

    def process_png(self, png: bytes) -> bytes:
        """Decode one PNG and push it through grey -> dither -> pack -> sink."""
        img = Image.open(BytesIO(png)).convert("RGB")
        rgb = np.asarray(img, dtype=np.uint8)
        packed = pack_1bit(floyd_steinberg(to_panel_grey(rgb)))
        self._sink.send(packed, PANEL_WIDTH, PANEL_HEIGHT)
        return packed

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def _unlink_stale(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self._path)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("EINKY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    backend = os.environ.get("EINKY_BACKEND", "spi")
    socket_path = os.environ.get("EINKY_SOCKET_PATH", PREVIEW_SOCKET)
    tcp_host = os.environ.get("EINKY_TCP_HOST", "0.0.0.0")
    tcp_port = int(os.environ.get("EINKY_TCP_PORT", str(FRAME_TCP_PORT)))
    eink_socket = os.environ.get("EINKY_EINK_SOCKET", EINK_SOCKET)

    sink = make_sink(backend, socket_path, tcp_host, tcp_port)
    EinkReceiver(sink=sink, socket_path=eink_socket).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
