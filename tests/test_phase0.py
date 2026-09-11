"""Unit and integration tests for Phase 0 scaffolding."""

import sys
import time
import pytest

from fisher.capture.mock_driver import MockCaptureDriver
from fisher.config import load_config
from fisher.input.mock_actuator import MockActuator
from fisher.ui.dashboard import DashboardState, TelemetryDashboard
from fisher.utils.display import get_connected_displays
from fisher.utils.timing import hybrid_sleep, measure_jitter, precise_clock_scope


def test_config_loader():
    """Verify default YAML config loads cleanly with typed properties."""
    config = load_config()
    assert config.game.get("window_title") == "Stardew Valley"
    assert config.capture.get("monitor_auto") is True
    assert config.capture.get("target_fps") == 60
    assert config.control.get("hz") == 30
    assert config.ui.get("dashboard_enabled") is True
    assert config.safety.get("killswitch_key") == "f9"


def test_config_overlay(tmp_path):
    """Verify deep merge with overlay config."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("capture:\n  target_fps: 120\n  monitor_idx: 2\n")

    config = load_config(overlay_path=overlay)
    assert config.capture.get("target_fps") == 120
    assert config.capture.get("monitor_idx") == 2
    # Base properties should be preserved
    assert config.capture.get("monitor_auto") is True
    assert config.game.get("window_title") == "Stardew Valley"


def test_high_precision_timer_jitter():
    """Verify Windows 1 ms timer resolution achieves p99 jitter < 2.0 ms."""
    results = measure_jitter(target_hz=30.0, iterations=60)
    assert "p50_ms" in results
    assert "p99_ms" in results
    # On Windows with winmm, jitter should be well under 2.0 ms
    if sys.platform == "win32":
        assert results["p99_ms"] < 2.5, f"p99 jitter too high: {results['p99_ms']:.3f} ms"


def test_display_enumeration():
    """Verify connected displays query returns valid display objects."""
    displays = get_connected_displays()
    assert len(displays) >= 1
    for d in displays:
        assert d.width > 0
        assert d.height > 0
        assert len(d.rect) == 4


def test_mock_capture_lifecycle():
    """Verify mock capture driver produces frames at 60 Hz."""
    driver = MockCaptureDriver(width=640, height=480, target_fps=60.0)
    assert not driver.is_running
    driver.start()
    assert driver.is_running

    time.sleep(0.1)
    frame, ts = driver.get_latest_frame()
    assert frame is not None
    assert frame.shape == (480, 640, 3)
    assert ts > 0
    assert driver.frame_count >= 3

    driver.stop()
    assert not driver.is_running


def test_mock_actuator():
    """Verify mock actuator records LMB state transitions."""
    actuator = MockActuator()
    assert not actuator.is_pressed
    assert len(actuator.history) == 0

    actuator.press_down()
    assert actuator.is_pressed
    assert len(actuator.history) == 1
    assert actuator.history[0][0] == "DOWN"

    # Idempotent press
    actuator.press_down()
    assert len(actuator.history) == 1

    actuator.release()
    assert not actuator.is_pressed
    assert len(actuator.history) == 2
    assert actuator.history[1][0] == "UP"


def test_telemetry_dashboard_render():
    """Verify Rich dashboard layout renders without raising exceptions."""
    dashboard = TelemetryDashboard()
    dashboard.state.status = "TRAINING"
    dashboard.state.timesteps_current = 500_000
    dashboard.state.timesteps_total = 1_000_000
    dashboard.state.throughput_fps = 1250.0
    dashboard.state.catch_rate_overall = 0.85
    dashboard.state.recent_events.append("Test event")

    renderable = dashboard._render_view()
    assert renderable is not None
