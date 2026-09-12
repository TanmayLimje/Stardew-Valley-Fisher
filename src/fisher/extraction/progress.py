"""Catch progress meter extractor tracking red-to-green gradient column."""

from __future__ import annotations

from typing import Optional, Tuple
import cv2
import numpy as np

from fisher.extraction.track import TrackDetector


class ProgressTracker:
    """Extracts catch progress (p in [0, 1]) from the red-to-green meter column."""

    def __init__(
        self,
        track_detector: TrackDetector,
        meter_x_offset: int = 31,  # Verified exact offset from track x1 to progress column center
        meter_width: int = 8,      # Inner fill width of progress bar (avoids outer border overlap)
        zero_debounce_frames: int = 8,  # ~250ms of sustained zeros needed to declare zero progress
    ) -> None:
        self.track_detector = track_detector
        self.meter_x_offset = meter_x_offset
        self.meter_width = meter_width
        self.zero_debounce_frames = zero_debounce_frames

        self._last_known_p: float = 0.30  # Stardew Valley default initial progress
        self._has_detected_progress: bool = False
        self._consecutive_zeros: int = 0

    def reset(self) -> None:
        self._last_known_p = 0.30
        self._has_detected_progress = False
        self._consecutive_zeros = 0

    def extract(self, roi_frame: np.ndarray) -> Tuple[float, float]:
        """
        Extract progress p in [0, 1] and confidence score.
        Returns:
            (p in [0, 1], confidence in [0, 1])
        """
        if roi_frame is None or roi_frame.size == 0:
            if self._has_detected_progress:
                self._consecutive_zeros += 1
                if self._consecutive_zeros >= self.zero_debounce_frames:
                    self._last_known_p = 0.0
                    return 0.0, 0.0
                decayed = max(0.0, self._last_known_p - 0.006)
                self._last_known_p = decayed
                return decayed, 0.2
            return 0.0, 0.0

        tb = self.track_detector.bounds
        h_frame, w_frame = roi_frame.shape[:2]

        # Scan nominal progress meter column with safe jitter tolerance (+/- 4 px)
        best_filled = 0
        nominal_cx = tb.x1 + self.meter_x_offset
        total_h = float(tb.y1 - tb.y0 + 8)

        dx_candidates = [0, 2, -2, 4, -4, 6]
        for dx in dx_candidates:
            cx = nominal_cx + dx
            col_x0 = max(0, cx - self.meter_width // 2 + 1)
            col_x1 = min(w_frame, cx + self.meter_width // 2 - 1)
            if col_x1 <= col_x0:
                continue
            col_y0 = max(0, tb.y0 - 4)
            col_y1 = min(h_frame, tb.y1 + 4)
            crop = roi_frame[col_y0:col_y1, col_x0:col_x1]
            if crop.size == 0:
                continue

            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            # Bright filled progress meter (red/orange/yellow/green)
            # BobberBar.cs:461: Color((int)(255 - distanceFromCatch * 255), (int)(distanceFromCatch * 255), 0)
            # Filled progress is vivid (V >= 165, S >= 60), whereas unfilled background channel is dark (V <= 145).
            # Blue suppression (b <= 120) and valid hue reject water/sky reflections.
            b_chan = crop[:, :, 0]
            valid_hue = (hsv[:, :, 0] <= 95) | (hsv[:, :, 0] >= 165)
            is_filled = (hsv[:, :, 1] >= 60) & (hsv[:, :, 2] >= 165) & (b_chan <= 120) & valid_hue
            row_fill = np.mean(is_filled, axis=1)
            filled_indices = np.where(row_fill >= 0.4)[0]
            # Stardew Valley BobberBar progress fills upwards from bottom
            if len(filled_indices) > 0 and filled_indices[-1] >= (crop.shape[0] - 45):
                cnt = len(filled_indices)
            else:
                cnt = 0
            if cnt > best_filled and cnt <= total_h + 10:
                best_filled = cnt

        if best_filled == 0:
            if not self._has_detected_progress:
                # Inactive scene before minigame starts
                self._last_known_p = 0.0
                return 0.0, 0.8

            # Active minigame experienced a single-frame dropout / occlusion
            self._consecutive_zeros += 1
            if self._consecutive_zeros >= self.zero_debounce_frames:
                self._last_known_p = 0.0
                return 0.0, 0.8

            # Physical rate-limiting: progress drains at 0.003/tick (0.006/step @ 30Hz)
            decayed = max(0.0, self._last_known_p - 0.006)
            self._last_known_p = decayed
            return decayed, 0.4

        # Valid progress bar detected
        self._consecutive_zeros = 0
        p_clamped = float(np.clip(best_filled / max(1.0, total_h), 0.0, 1.0))
        if p_clamped >= 0.08:
            self._has_detected_progress = True

        self._last_known_p = p_clamped
        confidence = 0.95
        return p_clamped, confidence

    def has_progress_fill(self, roi_frame: np.ndarray, min_progress: float = 0.04) -> bool:
        """Check if roi_frame contains a valid non-empty progress meter fill."""
        p, _ = self.extract(roi_frame)
        return bool(p >= min_progress)
