"""Automated test suite for Phase 3: DirectInput Actuator, LiveFishingEnv, and Live Eval."""

from __future__ import annotations

import os
from pathlib import Path
import pytest
import numpy as np

from fisher.capture.mock_driver import MockCaptureDriver
from fisher.config import load_config
from fisher.env.live_env import LiveFishingEnv
from fisher.extraction.extractor import FeatureExtractor
from fisher.extraction.types import ExtractionResult
from fisher.input.direct_input import DirectInputActuator
from fisher.input.mock_actuator import MockActuator
from scripts.eval_live import run_live_evaluation, normalize_observation


def test_direct_input_actuator_state():
    """Verify DirectInputActuator state tracking and idempotency with non-strict foreground."""
    # Using strict_foreground=False allows testing state transitions without needing physical Stardew window
    actuator = DirectInputActuator(strict_foreground=False)
    assert not actuator.is_pressed

    # Press down
    actuator.press_down()
    assert actuator.is_pressed

    # Repeated press should remain pressed
    actuator.press_down()
    assert actuator.is_pressed

    # Release
    actuator.release()
    assert not actuator.is_pressed

    # Idempotent set_press
    actuator.set_press(True)
    assert actuator.is_pressed
    actuator.set_press(True)
    assert actuator.is_pressed
    actuator.set_press(False)
    assert not actuator.is_pressed

    # Emergency release
    actuator.press_down()
    actuator.emergency_release()
    assert not actuator.is_pressed


def test_direct_input_foreground_guard():
    """Verify that strict_foreground blocks mouseDown when window is not Stardew Valley."""
    # Stardew Valley window is definitely not the foreground window in headless CI / pytest
    actuator = DirectInputActuator(window_title="NonExistentGameWindow_12345", strict_foreground=True)
    assert not actuator.is_game_foreground()

    # Attempt to press down - should be blocked by guard
    actuator.press_down()
    assert not actuator.is_pressed, "Foreground guard failed to block mouse press on un-focused window!"


def test_live_fishing_env_contract():
    """Verify LiveFishingEnv satisfies the Gymnasium environment contract."""
    cfg = load_config()
    mock_cap = MockCaptureDriver(width=190, height=650, target_fps=60.0)
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=True,
        wait_for_ui_on_reset=False,
        require_foreground=False,
    )

    try:
        assert env.action_space.n == 2
        assert env.observation_space.shape == (9,)
        assert env.observation_space.low[0] == -1.0
        assert env.observation_space.high[0] == 1.0

        obs, info = env.reset()
        assert isinstance(obs, np.ndarray)
        assert obs.shape == (9,)
        assert obs.dtype == np.float32
        assert "progress" in info
        assert "confidence" in info

        # Step with action 1 (HOLD)
        next_obs, reward, terminated, truncated, step_info = env.step(1)
        assert isinstance(next_obs, np.ndarray)
        assert next_obs.shape == (9,)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert "latency_ms" in step_info
        assert mock_act.is_pressed

        # Step with action 0 (RELEASE)
        next_obs, reward, terminated, truncated, step_info = env.step(0)
        assert not mock_act.is_pressed

        render_str = env.render()
        assert isinstance(render_str, str)
        assert "Prog:" in render_str
    finally:
        env.close()


