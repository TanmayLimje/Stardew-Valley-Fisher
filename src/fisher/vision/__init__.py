"""Transparent proxy re-exporting extraction components for fisher.vision compatibility."""

from fisher.extraction import (
    BobberBarExtractor,
    ExtractionResult,
    FeatureExtractor,
    FishTracker,
    LifecycleDetector,
    LifecycleState,
    ProgressTracker,
    TrackBounds,
    TrackDetector,
)

__all__ = [
    "FeatureExtractor",
    "TrackDetector",
    "BobberBarExtractor",
    "FishTracker",
    "ProgressTracker",
    "LifecycleDetector",
    "ExtractionResult",
    "LifecycleState",
    "TrackBounds",
]
