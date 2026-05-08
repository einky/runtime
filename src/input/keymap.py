"""GPIO -> keypress map.

Pin numbers MUST stay in sync with case/docs/wiring.md. If you change them
here, update that doc in the same PR.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ButtonBinding:
    name: str
    gpio: int  # BCM pin number
    key: str   # X keysym (xdotool name) or evdev keycode name


# Layout: 4-way D-pad + 3 action buttons.
#   D-pad: arrow keys
#   A     -> Space   (advance dialogue)
#   B     -> Escape  (back / menu)
#   Start -> Return  (confirm / open menu)
BUTTON_MAP: tuple[ButtonBinding, ...] = (
    ButtonBinding("up",    gpio=5,  key="Up"),
    ButtonBinding("down",  gpio=6,  key="Down"),
    ButtonBinding("left",  gpio=13, key="Left"),
    ButtonBinding("right", gpio=19, key="Right"),
    ButtonBinding("a",     gpio=16, key="space"),
    ButtonBinding("b",     gpio=20, key="Escape"),
    ButtonBinding("start", gpio=21, key="Return"),
)

# Software debounce window. gpiozero's bounce_time is the suppression interval
# after a transition; 30ms is comfortable for tactile dome switches.
DEBOUNCE_SECONDS = 0.03
