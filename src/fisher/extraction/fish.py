"""Fish icon tracker with contrast-invariant horizontal profile and saliency scanning."""

from __future__ import annotations

from typing import Optional, Tuple
import cv2
import numpy as np

from fisher.extraction.track import TrackDetector


class FishTracker:
    """Tracks fish vertical position (f) and velocity (f_dot) along the fishing track."""

    def __init__(
        self,
        track_detector: TrackDetector,
        f_dot_max: float = 2.5,  # max fish velocity in track units / sec
        ema_alpha: float = 0.4,
    ) -> None:
        self.track_detector = track_detector
        self.f_dot_max = f_dot_max
        self.ema_alpha = ema_alpha

        self._prev_f_norm: Optional[float] = None
        self._prev_timestamp: Optional[float] = None
        self._f_dot_norm: float = 0.0

        # Cached position (fish starts near bottom: 508 / 548 ≈ 0.07 in norm units)
        self._last_known_f: float = 0.10

    def reset(self) -> None:
        """Reset tracking state."""
        self._prev_f_norm = None
        self._prev_timestamp = None
        self._f_dot_norm = 0.0
        self._last_known_f = 0.10

    def track(
        self,
        roi_frame: np.ndarray,
        timestamp: float,
        bar_pos_norm: Optional[float] = None,
        bar_half_height_norm: Optional[float] = None,
    ) -> Tuple[float, float, float]:
        """
        Track fish position and velocity.
        Returns:
            (f_norm in [0, 1], f_dot_norm in [-1, 1], confidence in [0, 1])
        """
        if roi_frame is None or roi_frame.size == 0:
            return self._last_known_f, 0.0, 0.0

        tb = self.track_detector.bounds
        track_crop = roi_frame[tb.y0 : tb.y1, tb.x0 : tb.x1]
        if track_crop.size == 0:
            return self._last_known_f, 0.0, 0.0

        h, w = track_crop.shape[:2]
        gray = cv2.cvtColor(track_crop, cv2.COLOR_BGR2GRAY)

        # 1. Edge energy: horizontal Sobel highlights vertical sprite boundaries
        sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        edge_energy = np.mean(np.abs(sobel_x), axis=1)  # 1D profile along height (length h)

        # 2. Local vertical variance: fish sprite causes high localized variance in brightness
        # Compute rolling standard deviation with kernel ~15 px
        kernel_size = 15
        mean = cv2.blur(gray.astype(np.float32), (kernel_size, kernel_size))
        sq_mean = cv2.blur((gray.astype(np.float32)) ** 2, (kernel_size, kernel_size))
        variance = np.maximum(0.0, sq_mean - mean ** 2)
        var_profile = np.mean(np.sqrt(variance), axis=1)

        # 3. Saliency combination
        combined_profile = 0.5 * edge_energy + 0.5 * var_profile

        # Smooth combined profile with 1D Gaussian kernel
        smoothed_profile = cv2.GaussianBlur(combined_profile.reshape(-1, 1), (1, 11), 3.0).flatten()

        # Find peak
        peak_idx = int(np.argmax(smoothed_profile))
        peak_val = float(smoothed_profile[peak_idx])

        # Sub-pixel centroid in a window around peak
        win_radius = 16
        win_start = max(0, peak_idx - win_radius)
        win_end = min(h, peak_idx + win_radius + 1)
        weights = smoothed_profile[win_start:win_end]
        indices = np.arange(win_start, win_end, dtype=np.float32)

        total_weight = float(np.sum(weights))
        if total_weight > 1e-4:
            subpixel_y = float(np.sum(indices * weights) / total_weight)
            f_norm = self.track_detector.screen_y_to_norm(tb.y0 + subpixel_y)
            confidence = min(1.0, peak_val / 25.0)
            self._last_known_f = f_norm
        else:
            f_norm = self._last_known_f
            confidence = 0.2

        # 4. Velocity estimation via EMA
        f_dot_norm = 0.0
        if self._prev_f_norm is not None and self._prev_timestamp is not None:
            dt = timestamp - self._prev_timestamp
            if 0.005 < dt < 0.2:
                raw_f_dot = (f_norm - self._prev_f_norm) / dt
                inst_norm = float(np.clip(raw_f_dot / self.f_dot_max, -1.0, 1.0))
                self._f_dot_norm = self.ema_alpha * inst_norm + (1.0 - self.ema_alpha) * self._f_dot_norm
                f_dot_norm = self._f_dot_norm

        self._prev_f_norm = f_norm
        self._prev_timestamp = timestamp

        return f_norm, f_dot_norm, confidence
