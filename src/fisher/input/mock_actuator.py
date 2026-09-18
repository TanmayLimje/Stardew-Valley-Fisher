"""Mock actuator recording mouse and keyboard actions for testing."""

from __future__ import annotations

import time
from typing import List, Tuple
from fisher.input.base import Actuator


class MockActuator(Actuator):
    """Records LMB presses, key presses, and cursor moves without dispatching OS events."""

    def __init__(self):
        self._pressed = False
        self._history: List[Tuple[str, float]] = []
        self._last_action_time: float = 0.0
        self._held_keys: set[str] = set()

    def press_down(self) -> None:
        if not self._pressed:
            now = time.perf_counter()
            self._pressed = True
            self._last_action_time = now
            self._history.append(("DOWN", now))

    def release(self) -> None:
        if self._pressed:
            now = time.perf_counter()
            self._pressed = False
            self._last_action_time = now
            self._history.append(("UP", now))

    def emergency_release(self) -> None:
        self.release()
        self.release_all_keys()

    @property
    def is_pressed(self) -> bool:
        return self._pressed

    @property
    def history(self) -> List[Tuple[str, float]]:
        return self._history

    @property
    def last_action_time(self) -> float:
        return self._last_action_time

    # ------------------------------------------------------------------
    # Keyboard & cursor (waterer WASD navigation / tool aiming)
    # ------------------------------------------------------------------

    def key_down(self, key: str) -> None:
        now = time.perf_counter()
        self._held_keys.add(key)
        self._last_action_time = now
        self._history.append((f"KEY_DOWN:{key}", now))

    def key_up(self, key: str) -> None:
        now = time.perf_counter()
        self._held_keys.discard(key)
        self._last_action_time = now
        self._history.append((f"KEY_UP:{key}", now))

    def move_cursor(self, x: int, y: int) -> None:
        now = time.perf_counter()
        self._last_action_time = now
        self._history.append((f"MOVE_CURSOR:{x},{y}", now))

    def click(self, button: str = "left", duration: float = 0.05) -> None:
        """Simulate a timed mouse click."""
        now = time.perf_counter()
        self._last_action_time = now
        self._history.append((f"CLICK:{button}", now))

    def release_all_keys(self) -> None:
        now = time.perf_counter()
        for key in list(self._held_keys):
            self._history.append((f"KEY_UP:{key}", now))
        self._held_keys.clear()
