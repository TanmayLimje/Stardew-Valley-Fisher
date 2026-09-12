"""Bobber bar (green paddle) feature extractor with sub-pixel moment estimation."""

from __future__ import annotations

from typing import Optional, Tuple
import cv2
import numpy as np

from fisher.extraction.track import TrackDetector


class BobberBarExtractor:
    """Extracts position (b), half-height (h), and velocity (v) of the player's bobber bar."""

    def __init__(
        self,
        track_detector: TrackDetector,
        v_max: float = 2.0,  # normalized track heights per second
    ) -> None:
        self.track_detector = track_detector
        self.v_max = v_max

        # State tracking for velocity estimation
        self._prev_pos_norm: Optional[float] = None
        self._prev_timestamp: Optional[float] = None
        self._prev_velocity: float = 0.0

        # Cached nominal half-height in normalized units (default 96 px / 568 px / 2 ≈ 0.0845)
        self._last_known_h: float = 96.0 / (568.0 * 2.0)
        self._last_known_b: float = self._last_known_h  # resting at bottom initially

    def reset(self) -> None:
        """Reset velocity history."""
        self._prev_pos_norm = None
        self._prev_timestamp = None
        self._prev_velocity = 0.0

    def extract(
        self,
        roi_frame: np.ndarray,
        timestamp: float,
    ) -> Tuple[float, float, float, bool]:
        """
        Extract bar center (b), half-height (h), bar velocity (v), and whether bar is white-bright.
        Returns:
            (b_norm in [0, 1], h_norm in [0, 0.5], v_norm in [-1, 1], is_flashing_white)
        """
        if roi_frame is None or roi_frame.size == 0:
            return self._last_known_b, self._last_known_h, 0.0, False

        tb = self.track_detector.bounds
        # Crop the track region
        track_crop = roi_frame[tb.y0 : tb.y1, tb.x0 : tb.x1]
        if track_crop.size == 0:
            return self._last_known_b, self._last_known_h, 0.0, False

        hsv = cv2.cvtColor(track_crop, cv2.COLOR_BGR2HSV)

        # 1. Primary Green Mask: H in [35, 85], S in [60, 255], V in [60, 255]
        green_mask = cv2.inRange(
            hsv,
            np.array([35, 55, 50], dtype=np.uint8),
            np.array([85, 255, 255], dtype=np.uint8),
        )

        # 2. White Flash Mask (when fish is inside the bar, Stardew Valley draws it white: Color.White)
        # Low saturation, high value
        white_mask = cv2.inRange(
            hsv,
            np.array([0, 0, 190], dtype=np.uint8),
            np.array([180, 50, 255], dtype=np.uint8),
        )

        green_pixels = cv2.countNonZero(green_mask)
        white_pixels = cv2.countNonZero(white_mask)

        is_flashing_white = False
        active_mask = green_mask

        # If significant white pixels exist inside the track column (often > 200),
        # the bar is flashing white due to in-bar fish contact
        if white_pixels > 150 and (white_pixels > green_pixels or green_pixels < 200):
            active_mask = cv2.bitwise_or(green_mask, white_mask)
            is_flashing_white = True

        # Morphological clean up to remove small sparkle speckles
        open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5))
        cleaned_mask = cv2.morphologyEx(active_mask, cv2.MORPH_OPEN, open_kernel)

        # Bridge vertical occlusion caused by fish sprite crossing the green bar
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 35))
        cleaned_mask = cv2.morphologyEx(cleaned_mask, cv2.MORPH_CLOSE, close_kernel)

        # Find vertical projection / contours
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(cleaned_mask, connectivity=8)

        # Look for the largest component with height > 25 px
        best_comp_idx = -1
        best_area = 0
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            h_px = stats[i, cv2.CC_STAT_HEIGHT]
            w_px = stats[i, cv2.CC_STAT_WIDTH]
            # Bobber bar is typically 20-40 px wide and 60-180 px tall (max ~200 px with Cork Bobber)
            # Rejects oversized scenery grass / tree components (area > 5500, height > 240)
            if area > 100 and 25 <= h_px <= 240 and area <= 5500 and area > best_area:
                best_area = area
                best_comp_idx = i

        if best_comp_idx != -1:
            top_px = tb.y0 + stats[best_comp_idx, cv2.CC_STAT_TOP]
            height_px = stats[best_comp_idx, cv2.CC_STAT_HEIGHT]
            bot_px = top_px + height_px

            # Sub-pixel centroid from component moments
            comp_mask = (labels == best_comp_idx).astype(np.uint8)
            moments = cv2.moments(comp_mask)
            if moments["m00"] > 0:
                center_y_local = moments["m01"] / moments["m00"]
                center_px = tb.y0 + center_y_local
            else:
                center_px = (top_px + bot_px) / 2.0

            b_norm = self.track_detector.screen_y_to_norm(center_px)
            h_norm = self.track_detector.height_px_to_norm(height_px / 2.0)

            self._last_known_b = b_norm
            self._last_known_h = h_norm
        else:
            # Fallback to last known position
            b_norm = self._last_known_b
            h_norm = self._last_known_h

        # Velocity estimation: finite difference over timestamp
        v_norm = 0.0
        if self._prev_pos_norm is not None and self._prev_timestamp is not None:
            dt = timestamp - self._prev_timestamp
            if 0.005 < dt < 0.2:
                # Raw velocity in track units / sec
                v_raw = (b_norm - self._prev_pos_norm) / dt
                # Normalize by v_max to [-1, 1]
                v_norm = float(np.clip(v_raw / self.v_max, -1.0, 1.0))
                # 1-step smoothing
                v_norm = 0.7 * v_norm + 0.3 * self._prev_velocity

        self._prev_pos_norm = b_norm
        self._prev_timestamp = timestamp
        self._prev_velocity = v_norm

        return b_norm, h_norm, v_norm, is_flashing_white
