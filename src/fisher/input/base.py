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
