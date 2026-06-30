"""Integration test for the TCP frame sink (ESP32 dev bridge transport).

Boots the sink on an ephemeral port, connects a fake client, and verifies one
frame arrives with the expected EINK header and payload length. Drop+reconnect
is also exercised so the sink does not crash if the ESP32 disappears mid-demo.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

from frame_processor.constants import MAGIC, PANEL_HEIGHT, PANEL_WIDTH
from frame_processor.dispatch import TcpFrameSink


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    remaining = n
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError("peer closed before all bytes arrived")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def test_tcp_sink_serves_one_frame() -> None:
    sink = TcpFrameSink("127.0.0.1", 0)
    host, port = sink._server.getsockname()

    payload = b"\x00" * (PANEL_WIDTH // 8 * PANEL_HEIGHT)
    received: list[bytes] = []

    def client() -> None:
        with socket.create_connection((host, port), timeout=2.0) as c:
            received.append(_recv_exact(c, 12 + len(payload)))

    t = threading.Thread(target=client)
    t.start()

    # The server's accept() polls every 0.5s; loop send() until the client
    # has been picked up and the frame goes through.
    for _ in range(20):
        sink.send(payload, PANEL_WIDTH, PANEL_HEIGHT)
        if received:
            break
        time.sleep(0.1)
    t.join(timeout=3)
    sink.close()

    assert received, "client did not receive a frame"
    blob = received[0]
    assert blob[:4] == MAGIC
    assert struct.unpack("<II", blob[4:12]) == (PANEL_WIDTH, PANEL_HEIGHT)
    assert blob[12:] == payload


def test_tcp_sink_survives_client_drop() -> None:
    sink = TcpFrameSink("127.0.0.1", 0)
    host, port = sink._server.getsockname()
    payload = b"\x00" * (PANEL_WIDTH // 8 * PANEL_HEIGHT)

    # First client connects, receives nothing, drops.
    c1 = socket.create_connection((host, port), timeout=2.0)
    for _ in range(10):
        sink.send(payload, PANEL_WIDTH, PANEL_HEIGHT)
        if sink._client is not None:
            break
        time.sleep(0.1)
    c1.close()
    # Force the sink to notice the drop on the next send.
    for _ in range(3):
        sink.send(payload, PANEL_WIDTH, PANEL_HEIGHT)

    # Second client should be accepted cleanly.
    received: list[bytes] = []

    def client2() -> None:
        with socket.create_connection((host, port), timeout=2.0) as c:
            received.append(_recv_exact(c, 12 + len(payload)))

    t = threading.Thread(target=client2)
    t.start()
    for _ in range(20):
        sink.send(payload, PANEL_WIDTH, PANEL_HEIGHT)
        if received:
            break
        time.sleep(0.1)
    t.join(timeout=3)
    sink.close()

    assert received, "second client did not receive a frame after drop"
