"""Unit tests for dithering and bit-packing."""

from __future__ import annotations

import numpy as np

from frame_processor.dither import floyd_steinberg, pack_1bit


def test_floyd_steinberg_pure_white_stays_white() -> None:
    grey = np.full((16, 16), 255, dtype=np.uint8)
    out = floyd_steinberg(grey)
    assert (out == 255).all()


def test_floyd_steinberg_pure_black_stays_black() -> None:
    grey = np.zeros((16, 16), dtype=np.uint8)
    out = floyd_steinberg(grey)
    assert (out == 0).all()


def test_floyd_steinberg_output_is_binary() -> None:
    rng = np.random.default_rng(0xE1)
    grey = rng.integers(0, 256, size=(32, 32), dtype=np.uint8)
    out = floyd_steinberg(grey)
    unique = np.unique(out)
    assert set(unique.tolist()) <= {0, 255}


def test_floyd_steinberg_mid_grey_density() -> None:
    """A 50% grey field should dither to roughly 50% white pixels."""
    grey = np.full((64, 64), 128, dtype=np.uint8)
    out = floyd_steinberg(grey)
    white_fraction = (out == 255).mean()
    assert 0.45 < white_fraction < 0.55


def test_pack_1bit_round_trip() -> None:
    binary = np.array(
        [[0, 255, 0, 255, 0, 255, 0, 255]],
        dtype=np.uint8,
    )
    assert pack_1bit(binary) == bytes([0b01010101])


def test_pack_1bit_full_white_row() -> None:
    binary = np.full((1, 800), 255, dtype=np.uint8)
    packed = pack_1bit(binary)
    assert len(packed) == 100
    assert packed == b"\xff" * 100


def test_pack_1bit_rejects_non_byte_aligned_width() -> None:
    binary = np.zeros((1, 7), dtype=np.uint8)
    try:
        pack_1bit(binary)
    except ValueError:
        return
    raise AssertionError("expected ValueError for width=7")