def test_live_fishing_env_terminals():
    """Verify terminal conditions for catch, escape, and UI disappearance."""
    cfg = load_config()
    mock_cap = MockCaptureDriver(width=190, height=650, target_fps=60.0)
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=True,
        wait_for_ui_on_reset=False,
        require_foreground=False,
        ui_lost_threshold_frames=3,
    )

    try:
        env.reset()

        def make_extraction(prog: float, active: bool = True) -> ExtractionResult:
            return ExtractionResult(
                is_active=active,
                bar_pos=0.5,
                bar_height=0.1,
                bar_vel=0.0,
                fish_pos=0.5,
                fish_vel=0.0,
                progress=prog,
                in_bar=True,
                confidence=1.0,
                timestamp=0.0,
                features=np.zeros(9, dtype=np.float32),
            )

        # 1. Simulate catch terminal: progress >= 1.0 (requires step_count >= 15)
        env.step_count = 14
        env.extractor.extract_features = lambda frame, ts, prev_action=None: make_extraction(1.0, True)
        _, _, terminated, _, info = env.step(0)
        assert terminated
        assert info["is_catch"]

        # 2. Reset and simulate escape terminal: requires step_count >= min_steps_for_escape and sustained zero count
        env.reset()
        env.step_count = 24
        env.progress_zero_count = 7
        env.extractor.extract_features = lambda frame, ts, prev_action=None: make_extraction(0.0, True)
        _, _, terminated, _, info = env.step(0)
        assert terminated
        assert info["is_escape"]

        # 3. Reset and simulate UI disappearance: inactive frames trigger threshold
        env.reset()
        env.extractor.extract_features = lambda frame, ts, prev_action=None: make_extraction(0.5, False)
        env.ui_lost_count = 2  # next inactive frame hits threshold 3
        _, _, terminated, _, info = env.step(0)
        assert terminated
        assert info["ui_lost"]
    finally:
        env.close()


def test_eval_live_mock_run(tmp_path):
    """Verify that run_live_evaluation executes cleanly in mock mode."""
    summary = run_live_evaluation(
        episodes=2,
        policy_path="models/ppo_fisher_best.zip",
        use_baseline=False,
        mock_mode=True,
        output_dir=str(tmp_path),
    )

    assert summary["episodes_completed"] == 2
    assert "catch_rate_pct" in summary
    assert "latency_p50_ms" in summary
    assert "transfer_gap_pts" in summary
    assert len(summary["episodes_detail"]) == 2


def test_dynamic_widget_localization():
    """Verify TrackDetector.locate_widget detects BobberBar anywhere on 1080p screen."""
    from tests.fixtures.synthetic_generator import SyntheticFrameGenerator, SyntheticMinigameState
    from fisher.extraction.track import TrackDetector

    detector = TrackDetector()
    gen = SyntheticFrameGenerator()

    # 1. Minigame when character faces Left (X=1088, Y=220 per BobberBar.cs Reposition)
    full_frame = np.full((1080, 1920, 3), [120, 75, 30], dtype=np.uint8)
    state = SyntheticMinigameState(bar_pos=0.2, fish_pos=0.5, progress=0.30, is_active=True)
    roi, _ = gen.generate_roi_frame(state)
    full_frame[220 : 220 + roi.shape[0], 1088 : 1088 + roi.shape[1]] = roi

    bbox = detector.locate_widget(full_frame)
    assert bbox is not None
    rx0, ry0, rx1, ry1 = bbox
    assert abs(rx0 - 1088) <= 10
    assert abs(ry0 - 220) <= 50

    # 2. Minigame when character faces Right (X=720, Y=150)
    full_frame2 = np.full((1080, 1920, 3), [120, 75, 30], dtype=np.uint8)
    full_frame2[150 : 150 + roi.shape[0], 720 : 720 + roi.shape[1]] = roi
    bbox2 = detector.locate_widget(full_frame2)
    assert bbox2 is not None
    assert abs(bbox2[0] - 720) <= 10

    # 3. Pure scenery (no minigame) returns None
    inactive_frame = np.full((1080, 1920, 3), [120, 75, 30], dtype=np.uint8)
    inactive_roi, _ = gen.generate_roi_frame(SyntheticMinigameState(is_active=False))
    inactive_frame[200 : 200 + inactive_roi.shape[0], 800 : 800 + inactive_roi.shape[1]] = inactive_roi
    assert detector.locate_widget(inactive_frame) is None


