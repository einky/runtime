"""Frame dispatch: SPI driver in prod, Unix-socket preview in dev.

Dev-mode socket protocol (length-prefixed, little-endian):

    | 4 bytes | 4 bytes | 4 bytes | N bytes |
    | magic   | width   | height  | packed  |
    | 'EINK'  | u32     | u32     | 1-bit   |

`packed` length = height * (width / 8). One frame per send. The receiver
(typically `tools/preview.py` on a developer workstation) decodes and shows
the frame in a window.
"""

from __future__ import annotations

import socket
import struct
from typing import Protocol

MAGIC = b"EINK"


class FrameSink(Protocol):
    def send(self, packed: bytes, width: int, height: int) -> None: ...
    def close(self) -> None: ...


class SocketSink:
    """Dev-mode sink that writes length-prefixed frames to a Unix socket."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._sock: socket.socket | None = None

    def _connect(self) -> socket.socket:
        if self._sock is None:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self._path)
            self._sock = s
        return self._sock

    def send(self, packed: bytes, width: int, height: int) -> None:
        sock = self._connect()
        header = MAGIC + struct.pack("<II", width, height)
        sock.sendall(header + packed)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


class SpiSink:
    """Prod sink that pushes packed frames to the GDEM0397T81P via the C driver."""

    def __init__(self) -> None:
        from spi_driver import open_panel  # local import to avoid hard dep in dev

        self._panel = open_panel()
        self._panel.init()
        self._frame_count = 0
        self._partial_every = 1
        self._full_every = 30  # full refresh every N frames to clear ghosting

    def send(self, packed: bytes, width: int, height: int) -> None:
        self._frame_count += 1
        if self._frame_count % self._full_every == 0:
            self._panel.full_refresh(packed)
        else:
            self._panel.partial_refresh(packed)

    def close(self) -> None:
        self._panel.sleep()


def make_sink(backend: str, socket_path: str) -> FrameSink:
    if backend == "socket":
        return SocketSink(socket_path)
    if backend == "spi":
        return SpiSink()
    raise ValueError(f"unknown backend: {backend!r} (expected 'spi' or 'socket')")
