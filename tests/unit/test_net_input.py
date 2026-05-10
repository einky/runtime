"""Unit tests for the network input handler."""

from __future__ import annotations

import socket
import threading
import time

from input.keymap import BUTTON_MAP
from input.net_handler import NetInputHandler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_dispatches_known_button_names() -> None:
    injected: list[str] = []
    port = _free_port()
    handler = NetInputHandler(host="127.0.0.1", port=port, inject=injected.append)

    t = threading.Thread(target=handler.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)  # let the listener bind

    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as c:
        c.sendall(b"up\ndown\na\n")
        c.shutdown(socket.SHUT_WR)
        time.sleep(0.2)

    keys_by_name = {b.name: b.key for b in BUTTON_MAP}
    assert injected == [keys_by_name["up"], keys_by_name["down"], keys_by_name["a"]]


def test_ignores_unknown_button_names() -> None:
    injected: list[str] = []
    port = _free_port()
    handler = NetInputHandler(host="127.0.0.1", port=port, inject=injected.append)

    threading.Thread(target=handler.serve_forever, daemon=True).start()
    time.sleep(0.1)

    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as c:
        c.sendall(b"bogus\nstart\n\n")
        c.shutdown(socket.SHUT_WR)
        time.sleep(0.2)

    keys_by_name = {b.name: b.key for b in BUTTON_MAP}
    assert injected == [keys_by_name["start"]]
