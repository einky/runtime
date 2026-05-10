"""Network input handler: receive button events from the ESP32 dev bridge.

When the runtime runs on WSL (see ADR 0006) there is no local GPIO. Instead,
the ESP32 reads the buttons and pushes newline-delimited button *names*
("up", "down", "a", ...) over a TCP connection. We look the name up in the
shared `keymap.BUTTON_MAP` and inject the corresponding X keysym, so the
keymap stays the single source of truth across both transports.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterable
from dataclasses import dataclass, field

from input.handler import KeyInjector, xdotool_inject
from input.keymap import BUTTON_MAP, ButtonBinding

log = logging.getLogger(__name__)


@dataclass
class NetInputHandler:
    bindings: Iterable[ButtonBinding] = BUTTON_MAP
    inject: KeyInjector = xdotool_inject
    host: str = "0.0.0.0"
    port: int = 5334
    _name_to_key: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self._name_to_key = {b.name: b.key for b in self.bindings}

    def serve_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen(1)
            log.info("input listener on %s:%d", self.host, self.port)
            while True:
                conn, peer = srv.accept()
                log.info("input client %s:%d connected", peer[0], peer[1])
                try:
                    self._handle(conn)
                except OSError as e:
                    log.warning("input client error: %s", e)
                finally:
                    conn.close()
                    log.info("input client disconnected; awaiting reconnect")

    def _handle(self, conn: socket.socket) -> None:
        buf = b""
        while True:
            chunk = conn.recv(256)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._dispatch(line.strip().decode("ascii", errors="replace"))

    def _dispatch(self, name: str) -> None:
        if not name:
            return
        key = self._name_to_key.get(name)
        if key is None:
            log.warning("unknown button %r (known: %s)", name, sorted(self._name_to_key))
            return
        log.debug("net press %s -> %s", name, key)
        self.inject(key)
