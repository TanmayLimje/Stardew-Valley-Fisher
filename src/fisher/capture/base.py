"""Abstract base class for screen capture drivers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Tuple
import numpy as np


class CaptureDriver(ABC):
    """Abstract interface for screen capture."""

    @abstractmethod
    def start(self) -> None:
        """Start the capture worker thread or subsystem."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop capture worker and release system resources."""
        pass

    @abstractmethod
    def get_latest_frame(self) -> Tuple[Optional[np.ndarray], float]:
        """Return the latest available frame and its capture timestamp (seconds)."""
        pass

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """True if the driver is actively capturing."""
        pass
