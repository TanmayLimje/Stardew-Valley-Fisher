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
        meter_x_offset: int = 32,  # Verified exact offset from track x1 to progress column center
        meter_width: int = 12,      # Verified width of progress bar fill column
    ) -> None:
        self.track_detector = track_detector
        self.meter_x_offset = meter_x_offset
        self.meter_width = meter_width

        self._last_known_p: float = 0.30  # Stardew Valley default initial progress

    def reset(self) -> None:
        self._last_known_p = 0.30

    def extract(self, roi_frame: np.ndarray) -> Tuple[float, float]:
        """
        Extract progress p in [0, 1] and confidence score.
        Returns:
            (p in [0, 1], confidence in [0, 1])
        """
        if roi_frame is None or roi_frame.size == 0:
            return self._last_known_p, 0.0

        tb = self.track_detector.bounds
        h_frame, w_frame = roi_frame.shape[:2]

        # Calculate progress meter column bounds
        col_x0 = tb.x1 + self.meter_x_offset - self.meter_width // 2
        col_x1 = col_x0 + self.meter_width
        col_y0 = tb.y0 - 4   # Progress bar is 580 px tall (track is 568 px)
        col_y1 = tb.y1 + 4

        # Bounds safety checks
        col_x0 = max(0, min(col_x0, w_frame - 2))
        col_x1 = max(col_x0 + 1, min(col_x1, w_frame))
        col_y0 = max(0, min(col_y0, h_frame - 2))
        col_y1 = max(col_y0 + 1, min(col_y1, h_frame))

        col_crop = roi_frame[col_y0:col_y1, col_x0:col_x1]
        if col_crop.size == 0:
            return self._last_known_p, 0.0

        total_rows = col_crop.shape[0]
        if total_rows < 50:
            return self._last_known_p, 0.0

        hsv = cv2.cvtColor(col_crop, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]

        # The progress bar is brightly colored (Red/Orange/Yellow/Green) when filled,
        # with high saturation (S >= 60) and high brightness (V >= 170).
        # Unfilled background is darker (V <= 130).
        is_filled_pixel = (sat >= 60) & (val >= 170)

        # Average across the horizontal width of the column
        row_fill_ratio = np.mean(is_filled_pixel, axis=1)  # shape: (total_rows,)

        # Scan bottom-up (from row index total_rows - 1 up to 0)
        # Find the transition from filled (ratio >= 0.5) to unfilled
        filled_count = 0
        for r in range(total_rows - 1, -1, -1):
            if row_fill_ratio[r] >= 0.4:
                filled_count += 1
            else:
                # Small noise filter: check if preceding 3 rows are also unfilled
                lookahead = max(0, r - 3)
                if np.mean(row_fill_ratio[lookahead : r + 1]) < 0.3:
                    break
                else:
                    filled_count += 1

        p_raw = float(filled_count) / float(total_rows)
        p_clamped = float(np.clip(p_raw, 0.0, 1.0))

        confidence = min(1.0, float(np.mean(row_fill_ratio[:filled_count])) + 0.2) if filled_count > 0 else 0.8
        self._last_known_p = p_clamped

        return p_clamped, confidence
