"""Tile-by-tile WASD navigator for the waterer.

Moves the player one tile at a time using WASD key presses dispatched through
the :class:`~fisher.input.base.Actuator` interface. Movement only — tool aim
is the assistant's job via ``actuator.move_cursor()`` (cursor targeting).

All waits use :func:`fisher.utils.timing.interruptible_sleep` so the F9
killswitch meets its < 200 ms gate even mid-WASD-hold.
"""

from __future__ import annotations

import threading

from fisher.input.base import Actuator
from fisher.extraction.tiles import TileCoord
from fisher.utils.timing import interruptible_sleep


# Direction vector → WASD key mapping.
DIRECTION_KEYS: dict[tuple[int, int], str] = {
    (0, 1): "d",   # east  (right)
    (0, -1): "a",  # west  (left)
    (1, 0): "s",   # south (down)
    (-1, 0): "w",  # north (up)
}

# Facing taps drift the farmer ~0.23 tile per 50 ms; kept short by design.
_FACE_TAP_S = 0.05
_SETTLE_S = 0.05


def direction_key(dr: int, dc: int) -> str:
    """Return the WASD key that faces the dominant axis of ``(dr, dc)``.

    Args:
        dr: Row delta (positive = south).
        dc: Column delta (positive = east).

    Returns:
        One of ``w``, ``a``, ``s``, ``d``.
    """
    if abs(dr) >= abs(dc) and dr != 0:
        return "s" if dr > 0 else "w"
    if dc != 0:
        return "d" if dc > 0 else "a"
    return "s"


class TileNavigator:
    """Move the player tile-by-tile using WASD key presses.

    Tracks the player's relative grid position and facing direction. All
    sleeps are abort-checked so the F9 killswitch stays responsive.
    """

    def __init__(
        self,
        actuator: Actuator,
        abort_event: threading.Event,
        tile_walk_ms: int = 250,
    ) -> None:
        self.actuator = actuator
        self.abort_event = abort_event
        self.tile_walk_s = tile_walk_ms / 1000.0
        self.current_pos = TileCoord(0, 0)
        self.facing: str = "s"

    def move_one_tile(self, direction: tuple[int, int]) -> bool:
        """Hold WASD for one tile duration.

        Args:
            direction: ``(dr, dc)`` unit vector — e.g. ``(0, 1)`` = east.

        Returns:
            True if the movement completed, False if aborted.
        """
        key = DIRECTION_KEYS.get(direction)
        if key is None:
            return True  # Zero vector — no movement requested.

        if self.abort_event.is_set():
            return False

        self.actuator.key_down(key)
        try:
            completed = interruptible_sleep(self.tile_walk_s, self.abort_event)
        finally:
            # Always release the key — even when aborted mid-hold.
            self.actuator.key_up(key)

        if not completed:
            return False

        self.facing = key
        dr, dc = direction
        self.current_pos = TileCoord(self.current_pos.row + dr, self.current_pos.col + dc)

        # Brief settle after movement (abort-checked so F9 stays responsive).
        return interruptible_sleep(_SETTLE_S, self.abort_event)

    def navigate_to(self, target: TileCoord) -> bool:
        """Navigate from the current position to ``target``.

        Moves one axis at a time (horizontal first).

        Args:
            target: Destination tile coordinate.

        Returns:
            True if the target was reached, False if aborted.
        """
        while self.current_pos != target:
            if self.abort_event.is_set():
                return False

            dr = target.row - self.current_pos.row
            dc = target.col - self.current_pos.col
            if dc != 0:
                step = (0, 1 if dc > 0 else -1)
            else:
                step = (1 if dr > 0 else -1, 0)

            if not self.move_one_tile(step):
                return False

        return True

    def face_direction(self, key: str) -> None:
        """Face a direction without moving (brief tap).

        FALLBACK ONLY — used when ``cursor_aim`` is disabled. Each 50 ms tap
        drifts the farmer ~15 px (~0.23 tile); prefer cursor aiming.
        """
        if self.facing == key:
            return
        self.actuator.key_down(key)
        interruptible_sleep(_FACE_TAP_S, self.abort_event)
        self.actuator.key_up(key)
        self.facing = key

    def reset_position(self) -> None:
        """Reset tracked position to origin (e.g. after a re-scan)."""
        self.current_pos = TileCoord(0, 0)
