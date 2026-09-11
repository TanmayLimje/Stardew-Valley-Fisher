"""Unit tests for SimulatedFish kinematics matching BobberBar.cs."""

import pytest
from fisher.sim.fish import FishConfig, FishMotionType, SimulatedFish


def test_fish_initialization():
    """Verify fish initial state matches BobberBar.cs constructor."""
    fish = SimulatedFish(FishConfig(difficulty=50.0))
    assert fish.pos == 508.0
    assert fish.speed == 0.0
    # Target = (100 - 50) / 100 * 548 = 274.0
    assert abs(fish.target_pos - 274.0) < 1e-5


def test_fish_clamp_bounds():
    """Verify fish position is clamped to [0, 532]."""
    fish = SimulatedFish(FishConfig(difficulty=100.0), seed=42)
    for _ in range(1200):
        pos = fish.step()
        assert 0.0 <= pos <= 532.0


def test_five_archetypes_run():
    """Verify all 5 archetypes step cleanly for 600 ticks (10s @ 60 Hz)."""
    for m in FishMotionType:
        fish = SimulatedFish(FishConfig(difficulty=60.0, motion_type=m), seed=123)
        for _ in range(600):
            pos = fish.step()
            assert 0.0 <= pos <= 532.0


def test_sinker_floater_acceleration_direction():
    """Verify Sinker drifts downward (positive accel) and Floater drifts upward (negative accel)."""
    floater = SimulatedFish(FishConfig(difficulty=40.0, motion_type=FishMotionType.FLOATER), seed=1)
    sinker = SimulatedFish(FishConfig(difficulty=40.0, motion_type=FishMotionType.SINKER), seed=1)

    for _ in range(60):
        floater.step()
        sinker.step()

    # Floater floater_sinker_accel should be negative (upwards)
    assert floater.floater_sinker_accel < 0.0
    # Sinker floater_sinker_accel should be positive (downwards)
    assert sinker.floater_sinker_accel > 0.0


def test_in_bar_detection():
    """Verify in-bar detection logic matches BobberBar.cs lines 417-421."""
    fish = SimulatedFish()
    bar_height = 96.0

    # Fish at 200 px.
    # In-bar condition:
    # fish.pos + 12 <= bar_pos - 32 + bar_height  => 212 <= bar_pos + 64 => bar_pos >= 148
    # fish.pos - 16 >= bar_pos - 32               => 184 >= bar_pos - 32 => bar_pos <= 216
    fish.pos = 200.0

    # Test center: bar_pos = 182
    assert fish.is_in_bar(bar_pos_px=182.0, bar_height_px=bar_height)

    # Test outside: bar_pos = 100
    assert not fish.is_in_bar(bar_pos_px=100.0, bar_height_px=bar_height)

    # Test bottom edge assist (lines 418-421)
    # fish.pos >= 548 - 96 = 452
    # bar_pos >= 568 - 96 - 4 = 468
    fish.pos = 460.0
    assert fish.is_in_bar(bar_pos_px=470.0, bar_height_px=bar_height)
