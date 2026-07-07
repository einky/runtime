"""Frame dispatch: SPI driver in prod, Unix-socket / TCP preview in dev.

Dev-mode wire protocol (length-prefixed, little-endian, identical for both
`socket` and `tcp` backends):

    | 4 bytes | 4 bytes | 4 bytes | N bytes |
    | magic   | width   | height  | packed  |
    | 'EINK'  | u32     | u32     | 1-bit   |

`packed` length = height * (width / 8). One frame per send. The Unix-socket
receiver is `tools/preview.py` on the workstation; the TCP receiver is the
ESP32 dev bridge (see ADR 0006) which forwards frames to a real e-paper.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
from contextlib import suppress
from typing import Protocol

from frame_processor.constants import MAGIC

log = logging.getLogger(__name__)


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


class TcpFrameSink:
    """Dev-mode sink that streams frames to a TCP client (host preview / bridge).

    We bind+listen on `host:port`, accept one client at a time (in a background
    thread), and re-accept on disconnect. A newly connected client is immediately
    sent the *last* produced frame, so a preview that attaches after boot shows
    the current screen right away instead of a blank window until the next redraw
    (the launcher is event-driven and may sit idle for a long time). Frames
    produced while no client is connected are still dropped.
    """

    _ACCEPT_POLL_SECONDS = 0.5

    def __init__(self, host: str, port: int) -> None:
        self._addr = (host, port)
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(self._addr)
        self._server.listen(1)
        self._server.settimeout(self._ACCEPT_POLL_SECONDS)
        self._lock = threading.Lock()
        self._client: socket.socket | None = None
        self._last: bytes | None = None  # last full frame (header + packed)
        self._stop = threading.Event()
        self._acceptor = threading.Thread(
            target=self._accept_loop, name="frame-accept", daemon=True
        )
        self._acceptor.start()
        log.info("TCP frame sink listening on %s:%d", host, port)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, peer = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log.info("frame client connected from %s:%d", peer[0], peer[1])
            with self._lock:
                if self._client is not None:  # one client at a time; drop the old one
                    with suppress(OSError):
                        self._client.close()
                self._client = conn
                last = self._last
            if last is not None:  # replay current screen to the fresh client
                self._send_to(conn, last)

    def _send_to(self, client: socket.socket, framed: bytes) -> None:
        try:
            client.sendall(framed)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError) as e:
            log.warning("frame client dropped (%s); will re-accept", e)
            with self._lock:
                if self._client is client:
                    self._client = None
            with suppress(OSError):
                client.close()

    def send(self, packed: bytes, width: int, height: int) -> None:
        framed = MAGIC + struct.pack("<II", width, height) + packed
        with self._lock:
            self._last = framed
            client = self._client
        if client is not None:
            self._send_to(client, framed)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            with suppress(OSError):
                client.close()
        self._server.close()


def make_sink(backend: str, socket_path: str, tcp_host: str, tcp_port: int) -> FrameSink:
    if backend == "socket":
        return SocketSink(socket_path)
    if backend == "tcp":
        return TcpFrameSink(tcp_host, tcp_port)
    if backend == "spi":
        return SpiSink()
    raise ValueError(f"unknown backend: {backend!r} (expected 'spi', 'socket', or 'tcp')")
