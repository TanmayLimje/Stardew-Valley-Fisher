from __future__ import annotations

from typing import Any, Optional, Tuple

from fisher.capture.base import CaptureDriver
from fisher.capture.bettercam_driver import BetterCamCaptureDriver
from fisher.capture.gdi_driver import GdiCaptureDriver
from fisher.capture.mock_driver import MockCaptureDriver
from fisher.config import FisherConfig, load_config


def create_capture_driver(
    config: Optional[FisherConfig] = None,
    force_driver: Optional[str] = None,
) -> CaptureDriver:
    """Factory function to instantiate capture driver according to config."""
    cfg = config or load_config()
    driver_name = force_driver or cfg.capture.get("driver", "bettercam").lower()

    roi_dict = cfg.capture.get("roi")
    roi: Optional[Tuple[int, int, int, int]] = None
    if roi_dict:
        roi = (roi_dict["x0"], roi_dict["y0"], roi_dict["x1"], roi_dict["y1"])

    target_fps = float(cfg.capture.get("target_fps", 60.0))
    mon_idx = cfg.capture.get("monitor_idx")
    window_title = cfg.game.get("window_title", "Stardew Valley")

    if driver_name == "bettercam":
        return BetterCamCaptureDriver(
            roi=roi,
            target_fps=target_fps,
            auto_bind_window=window_title,
        )
    elif driver_name in ("gdi", "mss"):
        return GdiCaptureDriver(
            monitor_idx=mon_idx,
            roi=roi,
            target_fps=target_fps,
            auto_bind_window=window_title,
        )
    elif driver_name == "mock":
        w = roi[2] - roi[0] if roi else 1920
        h = roi[3] - roi[1] if roi else 1080
        return MockCaptureDriver(width=w, height=h, target_fps=target_fps)
    else:
        raise ValueError(f"Unknown capture driver '{driver_name}'. Must be bettercam, gdi, or mock.")


__all__ = [
    "CaptureDriver",
    "BetterCamCaptureDriver",
    "GdiCaptureDriver",
    "MockCaptureDriver",
    "create_capture_driver",
]

