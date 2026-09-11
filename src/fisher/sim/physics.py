"""Physics engine for the Stardew Valley fishing bobber bar.

Grounded directly in the decompiled game logic:
`StardewValley.Menus.BobberBar` (Stardew Valley 1.6.x).
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass
class BarPhysicsConfig:
    """Configuration parameters for bar dynamics, matching BobberBar.cs."""

    track_height: float = 568.0  # bobberBarTrackHeight = 568 px
    bar_height: float = 96.0     # 96 + fishing_level * 8
    gravity: float = 0.25        # 0.25f px/tick^2
    in_bar_factor: float = 0.6   # num5 *= 0.6f when bobberInBar
    restitution_bottom: float = 2.0 / 3.0  # bounce restitution
    restitution_top: float = 2.0 / 3.0
    lead_bobber: bool = False    # reduces bottom bounce to 0.1x
    v_max_track_s: float = 2.0   # normalization constant for velocity (~1.78 track/s max free fall)


class BobberBarPhysics:
    """1D explicit Euler physics simulation of the player bobber bar.
    
    All internal calculations run in exact game pixel units at 60 Hz,
    with zero velocity damping (pure Euler integration matching BobberBar.cs).
    Coordinate conversion to normalized space ([0, 1], 0=bottom, 1=top)
    is provided via property accessors.
    """

    def __init__(self, config: BarPhysicsConfig | None = None) -> None:
        self.cfg = config or BarPhysicsConfig()
        self.reset()

    def reset(self, initial_pos_px: float | None = None, initial_speed_px: float = 0.0) -> None:
        """Reset the bar to starting state (default: sitting at bottom bound)."""
        max_pos = self.cfg.track_height - self.cfg.bar_height
        if initial_pos_px is None:
            self.bar_pos = max_pos  # Game constructor: 568 - bobberBarHeight
        else:
            self.bar_pos = float(np.clip(initial_pos_px, 0.0, max_pos))
        self.bar_speed = float(initial_speed_px)

    def step(self, button_pressed: bool, in_bar: bool = False) -> tuple[float, float]:
        """Execute one 60 Hz physics tick.
        
        Args:
            button_pressed: True if LMB is held down, False if released.
            in_bar: True if fish icon is inside the bobber bar.
            
        Returns:
            Tuple of (bar_pos_px, bar_speed_px).
        """
        # Line 428: float num5 = (buttonPressed ? (-0.25f) : 0.25f);
        accel = -self.cfg.gravity if button_pressed else self.cfg.gravity

        max_pos = self.cfg.track_height - self.cfg.bar_height

        # Line 429: button pinned at bounds zeroes speed
        if button_pressed and accel < 0.0 and (self.bar_pos == 0.0 or self.bar_pos == max_pos):
            self.bar_speed = 0.0

        # Line 435: in-bar gravity reduction (0.6x nominal)
        if in_bar:
            accel *= self.cfg.in_bar_factor

        # Line 456-457: pure Euler integration (no damping: c_d = 1.0)
        self.bar_speed += accel
        self.bar_pos += self.bar_speed

        # Line 458-466: bottom bound collision
        if self.bar_pos > max_pos:
            self.bar_pos = max_pos
            rest = self.cfg.restitution_bottom * (0.1 if self.cfg.lead_bobber else 1.0)
            self.bar_speed = -self.bar_speed * rest

        # Line 467-475: top bound collision
        elif self.bar_pos < 0.0:
            self.bar_pos = 0.0
            self.bar_speed = -self.bar_speed * self.cfg.restitution_top

        return self.bar_pos, self.bar_speed

    # --------------------------------------------------------------------------
    # Normalized Coordinate Accessors (0 = bottom, 1 = top)
    # --------------------------------------------------------------------------

    @property
    def bar_half_height_norm(self) -> float:
        """Half-height in normalized track units [0, 1]."""
        return (self.cfg.bar_height / 2.0) / self.cfg.track_height

    @property
    def bar_center_norm(self) -> float:
        """Bar center in normalized coordinates [0, 1] where 0=bottom, 1=top."""
        center_px = self.bar_pos + self.cfg.bar_height / 2.0
        return 1.0 - (center_px / self.cfg.track_height)

    @property
    def bar_velocity_norm(self) -> float:
        """Bar velocity normalized to [-1, 1], positive = moving UP."""
        # bar_speed is in px/tick (60 ticks/s). Negative in game = moving up towards 0.
        # In normalized track/s:
        v_track_s = -self.bar_speed * 60.0 / self.cfg.track_height
        return float(np.clip(v_track_s / self.cfg.v_max_track_s, -1.0, 1.0))

    @property
    def bar_bounds_norm(self) -> tuple[float, float]:
        """Return (bottom_edge, top_edge) in normalized coordinates [0, 1]."""
        c = self.bar_center_norm
        h = self.bar_half_height_norm
        return c - h, c + h
