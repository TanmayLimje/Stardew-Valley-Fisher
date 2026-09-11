"""Fish kinematic model for the Stardew Valley fishing minigame.

Grounded directly in the decompiled game logic:
`StardewValley.Menus.BobberBar` (Stardew Valley 1.6.x).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import numpy as np


class FishMotionType(IntEnum):
    """Fish movement behavior archetypes matching BobberBar.cs constants."""

    MIXED = 0    # Default: regular retargeting proportional to difficulty
    DART = 1     # Explosive jumps with resting intervals
    SMOOTH = 2   # High-frequency retargeting (20x) for fluid sine-like motion
    SINKER = 3   # Downward acceleration bias
    FLOATER = 4  # Upward acceleration bias


@dataclass
class FishConfig:
    """Configuration parameters for a simulated fish."""

    difficulty: float = 40.0         # 5 to 110
    motion_type: FishMotionType = FishMotionType.MIXED
    track_height: float = 568.0     # Reference track height
    fish_track_height: float = 548.0 # bobberTrackHeight = 548 in C#
    fish_max_pos: float = 532.0      # Maximum bobberPosition clamp
    v_f_max: float = 24.0            # Max fish velocity px/tick for normalization (up to 24 px/tick for d=110)


def safe_next(rng: np.random.Generator, min_val: int, max_val: int) -> int:
    """Match BobberBar.SafeNext: returns maxValue if min >= max, else random in [min, max)."""
    if min_val >= max_val:
        return max_val
    return int(rng.integers(min_val, max_val))


class SimulatedFish:
    """Simulates fish kinematics and retargeting matching BobberBar.cs update().
    
    All updates run at 60 Hz in game pixel coordinates (0 = top, 548 = bottom).
    """

    def __init__(self, config: FishConfig | None = None, seed: int | None = None) -> None:
        self.cfg = config or FishConfig()
        self.rng = np.random.default_rng(seed)
        self.reset()

    def reset(
        self,
        difficulty: float | None = None,
        motion_type: FishMotionType | None = None,
        initial_pos: float | None = None,
    ) -> None:
        """Reset fish state to constructor defaults from BobberBar.cs."""
        if difficulty is not None:
            self.cfg.difficulty = float(difficulty)
        if motion_type is not None:
            self.cfg.motion_type = motion_type

        # Constructor initialization:
        # bobberPosition = 508f;
        # bobberTargetPosition = (100f - difficulty) / 100f * 548f;
        if initial_pos is None:
            self.pos = 508.0
        else:
            self.pos = float(np.clip(initial_pos, 0.0, self.cfg.fish_max_pos))

        self.speed = 0.0
        self.accel = 0.0
        self.target_pos = (100.0 - self.cfg.difficulty) / 100.0 * self.cfg.fish_track_height
        self.floater_sinker_accel = 0.0

        # Velocity EMA tracker for observation
        self.speed_ema = 0.0

    def step(self) -> float:
        """Execute one 60 Hz tick of fish kinematics matching BobberBar.cs lines 374-416.
        
        Returns:
            Current fish position in screen pixel coordinates [0, 532].
        """
        diff = self.cfg.difficulty
        motion = self.cfg.motion_type

        # Lines 374-380: Retargeting check
        rate_multiplier = 20.0 if motion == FishMotionType.SMOOTH else 1.0
        retarget_prob = diff * rate_multiplier / 4000.0
        if self.rng.random() < retarget_prob and (motion != FishMotionType.SMOOTH or self.target_pos == -1.0):
            num = self.cfg.fish_track_height - self.pos
            num2 = self.pos
            num3 = min(99.0, diff + float(self.rng.integers(10, 45))) / 100.0
            lower = int(min(-num2, num))
            upper = int(num)
            if lower < upper:
                self.target_pos = self.pos + float(self.rng.integers(lower, upper)) * num3
            else:
                self.target_pos = self.pos

        # Lines 381-389: Floater/Sinker acceleration drift
        if motion == FishMotionType.FLOATER:
            self.floater_sinker_accel = max(self.floater_sinker_accel - 0.01, -1.5)
        elif motion == FishMotionType.SINKER:
            self.floater_sinker_accel = min(self.floater_sinker_accel + 0.01, 1.5)

        # Lines 390-402: Velocity chase towards target
        if abs(self.pos - self.target_pos) > 3.0 and self.target_pos != -1.0:
            denom = float(self.rng.integers(10, 30)) + (100.0 - min(100.0, diff))
            self.accel = (self.target_pos - self.pos) / denom
            self.speed += (self.accel - self.speed) / 5.0
        elif motion != FishMotionType.SMOOTH and self.rng.random() < (diff / 2000.0):
            if self.rng.random() < 0.5:
                jump = float(self.rng.integers(-100, -50))  # -100 to -51 inclusive
            else:
                jump = float(self.rng.integers(50, 101))    # 50 to 100 inclusive
            self.target_pos = self.pos + jump
        else:
            self.target_pos = -1.0

        # Lines 403-406: Dart archetype extra jump
        if motion == FishMotionType.DART and self.rng.random() < (diff / 1000.0):
            if self.rng.random() < 0.5:
                jump = float(safe_next(self.rng, -100 - int(diff) * 2, -50))
            else:
                jump = float(safe_next(self.rng, 50, 101 + int(diff) * 2))
            self.target_pos = self.pos + jump

        # Lines 407-416: Clamp target, integrate position, clamp position
        self.target_pos = max(-1.0, min(self.target_pos, self.cfg.fish_track_height))
        self.pos += self.speed + self.floater_sinker_accel

        if self.pos > self.cfg.fish_max_pos:
            self.pos = self.cfg.fish_max_pos
        elif self.pos < 0.0:
            self.pos = 0.0

        # Update EMA of fish speed (normalized track units per tick)
        self.speed_ema = 0.7 * self.speed_ema + 0.3 * self.speed
        return self.pos

    def is_in_bar(self, bar_pos_px: float, bar_height_px: float) -> bool:
        """Check if fish is within the bobber bar, matching BobberBar.cs lines 417-421."""
        # Line 417: bobberInBar = bobberPosition + 12f <= bobberBarPos - 32f + (float)bobberBarHeight
        #                         && bobberPosition - 16f >= bobberBarPos - 32f;
        cond1 = (self.pos + 12.0 <= bar_pos_px - 32.0 + bar_height_px) and (
            self.pos - 16.0 >= bar_pos_px - 32.0
        )
        # Line 418-421: bottom-edge assist
        # if (bobberPosition >= (float)(548 - bobberBarHeight) && bobberBarPos >= (float)(568 - bobberBarHeight - 4))
        cond2 = (self.pos >= (self.cfg.fish_track_height - bar_height_px)) and (
            bar_pos_px >= (self.cfg.track_height - bar_height_px - 4.0)
        )
        return bool(cond1 or cond2)

    # --------------------------------------------------------------------------
    # Normalized Coordinate Accessors (0 = bottom, 1 = top)
    # --------------------------------------------------------------------------

    @property
    def position_norm(self) -> float:
        """Normalized fish visual centroid [0, 1] where 0=bottom, 1=top."""
        # Visual center of fish icon is pos + 30 px
        center_px = self.pos + 30.0
        return 1.0 - (center_px / self.cfg.track_height)

    @property
    def velocity_norm(self) -> float:
        """Normalized velocity estimate [-1, 1], positive = moving UP."""
        v_track_s = -self.speed_ema * 60.0 / self.cfg.track_height
        v_max = (self.cfg.v_f_max * 60.0) / self.cfg.track_height
        return float(np.clip(v_track_s / v_max, -1.0, 1.0))
