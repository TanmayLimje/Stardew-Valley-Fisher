"""Mock actuator recording mouse actions for testing."""

from __future__ import annotations

import time
from typing import List, Tuple
from fisher.input.base import Actuator


class MockActuator(Actuator):
    """Records LMB presses and releases without dispatching OS mouse events."""

    def __init__(self):
        self._pressed = False
        self._history: List[Tuple[str, float]] = []
        self._last_action_time: float = 0.0

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

    @property
    def is_pressed(self) -> bool:
        return self._pressed

    @property
    def history(self) -> List[Tuple[str, float]]:
        return self._history

    @property
    def last_action_time(self) -> float:
        return self._last_action_time
