"""Unified CV feature extractor building the 9-d observation vector for RL policy inference."""

from __future__ import annotations

import time
from typing import Optional, Tuple
import numpy as np

from fisher.extraction.bar import BobberBarExtractor
from fisher.extraction.fish import FishTracker
from fisher.extraction.progress import ProgressTracker
from fisher.extraction.track import TrackDetector
from fisher.extraction.types import ExtractionResult, TrackBounds


class FeatureExtractor:
    """Coordinates track localization, bobber bar extraction, fish tracking,

    and progress tracking to produce the 9-dimensional normalized state vector.
    """

    def __init__(
        self,
        bounds: Optional[TrackBounds] = None,
        debounce_frames: int = 12,
    ) -> None:
        self.track_detector = TrackDetector(default_bounds=bounds, debounce_frames=debounce_frames)
        self.bar_extractor = BobberBarExtractor(track_detector=self.track_detector)
        self.fish_tracker = FishTracker(track_detector=self.track_detector)
        self.progress_tracker = ProgressTracker(track_detector=self.track_detector)

        self._prev_action: int = 0
        self._last_extraction: Optional[ExtractionResult] = None

    def reset(self) -> None:
        """Reset internal trackers for a new fishing minigame episode."""
        self.track_detector.reset()
        self.bar_extractor.reset()
        self.fish_tracker.reset()
        self.progress_tracker.reset()
        self._prev_action = 0
        self._last_extraction = None

    def extract_features(
        self,
        roi_frame: np.ndarray,
        timestamp: Optional[float] = None,
        prev_action: Optional[int] = None,
    ) -> ExtractionResult:
        """
        Process a single ROI frame and return an ExtractionResult with the 9-d observation.

        Args:
            roi_frame: BGR numpy array cropped to the fishing minigame ROI.
            timestamp: Capture timestamp (defaults to time.perf_counter()).
            prev_action: Most recent binary action dispatched (0: RELEASE, 1: HOLD).
        """
        ts = timestamp if timestamp is not None else time.perf_counter()
        if prev_action is not None:
            self._prev_action = int(prev_action)

        # 1. Detect if minigame track is active
        is_active, track_conf = self.track_detector.detect_track(roi_frame)
        if not is_active:
            features = np.zeros(9, dtype=np.float32)
            features[5] = 2.0 * 0.30 - 1.0  # default progress p=0.30
            features[8] = float(self._prev_action)
            res = ExtractionResult(
                is_active=False,
                bar_pos=0.0845,
                bar_height=0.0845,
                bar_vel=0.0,
                fish_pos=0.10,
                fish_vel=0.0,
                progress=0.30,
                in_bar=False,
                confidence=track_conf,
                timestamp=ts,
                features=features,
            )
            self._last_extraction = res
            return res

        # 2. Extract Bobber Bar (b, h, v)
        b, h, v, is_white_flash = self.bar_extractor.extract(roi_frame, ts)

        # 3. Track Fish (f, f_dot)
        f, f_dot, fish_conf = self.fish_tracker.track(
            roi_frame,
            ts,
            bar_pos_norm=b,
            bar_half_height_norm=h,
        )

        # 4. Extract Catch Progress (p)
        p, prog_conf = self.progress_tracker.extract(roi_frame)

        # 5. In-bar overlap indicator
        in_bar_exact = abs(f - b) <= h
        in_bar_state = in_bar_exact or is_white_flash
        in_bar_float = 1.0 if in_bar_state else 0.0

        # Signed error delta = f - b
        delta = f - b

        # 6. Build the 9-dimensional normalized observation vector per Table 4.1:
        # [2b - 1, v, 2f - 1, f_dot, 2h - 1, 2p - 1, I, clip(2*delta, -1, 1), a_prev]
        obs = np.array(
            [
                float(np.clip(2.0 * b - 1.0, -1.0, 1.0)),
                float(np.clip(v, -1.0, 1.0)),
                float(np.clip(2.0 * f - 1.0, -1.0, 1.0)),
                float(np.clip(f_dot, -1.0, 1.0)),
                float(np.clip(2.0 * h - 1.0, -1.0, 1.0)),
                float(np.clip(2.0 * p - 1.0, -1.0, 1.0)),
                in_bar_float,
                float(np.clip(2.0 * delta, -1.0, 1.0)),
                float(self._prev_action),
            ],
            dtype=np.float32,
        )

        overall_conf = float(np.mean([track_conf, fish_conf, prog_conf]))

        result = ExtractionResult(
            is_active=True,
            bar_pos=b,
            bar_height=h,
            bar_vel=v,
            fish_pos=f,
            fish_vel=f_dot,
            progress=p,
            in_bar=in_bar_state,
            confidence=overall_conf,
            timestamp=ts,
            features=obs,
        )

        self._last_extraction = result
        return result
