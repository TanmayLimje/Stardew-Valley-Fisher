"""Diagnostic script: captures a live frame and dumps every detection stage result.

Run while the fishing minigame is visible on Screen 3:
    python scripts/debug_detection.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cv2
import numpy as np

from fisher.config import load_config
from fisher.capture import create_capture_driver
from fisher.extraction.track import TrackDetector
from fisher.extraction.bar import BobberBarExtractor
from fisher.extraction.fish import FishTracker
from fisher.extraction.progress import ProgressTracker
from fisher.extraction.extractor import FeatureExtractor
from fisher.extraction.types import TrackBounds


def main() -> None:
    cfg = load_config()
    out_dir = REPO_ROOT / "reports" / "debug_detection"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Start capture
    print("="*80)
    print("FISHER DETECTION DIAGNOSTIC")
    print("="*80)
    print()

    cap = create_capture_driver(cfg)
    cap.start()
    print(f"[OK] Capture driver started: {type(cap).__name__}")
    print(f"[INFO] dynamic_roi = {cfg.capture.get('dynamic_roi', False)}")
    print(f"[INFO] roi config  = {cfg.capture.get('roi')}")
    print(f"[INFO] track_bounds config = {cfg.capture.get('track_bounds')}")
    print()

    # Wait a moment for capture to warm up
    time.sleep(1.0)

    # 2. Grab frame
    frame, ts = cap.get_latest_frame()
    if frame is None:
        print("[ERROR] No frame received from capture driver!")
        print("  - Is Stardew Valley running on Screen 3?")
        print("  - Is the desktop unlocked (DXGI needs composition)?")
        cap.stop()
        return

    h, w = frame.shape[:2]
    print(f"[OK] Captured frame: {w}x{h} px (shape={frame.shape})")
    cv2.imwrite(str(out_dir / "01_raw_frame.png"), frame)
    print(f"  -> Saved to reports/debug_detection/01_raw_frame.png")
    print()

    # 3. Check if frame is full-screen or pre-cropped ROI
    is_full = w > 600 or h > 800
    print(f"[INFO] Frame classification: {'FULL SCREEN' if is_full else 'PRE-CROPPED ROI'}")
    print()

    # 4. Load configured track bounds
    tb_dict = cfg.capture.get("track_bounds")
    if tb_dict:
        bounds = TrackBounds(
            x0=tb_dict["x0"], y0=tb_dict["y0"],
            x1=tb_dict["x1"], y1=tb_dict["y1"],
            height_px=tb_dict.get("height_px", 568),
        )
        print(f"[INFO] Configured TrackBounds: x0={bounds.x0}, y0={bounds.y0}, x1={bounds.x1}, y1={bounds.y1}, height_px={bounds.height_px}")
    else:
        bounds = None
        print("[WARN] No track_bounds in config, using defaults")

    # 5. Test with configured ROI crop (if full-screen)
    roi_dict = cfg.capture.get("roi")
    roi_frame = frame
    if is_full and roi_dict:
        x0, y0, x1, y1 = roi_dict["x0"], roi_dict["y0"], roi_dict["x1"], roi_dict["y1"]
        print(f"\n--- Testing with STATIC ROI crop [{x0}, {y0}, {x1}, {y1}] ---")
        if y1 <= h and x1 <= w:
            roi_frame = frame[y0:y1, x0:x1]
            print(f"[OK] ROI crop shape: {roi_frame.shape}")
            cv2.imwrite(str(out_dir / "02_static_roi_crop.png"), roi_frame)
        else:
            print(f"[ERROR] ROI [{x0},{y0},{x1},{y1}] exceeds frame bounds [{w},{h}]!")
    print()

    # 6. Track detection on static ROI
    print("--- TrackDetector on static ROI ---")
    td = TrackDetector(default_bounds=bounds)
    active, conf = td.detect_track(roi_frame)
    rh, rw = roi_frame.shape[:2]
    print(f"  ROI frame size: {rw}x{rh}")
    print(f"  bounds.x1={td.bounds.x1} vs frame width={rw}  -> {'OK' if rw >= td.bounds.x1 else 'FAIL: frame too narrow!'}")
    print(f"  bounds.y1={td.bounds.y1} vs frame height={rh} -> {'OK' if rh >= td.bounds.y1 else 'FAIL: frame too short!'}")

    # Detailed border check
    if rw >= td.bounds.x1 and rh >= td.bounds.y1:
        lines_l = [roi_frame[td.bounds.y0:td.bounds.y1, x] for x in range(max(0, td.bounds.x0-2), min(rw, td.bounds.x0+3))]
        lines_r = [roi_frame[td.bounds.y0:td.bounds.y1, x] for x in range(max(0, td.bounds.x1-2), min(rw, td.bounds.x1+3))]
        min_std_l = min(float(np.mean(np.std(l, axis=0))) for l in lines_l)
        min_std_r = min(float(np.mean(np.std(r, axis=0))) for r in lines_r)
        print(f"  Left border std:  {min_std_l:.2f} (threshold <= 25.0) -> {'PASS' if min_std_l <= 25.0 else 'FAIL'}")
        print(f"  Right border std: {min_std_r:.2f} (threshold <= 25.0) -> {'PASS' if min_std_r <= 25.0 else 'FAIL'}")

        # Green/white paddle check
        track_crop = roi_frame[td.bounds.y0:td.bounds.y1, td.bounds.x0:td.bounds.x1]
        hsv = cv2.cvtColor(track_crop, cv2.COLOR_BGR2HSV)
        green_mask = (hsv[:,:,0] >= 35) & (hsv[:,:,0] <= 85) & (hsv[:,:,1] >= 50) & (hsv[:,:,2] >= 50)
        white_mask = (hsv[:,:,1] <= 55) & (hsv[:,:,2] >= 190)
        combined = (green_mask | white_mask).astype(np.uint8)
        green_px = int(np.sum(green_mask))
        white_px = int(np.sum(white_mask))
        print(f"  Green pixels in track: {green_px}")
        print(f"  White pixels in track: {white_px}")

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(combined, connectivity=8)
        print(f"  Connected components (green|white): {num_labels - 1}")
        has_valid_bar = False
        for i in range(1, num_labels):
            wc = stats[i, cv2.CC_STAT_WIDTH]
            hc = stats[i, cv2.CC_STAT_HEIGHT]
            area = stats[i, cv2.CC_STAT_AREA]
            rect_ratio = area / max(1, wc * hc)
            valid = 20 <= wc <= 48 and 25 <= hc <= 250 and area >= 250 and rect_ratio >= 0.55
            print(f"    Component {i}: w={wc}, h={hc}, area={area}, fill_ratio={rect_ratio:.2f} -> {'VALID' if valid else 'rejected'}")
            if valid:
                has_valid_bar = True
        print(f"  has_valid_bar: {has_valid_bar}")

        # Progress bar check
        pm_x0 = max(0, td.bounds.x1 + 22)
        pm_x1 = min(rw, td.bounds.x1 + 42)
        pm_y0 = max(0, td.bounds.y0 - 4)
        pm_y1 = min(rh, td.bounds.y1 + 4)
        if pm_x1 > pm_x0 and pm_y1 > pm_y0:
            pm_crop = roi_frame[pm_y0:pm_y1, pm_x0:pm_x1]
            pm_hsv = cv2.cvtColor(pm_crop, cv2.COLOR_BGR2HSV)
            pm_filled = (pm_hsv[:,:,1] >= 60) & (pm_hsv[:,:,2] >= 160)
            pm_row_fill = np.mean(pm_filled, axis=1)
            rows_above_thresh = int(np.sum(pm_row_fill >= 0.35))
            print(f"  Progress bar region [{pm_x0}:{pm_x1}, {pm_y0}:{pm_y1}]: {rows_above_thresh} rows with fill >= 35% (need >= 15)")
            cv2.imwrite(str(out_dir / "03_progress_bar_region.png"), pm_crop)
        else:
            print(f"  [WARN] Progress bar region out of bounds: [{pm_x0}:{pm_x1}]")
    else:
        print(f"  [SKIP] Cannot check detection — bounds exceed frame size")

    print(f"\n  => detect_track result: active={active}, confidence={conf}")
    print()

    # 7. Dynamic widget localization (locate_widget) on full frame
    print("--- TrackDetector.locate_widget() on full frame ---")
    td2 = TrackDetector(default_bounds=bounds)
    bbox = td2.locate_widget(frame)
    print(f"  locate_widget result: {bbox}")
    if bbox:
        rx0, ry0, rx1, ry1 = bbox
        widget_crop = frame[ry0:ry1, rx0:rx1]
        print(f"  Widget crop shape: {widget_crop.shape}")
        cv2.imwrite(str(out_dir / "04_widget_crop.png"), widget_crop)
    print()

    # 8. Full FeatureExtractor test
    print("--- FeatureExtractor.extract_features() ---")
    extractor = FeatureExtractor(bounds=bounds)
    test_frame = roi_frame
    if bbox:
        rx0, ry0, rx1, ry1 = bbox
        test_frame = frame[ry0:ry1, rx0:rx1]
    result = extractor.extract_features(test_frame, time.perf_counter())
    print(f"  is_active:   {result.is_active}")
    print(f"  bar_pos:     {result.bar_pos:.4f}")
    print(f"  bar_height:  {result.bar_height:.4f}")
    print(f"  fish_pos:    {result.fish_pos:.4f}")
    print(f"  progress:    {result.progress:.4f}")
    print(f"  in_bar:      {result.in_bar}")
    print(f"  confidence:  {result.confidence:.4f}")
    print(f"  features:    {result.features}")
    print()

    # 9. Save annotated frame
    annotated = frame.copy()
    if roi_dict and is_full:
        x0, y0, x1, y1 = roi_dict["x0"], roi_dict["y0"], roi_dict["x1"], roi_dict["y1"]
        cv2.rectangle(annotated, (x0, y0), (x1, y1), (0, 255, 255), 2)
        cv2.putText(annotated, "Static ROI", (x0, y0-8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    if bbox:
        rx0, ry0, rx1, ry1 = bbox
        cv2.rectangle(annotated, (rx0, ry0), (rx1, ry1), (0, 255, 0), 2)
        cv2.putText(annotated, "Dynamic Widget", (rx0, ry0-8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    cv2.imwrite(str(out_dir / "05_annotated.png"), annotated)
    print(f"[OK] Annotated frame saved to reports/debug_detection/05_annotated.png")

    cap.stop()
    print("\n[DONE] Diagnostic complete. Check reports/debug_detection/ for all outputs.")


if __name__ == "__main__":
    main()