def test_foreground_loss_debouncing():
    """Verify that momentary focus loss is debounced and does not prematurely abort episodes."""
    cfg = load_config()
    mock_cap = MockCaptureDriver(width=190, height=650, target_fps=60.0)
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=True,
        wait_for_ui_on_reset=False,
        require_foreground=True,
    )

    try:
        env.reset()
        # Mock DirectInputActuator behavior on foreground check
        from fisher.input.direct_input import DirectInputActuator
        fake_act = DirectInputActuator()
        fake_act.is_game_foreground = lambda: False
        env.actuator = fake_act

        # Tick 1: Focus lost, but debounce threshold (15 frames) prevents truncation
        _, _, terminated, truncated, _ = env.step(0)
        assert not truncated
        assert env.foreground_lost_count == 1

        # Ticks 2-14: Still debouncing
        for _ in range(13):
            _, _, terminated, truncated, _ = env.step(0)
            assert not truncated

        # Tick 15: Hits debounce threshold -> truncated
        _, _, terminated, truncated, _ = env.step(0)
        assert truncated
        assert env.foreground_lost_count >= 15
    finally:
        env.close()


def test_minigame_reset_requires_valid_progress():
    """Verify that LiveFishingEnv.reset() rejects dialog boxes (p=0.0) and requires valid initial progress (p >= 0.15)."""
    cfg = load_config()
    mock_cap = MockCaptureDriver(width=190, height=650, target_fps=60.0)
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=True,
        wait_for_ui_on_reset=True,
        require_foreground=False,
    )

    try:
        # Mock detector to simulate dialog box (active=True, conf=0.95, but progress=0.0)
        env.extractor.track_detector.detect_track = lambda frame: (True, 0.95)
        env.extractor.track_detector.locate_widget = lambda frame: None
        env.extractor.progress_tracker.extract = lambda frame: (0.0, 0.95)

        # reset() with 0.15s timeout should fail to find UI because p=0.0
        _, info_dialog = env.reset(options={"wait_for_ui": True, "wait_timeout": 0.15})
        assert not info_dialog["ui_found"]

        # Now simulate real minigame start (active=True, conf=0.95, progress=0.30)
        env.extractor.progress_tracker.extract = lambda frame: (0.30, 0.95)
        _, info_real = env.reset(options={"wait_for_ui": True, "wait_timeout": 0.50})
        assert info_real["ui_found"]
    finally:
        env.close()


def test_actuator_cursor_and_focus_helpers():
    """Verify DirectInputActuator focus and cursor helpers handle missing window gracefully."""
    actuator = DirectInputActuator(window_title="NonExistentWindow_99999", strict_foreground=False)
    assert not actuator.focus_game_window()
    assert not actuator.ensure_cursor_in_window()
    assert actuator.find_game_window() is None


def test_escape_debouncing_prevents_premature_abort():
    """Verify that a single-frame or multi-frame glitch to p=0.0 in early steps does not trigger escape."""
    cfg = load_config()
    mock_cap = MockCaptureDriver(width=190, height=650, target_fps=60.0)
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=True,
        wait_for_ui_on_reset=False,
        require_foreground=False,
    )

    try:
        env.reset()

        def make_extraction(prog: float) -> ExtractionResult:
            return ExtractionResult(
                is_active=True,
                bar_pos=0.3,
                bar_height=0.1,
                bar_vel=0.0,
                fish_pos=0.4,
                fish_vel=0.0,
                progress=prog,
                in_bar=True,
                confidence=1.0,
                timestamp=0.0,
                features=np.zeros(9, dtype=np.float32),
            )

        # Step 1: Normal progress p=0.60
        env.extractor.extract_features = lambda frame, ts, prev_action=None: make_extraction(0.60)
        _, _, term1, trunc1, info1 = env.step(1)
        assert not term1 and not trunc1
        assert not info1["is_escape"]

        # Step 2: Glitch drops progress to 0.0 (step_count = 2 < min_steps_for_escape)
        env.extractor.extract_features = lambda frame, ts, prev_action=None: make_extraction(0.0)
        _, _, term2, trunc2, info2 = env.step(1)
        assert not term2, "Early step glitch incorrectly terminated episode as ESCAPE!"
        assert not info2["is_escape"]

        # Step 3: Still 0.0, still step_count = 3 < 25 -> must not terminate
        _, _, term3, trunc3, info3 = env.step(1)
        assert not term3
        assert not info3["is_escape"]

        # Step 4: Progress recovers to 0.55 -> progress_zero_count resets
        env.extractor.extract_features = lambda frame, ts, prev_action=None: make_extraction(0.55)
        _, _, term4, trunc4, info4 = env.step(1)
        assert not term4
        assert env.progress_zero_count == 0
    finally:
        env.close()


