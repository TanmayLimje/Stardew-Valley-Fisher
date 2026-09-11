"""Unit tests for BobberBarPhysics engine grounded in BobberBar.cs."""

import pytest
from fisher.sim.physics import BarPhysicsConfig, BobberBarPhysics


def test_bar_initial_position():
    """Verify bar initializes at the bottom bound (568 - bar_height)."""
    physics = BobberBarPhysics(BarPhysicsConfig(track_height=568.0, bar_height=96.0))
    assert physics.bar_pos == 472.0
    assert physics.bar_speed == 0.0

    # In normalized coordinates, bottom bound corresponds to half-height h
    h = physics.bar_half_height_norm
    assert abs(physics.bar_center_norm - h) < 1e-5


def test_free_fall_acceleration():
    """Verify free fall under gravity adds exactly 0.25 px/tick^2."""
    physics = BobberBarPhysics(BarPhysicsConfig(track_height=568.0, bar_height=96.0, gravity=0.25))
    physics.reset(initial_pos_px=200.0, initial_speed_px=0.0)

    pos, speed = physics.step(button_pressed=False, in_bar=False)
    assert speed == 0.25
    assert pos == 200.25

    pos, speed = physics.step(button_pressed=False, in_bar=False)
    assert speed == 0.50
    assert pos == 200.75


def test_in_bar_gravity_scaling():
    """Verify when in_bar=True, acceleration scales by 0.6x (0.15f px/tick^2)."""
    physics = BobberBarPhysics(BarPhysicsConfig(gravity=0.25, in_bar_factor=0.6))
    physics.reset(initial_pos_px=200.0, initial_speed_px=0.0)

    pos, speed = physics.step(button_pressed=False, in_bar=True)
    assert abs(speed - 0.15) < 1e-6
    assert abs(pos - 200.15) < 1e-6


def test_upward_thrust():
    """Verify holding button applies negative (upward) acceleration -0.25 px/tick^2."""
    physics = BobberBarPhysics(BarPhysicsConfig(gravity=0.25))
    physics.reset(initial_pos_px=200.0, initial_speed_px=0.0)

    pos, speed = physics.step(button_pressed=True, in_bar=False)
    assert speed == -0.25
    assert pos == 199.75


def test_bottom_bounce_restitution():
    """Verify bottom bound collision reverses velocity with 2/3 restitution."""
    physics = BobberBarPhysics(BarPhysicsConfig(track_height=568.0, bar_height=96.0, restitution_bottom=2.0 / 3.0))
    max_pos = 472.0
    # Position right at edge with downward speed 6.0
    physics.reset(initial_pos_px=470.0, initial_speed_px=6.0)

    pos, speed = physics.step(button_pressed=False)
    # Next pos would be 470 + (6.0 + 0.25) = 476.25 > 472.0
    # Clamps to 472.0, speed becomes -(6.25) * (2/3)
    assert pos == max_pos
    expected_speed = -6.25 * (2.0 / 3.0)
    assert abs(speed - expected_speed) < 1e-5


def test_top_bounce_restitution():
    """Verify top bound collision reverses velocity with 2/3 restitution."""
    physics = BobberBarPhysics(BarPhysicsConfig(track_height=568.0, bar_height=96.0, restitution_top=2.0 / 3.0))
    physics.reset(initial_pos_px=2.0, initial_speed_px=-6.0)

    pos, speed = physics.step(button_pressed=True)
    # Next speed = -6.25, pos = 2.0 - 6.25 = -4.25 < 0.0
    assert pos == 0.0
    expected_speed = -(-6.25) * (2.0 / 3.0)
    assert abs(speed - expected_speed) < 1e-5


def test_boundary_pinning_zeroes_velocity():
    """Verify holding button while pinned zeroes velocity (BobberBar.cs line 429)."""
    physics = BobberBarPhysics(BarPhysicsConfig(track_height=568.0, bar_height=96.0))
    # Pin at bottom: pos = 472.0, prior downward speed = 8.0
    # Holding button zeroes speed, then applies -0.25 upward accel -> speed = -0.25
    physics.reset(initial_pos_px=472.0, initial_speed_px=8.0)
    pos, speed = physics.step(button_pressed=True)
    assert abs(speed - (-0.25)) < 1e-5
    assert abs(pos - (472.0 - 0.25)) < 1e-5

    # Pin at top: pos = 0.0, prior downward speed = 4.0
    # Holding button zeroes speed, then applies -0.25, hits top, bounces with 2/3
    physics.reset(initial_pos_px=0.0, initial_speed_px=4.0)
    pos, speed = physics.step(button_pressed=True)
    assert pos == 0.0
    # -(-0.25) * (2/3) = 0.166667
    assert abs(speed - (0.25 * 2.0 / 3.0)) < 1e-5


def test_lead_bobber_restitution():
    """Verify lead bobber reduces bottom bounce by 0.1x."""
    physics = BobberBarPhysics(BarPhysicsConfig(track_height=568.0, bar_height=96.0, lead_bobber=True))
    physics.reset(initial_pos_px=470.0, initial_speed_px=6.0)
    pos, speed = physics.step(button_pressed=False)
    assert pos == 472.0
    expected_speed = -6.25 * (2.0 / 3.0) * 0.1
    assert abs(speed - expected_speed) < 1e-5
