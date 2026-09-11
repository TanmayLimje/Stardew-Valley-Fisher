"""Unit tests for lifecycle detectors: bite alerts, stamina levels, night cutoff, and dialogs."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from fisher.extraction.lifecycle import LifecycleDetector
from tests.fixtures.synthetic_generator import SyntheticFrameGenerator, SyntheticMinigameState


@pytest.fixture
def detector() -> LifecycleDetector:
    return LifecycleDetector()


@pytest.fixture
def generator() -> SyntheticFrameGenerator:
    return SyntheticFrameGenerator()


def test_bite_cue_detection(detector: LifecycleDetector, generator: SyntheticFrameGenerator) -> None:
    # Frame with bite cue
    state_bite = SyntheticMinigameState(bite_cue=True)
    frame_bite, _ = generator.generate_full_1080p_frame(state_bite)
    is_bite, conf = detector.detect_bite(frame_bite)
    assert is_bite is True
    assert conf > 0.5

    # Frame without bite cue
    state_no_bite = SyntheticMinigameState(bite_cue=False)
    frame_no_bite, _ = generator.generate_full_1080p_frame(state_no_bite)
    is_bite_none, _ = detector.detect_bite(frame_no_bite)
    assert is_bite_none is False


def test_stamina_gauge_detection(detector: LifecycleDetector, generator: SyntheticFrameGenerator) -> None:
    for target_pct in [15.0, 50.0, 85.0]:
        state = SyntheticMinigameState(stamina_pct=target_pct)
        frame, _ = generator.generate_full_1080p_frame(state)
        detected_pct = detector.detect_stamina(frame)
        assert abs(detected_pct - target_pct) < 10.0, f"Stamina detection error too high for {target_pct}%"


def test_clock_night_cutoff_detection(detector: LifecycleDetector, generator: SyntheticFrameGenerator) -> None:
    # Daytime frame (10:30 am)
    state_day = SyntheticMinigameState(is_night_cutoff=False)
    frame_day, _ = generator.generate_full_1080p_frame(state_day)
    cutoff_day, _ = detector.detect_clock(frame_day)
    assert cutoff_day is False

    # Late night frame (1:40 am, with red warning alert)
    state_night = SyntheticMinigameState(is_night_cutoff=True)
    frame_night, _ = generator.generate_full_1080p_frame(state_night)
    cutoff_night, _ = detector.detect_clock(frame_night)
    assert cutoff_night is True


def test_update_lifecycle_state_aggregation(detector: LifecycleDetector, generator: SyntheticFrameGenerator) -> None:
    state = SyntheticMinigameState(
        bite_cue=True,
        stamina_pct=80.0,
        is_night_cutoff=False,
    )
    frame, _ = generator.generate_full_1080p_frame(state)
    lifecycle = detector.update_lifecycle_state(frame)

    assert lifecycle.bite_detected is True
    assert lifecycle.stamina_pct > 65.0
    assert lifecycle.is_night_cutoff is False
