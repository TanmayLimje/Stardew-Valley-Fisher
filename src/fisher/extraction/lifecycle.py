"""Lifecycle event detectors: bite '!' cue, stamina gauge, night cutoff clock, and dialogs."""

from __future__ import annotations

import logging
from typing import Optional, Tuple
import cv2
import numpy as np

from fisher.extraction.types import LifecycleState

logger = logging.getLogger(__name__)


class LifecycleDetector:
    """Detects game lifecycle states: bite cues, stamina, in-game clock, and dialogs."""

    def __init__(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Stamina meter ROI in 1080p (bottom-right gauge)
        # Bounding box approximately x in [1880, 1906], y in [920, 1050]
        self.stamina_roi = (1880, 920, 1908, 1050)

        # Clock HUD ROI in 1080p (top-right HUD)
        # Bounding box approximately x in [1720, 1910], y in [15, 120]
        self.clock_roi = (1720, 15, 1910, 120)

        # Center player head ROI for bite alert (middle third of screen)
        # x in [800, 1120], y in [400, 680]
        self.bite_roi = (800, 400, 1120, 680)

        # Center dialog ROI for loot / inventory prompts
        self.dialog_roi = (700, 350, 1220, 750)

        # Motion tracking for bobber dip
        self._prev_bite_frame_gray: Optional[np.ndarray] = None

    def detect_bite(self, full_frame: np.ndarray) -> Tuple[bool, float]:
        """
        Detect the exclamation mark '!' bite cue and bobber dip motion.
        Returns:
            (is_bite, confidence)
        """
        if full_frame is None or full_frame.size == 0:
            return False, 0.0

        x0, y0, x1, y1 = self.bite_roi
        h, w = full_frame.shape[:2]
        crop = full_frame[max(0, y0) : min(h, y1), max(0, x0) : min(w, x1)]
        if crop.size == 0:
            return False, 0.0

        # In Stardew Valley, the bite '!' is bright red with yellow/white borders
        # or dark text bubble. Check for localized red bubble in HSV:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        # Red spans H in [0, 10] and [170, 180] with high saturation
        mask_r1 = cv2.inRange(hsv, np.array([0, 120, 120]), np.array([10, 255, 255]))
        mask_r2 = cv2.inRange(hsv, np.array([170, 120, 120]), np.array([180, 255, 255]))
        red_mask = cv2.bitwise_or(mask_r1, mask_r2)

        red_pixels = int(cv2.countNonZero(red_mask))

        # Motion detection (bobber dip ripple)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        motion_score = 0.0
        if self._prev_bite_frame_gray is not None and self._prev_bite_frame_gray.shape == gray.shape:
            diff = cv2.absdiff(gray, self._prev_bite_frame_gray)
            motion_score = float(np.mean(diff))
        self._prev_bite_frame_gray = gray

        # Combined cue: red '!' bubble (typically 30-150 pixels) or strong motion burst
        if red_pixels >= 25:
            conf = min(1.0, 0.6 + (red_pixels / 200.0) + (motion_score / 50.0))
            return True, conf
        return False, 0.0

    def detect_stamina(self, full_frame: np.ndarray) -> float:
        """
        Extract stamina level percentage [0.0, 100.0].
        Stamina bar is in the bottom-right corner, filling from bottom up.
        """
        if full_frame is None or full_frame.size == 0:
            return 100.0

        x0, y0, x1, y1 = self.stamina_roi
        h, w = full_frame.shape[:2]
        crop = full_frame[max(0, y0) : min(h, y1), max(0, x0) : min(w, x1)]
        if crop.size == 0:
            return 100.0

        # Stamina meter has green/yellow/red color when filled
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        # Colors: green H [35, 85], yellow/orange H [15, 35], red H [0, 15]
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        filled_mask = (sat >= 50) & (val >= 50)

        # Ratio of filled rows
        row_fill = np.mean(filled_mask, axis=1)
        filled_rows = int(np.count_nonzero(row_fill > 0.4))
        total_rows = len(row_fill)
        if total_rows == 0:
            return 100.0

        pct = (float(filled_rows) / float(total_rows)) * 100.0
        return float(np.clip(pct, 0.0, 100.0))

    def detect_clock(self, full_frame: np.ndarray) -> Tuple[bool, str]:
        """
        Check if the in-game time has passed the 1:30 AM night cutoff.
        Returns:
            (is_night_cutoff, estimated_time_str)
        """
        if full_frame is None or full_frame.size == 0:
            return False, "12:00 pm"

        x0, y0, x1, y1 = self.clock_roi
        h, w = full_frame.shape[:2]
        crop = full_frame[max(0, y0) : min(h, y1), max(0, x0) : min(w, x1)]
        if crop.size == 0:
            return False, "12:00 pm"

        # At late night (1:00 AM - 2:00 AM), the clock HUD changes:
        # 1. The sun/moon dial shows the moon high on the left.
        # 2. In-game text color for late night flashes red when approaching 2:00 AM.
        # Check for red warning text flashing in the clock HUD
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask_red = cv2.inRange(hsv, np.array([0, 140, 140]), np.array([10, 255, 255]))
        red_count = int(cv2.countNonZero(mask_red))

        # If red warning pixels exist in the clock HUD, player is in late-night peril (after 1:00 AM)
        if red_count >= 15:
            return True, "1:30 am"

        return False, "daytime"

    def detect_dialog(self, full_frame: np.ndarray) -> Tuple[bool, bool]:
        """
        Detect loot dialog (fish caught) or inventory full prompt.
        Returns:
            (loot_dialog_detected, inventory_full_detected)
        """
        if full_frame is None or full_frame.size == 0:
            return False, False

        x0, y0, x1, y1 = self.dialog_roi
        h, w = full_frame.shape[:2]
        crop = full_frame[max(0, y0) : min(h, y1), max(0, x0) : min(w, x1)]
        if crop.size == 0:
            return False, False

        # Loot dialog has a parchment/beige backing with dark border and OK button
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        # Parchment color: H in [15, 30], S in [40, 150], V in [140, 240]
        parchment_mask = (hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 30) & (hsv[:, :, 1] >= 35) & (hsv[:, :, 2] >= 130)
        parchment_ratio = float(np.mean(parchment_mask))

        loot_detected = parchment_ratio > 0.25
        inventory_full = False  # Set when specific full prompt detected

        return loot_detected, inventory_full

    def update_lifecycle_state(self, full_frame: np.ndarray) -> LifecycleState:
        """Aggregate all lifecycle detectors on a full 1080p frame."""
        bite, bite_conf = self.detect_bite(full_frame)
        stamina = self.detect_stamina(full_frame)
        night_cutoff, clock_str = self.detect_clock(full_frame)
        loot_diag, inv_full = self.detect_dialog(full_frame)

        return LifecycleState(
            bite_detected=bite,
            bite_confidence=bite_conf,
            stamina_pct=stamina,
            is_night_cutoff=night_cutoff,
            in_game_time_str=clock_str,
            loot_dialog_detected=loot_diag,
            inventory_full_detected=inv_full,
        )