def test_progress_temporal_rate_limiting():
    """Verify ProgressTracker rate-limits sudden dropouts and decays gracefully instead of jumping to 0.0."""
    from fisher.extraction.track import TrackDetector
    from fisher.extraction.progress import ProgressTracker
    from tests.fixtures.synthetic_generator import SyntheticFrameGenerator, SyntheticMinigameState

    detector = TrackDetector()
    tracker = ProgressTracker(track_detector=detector, zero_debounce_frames=6)
    gen = SyntheticFrameGenerator()

    # 1. Normal active frame with p = 0.50
    st = SyntheticMinigameState(bar_pos=0.4, fish_pos=0.4, progress=0.50, is_active=True)
    frame, _ = gen.generate_roi_frame(st)
    p1, conf1 = tracker.extract(frame)
    assert abs(p1 - 0.50) < 0.02
    assert conf1 >= 0.90

    # 2. Black/corrupt frame (dropout) - should NOT jump to 0.0, but decay smoothly by 0.006
    black = np.zeros_like(frame)
    p2, conf2 = tracker.extract(black)
    assert abs(p2 - (p1 - 0.006)) < 1e-4, f"Progress did not decay smoothly: {p2}"
    assert tracker._consecutive_zeros == 1

    # 3. Another dropout frame - decays again
    p3, conf3 = tracker.extract(black)
    assert abs(p3 - (p1 - 0.012)) < 1e-4
    assert tracker._consecutive_zeros == 2

    # 4. Progress recovers on next valid frame
    p4, conf4 = tracker.extract(frame)
    assert abs(p4 - 0.50) < 0.02
    assert tracker._consecutive_zeros == 0


def test_clock_hud_false_positive_rejected():
    """Verify TrackDetector.locate_widget rejects top-right HUD on full 1080p frame."""
    import cv2
    from fisher.extraction.track import TrackDetector
    from fisher.extraction.types import TrackBounds

    tb = TrackBounds(x0=76, y0=47, x1=112, y1=615, height_px=568)
    detector = TrackDetector(default_bounds=tb)

    # Real 1080p screenshot contains both the real minigame and the top-right clock HUD
    img = cv2.imread("data/goldens/real_screenshot_1080p.png")
    bbox = detector.locate_widget(img)

    assert bbox is not None
    rx0, ry0, rx1, ry1 = bbox
    # Must locate the real BobberBar (rx0 in [600, 800]), NOT the top-right HUD (rx0 > 1700)
    assert rx0 < 1000, f"locate_widget falsely matched the HUD at x={rx0}!"
    assert abs(rx0 - 720) <= 80
    assert abs(ry0 - 150) <= 50


def test_live_fishing_env_render_modes():
    """Verify LiveFishingEnv.render supports both ascii and rgb_array visual overlay."""
    cfg = load_config()
    mock_cap = MockCaptureDriver(width=190, height=650, target_fps=60.0)
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=True,
        wait_for_ui_on_reset=False,
        require_foreground=False,
        render_mode="rgb_array",
    )

    try:
        env.reset()
        env.step(1)

        # rgb_array render mode should produce 960x540x3 image
        frame = env.render()
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (540, 960, 3)
        assert frame.dtype == np.uint8
    finally:
        env.close()


def test_preview_overlay_on_real_frame():
    """Verify draw_preview_overlay generates valid visual telemetry on full 1080p frame."""
    import cv2
    from fisher.ui.preview import draw_preview_overlay

    img = cv2.imread("data/goldens/real_screenshot_1080p.png")
    assert img is not None

    display = draw_preview_overlay(
        full_frame=img,
        fps=30.0,
        driver_name="bettercam",
        status_text="TESTING OVERLAY",
    )
    assert isinstance(display, np.ndarray)
    assert display.shape == (540, 960, 3)


