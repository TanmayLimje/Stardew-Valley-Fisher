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
        if isinstance(default_bounds, dict):
            self.bounds = TrackBounds(
                x0=int(default_bounds.get("x0", 72)),
                y0=int(default_bounds.get("y0", 70)),
                x1=int(default_bounds.get("x1", 116)),
                y1=int(default_bounds.get("y1", 638)),
                height_px=int(default_bounds.get("height_px", 568)),
            )
        else:
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

        # 1. Straight vertical borders check (Stardew UI borders are solid continuous lines with low std):
        lines_l = [roi_frame[self.bounds.y0 : self.bounds.y1, x] for x in range(max(0, self.bounds.x0 - 2), min(w, self.bounds.x0 + 3))]
        lines_r = [roi_frame[self.bounds.y0 : self.bounds.y1, x] for x in range(max(0, self.bounds.x1 - 2), min(w, self.bounds.x1 + 3))]
        min_std_l = min(float(np.mean(np.std(l, axis=0))) for l in lines_l)
        min_std_r = min(float(np.mean(np.std(r, axis=0))) for r in lines_r)
        is_straight_border = (min_std_l <= 45.0) and (min_std_r <= 45.0)

        # 2. Bounded solid rectangular bobber bar paddle (green or white flash)
        track_crop = roi_frame[self.bounds.y0 : self.bounds.y1, self.bounds.x0 : self.bounds.x1]
        hsv = cv2.cvtColor(track_crop, cv2.COLOR_BGR2HSV)
        green_mask = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 50) & (hsv[:, :, 2] >= 50)
        white_mask = (hsv[:, :, 1] <= 55) & (hsv[:, :, 2] >= 190)
        combined = (green_mask | white_mask).astype(np.uint8)

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(combined, connectivity=8)
        has_valid_bar = False
        for i in range(1, num_labels):
            w_c = stats[i, cv2.CC_STAT_WIDTH]
            h_c = stats[i, cv2.CC_STAT_HEIGHT]
            area = stats[i, cv2.CC_STAT_AREA]
            rect_ratio = area / max(1, (w_c * h_c))
            # Valid paddle: width in [20, 48] (nominal 36 px), height in [25, 250], area >= 250, fill ratio >= 0.55
            if 20 <= w_c <= 48 and 25 <= h_c <= 250 and area >= 250 and rect_ratio >= 0.55:
                has_valid_bar = True
                break

        # 3. Check for presence of progress bar fill adjacent to the track
        pm_x0 = max(0, self.bounds.x1 + 20)
        pm_x1 = min(w, self.bounds.x1 + 45)
        pm_y0 = max(0, self.bounds.y0 - 4)
        pm_y1 = min(h, self.bounds.y1 + 4)
        has_progress = False
        if pm_x1 > pm_x0 and pm_y1 > pm_y0:
            pm_crop = roi_frame[pm_y0:pm_y1, pm_x0:pm_x1]
            pm_hsv = cv2.cvtColor(pm_crop, cv2.COLOR_BGR2HSV)
            pm_filled = (
                ((pm_hsv[:, :, 0] <= 95) | (pm_hsv[:, :, 0] >= 165))
                & (pm_hsv[:, :, 1] >= 45)
                & (pm_hsv[:, :, 2] >= 110)
            )
            pm_row_matches = np.sum(pm_filled, axis=1)
            min_rows = 5 if self._is_active else 15
            has_progress = int(np.sum(pm_row_matches >= 3)) >= min_rows

        # Minigame is active if both straight vertical borders exist, bobber paddle is detected, and progress meter has fill
        active_now = is_straight_border and has_valid_bar and has_progress
        confidence = 0.95 if active_now else 0.05

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
        return float(np.clip(h_px / track_span, 0.0, 1.0))

    def locate_widget(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        """
        Locate the BobberBar minigame widget in a full or cropped frame.
        Scans for the paddle (green or white flash) paired with the red progress bar.
        Returns:
            (roi_x0, roi_y0, roi_x1, roi_y1) bounding box if found, else None.
        """
        if frame is None or frame.size == 0:
            return None
        h_frame, w_frame = frame.shape[:2]

        # If frame is already a cropped ROI (e.g. synthetic or pre-cropped)
        if w_frame < 600 and h_frame < 800:
            active, conf = self.detect_track(frame)
            if active and conf >= 0.40:
                return (0, 0, w_frame, h_frame)
            return None

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # 1. Detect candidate paddle (green or white flash)
        green_mask = cv2.inRange(hsv, (35, 50, 50), (85, 255, 255))
        white_mask = (hsv[:, :, 1] <= 55) & (hsv[:, :, 2] >= 190)
        paddle_mask = (green_mask | (white_mask.astype(np.uint8) * 255))

        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(paddle_mask)

        # Iterate candidates, prioritizing strongest candidate with valid detect_track confirmation
        best_bbox: Optional[Tuple[int, int, int, int]] = None
        best_conf = 0.0

        best_score = 0.0

        for i in range(1, num_labels):
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            x = stats[i, cv2.CC_STAT_LEFT]
            y = stats[i, cv2.CC_STAT_TOP]

            # In 1080p, paddle width is ~25-65 px, height is 25-300 px, within playable viewport [60, 1700]
            # Exclude top-right clock/date HUD area (x > 1750) and far-left display edge
            if 20 <= w <= 70 and 25 <= h <= 320 and 60 <= x <= 1700:
                # Check for progress meter fill to the right of the track (+30 to +85 px)
                # In Stardew Valley, meter color transitions smoothly: Red -> Orange -> Yellow -> Green
                pm_x0 = x + 30
                pm_x1 = min(w_frame, x + 85)
                # Vertical search constrained around paddle: track can span at most bounds.height_px
                pm_y0 = max(0, y - self.bounds.height_px)
                pm_y1 = min(h_frame, y + self.bounds.height_px + 60)
                pm_crop = hsv[pm_y0:pm_y1, pm_x0:pm_x1]
                if pm_crop.size == 0:
                    continue

                meter_mask = (
                    ((pm_crop[:, :, 0] <= 95) | (pm_crop[:, :, 0] >= 165))
                    & (pm_crop[:, :, 1] >= 45)
                    & (pm_crop[:, :, 2] >= 110)
                )
                meter_pixels = int(np.sum(meter_mask))
                row_matches = np.sum(meter_mask, axis=1)
                valid_rows = np.where(row_matches >= 3)[0]

                # A valid minigame progress bar has >= 100 vivid pixels and >= 20 continuous rows
                if meter_pixels >= 100 and len(valid_rows) >= 20:
                    diffs = np.diff(valid_rows)
                    splits = np.where(diffs > 8)[0]
                    chunks = np.split(valid_rows, splits + 1)
                    best_chunk = max(chunks, key=len)
                    if len(best_chunk) < 20:
                        continue

                    lowest_meter_y = pm_y0 + int(best_chunk[-1])
                    track_bot = lowest_meter_y + 4
                    track_top = track_bot - self.bounds.height_px
                    roi_x0 = max(0, x - self.bounds.x0)
                    roi_x1 = min(w_frame, roi_x0 + 190)
                    roi_y0 = max(0, track_top - self.bounds.y0)
                    roi_y1 = min(h_frame, roi_y0 + 650)

                    candidate_crop = frame[roi_y0:roi_y1, roi_x0:roi_x1]
                    if candidate_crop.shape[0] >= 500 and candidate_crop.shape[1] >= 150:
                        active, conf = self.detect_track(candidate_crop)
                        if active and conf >= 0.70:
                            score = float(stats[i, cv2.CC_STAT_AREA]) * float(len(best_chunk))
                            if score > best_score:
                                best_bbox = (int(roi_x0), int(roi_y0), int(roi_x1), int(roi_y1))
                                best_score = score

        return best_bbox
