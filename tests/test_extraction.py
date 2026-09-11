"""Comprehensive unit tests and golden-frame validation for CV feature extractor."""

from __future__ import annotations

import numpy as np
import pytest

from fisher.extraction.extractor import FeatureExtractor
from tests.fixtures.synthetic_generator import SyntheticFrameGenerator, SyntheticMinigameState


@pytest.fixture
def extractor() -> FeatureExtractor:
    return FeatureExtractor()


@pytest.fixture
def generator() -> SyntheticFrameGenerator:
    return SyntheticFrameGenerator()


def test_ui_presence_detection(extractor: FeatureExtractor, generator: SyntheticFrameGenerator) -> None:
    # 1. Active minigame frame
    state_active = SyntheticMinigameState(is_active=True, bar_pos=0.30, fish_pos=0.50)
    frame_active, _ = generator.generate_roi_frame(state_active)
    res_active = extractor.extract_features(frame_active)
    assert res_active.is_active is True
    assert res_active.confidence > 0.5

    # 2. Inactive scene (e.g. pond water with no fishing UI)
    state_inactive = SyntheticMinigameState(is_active=False)
    frame_inactive, _ = generator.generate_roi_frame(state_inactive)
    # Debounce requires consecutive misses to flip to False
    for _ in range(15):
        res_inactive = extractor.extract_features(frame_inactive)
    assert res_inactive.is_active is False


def test_bobber_bar_extraction_accuracy(extractor: FeatureExtractor, generator: SyntheticFrameGenerator) -> None:
    """Validate bar position (b) and half-height (h) extraction across position sweep."""
    positions = [0.15, 0.30, 0.45, 0.60, 0.75, 0.85]
    for b_target in positions:
        extractor.reset()
        # Keep fish far away to avoid white flash in pure bar test
        state = SyntheticMinigameState(
            bar_pos=b_target,
            bar_half_height=0.0845,
            fish_pos=min(0.95, b_target + 0.35),
            progress=0.40,
        )
        frame, gt = generator.generate_roi_frame(state)
        res = extractor.extract_features(frame)

        assert res.is_active is True
        error_b = abs(res.bar_pos - gt["bar_pos"])
        assert error_b < 0.02, f"Bar pos error too high at {b_target}: {error_b:.4f}"
        error_h = abs(res.bar_height - gt["bar_half_height"])
        assert error_h < 0.02, f"Bar half-height error too high: {error_h:.4f}"


def test_fish_tracking_accuracy(extractor: FeatureExtractor, generator: SyntheticFrameGenerator) -> None:
    """Validate fish vertical position tracking (f) across position sweep."""
    positions = [0.15, 0.30, 0.45, 0.60, 0.75, 0.85]
    for f_target in positions:
        extractor.reset()
        state = SyntheticMinigameState(
            bar_pos=0.10,  # keep bar at bottom
            fish_pos=f_target,
            progress=0.40,
        )
        frame, gt = generator.generate_roi_frame(state)
        res = extractor.extract_features(frame)

        assert res.is_active is True
        error_f = abs(res.fish_pos - gt["fish_pos"])
        assert error_f < 0.03, f"Fish pos error too high at {f_target}: {error_f:.4f}"


def test_in_bar_white_flash_handling(extractor: FeatureExtractor, generator: SyntheticFrameGenerator) -> None:
    """Verify that when the fish is in-bar and the bar turns white, both bar and fish are tracked."""
    extractor.reset()
    state = SyntheticMinigameState(
        bar_pos=0.50,
        bar_half_height=0.0845,
        fish_pos=0.52,  # inside the bar (|f - b| = 0.02 <= 0.0845)
        progress=0.60,
        in_bar_flash=True,
    )
    frame, gt = generator.generate_roi_frame(state)
    res = extractor.extract_features(frame)

    assert res.is_active is True
    assert res.in_bar is True
    assert abs(res.bar_pos - 0.50) < 0.025
    assert abs(res.fish_pos - 0.52) < 0.035
    assert res.features[6] == 1.0  # in_bar feature flag


