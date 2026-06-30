"""Input SENDER: button names -> ascii-lines over the in-engine input socket.

The mirror of ``net_handler``: where ``NetInputHandler`` *receives* button names
over TCP (from the ESP32 bridge) and injects keysyms, ``NetInputSender``
*produces* button names over the engine-capture Unix socket
(``[protocol.engine_capture]`` ``input_socket``) for buildroot_os's in-engine
``input_hook.rpy``, which maps each name to its ``renpy_events``.

The wire format is the shared ``ascii-lines`` encoding — one button ``name`` per
line, LF-terminated — so the button-name table stays the single source of truth
across every transport (GPIO, TCP-from-ESP32, and this in-engine socket).
"""

from __future__ import annotations

import logging
import socket
import sys
from collections.abc import Iterable

# INPUT_SOCKET is the shared [protocol.engine_capture] path (contract-derived).
from frame_processor.constants import INPUT_SOCKET
from input.keymap import BUTTON_MAP

log = logging.getLogger(__name__)

_KNOWN_NAMES = frozenset(b.name for b in BUTTON_MAP)


class NetInputSender:
    """Write button names as ascii-lines to a Unix socket, reconnecting as needed."""

    def __init__(self, socket_path: str = INPUT_SOCKET) -> None:
        self._path = socket_path
        self._sock: socket.socket | None = None

    def _connect(self) -> socket.socket:
        if self._sock is None:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self._path)
            self._sock = s
        return self._sock

    def send(self, name: str) -> None:
        """Send one button name. Unknown names are dropped to keep the table canonical."""
        if name not in _KNOWN_NAMES:
            log.warning("unknown button %r (known: %s)", name, sorted(_KNOWN_NAMES))
            return
        sock = self._connect()
        sock.sendall((name + "\n").encode("ascii"))
        log.debug("sent %s", name)

    def run(self, source: Iterable[str]) -> None:
        """Forward every button name produced by ``source`` until it is exhausted."""
        try:
            for name in source:
                self.send(name.strip())
        finally:
            self.close()

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


def main() -> int:
    """Forward button names read from stdin (one per line) to the input socket.

    A small manual-test harness; the device/in-engine wiring supplies its own
    source via :class:`NetInputSender`.
    """
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    NetInputSender().run(line for line in sys.stdin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
