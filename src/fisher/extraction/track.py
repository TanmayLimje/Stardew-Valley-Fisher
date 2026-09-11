"""Track detection, geometry localization, and coordinate normalization."""

from __future__ import annotations

import cv2
import numpy as np
from typing import Optional, Tuple
from fisher.extraction.types import TrackBounds


class TrackDetector:
    """Detects minigame track presence and handles screen <-> normalized coordinate conversions."""

    def __init__(
        self,
        default_bounds: Optional[TrackBounds] = None,
        debounce_frames: int = 12,  # ~200ms at 60Hz to ride through fade-in/out
    ) -> None:
        # Default 1080p Stardew Valley track coordinates within the standard ROI (1520, 220, 1900, 980)
        # ROI is 380x760 px.
        # Track column: x in [80, 120] relative to ROI, y in [70, 638] (height 568 px)
        self.bounds = default_bounds or TrackBounds(
            x0=72,
            y0=70,
            x1=116,
            y1=638,
            height_px=568,
        )
        self.debounce_frames = debounce_frames
        self._consecutive_misses: int = 0
        self._is_active: bool = False

    def detect_track(self, roi_frame: np.ndarray) -> Tuple[bool, float]:
        """
        Check if the fishing minigame track is present in the ROI frame.
        Returns:
            (is_active, confidence)
        """
        if roi_frame is None or roi_frame.size == 0:
            self._consecutive_misses += 1
            if self._consecutive_misses >= self.debounce_frames:
                self._is_active = False
            return False, 0.0

        h, w = roi_frame.shape[:2]
        # Validate that ROI is large enough to contain bounds
        if w < self.bounds.x1 or h < self.bounds.y1:
            self._consecutive_misses += 1
            if self._consecutive_misses >= self.debounce_frames:
                self._is_active = False
            return False, 0.0

        # Sample the track region
        track_crop = roi_frame[self.bounds.y0 : self.bounds.y1, self.bounds.x0 : self.bounds.x1]
        
        hsv = cv2.cvtColor(track_crop, cv2.COLOR_BGR2HSV)
        # 1. Green bar mask: H in [35, 85]
        green_mask = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 50) & (hsv[:, :, 2] >= 50)
        green_pixels = int(np.count_nonzero(green_mask))

        # 2. White flash mask (when fish is inside bar)
        white_mask = (hsv[:, :, 1] <= 55) & (hsv[:, :, 2] >= 190)
        white_pixels = int(np.count_nonzero(white_mask))

        # 3. Vertical edge contrast around track borders
        tb_x0 = max(0, self.bounds.x0 - 8)
        tb_x1 = min(w, self.bounds.x1 + 8)
        border_crop = roi_frame[self.bounds.y0 : self.bounds.y1, tb_x0:tb_x1]
        gray_border = cv2.cvtColor(border_crop, cv2.COLOR_BGR2GRAY)
        sobel_x = cv2.Sobel(gray_border, cv2.CV_32F, 1, 0, ksize=3)
        edge_energy = float(np.mean(np.abs(sobel_x)))

        # 4. Progress bar presence check (column adjacent to track)
        px_x0 = min(w - 10, self.bounds.x1 + 10)
        px_x1 = min(w, self.bounds.x1 + 35)
        prog_crop = roi_frame[self.bounds.y0 : self.bounds.y1, px_x0:px_x1]
        prog_sat_count = 0
        if prog_crop.size > 0:
            prog_hsv = cv2.cvtColor(prog_crop, cv2.COLOR_BGR2HSV)
            prog_sat_count = int(np.count_nonzero((prog_hsv[:, :, 1] >= 50) & (prog_hsv[:, :, 2] >= 50)))

        # 5. Dark interior channel ratio (the track is dark charcoal outside the bar/fish)
        gray_track = cv2.cvtColor(track_crop, cv2.COLOR_BGR2GRAY)
        dark_pixel_ratio = float(np.mean(gray_track < 55))

        # Minigame is active if:
        # 1) Green bar is visible (green_pixels >= 60) AND (track interior is dark OR strong vertical borders exist)
        # 2) OR white flashing bar is visible (white_pixels >= 100) and vertical track edges exist
        # 3) OR strong track vertical edges + dark channel + progress column activity
        active_now = False
        confidence = 0.0

        if green_pixels >= 60 and (dark_pixel_ratio >= 0.20 or edge_energy >= 15.0):
            active_now = True
            confidence = min(1.0, 0.6 + green_pixels / 1000.0)
        elif white_pixels >= 100 and edge_energy >= 16.0 and dark_pixel_ratio >= 0.15:
            active_now = True
            confidence = min(1.0, 0.6 + white_pixels / 1000.0)
        elif edge_energy >= 20.0 and dark_pixel_ratio >= 0.35:
            active_now = True
            confidence = 0.75
        else:
            confidence = 0.05



        if active_now:
            self._consecutive_misses = 0
            self._is_active = True
        else:
            self._consecutive_misses += 1
            if self._consecutive_misses >= self.debounce_frames:
                self._is_active = False

        return self._is_active, confidence

    def screen_y_to_norm(self, y_px: float) -> float:
        """
        Convert screen Y coordinate (in ROI pixels) to normalized track units [0, 1].
        0.0 = bottom of track, 1.0 = top of track.
        """
        track_span = float(self.bounds.y1 - self.bounds.y0)
        if track_span <= 0.0:
            return 0.5
        norm_y = 1.0 - ((y_px - self.bounds.y0) / track_span)
        return float(np.clip(norm_y, 0.0, 1.0))

    def norm_to_screen_y(self, y_norm: float) -> float:
        """
        Convert normalized track coordinate [0, 1] to screen Y (ROI pixels).
        """
        track_span = float(self.bounds.y1 - self.bounds.y0)
        y_clamped = np.clip(y_norm, 0.0, 1.0)
        return self.bounds.y0 + (1.0 - y_clamped) * track_span

    def height_px_to_norm(self, h_px: float) -> float:
        """Convert pixel height/half-height to normalized track units."""
        track_span = float(self.bounds.y1 - self.bounds.y0)
        if track_span <= 0.0:
            return 0.1
        return float(h_px / track_span)
