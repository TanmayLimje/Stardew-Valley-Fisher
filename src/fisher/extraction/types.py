"""Type definitions and data structures for CV feature extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple
import numpy as np


@dataclass
class TrackBounds:
    """Pixel bounding box of the 568 px fishing track within ROI."""
    x0: int
    y0: int
    x1: int
    y1: int
    height_px: int = 568

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass
class ExtractionResult:
    """Output of feature extraction for a single frame."""
    is_active: bool
    bar_pos: float = 0.0          # b in [0, 1] (0 = bottom, 1 = top)
    bar_height: float = 0.169     # h in [0, 1] (half-height in normalized units)
    bar_vel: float = 0.0          # v in [-1, 1] (normalized velocity)
    fish_pos: float = 0.0         # f in [0, 1] (0 = bottom, 1 = top)
    fish_vel: float = 0.0         # f_dot normalized
    progress: float = 0.30        # p in [0, 1]
    in_bar: bool = False          # True if |f - b| <= h
    confidence: float = 0.0       # Confidence in detection [0, 1]
    timestamp: float = 0.0        # Capture timestamp
    features: np.ndarray = field(
        default_factory=lambda: np.zeros(9, dtype=np.float32)
    )



@dataclass
class LifecycleState:
    """Detection state of non-minigame lifecycle events."""
    bite_detected: bool = False
    bite_confidence: float = 0.0
    stamina_pct: float = 100.0
    is_night_cutoff: bool = False
    in_game_time_str: str = ""
    loot_dialog_detected: bool = False
    inventory_full_detected: bool = False
