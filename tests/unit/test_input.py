"""Unit tests for the input handler with a mocked gpiozero.Button."""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock


class _FakeButton:
    """Minimal gpiozero.Button stand-in. Records callbacks; can fire them."""

    instances: list["_FakeButton"] = []

    def __init__(self, gpio: int, pull_up: bool = True, bounce_time: float | None = None) -> None:
        self.gpio = gpio
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.when_pressed: Callable[[], None] | None = None
        _FakeButton.instances.append(self)


def _install_fake_gpiozero() -> None:
    fake = types.ModuleType("gpiozero")
    fake.Button = _FakeButton  # type: ignore[attr-defined]
    sys.modules["gpiozero"] = fake


def test_handler_binds_every_button(monkeypatch: Any) -> None:
    _FakeButton.instances.clear()
    _install_fake_gpiozero()

    from input.handler import InputHandler
    from input.keymap import BUTTON_MAP

    injected: list[str] = []
    handler = InputHandler(inject=injected.append)

    assert len(_FakeButton.instances) == len(BUTTON_MAP)
    bound_pins = {b.gpio for b in _FakeButton.instances}
    expected_pins = {b.gpio for b in BUTTON_MAP}
    assert bound_pins == expected_pins
    # silence "unused" -- handler is the SUT
    assert handler is not None


def test_press_dispatches_correct_key() -> None:
    _FakeButton.instances.clear()
    _install_fake_gpiozero()

    from input.handler import InputHandler
    from input.keymap import BUTTON_MAP

    injected: list[str] = []
    InputHandler(inject=injected.append)

    pin_to_key = {b.gpio: b.key for b in BUTTON_MAP}
    for fake in _FakeButton.instances:
        assert fake.when_pressed is not None
        fake.when_pressed()

    assert sorted(injected) == sorted(pin_to_key.values())


def test_xdotool_inject_invokes_subprocess(monkeypatch: Any) -> None:
    from input import handler as handler_mod

    fake_run = MagicMock()
    monkeypatch.setattr(handler_mod.subprocess, "run", fake_run)
    handler_mod.xdotool_inject("space")

    fake_run.assert_called_once()
    args = fake_run.call_args.args[0]
    assert args[0] == "xdotool"
    assert "space" in args
