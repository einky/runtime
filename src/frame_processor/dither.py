"""Floyd-Steinberg dithering, 8-bit greyscale -> 1-bit packed."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def floyd_steinberg(grey: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Floyd-Steinberg dither an HxW uint8 greyscale array to HxW uint8 in {0, 255}.

    The output is still byte-per-pixel for ease of testing; pack with `pack_1bit`
    before sending to the panel.
    """
    if grey.ndim != 2:
        raise ValueError(f"expected 2-D greyscale, got shape {grey.shape}")

    work = grey.astype(np.float32, copy=True)
    h, w = work.shape

    for y in range(h):
        for x in range(w):
            old = work[y, x]
            new = 255.0 if old >= 128.0 else 0.0
            work[y, x] = new
            err = old - new
            if x + 1 < w:
                work[y, x + 1] += err * 7 / 16
            if y + 1 < h:
                if x > 0:
                    work[y + 1, x - 1] += err * 3 / 16
                work[y + 1, x] += err * 5 / 16
                if x + 1 < w:
                    work[y + 1, x + 1] += err * 1 / 16

    return work.astype(np.uint8)


def pack_1bit(binary: NDArray[np.uint8]) -> bytes:
    """Pack an HxW uint8 array (each value 0 or 255) into MSB-first 1-bit bytes.

    The panel expects rows of `ceil(W/8)` bytes; W must be a multiple of 8.
    """
    if binary.ndim != 2:
        raise ValueError(f"expected 2-D array, got shape {binary.shape}")
    h, w = binary.shape
    if w % 8 != 0:
        raise ValueError(f"width {w} not a multiple of 8")

    bits = (binary >= 128).astype(np.uint8)
    packed = np.packbits(bits, axis=1, bitorder="big")
    return packed.tobytes()
