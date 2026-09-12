"""Track detection, geometry localization, and coordinate normalization."""

from __future__ import annotations

import logging
import cv2
import numpy as np
from typing import Optional, Tuple
from fisher.extraction.types import TrackBounds

logger = logging.getLogger(__name__)


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

        # 1b. Outer wooden frame check on left border of the track:
        # Ground truth BobberBar.cs: The track is housed within a red/brown wooden UI frame (R >= G - 15 and R - B >= 15).
        # Scenery trees and grass have G > R and G > B (wood_ratio < 0.20).
        outer_l = roi_frame[self.bounds.y0 : self.bounds.y1, max(0, self.bounds.x0 - 8) : self.bounds.x0]
        r_w = outer_l[:, :, 2].astype(int)
        g_w = outer_l[:, :, 1].astype(int)
        b_w = outer_l[:, :, 0].astype(int)
        is_wood = (r_w >= g_w - 15) & (r_w - b_w >= 15)
        wood_ratio = float(np.mean(is_wood)) if is_wood.size > 0 else 0.0
        is_wooden_frame = wood_ratio >= 0.35

        # 2. Bounded solid rectangular bobber bar paddle (green or white flash)
        track_crop = roi_frame[self.bounds.y0 : self.bounds.y1, self.bounds.x0 : self.bounds.x1]
        hsv = cv2.cvtColor(track_crop, cv2.COLOR_BGR2HSV)
        green_mask = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 50) & (hsv[:, :, 2] >= 50)
        white_mask = (hsv[:, :, 1] <= 55) & (hsv[:, :, 2] >= 190)
        combined = (green_mask | white_mask).astype(np.uint8)

        # Apply vertical closing to bridge fish sprite occlusion gaps (matches bar.py logic)
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 35))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, close_kernel)

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(combined, connectivity=8)
        has_valid_bar = False
        for i in range(1, num_labels):
            w_c = stats[i, cv2.CC_STAT_WIDTH]
            h_c = stats[i, cv2.CC_STAT_HEIGHT]
            area = stats[i, cv2.CC_STAT_AREA]
            rect_ratio = area / max(1, (w_c * h_c))
            # Valid paddle after morphological close: paddle is 18-48 px wide (nominal 36 px),
            # 35-260 px tall (in-game bobberBarHeight=96..200 px; rejects 495 px fishing rod),
            # area >= 500, and fill ratio >= 0.40.
            if 18 <= w_c <= 48 and 35 <= h_c <= 260 and area >= 500 and rect_ratio >= 0.40:
                has_valid_bar = True
                break

        # Invariant: Track interior must NOT be completely solid green.
        # The Bobber paddle occupies 15% to 35% of track height. If green covers > 45% of the track,
        # it is solid pine tree or grass foliage, not a BobberBar track.
        g_ratio = float(np.mean(green_mask))
        bounded_paddle_ratio = (g_ratio <= 0.45)

        # 3. Check for presence of progress bar fill adjacent to the track
        pm_x0 = max(0, self.bounds.x1 + 18)
        pm_x1 = min(w, self.bounds.x1 + 45)
        pm_y0 = max(0, self.bounds.y0 - 4)
        pm_y1 = min(h, self.bounds.y1 + 4)
        has_progress = False
        if pm_x1 > pm_x0 and pm_y1 > pm_y0:
            pm_crop = roi_frame[pm_y0:pm_y1, pm_x0:pm_x1]
            pm_hsv = cv2.cvtColor(pm_crop, cv2.COLOR_BGR2HSV)
            b_chan_pm = pm_crop[:, :, 0].astype(int)
            # Stardew progress meter: Red/Yellow/Green (H <= 95 or H >= 165), B <= 120 (suppress deep water reflections)
            pm_filled = (
                ((pm_hsv[:, :, 0] <= 95) | (pm_hsv[:, :, 0] >= 165))
                & (b_chan_pm <= 120)
                & (pm_hsv[:, :, 1] >= 45)
                & (pm_hsv[:, :, 2] >= 110)
            )
            pm_row_matches = np.sum(pm_filled, axis=1)
            # Progress meter must be anchored at bottom
            min_rows = 5 if self._is_active else 15
            valid_bottom_rows = np.sum(pm_row_matches[-120:] >= 3)
            has_progress = int(valid_bottom_rows) >= min_rows

        # Minigame is active only if all physical invariants hold:
        active_now = is_straight_border and is_wooden_frame and has_valid_bar and bounded_paddle_ratio and has_progress
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
        # 1. Detect candidate paddle: green bobber bar paddle or white contact flash.
        green_mask = cv2.inRange(hsv, (35, 50, 50), (85, 255, 255))
        white_mask = (hsv[:, :, 1] <= 55) & (hsv[:, :, 2] >= 190)
        paddle_mask = (green_mask | (white_mask.astype(np.uint8) * 255))

        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(paddle_mask)

        # Iterate candidates, prioritizing strongest candidate with valid detect_track confirmation
        best_bbox: Optional[Tuple[int, int, int, int]] = None
        best_score = 0.0

        for i in range(1, num_labels):
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            x = stats[i, cv2.CC_STAT_LEFT]
            y = stats[i, cv2.CC_STAT_TOP]
            area = stats[i, cv2.CC_STAT_AREA]

            # In 1080p Stardew Valley:
            # - BobberBar paddle is 36 px wide (nominal 20-55 px), height 35-280 px (nominal 96-200 px),
            #   area >= 800 px (nominal ~3450 px; rejects tiny scenery specks), and vertical (h >= w - 10).
            # - Ground truth BobberBar.cs Reposition(): The minigame spawns adjacent to the player (x in [400, 1450]).
            #   Positions outside [400, 1450] (e.g. monitor edges, side scenery) are physically impossible.
            if 20 <= w <= 55 and 35 <= h <= 280 and area >= 800 and h >= (w - 10) and 400 <= x <= 1450 and 100 <= y <= 850:
                # Check for progress meter fill to the right of the track (+30 to +85 px)
                # In Stardew Valley, meter color transitions smoothly: Red -> Orange -> Yellow -> Green (zero blue!)
                pm_x0 = x + 30
                pm_x1 = min(w_frame, x + 85)
                pm_y0 = max(0, y - self.bounds.height_px - 20)
                pm_y1 = min(h_frame, y + self.bounds.height_px + 40)
                pm_crop = hsv[pm_y0:pm_y1, pm_x0:pm_x1]
                if pm_crop.size == 0:
                    continue

                b_crop = frame[pm_y0:pm_y1, pm_x0:pm_x1, 0]
                meter_mask = (
                    ((pm_crop[:, :, 0] <= 95) | (pm_crop[:, :, 0] >= 165))
                    & (b_crop <= 120)
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

                    # Select valid progress chunks whose bottom is physically consistent with the track bottom
                    valid_chunks = []
                    for chunk in chunks:
                        if len(chunk) >= 20:
                            global_bot = pm_y0 + int(chunk[-1])
                            if (y + h - 25) <= global_bot <= (y + self.bounds.height_px + 40):
                                valid_chunks.append(chunk)

                    if not valid_chunks:
                        valid_chunks = [c for c in chunks if len(c) >= 20]
                    if not valid_chunks:
                        continue

                    # The true progress bar fill is the longest contiguous run of progress rows
                    best_chunk = max(valid_chunks, key=len)
                    lowest_meter_y = pm_y0 + int(best_chunk[-1])
                    track_bot = lowest_meter_y + 4
                    track_top = track_bot - self.bounds.height_px
                    roi_x0 = max(0, x - self.bounds.x0)
                    roi_x1 = min(w_frame, roi_x0 + 190)
                    roi_y0 = max(0, track_top - self.bounds.y0)
                    roi_y1 = min(h_frame, roi_y0 + 650)

                    # Ground truth: Minigame header top is always in the upper viewport region [80, 300]
                    if not (80 <= roi_y0 <= 300):
                        continue

                    candidate_crop = frame[roi_y0:roi_y1, roi_x0:roi_x1]
                    if candidate_crop.shape[0] >= 500 and candidate_crop.shape[1] >= 150:
                        # Wooden casing check on candidate crop
                        outer_l = candidate_crop[self.bounds.y0:self.bounds.y1, max(0, self.bounds.x0 - 8):self.bounds.x0]
                        r_c = outer_l[:, :, 2].astype(int)
                        g_c = outer_l[:, :, 1].astype(int)
                        b_c = outer_l[:, :, 0].astype(int)
                        wood_ratio = float(np.mean((r_c >= g_c - 15) & (r_c - b_c >= 15))) if outer_l.size > 0 else 0.0
                        if wood_ratio < 0.35:
                            continue

                        # Force strict detection mode (min_rows=15) for candidate crops.
                        _saved_active = self._is_active
                        self._is_active = False
                        active, conf = self.detect_track(candidate_crop)
                        self._is_active = _saved_active
                        if active and conf >= 0.70:
                            score = float(area) * float(len(best_chunk))
                            if score > best_score:
                                best_bbox = (int(roi_x0), int(roi_y0), int(roi_x1), int(roi_y1))
                                best_score = score

        return best_bbox
