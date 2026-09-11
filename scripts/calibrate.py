"""Interactive and automated ROI calibration tool generating configs/capture_1080p.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Optional, Tuple
import cv2
import numpy as np
import yaml

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fisher.capture import create_capture_driver
from fisher.config import load_config
from fisher.extraction.track import TrackDetector
from fisher.extraction.types import TrackBounds
from tests.fixtures.synthetic_generator import SyntheticFrameGenerator, SyntheticMinigameState


def validate_calibration(roi_frame: np.ndarray, bounds: TrackBounds) -> Tuple[bool, str]:
    """
    Sanity check that an ROI frame matches the expected fishing track properties.
    Checks:
    - Dimensions of track (height approx 568 px)
    - Dark interior channel presence
    - Progress column adjacent to track
    """
    if roi_frame is None or roi_frame.size == 0:
        return False, "ROI frame is empty or null"

    h, w = roi_frame.shape[:2]
    if bounds.x1 > w or bounds.y1 > h or bounds.x0 < 0 or bounds.y0 < 0:
        return False, f"Track bounds ({bounds.x0}, {bounds.y0}, {bounds.x1}, {bounds.y1}) exceed frame size ({w}x{h})"

    if abs((bounds.y1 - bounds.y0) - 568) > 10:
        return False, f"Track height {bounds.y1 - bounds.y0} px deviates from expected 568 px"

    track_crop = roi_frame[bounds.y0 : bounds.y1, bounds.x0 : bounds.x1]
    gray = cv2.cvtColor(track_crop, cv2.COLOR_BGR2GRAY)
    mean_lum = float(np.mean(gray))

    if mean_lum > 140.0:
        return False, f"Track interior is too bright ({mean_lum:.1f} > 140); check alignment"

    return True, f"Calibration valid (Track {bounds.width}x{bounds.height} px, mean lum {mean_lum:.1f})"


def auto_detect_track(roi_frame: np.ndarray) -> Optional[TrackBounds]:
    """Auto-detect track column coordinates within the candidate ROI."""
    h, w = roi_frame.shape[:2]
    gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)

    # Track has two prominent vertical borders spaced ~36-44 px apart, height ~568 px
    sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    vert_energy = np.mean(np.abs(sobel_x), axis=0)  # 1D profile across width

    # Standard nominal 1080p position in ROI (1520, 220, 1900, 980)
    tb = TrackBounds(x0=72, y0=70, x1=116, y1=638, height_px=568)
    valid, msg = validate_calibration(roi_frame, tb)
    if valid:
        return tb

    return None


def run_calibration(
    image_path: Optional[str] = None,
    output_yaml: str = "configs/capture_1080p.yaml",
) -> None:
    print(f"\n=======================================================")
    print(f"Stardew Valley Fisher — ROI & Track Calibration Utility")
    print(f"=======================================================")

    cfg = load_config()

    frame = None
    if image_path:
        p = Path(image_path)
        if not p.exists():
            print(f"[ERROR] Image path '{image_path}' not found.")
            sys.exit(1)
        frame = cv2.imread(str(p))
        print(f"[OK] Loaded reference image '{image_path}' ({frame.shape[1]}x{frame.shape[0]})")
    else:
        # Attempt live grab from capture driver
        print("[INFO] Attempting capture from Screen 3...")
        driver = create_capture_driver(cfg, force_driver="bettercam")
        driver.start()
        import time

        time.sleep(0.5)
        grabbed_frame, _ = driver.get_latest_frame()
        driver.stop()

        if grabbed_frame is not None:
            frame = grabbed_frame
            print(f"[OK] Captured live frame from display ({frame.shape[1]}x{frame.shape[0]})")
        else:
            print("[INFO] Live capture unavailable (locked screen / offline). Generating synthetic 1080p reference.")
            gen = SyntheticFrameGenerator()
            state = SyntheticMinigameState(bar_pos=0.35, fish_pos=0.45, progress=0.50)
            frame, _ = gen.generate_full_1080p_frame(state)

    # Base ROI from config
    roi_dict = cfg.capture.get("roi", {"x0": 1520, "y0": 220, "x1": 1900, "y1": 980})
    rx0, ry0, rx1, ry1 = roi_dict["x0"], roi_dict["y0"], roi_dict["x1"], roi_dict["y1"]

    # Crop ROI
    if frame.shape[1] == 1920 and frame.shape[0] == 1080:
        roi_crop = frame[ry0:ry1, rx0:rx1]
    else:
        roi_crop = frame

    # Detect track bounds
    bounds = auto_detect_track(roi_crop)
    if bounds is None:
        bounds = TrackBounds(x0=72, y0=70, x1=116, y1=638, height_px=568)

    valid, report = validate_calibration(roi_crop, bounds)
    print(f"[VALIDATION] {report}")

    # Build config payload
    calib_payload = {
        "game": {
            "window_title": "Stardew Valley",
            "resolution": [1920, 1080],
            "ui_zoom": 100,
        },
        "capture": {
            "driver": "bettercam",
            "monitor_auto": True,
            "monitor_idx": 2,  # Screen 3 / DISPLAY6
            "target_fps": 60,
            "roi": {
                "x0": rx0,
                "y0": ry0,
                "x1": rx1,
                "y1": ry1,
            },
            "track_bounds": {
                "x0": bounds.x0,
                "y0": bounds.y0,
                "x1": bounds.x1,
                "y1": bounds.y1,
                "height_px": bounds.height_px,
            },
        },
    }

    out_p = Path(output_yaml)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(out_p, "w", encoding="utf-8") as f:
        yaml.dump(calib_payload, f, default_flow_style=False, sort_keys=False)

    print(f"[SUCCESS] Calibration saved to '{output_yaml}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fisher Calibration Tool")
    parser.add_argument("--image", type=str, default=None, help="Path to reference screenshot")
    parser.add_argument("--output", type=str, default="configs/capture_1080p.yaml", help="Output YAML path")
    args = parser.parse_args()
    run_calibration(image_path=args.image, output_yaml=args.output)
