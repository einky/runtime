"""Unit tests for the GPIO input path with a mocked gpiod (libgpiod v2) module."""

from __future__ import annotations

import enum
import queue
import sys
import time
import types
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


class _EdgeType(enum.Enum):
    RISING_EDGE = 1
    FALLING_EDGE = 2


class _FakeRequest:
    """Stand-in for gpiod's LineRequest: tests feed edge events into a queue."""

    def __init__(self) -> None:
        self._q: queue.Queue[Any] = queue.Queue()
        self._buf: list[Any] = []
        self.released = False

    def feed(self, offset: int, edge: _EdgeType) -> None:
        self._q.put(SimpleNamespace(line_offset=offset, event_type=edge))

    def wait_edge_events(self, timeout: float | None = None) -> bool:
        if self.released:
            raise OSError("request released")
        try:
            self._buf.append(self._q.get(timeout=timeout))
            return True
        except queue.Empty:
            return False

    def read_edge_events(self) -> list[Any]:
        out, self._buf = self._buf, []
        return out

    def release(self) -> None:
        self.released = True


def _install_fake_gpiod() -> tuple[_FakeRequest, dict[str, Any]]:
    """Install a minimal fake `gpiod` module; return its request + call record."""
    request = _FakeRequest()
    calls: dict[str, Any] = {}

    def request_lines(path: str, config: dict[Any, Any], consumer: str | None = None) -> Any:
        calls["path"] = path
        calls["config"] = config
        calls["consumer"] = consumer
        return request

    fake = types.ModuleType("gpiod")
    fake.LineSettings = lambda **kw: dict(kw)  # type: ignore[attr-defined]
    fake.request_lines = request_lines  # type: ignore[attr-defined]
    fake.line = SimpleNamespace(  # type: ignore[attr-defined]
        Direction=SimpleNamespace(INPUT="input"),
        Bias=SimpleNamespace(PULL_UP="pull-up"),
        Edge=SimpleNamespace(BOTH="both"),
    )
    fake.EdgeEvent = SimpleNamespace(Type=_EdgeType)  # type: ignore[attr-defined]
    sys.modules["gpiod"] = fake
    return request, calls


def _wait_for(cond: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_reader_requests_every_button_with_contract_settings() -> None:
    request, calls = _install_fake_gpiod()

    from input.gpio_reader import GpioButtonReader
    from input.keymap import BUTTON_MAP, DEBOUNCE_SECONDS

    reader = GpioButtonReader(on_press=lambda b: None)
    reader.start()
    try:
        (offsets,) = calls["config"].keys()
        assert sorted(offsets) == sorted(b.gpio for b in BUTTON_MAP)
        settings = calls["config"][offsets]
        assert settings["bias"] == "pull-up"
        assert settings["edge_detection"] == "both"
        assert settings["debounce_period"] == timedelta(seconds=DEBOUNCE_SECONDS)
        assert calls["consumer"] == "einky-buttons"
    finally:
        reader.stop()
    assert request.released


def test_press_dispatches_correct_key_via_handler() -> None:
    request, _ = _install_fake_gpiod()

    from input.handler import InputHandler
    from input.keymap import BUTTON_MAP

    injected: list[str] = []
    handler = InputHandler(inject=injected.append)
    try:
        for b in BUTTON_MAP:
            request.feed(b.gpio, _EdgeType.FALLING_EDGE)
        assert _wait_for(lambda: len(injected) == len(BUTTON_MAP))
        assert sorted(injected) == sorted(b.key for b in BUTTON_MAP)
    finally:
        handler._reader.stop()


def test_release_edge_is_not_a_press() -> None:
    request, _ = _install_fake_gpiod()

    from input.gpio_reader import GpioButtonReader
    from input.keymap import BUTTON_MAP

    presses: list[str] = []
    reader = GpioButtonReader(on_press=lambda b: presses.append(b.name))
    reader.start()
    try:
        request.feed(BUTTON_MAP[0].gpio, _EdgeType.RISING_EDGE)
        request.feed(BUTTON_MAP[1].gpio, _EdgeType.FALLING_EDGE)
        assert _wait_for(lambda: presses == [BUTTON_MAP[1].name])
        time.sleep(0.05)  # give a spurious rising-edge press a chance to appear
        assert presses == [BUTTON_MAP[1].name]
    finally:
        reader.stop()


def test_hold_fires_once_after_deadline() -> None:
    request, _ = _install_fake_gpiod()

    from input.gpio_reader import GpioButtonReader
    from input.keymap import BUTTON_MAP

    start = next(b for b in BUTTON_MAP if b.name == "start")
    presses: list[str] = []
    holds: list[str] = []
    reader = GpioButtonReader(
        on_press=lambda b: presses.append(b.name),
        on_hold=lambda b: holds.append(b.name),
        hold_button="start",
        hold_seconds=0.05,
    )
    reader.start()
    try:
        request.feed(start.gpio, _EdgeType.FALLING_EDGE)
        assert _wait_for(lambda: presses == ["start"])  # press fires on the way down
        assert _wait_for(lambda: holds == ["start"])  # hold fires after the deadline
        time.sleep(0.3)
        assert holds == ["start"]  # and only once per press
    finally:
        reader.stop()


def test_release_before_deadline_cancels_hold() -> None:
    request, _ = _install_fake_gpiod()

    from input.gpio_reader import GpioButtonReader
    from input.keymap import BUTTON_MAP

    start = next(b for b in BUTTON_MAP if b.name == "start")
    holds: list[str] = []
    reader = GpioButtonReader(
        on_press=lambda b: None,
        on_hold=lambda b: holds.append(b.name),
        hold_button="start",
        hold_seconds=0.3,
    )
    reader.start()
    try:
        request.feed(start.gpio, _EdgeType.FALLING_EDGE)
        request.feed(start.gpio, _EdgeType.RISING_EDGE)
        time.sleep(0.5)
        assert holds == []
    finally:
        reader.stop()


def test_xdotool_inject_invokes_subprocess(monkeypatch: Any) -> None:
    from input import handler as handler_mod

    fake_run = MagicMock()
    monkeypatch.setattr(handler_mod.subprocess, "run", fake_run)
    handler_mod.xdotool_inject("space")

    fake_run.assert_called_once()
    args = fake_run.call_args.args[0]
    assert args[0] == "xdotool"
    assert "space" in args