def test_locate_widget_across_full_progress_color_spectrum():
    """Verify TrackDetector.locate_widget detects minigame across all progress fill colors (Red, Yellow, Green)."""
    from tests.fixtures.synthetic_generator import SyntheticFrameGenerator, SyntheticMinigameState
    from fisher.extraction.track import TrackDetector

    gen = SyntheticFrameGenerator()
    td = TrackDetector()
    full_1080p = np.zeros((1080, 1920, 3), dtype=np.uint8)

    # Test across Red (p=0.10), Orange (p=0.25), Yellow (p=0.45), Lime (p=0.65), Green (p=0.85)
    for p in [0.10, 0.25, 0.45, 0.65, 0.85]:
        s = SyntheticMinigameState(is_active=True, bar_pos=0.5, fish_pos=0.5, progress=p)
        roi, _ = gen.generate_roi_frame(s)
        full_1080p[:] = 0
        full_1080p[100:860, 700:1080] = roi

        bbox = td.locate_widget(full_1080p)
        assert bbox is not None, f"Failed to locate BobberBar with progress={p} (color lerp failure)"
        rx0, ry0, rx1, ry1 = bbox
        assert abs(rx0 - 700) <= 20
        assert abs(ry0 - 100) <= 20


def test_live_env_static_roi_fallback_real_frame():
    """Verify LiveFishingEnv falls back to static ROI when dynamic_roi is disabled."""
    import cv2
    import time
    img = cv2.imread("data/goldens/real_screenshot_1080p.png")
    assert img is not None

    cfg = load_config()
    cfg.capture["dynamic_roi"] = False
    mock_cap = MockCaptureDriver(width=1920, height=1080, target_fps=60.0)
    mock_cap.get_latest_frame = lambda: (img.copy(), time.perf_counter())
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=False,
        wait_for_ui_on_reset=True,
        require_foreground=False,
    )
    try:
        obs, info = env.reset(options={"wait_timeout": 1.0})
        assert info["ui_found"] is True
        assert info["is_active"] is True
        assert env.current_roi == (720, 150, 910, 800)
        assert 0.05 <= info["bar_pos"] <= 0.25  # Bar is near the bottom
        assert 0.45 <= info["progress"] <= 0.65  # Progress is ~0.54, not falsely 1.0
    finally:
        env.close()


def test_live_env_dynamic_roi_default_real_frame():
    """Verify LiveFishingEnv defaults to dynamic ROI on 1080p real frame."""
    import cv2
    import time
    img = cv2.imread("data/goldens/real_screenshot_1080p.png")
    assert img is not None

    cfg = load_config()
    mock_cap = MockCaptureDriver(width=1920, height=1080, target_fps=60.0)
    mock_cap.get_latest_frame = lambda: (img.copy(), time.perf_counter())
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=False,
        wait_for_ui_on_reset=True,
        require_foreground=False,
    )
    try:
        obs, info = env.reset(options={"wait_timeout": 1.0})
        assert info["ui_found"] is True
        assert info["is_active"] is True
        assert env.current_roi == (720, 161, 910, 811)
        assert 0.05 <= info["bar_pos"] <= 0.25
        assert 0.45 <= info["progress"] <= 0.65
    finally:
        env.close()


def test_live_env_dynamic_roi_posing_frame():
    """Verify LiveFishingEnv dynamically locates BobberBar when character changes posing/facing direction."""
    import cv2
    import time
    img = cv2.imread("data/goldens/screen3_native_posing.png")
    assert img is not None

    cfg = load_config()
    mock_cap = MockCaptureDriver(width=1920, height=1080, target_fps=60.0)
    mock_cap.get_latest_frame = lambda: (img.copy(), time.perf_counter())
    mock_act = MockActuator()

    env = LiveFishingEnv(
        config=cfg,
        capture_driver=mock_cap,
        actuator=mock_act,
        auto_start_capture=False,
        wait_for_ui_on_reset=True,
        require_foreground=False,
    )
    try:
        obs, info = env.reset(options={"wait_timeout": 3.0})
        assert info["ui_found"] is True
        assert info["is_active"] is True
        # Dynamically located on right half of Screen 3
        rx0, ry0, rx1, ry1 = env.current_roi
        assert 1000 <= rx0 <= 1100
        assert 150 <= ry0 <= 200
        assert 0.05 <= info["bar_pos"] <= 0.30
    finally:
        env.close()



