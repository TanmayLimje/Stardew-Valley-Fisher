"""Unit tests for screen capture drivers and factory."""

from __future__ import annotations

import time
import pytest

from fisher.capture import (
    BetterCamCaptureDriver,
    GdiCaptureDriver,
    MockCaptureDriver,
    create_capture_driver,
)
from fisher.config import load_config


def test_mock_capture_lifecycle() -> None:
    driver = MockCaptureDriver(width=380, height=760, target_fps=60.0)
    assert not driver.is_running
    assert driver.frame_count == 0

    driver.start()
    assert driver.is_running

    time.sleep(0.1)
    frame, ts = driver.get_latest_frame()
    assert frame is not None
    assert frame.shape == (760, 380, 3)
    assert ts > 0.0
    assert driver.frame_count > 0

    driver.stop()
    assert not driver.is_running


def test_create_capture_driver_factory() -> None:
    cfg = load_config()

    # Mock driver
    driver_mock = create_capture_driver(cfg, force_driver="mock")
    assert isinstance(driver_mock, MockCaptureDriver)

    # GDI driver
    driver_gdi = create_capture_driver(cfg, force_driver="gdi")
    assert isinstance(driver_gdi, GdiCaptureDriver)

    # BetterCam driver — may auto-fallback to GDI if DXGI is unavailable (non-admin context)
    driver_bc = create_capture_driver(cfg, force_driver="bettercam")
    assert isinstance(driver_bc, (BetterCamCaptureDriver, GdiCaptureDriver))

    # Invalid driver
    with pytest.raises(ValueError, match="Unknown capture driver"):
        create_capture_driver(cfg, force_driver="invalid_driver_name")


def test_bettercam_driver_defensive_init() -> None:
    driver = BetterCamCaptureDriver(
        device_idx=0,
        output_idx=1,
        roi=(1520, 220, 1900, 980),
        target_fps=60.0,
    )
    assert not driver.is_running
    assert driver.frame_count == 0
    # Calling get_latest_frame before start should return (None, 0.0)
    frame, ts = driver.get_latest_frame()
    assert frame is None
    assert ts == 0.0


def test_gdi_driver_rect_resolution() -> None:
    driver = GdiCaptureDriver(
        monitor_idx=0,
        roi=(100, 100, 300, 400),
        target_fps=60.0,
    )
    left, top, width, height = driver._resolve_capture_rect()
    assert width == 200
    assert height == 300
