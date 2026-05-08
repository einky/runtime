"""Capture frames from an X display (typically Xvfb)."""

from __future__ import annotations

import os
import subprocess
from io import BytesIO

import numpy as np
from numpy.typing import NDArray
from PIL import Image


class XCapture:
    """Capture the root window of an X display via `xwd | convert`.

    We use the subprocess pipeline rather than python-xlib because Xvfb output
    is small (800x480) and the launch overhead is dwarfed by the SPI refresh.
    """

    def __init__(self, display: str | None = None) -> None:
        self.display = display or os.environ.get("DISPLAY", ":0")

    def grab(self) -> NDArray[np.uint8]:
        """Return the current root-window contents as an HxWx3 uint8 array."""
        env = {**os.environ, "DISPLAY": self.display}
        xwd = subprocess.run(
            ["xwd", "-root", "-silent"],
            check=True,
            capture_output=True,
            env=env,
        )
        png = subprocess.run(
            ["convert", "xwd:-", "png:-"],
            check=True,
            capture_output=True,
            input=xwd.stdout,
        )
        img = Image.open(BytesIO(png.stdout)).convert("RGB")
        return np.asarray(img, dtype=np.uint8)
