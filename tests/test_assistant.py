"""Automated test suite for the on-demand Fishing Assistant."""

from __future__ import annotations

import time
from typing import Any
import numpy as np
import pytest

from fisher.capture.mock_driver import MockCaptureDriver
from fisher.config import load_config
from fisher.extraction.extractor import FeatureExtractor
from fisher.extraction.types import ExtractionResult
from fisher.input.direct_input import DirectInputActuator
from fisher.input.mock_actuator import MockActuator
from fisher.orchestration.assistant import (
    AssistantState,
    FishingAssistant,
    MinigameResult,
)
from fisher.orchestration.safety import SafetySupervisor


def _make_extraction(
    prog: float = 0.5,
    active: bool = True,
    in_bar: bool = True,
    bar_pos: float = 0.5,
    fish_pos: float = 0.5,
) -> ExtractionResult:
    return ExtractionResult(
        is_active=active,
        bar_pos=bar_pos,
        bar_height=0.15,
        bar_vel=0.0,
        fish_pos=fish_pos,
        fish_vel=0.0,
        progress=prog,
        in_bar=in_bar,
        confidence=0.95,
        timestamp=time.perf_counter(),
        features=np.zeros(9, dtype=np.float32),
    )


def _build_test_assistant(
    detection_confirm_frames: int = 2,
    idle_scan_hz: float = 100.0,
    transition_delay_ms: float = 0.0,
    require_foreground: bool = False,
    actuator=None,
    **kwargs,
) -> tuple[FishingAssistant, MockCaptureDriver, Any, FeatureExtractor, SafetySupervisor]:
    config = load_config()
    config.control["hz"] = 300
    mock_cap = MockCaptureDriver(width=190, height=650, target_fps=120.0)
    mock_cap._latest_frame = np.zeros((650, 190, 3), dtype=np.uint8)
    mock_cap._latest_timestamp = time.perf_counter()
    mock_act = actuator if actuator is not None else MockActuator()
    extractor = FeatureExtractor()
    supervisor = SafetySupervisor(poll_hz=100.0)

    assistant = FishingAssistant(
        config=config,
        capture_driver=mock_cap,
        actuator=mock_act,
        extractor=extractor,
        policy=None,
        supervisor=supervisor,
        idle_scan_hz=idle_scan_hz,
        detection_confirm_frames=detection_confirm_frames,
        transition_delay_ms=transition_delay_ms,
        show_notifications=False,
        require_foreground=require_foreground,
        **kwargs,
    )
    return assistant, mock_cap, mock_act, extractor, supervisor


def test_idle_scan_detects_minigame():
    """Verify idle scan triggers activation after detection_confirm_frames consecutive detections."""
    assistant, mock_cap, mock_act, extractor, supervisor = _build_test_assistant(
        detection_confirm_frames=2
    )

    extractor.track_detector.detect_track = lambda frame: (True, 0.95)
    extractor.progress_tracker.extract = lambda frame: (0.30, 0.95)

    detected = assistant._idle_scan(max_frames=5)
    assert detected is True


def test_idle_scan_ignores_false_positives():
    """Verify single-frame UI flash or low-progress false positive does not trigger activation."""
    assistant, mock_cap, mock_act, extractor, supervisor = _build_test_assistant(
        detection_confirm_frames=2
    )

    # 1. Single frame flash followed by non-minigame frames
    flash_count = 0

    def mock_detect(frame):
        nonlocal flash_count
        flash_count += 1
        if flash_count == 1:
            return (True, 0.95)
        return (False, 0.0)

    extractor.track_detector.detect_track = mock_detect
    extractor.progress_tracker.extract = lambda frame: (0.30, 0.95)

    detected = assistant._idle_scan(max_frames=5)
    assert detected is False

    # 2. Track detected but progress is too low (e.g. dialog box, p < 0.15)
    extractor.track_detector.detect_track = lambda frame: (True, 0.95)
    extractor.progress_tracker.extract = lambda frame: (0.05, 0.95)

    detected_low_prog = assistant._idle_scan(max_frames=5)
    assert detected_low_prog is False


def test_rl_active_returns_to_idle():
    """Verify assistant completes minigame (catch) and returns to IDLE state."""
    assistant, mock_cap, mock_act, extractor, supervisor = _build_test_assistant()

    step_counter = 0

    def step_extraction(frame, ts, prev_action=None):
        nonlocal step_counter
        step_counter += 1
        prog = 0.30 + (step_counter * 0.05)
        if step_counter >= 16:
            prog = 1.0
        return _make_extraction(prog=prog, active=True)

    extractor.extract_features = step_extraction

    result = assistant._play_minigame()
    assert result.caught is True
    assert result.steps >= 15
    assert not mock_act.is_pressed


