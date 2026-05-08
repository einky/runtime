"""Wire GPIO buttons to keypress injection."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

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
        # gpiozero is a hard runtime dep but we keep the import local so unit
        # tests can mock it without needing the lgpio shared library.
        from gpiozero import Button  # type: ignore[import-untyped]

        self._buttons: list[Button] = []
        for b in self.bindings:
            btn = Button(b.gpio, pull_up=True, bounce_time=self.bounce_time)
            btn.when_pressed = self._make_callback(b)
            self._buttons.append(btn)
            log.info("bound GPIO %d (%s) -> %s", b.gpio, b.name, b.key)

    def _make_callback(self, binding: ButtonBinding) -> Callable[[], None]:
        def _cb() -> None:
            log.debug("press %s -> %s", binding.name, binding.key)
            self.inject(binding.key)

        return _cb

    def run_forever(self) -> None:
        from signal import pause

        pause()
