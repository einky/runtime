"""GPIO button reader on the gpiochip character device (libgpiod v2).

Replaces the gpiozero/RPi.GPIO stack. RPi.GPIO implements edge detection via
the legacy ``/sys/class/gpio`` interface with raw global GPIO numbers; kernels
>= 6.6 give the Pi's gpiochip a dynamic global base (512+), so those exports
fail (``export_store: invalid GPIO 5``) — and every other gpiozero factory is
equally sysfs-/``/dev/gpiomem``-bound or unpackaged. The character-device API
does bias (pull-up), debounce, and edge detection in the kernel, and is the
same stack the C SPI driver uses for DC/RST/BUSY.

The 7 buttons are active-low with pull-ups (meta/shared/hardware.toml), so a
press is a FALLING edge and a release is a RISING edge; debounce runs
kernel-side with the contract's 30 ms.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from input.keymap import BUTTON_MAP, DEBOUNCE_SECONDS, ButtonBinding

log = logging.getLogger(__name__)

GPIOCHIP_DEFAULT = "/dev/gpiochip0"

# wait_edge_events wake-up cap: bounds stop() latency and the hold-deadline
# resolution. Edge events are buffered by the kernel, never lost to this.
_POLL_SECONDS = 0.2


def _chip_path() -> str:
    return os.environ.get("EINKY_GPIOCHIP") or GPIOCHIP_DEFAULT


@dataclass
class GpioButtonReader:
    """Owns the button lines and dispatches press (and optional hold) events.

    ``on_press`` fires on the press edge of every button. If ``hold_button``
    and ``on_hold`` are set, keeping that button down for ``hold_seconds``
    additionally fires ``on_hold`` once per press (the press callback still
    fires on the way down — same semantics as the gpiozero ``when_pressed`` /
    ``when_held`` pair this replaces).
    """

    on_press: Callable[[ButtonBinding], None]
    bindings: Iterable[ButtonBinding] = BUTTON_MAP
    on_hold: Callable[[ButtonBinding], None] | None = None
    hold_button: str | None = None
    hold_seconds: float = 2.0
    debounce: float = DEBOUNCE_SECONDS
    chip: str = field(default_factory=_chip_path)

    def start(self) -> None:
        # gpiod is a hard runtime dep but the import stays local so unit tests
        # can install a fake module instead of the real chardev bindings.
        import gpiod

        by_offset = {b.gpio: b for b in self.bindings}
        settings = gpiod.LineSettings(
            direction=gpiod.line.Direction.INPUT,
            bias=gpiod.line.Bias.PULL_UP,
            edge_detection=gpiod.line.Edge.BOTH,
            debounce_period=timedelta(seconds=self.debounce),
        )
        self._request: Any = gpiod.request_lines(
            self.chip,
            consumer="einky-buttons",
            config={tuple(by_offset.keys()): settings},
        )
        self._falling: Any = gpiod.EdgeEvent.Type.FALLING_EDGE
        self._by_offset = by_offset
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gpio-input", daemon=True)
        self._thread.start()
        for b in by_offset.values():
            log.info("bound GPIO %d -> %s (%s)", b.gpio, b.name, self.chip)

    def stop(self) -> None:
        stop_event = getattr(self, "_stop", None)
        if stop_event is None:
            return  # never started
        stop_event.set()
        self._thread.join(timeout=2 * _POLL_SECONDS + 1.0)
        self._request.release()

    def _run(self) -> None:
        hold: tuple[ButtonBinding, float] | None = None  # (binding, deadline)
        while not self._stop.is_set():
            timeout = _POLL_SECONDS
            if hold is not None:
                timeout = min(timeout, max(hold[1] - time.monotonic(), 0.0))
            try:
                ready = self._request.wait_edge_events(timeout)
            except OSError:
                if self._stop.is_set():
                    return  # request released under us during stop()
                raise
            if hold is not None and time.monotonic() >= hold[1]:
                held, _ = hold
                hold = None
                log.debug("hold %s", held.name)
                if self.on_hold is not None:
                    self.on_hold(held)
            if not ready:
                continue
            for event in self._request.read_edge_events():
                binding = self._by_offset.get(event.line_offset)
                if binding is None:
                    continue
                if event.event_type == self._falling:
                    log.debug("press %s", binding.name)
                    if binding.name == self.hold_button and self.on_hold is not None:
                        hold = (binding, time.monotonic() + self.hold_seconds)
                    self.on_press(binding)
                elif binding.name == self.hold_button:
                    hold = None  # released before the hold deadline
