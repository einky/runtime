"""Unit tests for the input sender (button names -> ascii-lines over a Unix socket)."""

from __future__ import annotations

import socket
import threading
from pathlib import Path

from input.keymap import BUTTON_MAP
from input.net_sender import NetInputSender


def _serve_once(server: socket.socket, out: list[bytes]) -> None:
    conn, _ = server.accept()
    chunks: list[bytes] = []
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    conn.close()
    out.append(b"".join(chunks))


def _bound_server(path: str) -> socket.socket:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    return server


def test_sends_known_names_as_ascii_lines(tmp_path: Path) -> None:
    path = str(tmp_path / "input.sock")
    server = _bound_server(path)
    received: list[bytes] = []
    t = threading.Thread(target=_serve_once, args=(server, received), daemon=True)
    t.start()

    # run() forwards each name then closes the connection, giving the server EOF.
    NetInputSender(socket_path=path).run(["up", "a", "start"])

    t.join(timeout=3)
    server.close()
    assert received == [b"up\na\nstart\n"]


def test_drops_unknown_names(tmp_path: Path) -> None:
    path = str(tmp_path / "input.sock")
    server = _bound_server(path)
    received: list[bytes] = []
    t = threading.Thread(target=_serve_once, args=(server, received), daemon=True)
    t.start()

    NetInputSender(socket_path=path).run(["up", "bogus", "down"])

    t.join(timeout=3)
    server.close()
    assert received == [b"up\ndown\n"]
    names = {b.name for b in BUTTON_MAP}
    assert {"up", "down"} <= names and "bogus" not in names
