"""Abstract base class for mouse actuator."""

from __future__ import annotations

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