def test_progress_meter_gradient_sweep(extractor: FeatureExtractor, generator: SyntheticFrameGenerator) -> None:
    """Verify catch progress meter accuracy across red, orange, and green levels."""
    progress_levels = [0.10, 0.25, 0.45, 0.70, 0.85, 0.95]
    for p_target in progress_levels:
        state = SyntheticMinigameState(
            bar_pos=0.40,
            fish_pos=0.50,
            progress=p_target,
        )
        frame, gt = generator.generate_roi_frame(state)
        res = extractor.extract_features(frame)

        assert res.is_active is True
        error_p = abs(res.progress - p_target)
        assert error_p < 0.025, f"Progress error too high at {p_target}: {error_p:.4f}"


def test_weather_and_sparkle_invariance(extractor: FeatureExtractor, generator: SyntheticFrameGenerator) -> None:
    """Verify robustness against night darkness, rain streaks, and sparkles."""
    for weather in ["clear", "night", "rain"]:
        extractor.reset()
        state = SyntheticMinigameState(
            bar_pos=0.45,
            fish_pos=0.65,
            progress=0.55,
            weather=weather,
            sparkles=True,
        )
        frame, gt = generator.generate_roi_frame(state)
        res = extractor.extract_features(frame)

        assert res.is_active is True
        assert abs(res.bar_pos - 0.45) < 0.025
        assert abs(res.fish_pos - 0.65) < 0.035
        assert abs(res.progress - 0.55) < 0.025


def test_observation_vector_contract(extractor: FeatureExtractor, generator: SyntheticFrameGenerator) -> None:
    """Verify that the generated 9-d observation conforms to Gymnasium Box([-1, 1])."""
    state = SyntheticMinigameState(bar_pos=0.35, fish_pos=0.65, progress=0.75)
    frame, _ = generator.generate_roi_frame(state)
    res = extractor.extract_features(frame, prev_action=1)

    obs = res.features
    assert isinstance(obs, np.ndarray)
    assert obs.shape == (9,)
    assert obs.dtype == np.float32

    # Check bounds
    assert np.all(obs >= -1.0)
    assert np.all(obs <= 1.0)

    # Feature 0: 2b - 1
    assert abs(obs[0] - (2.0 * res.bar_pos - 1.0)) < 1e-4
    # Feature 2: 2f - 1
    assert abs(obs[2] - (2.0 * res.fish_pos - 1.0)) < 1e-4
    # Feature 4: 2h - 1
    assert abs(obs[4] - (2.0 * res.bar_height - 1.0)) < 1e-4
    # Feature 5: 2p - 1
    assert abs(obs[5] - (2.0 * res.progress - 1.0)) < 1e-4
    # Feature 6: in_bar indicator in {0, 1}
    assert obs[6] in (0.0, 1.0)
    # Feature 8: prev_action in {0, 1}
    assert obs[8] == 1.0


def test_golden_suite_benchmark(extractor: FeatureExtractor, generator: SyntheticFrameGenerator) -> None:
    """Run 60 randomized golden frames and verify >= 99% accuracy across all metrics."""
    rng = np.random.default_rng(seed=42)
    success_count = 0
    total_tests = 60

    for i in range(total_tests):
        extractor.reset()
        b_val = float(rng.uniform(0.15, 0.85))
        f_val = float(rng.uniform(0.15, 0.85))
        p_val = float(rng.uniform(0.10, 0.90))
        weather = str(rng.choice(["clear", "night", "rain"]))
        sparkles = bool(rng.choice([True, False]))

        state = SyntheticMinigameState(
            bar_pos=b_val,
            fish_pos=f_val,
            progress=p_val,
            weather=weather,
            sparkles=sparkles,
        )
        frame, gt = generator.generate_roi_frame(state)
        res = extractor.extract_features(frame)

        b_ok = abs(res.bar_pos - b_val) < 0.03
        f_ok = abs(res.fish_pos - f_val) < 0.04
        p_ok = abs(res.progress - p_val) < 0.03

        if res.is_active and b_ok and f_ok and p_ok:
            success_count += 1

    accuracy = success_count / total_tests
    print(f"\nGolden Suite Accuracy: {accuracy * 100.0:.1f}% ({success_count}/{total_tests})")
    assert accuracy >= 0.99, f"Golden suite accuracy {accuracy:.3f} fell below 0.99 threshold"
