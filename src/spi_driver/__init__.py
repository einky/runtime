"""Python wrapper around the GDEM0397T81P SPI driver.

The C extension is built lazily — `open_panel()` raises a clear error if the
binary hasn't been compiled yet, rather than at import time.
"""

from __future__ import annotations

import os
from typing import Protocol


class Panel(Protocol):
    def init(self) -> None: ...
    def full_refresh(self, frame: bytes) -> None: ...
    def partial_refresh(self, frame: bytes) -> None: ...
    def sleep(self) -> None: ...


class _CFFIPanel:
    def __init__(self, spi_dev: str) -> None:
        try:
            from _spi_driver import ffi, lib  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "spi_driver C extension not built. Run `make build-c` first."
            ) from e
        self._ffi = ffi
        self._lib = lib
        handle = lib.einky_open(spi_dev.encode("utf-8"))
        if handle == ffi.NULL:
            raise OSError(f"einky_open({spi_dev!r}) failed")
        self._handle = handle

    def init(self) -> None:
        if self._lib.einky_init(self._handle) != 0:
            raise OSError("einky_init failed")

    def full_refresh(self, frame: bytes) -> None:
        if self._lib.einky_full_refresh(self._handle, frame, len(frame)) != 0:
            raise OSError("einky_full_refresh failed")

    def partial_refresh(self, frame: bytes) -> None:
        if self._lib.einky_partial_refresh(self._handle, frame, len(frame)) != 0:
            raise OSError("einky_partial_refresh failed")

    def sleep(self) -> None:
        if self._lib.einky_sleep(self._handle) != 0:
            raise OSError("einky_sleep failed")

    def __del__(self) -> None:
        if getattr(self, "_handle", None) is not None:
            self._lib.einky_close(self._handle)
            self._handle = None


def open_panel(spi_dev: str | None = None) -> Panel:
    dev = spi_dev or os.environ.get("EINKY_SPI_DEV", "/dev/spidev0.0")
    return _CFFIPanel(dev)


__all__ = ["Panel", "open_panel"]