def test_mouse_released_on_exit():
    """Verify actuator LMB is always released regardless of exit condition (catch, escape, error)."""
    assistant, mock_cap, mock_act, extractor, supervisor = _build_test_assistant()

    # Case A: Escape exit
    step_counter = 0

    def escape_extraction(frame, ts, prev_action=None):
        nonlocal step_counter
        step_counter += 1
        return _make_extraction(prog=0.0, active=True)

    extractor.extract_features = escape_extraction

    mock_act.press_down()
    assert mock_act.is_pressed

    result = assistant._play_minigame()
    assert not mock_act.is_pressed
    assert result.escaped is True

    # Case B: Error during minigame
    mock_act.press_down()
    assert mock_act.is_pressed

    def error_extraction(frame, ts, prev_action=None):
        raise RuntimeError("Simulated perception crash")

    extractor.extract_features = error_extraction

    result_err = assistant._play_minigame()
    assert not mock_act.is_pressed
    assert result_err.truncated is True
    assert "Simulated perception crash" in result_err.termination_reason


def test_killswitch_stops_assistant():
    """Verify supervisor abort request stops assistant cleanly and releases mouse."""
    # Scenario 1: Abort requested before run starts
    assistant, mock_cap, mock_act, extractor, supervisor = _build_test_assistant()
    supervisor.request_abort("killswitch (f9)")
    assert supervisor.is_abort_requested()

    stats = assistant.run()
    assert assistant.state == AssistantState.STOPPED
    assert not mock_act.is_pressed
    assert stats.minigames_played == 0

    # Scenario 2: Abort requested during active minigame
    assistant2, mock_cap2, mock_act2, extractor2, supervisor2 = _build_test_assistant()
    step_count = 0

    def aborting_features(frame, ts, prev_action=None):
        nonlocal step_count
        step_count += 1
        if step_count >= 3:
            supervisor2.request_abort("killswitch (f9)")
        return _make_extraction(prog=0.5, active=True)

    extractor2.extract_features = aborting_features
    mock_act2.press_down()

    result = assistant2._play_minigame()
    assert result.truncated is True
    assert result.termination_reason == "killswitch"
    assert not mock_act2.is_pressed


def test_foreground_loss_pauses():
    """Verify losing game window foreground during minigame truncates episode and returns to IDLE without process abort."""
    fake_act = DirectInputActuator(strict_foreground=False)
    fake_act.is_game_foreground = lambda: False

    assistant, mock_cap, _, extractor, supervisor = _build_test_assistant(
        require_foreground=True,
        actuator=fake_act,
    )

    step_counter = 0

    def normal_extraction(frame, ts, prev_action=None):
        nonlocal step_counter
        step_counter += 1
        return _make_extraction(prog=0.5, active=True)

    extractor.extract_features = normal_extraction

    result = assistant._play_minigame()
    assert result.truncated is True
    assert not fake_act.is_pressed
    assert not supervisor.is_abort_requested()


def test_full_session_two_minigames():
    """Verify end-to-end multi-episode session: IDLE -> Minigame 1 -> IDLE -> Minigame 2 -> Stop."""
    assistant, mock_cap, mock_act, extractor, supervisor = _build_test_assistant(
        detection_confirm_frames=1,
    )
    assistant.config.safety["max_episodes"] = 2

    step_count = 0

    extractor.track_detector.detect_track = lambda frame: (True, 0.95)
    extractor.progress_tracker.extract = lambda frame: (0.50, 0.95)

    def dynamic_features(frame, ts, prev_action=None):
        nonlocal step_count
        step_count += 1
        if step_count >= 16:
            return _make_extraction(prog=1.0, active=True)
        return _make_extraction(prog=0.50 + step_count * 0.02, active=True)

    extractor.extract_features = dynamic_features

    orig_play = assistant._play_minigame

    def wrapped_play():
        nonlocal step_count
        step_count = 0
        return orig_play()

    assistant._play_minigame = wrapped_play

    stats = assistant.run()
    assert stats.minigames_played == 2
    assert stats.catches == 2
    assert assistant.state == AssistantState.STOPPED
    assert not mock_act.is_pressed
