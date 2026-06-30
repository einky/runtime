"""Integration test: PNG over the engine-capture socket -> packed frame to the sink.

Exercises the [protocol.engine_capture] framing (u32 big-endian length + PNG) and
confirms the decoded image flows through the SAME resize -> dither -> pack path,
emitting a standard 48000-byte panel frame.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from frame_processor.constants import PANEL_HEIGHT, PANEL_WIDTH
from frame_processor.eink_receiver import EinkReceiver


class _RecordingSink:
    def __init__(self) -> None:
        self.frames: list[tuple[bytes, int, int]] = []
        self.closed = False

    def send(self, packed: bytes, width: int, height: int) -> None:
        self.frames.append((packed, width, height))

    def close(self) -> None:
        self.closed = True


def _png_bytes(width: int, height: int) -> bytes:
    rng = np.random.default_rng(0x5A)
    arr = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    buf = BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def test_receiver_decodes_png_into_pipeline(tmp_path: Path) -> None:
    path = str(tmp_path / "eink.sock")
    sink = _RecordingSink()
    receiver = EinkReceiver(sink=sink, socket_path=path)

    t = threading.Thread(target=receiver.serve_forever, daemon=True)
    t.start()

    deadline = time.monotonic() + 3.0
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.02)

    # Deliberately off panel-size so the resize path is exercised too.
    png = _png_bytes(320, 200)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.connect(path)
        c.sendall(struct.pack(">I", len(png)) + png)
        c.shutdown(socket.SHUT_WR)

    # The pure-Python Floyd-Steinberg dither over 800x480 takes ~1-2s; poll.
    deadline = time.monotonic() + 30.0
    while not sink.frames and time.monotonic() < deadline:
        time.sleep(0.05)

    assert sink.frames, "receiver did not emit a frame"
    packed, width, height = sink.frames[0]
    assert (width, height) == (PANEL_WIDTH, PANEL_HEIGHT)
    assert len(packed) == PANEL_WIDTH // 8 * PANEL_HEIGHT
