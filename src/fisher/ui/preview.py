"""Real-time visual preview and debug telemetry overlay for Screen 3.

Provides high-resolution visual feedback of the captured frame, detected
BobberBar ROI bounding box, zoomed track extraction Picture-in-Picture (PiP),
and physical state metrics (bar, fish, progress, in-bar status).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time
from typing import Optional, Tuple

import cv2
import numpy as np

from fisher.capture import create_capture_driver
from fisher.config import load_config
from fisher.extraction.extractor import FeatureExtractor
from fisher.extraction.types import ExtractionResult, TrackBounds

logger = logging.getLogger(__name__)


class PreviewVideoRecorder:
    """Records real-time annotated preview frames and metadata to an MP4 video."""

    def __init__(
        self,
        output_path: Optional[str | Path] = None,
        fps: float = 30.0,
        frame_size: Tuple[int, int] = (960, 540),
        record_metadata: bool = True,
    ) -> None:
        self.fps = float(fps)
        self.frame_size = frame_size  # (width, height)
        self.record_metadata = record_metadata
        self.writer: Optional[cv2.VideoWriter] = None
        self.meta_file = None
        self.output_path: Optional[Path] = None
        self.meta_path: Optional[Path] = None
        self.frame_count: int = 0
        self.start_time: float = 0.0
        self.is_recording: bool = False

        if output_path is not None:
            self.start(output_path)

    def start(self, output_path: Optional[str | Path] = None) -> Path:
        """Start or resume recording to the specified MP4 file."""
        if self.is_recording:
            return self.output_path

        if output_path is None:
            from datetime import datetime
            t_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            repo_root = Path(__file__).resolve().parent.parent.parent
            self.output_path = repo_root / "reports" / "recordings" / f"preview_{t_str}.mp4"
        else:
            self.output_path = Path(output_path)
            if not self.output_path.suffix:
                self.output_path = self.output_path.with_suffix(".mp4")

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(
            str(self.output_path),
            fourcc,
            self.fps,
            self.frame_size,
        )

        if not self.writer.isOpened():
            # Fallback to XVID codec if mp4v is unavailable on this host platform
            fourcc_fallback = cv2.VideoWriter_fourcc(*"XVID")
            self.writer = cv2.VideoWriter(
                str(self.output_path.with_suffix(".avi")),
                fourcc_fallback,
                self.fps,
                self.frame_size,
            )
            if self.writer.isOpened():
                self.output_path = self.output_path.with_suffix(".avi")

        if self.record_metadata:
            self.meta_path = self.output_path.with_suffix(".jsonl")
            self.meta_file = open(self.meta_path, "w", encoding="utf-8")

        self.start_time = time.time()
        self.frame_count = 0
        self.is_recording = True
        logger.info("Preview video recording started: %s", self.output_path)
        return self.output_path

    def write_frame(
        self,
        frame: np.ndarray,
        metadata: Optional[dict] = None,
    ) -> None:
        """Write an annotated frame and optional telemetry metadata."""
        if not self.is_recording or self.writer is None:
            return

        if (frame.shape[1], frame.shape[0]) != self.frame_size:
            frame = cv2.resize(frame, self.frame_size, interpolation=cv2.INTER_LINEAR)

        self.writer.write(frame)
        self.frame_count += 1

        if self.meta_file is not None and metadata is not None:
            import json
            entry = {
                "frame": self.frame_count,
                "timestamp": time.time(),
                "elapsed_s": round(time.time() - self.start_time, 4),
                **metadata,
            }
            self.meta_file.write(json.dumps(entry) + "\n")

    def stop(self) -> Optional[Path]:
        """Stop recording and cleanly release resources."""
        if not self.is_recording:
            return None

        self.is_recording = False
        saved_path = self.output_path

        if self.writer is not None:
            self.writer.release()
            self.writer = None

        if self.meta_file is not None:
            self.meta_file.flush()
            self.meta_file.close()
            self.meta_file = None

        elapsed = max(0.001, time.time() - self.start_time)
        actual_fps = self.frame_count / elapsed
        logger.info(
            "Preview recording saved: %s (%d frames, %.1fs @ %.1f FPS)",
            saved_path, self.frame_count, elapsed, actual_fps
        )
        return saved_path

    @property
    def elapsed_s(self) -> float:
        if not self.is_recording:
            return 0.0
        return time.time() - self.start_time

    @property
    def status_str(self) -> str:
        if not self.is_recording:
            return ""
        secs = int(self.elapsed_s)
        mins = secs // 60
        secs = secs % 60
        return f"{mins:02d}:{secs:02d} ({self.frame_count}f)"


def draw_preview_overlay(
    full_frame: Optional[np.ndarray],
    extractor: Optional[FeatureExtractor] = None,
    extraction: Optional[ExtractionResult] = None,
    current_roi: Optional[Tuple[int, int, int, int]] = None,
    fps: float = 0.0,
    driver_name: str = "bettercam",
    status_text: Optional[str] = None,
    recorder: Optional[PreviewVideoRecorder] = None,
    auto_locate: bool = False,
) -> np.ndarray:
    """Render a rich real-time visual telemetry overlay onto the captured screen frame.

    Args:
        full_frame: Full-resolution capture frame from Screen 3 (e.g., 1920x1080).
        extractor: FeatureExtractor instance (used if extraction is None).
        extraction: Pre-computed ExtractionResult (avoids redundant processing).
        current_roi: Detected or calibrated BobberBar ROI (rx0, ry0, rx1, ry1).
        fps: Current capture/processing framerate.
        driver_name: Capture backend name (e.g. 'bettercam', 'gdi', 'mock').
        status_text: Optional override status string for HUD footer.
        recorder: Optional PreviewVideoRecorder instance to render recording indicator.
        auto_locate: Whether to dynamically locate widget if current_roi is None.

    Returns:
        Annotated BGR image scaled to 960x540 for live display.
    """
    display_w = 960
    display_h = 540

    if full_frame is None or full_frame.size == 0:
        display = np.zeros((display_h, display_w, 3), dtype=np.uint8)
        cv2.putText(
            display,
            "NO CAPTURE FRAME AVAILABLE",
            (display_w // 2 - 200, display_h // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        return display

    h_orig, w_orig = full_frame.shape[:2]
    scale_x = display_w / float(w_orig)
    scale_y = display_h / float(h_orig)

    display = cv2.resize(full_frame, (display_w, display_h), interpolation=cv2.INTER_LINEAR)

    # 1. Top Header Banner
    cv2.rectangle(display, (0, 0), (display_w, 36), (20, 20, 25), -1)
    cv2.line(display, (0, 36), (display_w, 36), (0, 200, 255), 2)
    cv2.putText(
        display,
        f"FISHER AI  |  Screen 3 (1080p)  |  Driver: {driver_name.upper()}  |  FPS: {fps:4.1f}",
        (15, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )

    # 1b. Recording Badge if active
    if recorder is not None and recorder.is_recording:
        rec_str = f"REC {recorder.status_str}"
        blink = (int(time.time() * 2) % 2) == 0
        dot_color = (0, 0, 255) if blink else (0, 0, 140)
        rec_x = display_w - 220
        cv2.circle(display, (rec_x, 18), 6, dot_color, -1)
        cv2.circle(display, (rec_x, 18), 7, (255, 255, 255), 1)
        cv2.putText(
            display,
            rec_str,
            (rec_x + 12, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 50, 255),
            2,
            cv2.LINE_AA,
        )

    # 2. BobberBar widget localization
    bbox = current_roi
    if bbox is None and auto_locate and extractor is not None:
        bbox = extractor.track_detector.locate_widget(full_frame)

    if bbox is not None:
        rx0, ry0, rx1, ry1 = bbox
        # Clamp to bounds
        rx0 = max(0, min(rx0, w_orig - 1))
        ry0 = max(0, min(ry0, h_orig - 1))
        rx1 = max(rx0 + 1, min(rx1, w_orig))
        ry1 = max(ry0 + 1, min(ry1, h_orig))

        # Scaled bounds on 960x540 canvas
        dx0 = int(rx0 * scale_x)
        dy0 = int(ry0 * scale_y)
        dx1 = int(rx1 * scale_x)
        dy1 = int(ry1 * scale_y)

        # Draw green bounding box around detected BobberBar
        cv2.rectangle(display, (dx0, dy0), (dx1, dy1), (0, 255, 100), 2)
        cv2.rectangle(display, (dx0, max(0, dy0 - 20)), (dx0 + 160, dy0), (0, 255, 100), -1)
        cv2.putText(
            display,
            f"BOBBERBAR [{rx0},{ry0}]",
            (dx0 + 4, max(14, dy0 - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

        # Crop subframe for PiP
        sub_frame = full_frame[ry0:ry1, rx0:rx1]
        if sub_frame.size > 0:
            if extraction is None and extractor is not None:
                extraction = extractor.extract_features(sub_frame, time.perf_counter())

            # 3. Picture-in-Picture (PiP) Zoomed Widget Display (Top-Right)
            pip_w = 150
            pip_h = 420
            pip_x = display_w - pip_w - 20
            pip_y = 50

            pip_crop = cv2.resize(sub_frame, (pip_w, pip_h), interpolation=cv2.INTER_NEAREST)

            # Draw PiP background and border
            cv2.rectangle(display, (pip_x - 4, pip_y - 24), (pip_x + pip_w + 4, pip_y + pip_h + 4), (30, 30, 35), -1)
            cv2.rectangle(display, (pip_x - 4, pip_y - 24), (pip_x + pip_w + 4, pip_y + pip_h + 4), (0, 200, 255), 2)
            cv2.putText(
                display,
                "ZOOMED MINIGAME TRACK",
                (pip_x + 4, pip_y - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )

            # Insert PiP into display
            display[pip_y : pip_y + pip_h, pip_x : pip_x + pip_w] = pip_crop

            if extraction is not None:
                b_norm = extraction.bar_pos
                f_norm = extraction.fish_pos
                h_norm = extraction.bar_height
                p_val = extraction.progress

                # Y in PiP coordinates: norm 1.0 = top (0 px), 0.0 = bottom (pip_h px)
                b_pip_y = int((1.0 - b_norm) * pip_h)
                h_pip_y = int(h_norm * pip_h)
                f_pip_y = int((1.0 - f_norm) * pip_h)

                # Bar overlay (bright green box on track)
                bar_color = (255, 255, 255) if extraction.in_bar else (0, 255, 0)
                cv2.rectangle(
                    display,
                    (pip_x + 30, max(pip_y, pip_y + b_pip_y - h_pip_y)),
                    (pip_x + 75, min(pip_y + pip_h, pip_y + b_pip_y + h_pip_y)),
                    bar_color,
                    2,
                )

                # Fish marker (orange circle with bright border)
                cv2.circle(display, (pip_x + 52, pip_y + f_pip_y), 7, (0, 140, 255), -1)
                cv2.circle(display, (pip_x + 52, pip_y + f_pip_y), 8, (255, 255, 255), 1)

                # In-Bar status indicator
                in_bar_str = "IN-BAR" if extraction.in_bar else "OUT-OF-BAR"
                in_bar_color = (0, 255, 100) if extraction.in_bar else (0, 70, 255)
                cv2.putText(
                    display,
                    in_bar_str,
                    (pip_x + 18, pip_y + pip_h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.50,
                    in_bar_color,
                    2,
                    cv2.LINE_AA,
                )

                # 4. Telemetry Metrics HUD (Bottom-Left)
                hud_y = display_h - 55
                cv2.rectangle(display, (10, hud_y - 20), (460, display_h - 10), (15, 15, 20), -1)
                cv2.rectangle(display, (10, hud_y - 20), (460, display_h - 10), (0, 255, 100), 1)

                metrics_text = f"Bar: {b_norm*100:4.1f}%  |  Fish: {f_norm*100:4.1f}%  |  Catch: {p_val*100:4.1f}%"
                cv2.putText(display, metrics_text, (20, hud_y + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

                current_status = status_text or "MINIGAME ENGAGED - AI ACTIVE"
                cv2.putText(display, f"STATUS: {current_status}", (20, hud_y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 100), 2, cv2.LINE_AA)

    else:
        # Searching indicator
        hud_y = display_h - 50
        cv2.rectangle(display, (10, hud_y - 20), (620, display_h - 10), (15, 15, 20), -1)
        cv2.rectangle(display, (10, hud_y - 20), (620, display_h - 10), (0, 200, 255), 1)

        search_msg = status_text or "SEARCHING FOR BOBBERBAR... (Cast your rod on Screen 3 to test)"
        cv2.putText(
            display,
            f"STATUS: {search_msg}",
            (20, hud_y + 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (0, 200, 255),
            1,
            cv2.LINE_AA,
        )

    # Controls footer
    rec_ctl = "[R] Stop Rec" if (recorder is not None and recorder.is_recording) else "[R] Record"
    cv2.putText(
        display,
        f"Controls: [Q/ESC] Quit  |  {rec_ctl}  |  [S] Snapshot",
        (display_w - 410, display_h - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        (180, 180, 180),
        1,
        cv2.LINE_AA,
    )

    return display


def run_preview(
    driver_name: str = "bettercam",
    dynamic_roi: bool = True,
    record_video: bool = False,
    output_video_path: Optional[str] = None,
) -> None:
    """Run real-time capture and extraction preview loop with optional MP4 recording."""
    print("=" * 65)
    print("FISHER AI — LIVE CAPTURE & EXTRACTION PREVIEW")
    print("=" * 65)
    print("Connecting to Screen 3 (Stardew Valley)...")
    print("Controls: Press 'Q' or ESC in preview window to exit.")
    print("          Press 'R' to toggle video recording.")
    print("          Press 'S' to save a snapshot image.")
    print("-" * 65)

    cfg = load_config()
    cfg.capture["dynamic_roi"] = dynamic_roi
    if driver_name:
        cfg.capture["driver"] = driver_name

    cap = create_capture_driver(cfg)
    cap.start()

    tb_dict = cfg.capture.get("track_bounds", {})
    bounds = TrackBounds(
        x0=tb_dict.get("x0", 76),
        y0=tb_dict.get("y0", 47),
        x1=tb_dict.get("x1", 112),
        y1=tb_dict.get("y1", 615),
        height_px=tb_dict.get("height_px", 568),
    )
    extractor = FeatureExtractor(bounds=bounds)

    recorder = PreviewVideoRecorder()
    if record_video:
        recorder.start(output_video_path)
        print(f"[RECORDER] Automatically recording preview to: {recorder.output_path}")

    window_name = "Fisher AI — Live Capture Preview (Screen 3)"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 960, 540)

    frame_count = 0
    start_time = time.time()
    fps = 0.0
    current_roi = None
    roi_lost_count = 0
    consecutive_detect = 0
    MAX_ROI_LOST_FRAMES = 8  # ~250ms sustained loss before dropping ROI

    static_roi = None
    if "roi" in cfg.capture:
        r = cfg.capture["roi"]
        static_roi = (int(r["x0"]), int(r["y0"]), int(r["x1"]), int(r["y1"]))

    try:
        while True:
            frame, ts = cap.get_latest_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            frame_count += 1
            now = time.time()
            if now - start_time >= 1.0:
                fps = frame_count / (now - start_time)
                frame_count = 0
                start_time = now

            # BobberBar ROI detection & temporal tracking
            extraction = None
            is_full_frame = (frame.shape[1] > 600 or frame.shape[0] > 800)

            if not is_full_frame:
                # Pre-cropped frame (e.g. synthetic test or camera directly on ROI)
                current_roi = None
                extraction = extractor.extract_features(frame, ts)
            elif current_roi is not None:
                # 1. Continuous Tracking Phase: Minigame ROI already locked
                rx0, ry0, rx1, ry1 = current_roi
                sub_frame = frame[ry0:ry1, rx0:rx1]
                if sub_frame.size > 0:
                    extraction = extractor.extract_features(sub_frame, ts)
                    if extraction.is_active:
                        roi_lost_count = 0
                    else:
                        roi_lost_count += 1
                        if roi_lost_count >= MAX_ROI_LOST_FRAMES:
                            # Minigame concluded
                            current_roi = None
                            roi_lost_count = 0
                            consecutive_detect = 0
                            extraction = None
                            extractor.reset()
            else:
                # 2. Searching Phase: Look for BobberBar appearing on screen
                detected_cand = None
                if dynamic_roi:
                    detected_cand = extractor.track_detector.locate_widget(frame)

                if detected_cand is None and static_roi is not None:
                    sx0, sy0, sx1, sy1 = static_roi
                    sub = frame[sy0:sy1, sx0:sx1]
                    act, conf = extractor.track_detector.detect_track(sub)
                    has_fill = extractor.progress_tracker.has_progress_fill(sub, min_progress=0.04)
                    if act and conf >= 0.70 and has_fill:
                        detected_cand = static_roi

                if detected_cand is not None:
                    consecutive_detect += 1
                    if consecutive_detect >= 2:
                        current_roi = detected_cand
                        consecutive_detect = 0
                        roi_lost_count = 0
                        rx0, ry0, rx1, ry1 = current_roi
                        sub_frame = frame[ry0:ry1, rx0:rx1]
                        if sub_frame.size > 0:
                            extraction = extractor.extract_features(sub_frame, ts)
                else:
                    consecutive_detect = 0

            preview_img = draw_preview_overlay(
                full_frame=frame,
                extractor=extractor,
                extraction=extraction,
                current_roi=current_roi,
                fps=fps,
                driver_name=driver_name,
                recorder=recorder,
            )

            # Record frame if recording is active
            if recorder.is_recording:
                metadata = {
                    "roi": list(current_roi) if current_roi else None,
                    "fps": round(fps, 1),
                }
                if extraction is not None:
                    metadata.update({
                        "bar_pos": round(float(extraction.bar_pos), 4),
                        "fish_pos": round(float(extraction.fish_pos), 4),
                        "progress": round(float(extraction.progress), 4),
                        "in_bar": bool(extraction.in_bar),
                        "is_active": bool(extraction.is_active),
                        "confidence": round(float(extraction.confidence), 4),
                    })
                recorder.write_frame(preview_img, metadata=metadata)

            cv2.imshow(window_name, preview_img)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):  # 'Q' or ESC
                break
            elif key in (ord("r"), ord("R")):
                if recorder.is_recording:
                    saved = recorder.stop()
                    print(f"\n[RECORDER] Stopped recording: {saved}")
                else:
                    started = recorder.start(output_video_path)
                    print(f"\n[RECORDER] Started recording to: {started}")
            elif key in (ord("s"), ord("S")):
                repo_root = Path(__file__).resolve().parent.parent.parent
                out_path = repo_root / "reports" / "preview_snapshot.png"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out_path), preview_img)
                print(f"[OK] Saved snapshot to: {out_path}")

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nPreview stopped by user.")
    finally:
        if recorder.is_recording:
            saved = recorder.stop()
            print(f"[RECORDER] Video finalized and saved: {saved}")
        cap.stop()
        cv2.destroyAllWindows()
        print("Preview closed cleanly.")
