"""Top-level frame processor: glue capture -> resize -> dither -> dispatch."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from frame_processor.capture import XCapture
from frame_processor.constants import PANEL_HEIGHT, PANEL_WIDTH
from frame_processor.dispatch import FrameSink
from frame_processor.dither import floyd_steinberg, pack_1bit


def to_panel_grey(rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Resize an RGB frame to the panel and convert it to 8-bit greyscale.

    Shared by the X-capture pipeline and the in-engine PNG receiver so both feed
    the single Floyd-Steinberg dither with identically-prepared input.
    """
    img = Image.fromarray(rgb, mode="RGB")
    if img.size != (PANEL_WIDTH, PANEL_HEIGHT):
        img = img.resize((PANEL_WIDTH, PANEL_HEIGHT), Image.Resampling.LANCZOS)
    return np.asarray(img.convert("L"), dtype=np.uint8)


@dataclass
class FrameProcessor:
    capture: XCapture
    sink: FrameSink
    target_fps: float = 2.0  # e-paper isn't fast; keep CPU low

    def process_one(self) -> bytes:
        rgb = self.capture.grab()
        grey = to_panel_grey(rgb)
        dithered = floyd_steinberg(grey)
        packed = pack_1bit(dithered)
        self.sink.send(packed, PANEL_WIDTH, PANEL_HEIGHT)
        return packed

    def run(self) -> None:
        period = 1.0 / self.target_fps
        try:
            while True:
                start = time.monotonic()
                self.process_one()
                elapsed = time.monotonic() - start
                if elapsed < period:
                    time.sleep(period - elapsed)
        finally:
            self.sink.close()
