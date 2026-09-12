"""
Live Capture Preview - See What Fisher AI Sees on Screen 3
============================================================
Displays real-time screen capture, dynamic BobberBar widget localization,
feature extraction overlays (bar position, fish position, catch progress),
and detection metrics.

Inspired by HKM/preview_capture.py.

Controls:
    'Q' or ESC : Quit preview
    'S'        : Save current frame snapshot to reports/preview_snapshot.png
    'D'        : Toggle dynamic localization vs calibrated ROI
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
from typing import Optional, Tuple

import cv2
import numpy as np

# Ensure project root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fisher.ui.preview import run_preview


def main() -> None:
    parser = argparse.ArgumentParser(description="Fisher Live Capture Preview")
    parser.add_argument("--driver", type=str, default="bettercam", choices=["bettercam", "gdi", "mock"], help="Capture driver")
    parser.add_argument("--no-dynamic-roi", action="store_true", help="Disable dynamic ROI and use fixed [720, 150, 910, 800]")
    args = parser.parse_args()

    run_preview(driver_name=args.driver, dynamic_roi=not args.no_dynamic_roi)


if __name__ == "__main__":
    main()
