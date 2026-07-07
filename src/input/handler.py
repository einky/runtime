"""Wire GPIO buttons to keypress injection."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from input.gpio_reader import GpioButtonReader
from input.keymap import BUTTON_MAP, DEBOUNCE_SECONDS, ButtonBinding

log = logging.getLogger(__name__)


KeyInjector = Callable[[str], None]


def xdotool_inject(key: str) -> None:
    """Inject a key into the focused X window via xdotool."""
    subprocess.run(
        ["xdotool", "key", "--clearmodifiers", key],
        check=False,
        env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
    )


@dataclass
class InputHandler:
    bindings: Iterable[ButtonBinding] = BUTTON_MAP
    inject: KeyInjector = xdotool_inject
    bounce_time: float = DEBOUNCE_SECONDS

    def __post_init__(self) -> None:
        self._reader = GpioButtonReader(
            on_press=self._on_press,
            bindings=self.bindings,
            debounce=self.bounce_time,
        )
        self._reader.start()

    def _on_press(self, binding: ButtonBinding) -> None:
        log.debug("press %s -> %s", binding.name, binding.key)
        self.inject(binding.key)

    def run_forever(self) -> None:
        from signal import pause

        pause()
