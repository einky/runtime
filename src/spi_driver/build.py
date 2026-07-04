"""CFFI builder for the GDEM0397T81P SPI driver.

Run via `make build-c` (which invokes `python -m src.spi_driver.build` after
`pip install -e .[dev]`). The resulting `_spi_driver.*.so` is imported by
`spi_driver/__init__.py`.
"""

from __future__ import annotations

import pathlib

from cffi import FFI

HERE = pathlib.Path(__file__).resolve().parent

ffi = FFI()

ffi.cdef(
    """
    typedef struct einky_panel einky_panel_t;

    einky_panel_t *einky_open(const char *spi_dev);
    int  einky_init(einky_panel_t *p);
    int  einky_full_refresh(einky_panel_t *p, const uint8_t *frame, size_t len);
    int  einky_partial_refresh(einky_panel_t *p, const uint8_t *frame, size_t len);
    int  einky_sleep(einky_panel_t *p);
    void einky_close(einky_panel_t *p);
    """
)

ffi.set_source(
    "_spi_driver",
    '#include "spi_driver.h"',
    sources=[str(HERE / "spi_driver.c")],
    include_dirs=[str(HERE)],
    libraries=["gpiod"],  # DC/RST/BUSY control lines via libgpiod (v1 line API)
    extra_compile_args=["-std=c11", "-Wall", "-Wextra", "-O2"],
)


if __name__ == "__main__":
    ffi.compile(tmpdir=str(HERE / "build"), verbose=True)
