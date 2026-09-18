"""Abstract base class for mouse actuator."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class Actuator(ABC):
    """Abstract interface for game mouse inputs."""

    @abstractmethod
    def press_down(self) -> None:
        """Press down the primary mouse button (LMB)."""
        pass

    @abstractmethod
    def release(self) -> None:
        """Release the primary mouse button (LMB)."""
        pass

    @abstractmethod
    def emergency_release(self) -> None:
        """Hard release of LMB to guarantee safe state."""
        pass

    @property
    @abstractmethod
    def is_pressed(self) -> bool:
        """True if LMB is currently held down."""
        pass

    def set_press(self, pressed: bool) -> None:
        """Idempotently set mouse press state."""
        if pressed:
            if not self.is_pressed:
                self.press_down()
        else:
            if self.is_pressed:
                self.release()

    def send_escape(self) -> None:
        """Optional hook to send ESC key to abort minigame cleanly."""
        pass

    def focus_game_window(self) -> bool:
        """Optional hook to bring game window to foreground."""
        return True

    def ensure_cursor_in_window(self) -> bool:
        """Optional hook to ensure mouse cursor is placed within game bounds."""
        return True

    # ------------------------------------------------------------------
    # Keyboard & cursor hooks (waterer WASD navigation / tool aiming)
    # ------------------------------------------------------------------

    def key_down(self, key: str) -> None:
        """Hold a keyboard key down. Default: no-op."""
        pass

    def key_up(self, key: str) -> None:
        """Release a keyboard key. Default: no-op."""
        pass

    def key_press(self, key: str, duration: float = 0.05) -> None:
        """Press and release a key with specified hold duration."""
        self.key_down(key)
        time.sleep(duration)
        self.key_up(key)

    def key_tap(self, key: str) -> None:
        """Instant key press and release (~20 ms hold)."""
        self.key_press(key, duration=0.02)

    def move_cursor(self, x: int, y: int) -> None:
        """Move the OS cursor to absolute screen coordinates. Default: no-op."""
        pass

    def click(self, button: str = "left", duration: float = 0.05) -> None:
        """Timed mouse click. Default: best-effort LMB via press/release hooks."""
        if button != "left":
            return
        self.set_press(True)
        time.sleep(duration)
        self.set_press(False)

    def release_all_keys(self) -> None:
        """Release all currently held keys (safety).

        MUST be thread-safe: called directly from the SafetySupervisor daemon
        thread so F9-to-release stays < 200 ms while the main loop sleeps.
        """
        pass
