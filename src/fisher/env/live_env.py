"""Gymnasium live environment for the Stardew Valley fishing minigame.

Wires DXGI/GDI screen capture -> OpenCV feature extractor -> Policy inference -> DirectInput actuation
at a synchronized 30 Hz control loop using Windows multimedia 1 ms timer.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Tuple

import gymnasium as gym
from gymnasium import spaces
import numpy as np

from fisher.capture.base import CaptureDriver
from fisher.capture import create_capture_driver
from fisher.config import FisherConfig, load_config
from fisher.env.rewards import RewardCalculator, RewardConfig
from fisher.extraction.extractor import FeatureExtractor
from fisher.extraction.types import ExtractionResult
from fisher.input.base import Actuator
from fisher.input import create_actuator
from fisher.input.direct_input import DirectInputActuator
from fisher.utils.timing import HighPrecisionTimer

logger = logging.getLogger("fisher.env.live")


class LiveFishingEnv(gym.Env):
    """Gymnasium environment controlling the live Stardew Valley fishing minigame.
    
    Operates at 30 Hz (33.33 ms period) matching the trained PPO policy contract.
    """

    metadata = {"render_modes": ["ansi"], "render_fps": 30}

    def __init__(
        self,
        config: Optional[FisherConfig] = None,
        capture_driver: Optional[CaptureDriver] = None,
        actuator: Optional[Actuator] = None,
        extractor: Optional[FeatureExtractor] = None,
        reward_calc: Optional[RewardCalculator] = None,
        control_hz: int = 30,
        max_duration_s: float = 30.0,
        auto_start_capture: bool = True,
        wait_for_ui_on_reset: bool = False,
        ui_lost_threshold_frames: int = 20,
        require_foreground: bool = True,
        foreground_lost_threshold_frames: int = 15,
        render_mode: Optional[str] = None,
        video_recorder: Optional[Any] = None,
    ) -> None:
        super().__init__()
        self.cfg = config or load_config()
        self.video_recorder = video_recorder
        self.control_hz = int(self.cfg.control.get("hz", control_hz))
        self.period_s = 1.0 / self.control_hz
        self.max_duration_s = max_duration_s
        self.max_steps = int(self.max_duration_s * self.control_hz)
        self.wait_for_ui_on_reset = wait_for_ui_on_reset
        self.ui_lost_threshold_frames = ui_lost_threshold_frames
        self.require_foreground = require_foreground
        self.foreground_lost_threshold_frames = foreground_lost_threshold_frames
        self.foreground_lost_count = 0
        self.render_mode = render_mode
        self._window_name = "Fisher AI — Live Evaluation Preview"
        self._window_initialized = False
        self._last_full_frame: Optional[np.ndarray] = None
        self.current_roi: Optional[Tuple[int, int, int, int]] = None
        self.dynamic_roi: bool = bool(self.cfg.capture.get("dynamic_roi", True))

        roi_dict = self.cfg.capture.get("roi")
        self.static_roi: Optional[Tuple[int, int, int, int]] = None
        if roi_dict:
            self.static_roi = (
                int(roi_dict["x0"]),
                int(roi_dict["y0"]),
                int(roi_dict["x1"]),
                int(roi_dict["y1"]),
            )

        # Action: Discrete(2) -> 0: RELEASE, 1: HOLD
        self.action_space = spaces.Discrete(2)

        # Observation: 9 features normalized to [-1, 1] per Table 4.1:
        # [2b-1, v, 2f-1, f_dot, 2h-1, 2p-1, I, clip(2*delta, -1, 1), a_prev]
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(9,), dtype=np.float32
        )

        # Hardware & Perception components
        self.capture = capture_driver or create_capture_driver(self.cfg)
        self.actuator = actuator or create_actuator(self.cfg, strict_foreground=self.require_foreground)

        track_bounds = None
        tb_dict = self.cfg.capture.get("track_bounds")
        if tb_dict:
            from fisher.extraction.types import TrackBounds
            track_bounds = TrackBounds(
                x0=tb_dict["x0"],
                y0=tb_dict["y0"],
                x1=tb_dict["x1"],
                y1=tb_dict["y1"],
                height_px=tb_dict.get("height_px", 568),
            )
        self.extractor = extractor or FeatureExtractor(bounds=track_bounds)

        # Reward calculator (identical weights to simulation)
        rew_dict = self.cfg.reward
        rew_cfg = RewardConfig(
            w_o=float(rew_dict.get("w_o", 0.02)),
            w_p=float(rew_dict.get("w_p", 5.0)),
            w_v=float(rew_dict.get("w_v", 0.002)),
            w_s=float(rew_dict.get("w_s", 0.005)),
            r_win=float(rew_dict.get("r_win", 10.0)),
            r_lose=float(rew_dict.get("r_lose", 5.0)),
            gamma=float(rew_dict.get("gamma", 0.999)),
        )
        self.reward_calc = reward_calc or RewardCalculator(rew_cfg)

        # High-precision multimedia timer (1 ms period)
        self.timer = HighPrecisionTimer(target_hz=self.control_hz)

        # Start capture thread if requested
        if auto_start_capture and not self.capture.is_running:
            self.capture.start()

        # Episode runtime state
        self.step_count = 0
        self.prev_action = 0
        self.progress = 0.30
        self.peak_progress = 0.30
        self.progress_zero_count = 0
        self.min_steps_for_escape = 25  # ~0.83s minimum elapsed before escape can trigger (physics takes >= 1.67s)
        self.ui_lost_count = 0
        self.in_bar_steps = 0
        self.start_time = 0.0
        self.step_latencies_ms: list[float] = []
        self._last_extraction: Optional[ExtractionResult] = None

    def _crop_roi(
        self,
        frame: Optional[np.ndarray],
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[np.ndarray]:
        """Crop frame to specified ROI or active/static ROI if frame is full-screen."""
        if frame is None or frame.size == 0:
            return frame
        if frame.shape[1] > 600 or frame.shape[0] > 800:
            target_roi = roi or self.current_roi or self.static_roi
            if target_roi is not None:
                rx0, ry0, rx1, ry1 = target_roi
                return frame[ry0:ry1, rx0:rx1]
        return frame

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, dict[str, Any]]:
        """Reset the live environment for a new minigame episode."""
        super().reset(seed=seed)
        options = options or {}

        # 0. Reset ALL extractor state from previous episode immediately.
        # This clears _last_known_b, _last_known_f, _last_known_p, velocity histories,
        # debounce counters, and the track detector's _is_active flag. Without this,
        # stale values from a previous minigame leak into the new episode's observations.
        self.extractor.reset()

        # 1. Ensure mouse is released safely
        self.actuator.release()

        # 2. Ensure capture is running
        if not self.capture.is_running:
            self.capture.start()

        # 3. Optional: wait for minigame UI to appear on screen
        wait_ui = options.get("wait_for_ui", self.wait_for_ui_on_reset)
        wait_timeout = options.get("wait_timeout", 30.0)
        ui_found = True
        self.current_roi = None
        self.foreground_lost_count = 0

        if wait_ui:
            # 3a. Clearance phase: ensure previous minigame or catch dialog has closed
            t_clear_start = time.perf_counter()
            consecutive_cleared = 0
            while time.perf_counter() - t_clear_start < 4.0:
                frame, _ = self.capture.get_latest_frame()
                if frame is not None:
                    self._last_full_frame = frame
                    if self.render_mode == "human":
                        self.render(status_text="CLEARING PREVIOUS MINIGAME / DIALOG...")
                    sub_frame = self._crop_roi(frame, self.static_roi)
                    active, _ = self.extractor.track_detector.detect_track(sub_frame)
                    p, _ = self.extractor.progress_tracker.extract(sub_frame)
                    if not active or p < 0.10:
                        consecutive_cleared += 1
                        if consecutive_cleared >= 5:
                            break
                    else:
                        consecutive_cleared = 0
                time.sleep(0.04)

            # Reset extractor internal state so fresh minigame detection is clean
            self.extractor.reset()

            # 3b. Detection phase: wait for a NEW minigame UI (starts with p >= 0.15, default p0 = 0.30)
            logger.info("Waiting for fishing minigame UI to appear...")
            t_wait_start = time.perf_counter()
            ui_found = False
            consecutive_active = 0
            while time.perf_counter() - t_wait_start < wait_timeout:
                frame, ts = self.capture.get_latest_frame()
                if frame is not None:
                    self._last_full_frame = frame
                    if self.render_mode == "human":
                        rem = max(0, int(wait_timeout - (time.perf_counter() - t_wait_start)))
                        self.render(status_text=f"WAITING FOR MINIGAME (Timeout in {rem}s)...")

                    # Dynamic-first localization across full Screen 3
                    is_full_frame = (frame.shape[1] > 600 or frame.shape[0] > 800)
                    located = False

                    if self.dynamic_roi and is_full_frame:
                        # 1. Primary path: dynamic localization anywhere on screen
                        bbox = self.extractor.track_detector.locate_widget(frame)
                        if bbox is not None:
                            sub_frame = self._crop_roi(frame, bbox)
                            p, _ = self.extractor.progress_tracker.extract(sub_frame)
                            if p >= 0.01:
                                located = True
                                consecutive_active += 1
                                if consecutive_active >= 2:
                                    self.current_roi = bbox
                                    ui_found = True
                                    logger.info("BobberBar dynamically located at ROI: %s (initial p=%.2f)", bbox, p)
                                    break
                            else:
                                consecutive_active = 0
                        else:
                            consecutive_active = 0

                    if not located:
                        # 2. Fallback path: static ROI if configured
                        if self.static_roi is not None and is_full_frame:
                            sub_frame = self._crop_roi(frame, self.static_roi)
                            active, conf = self.extractor.track_detector.detect_track(sub_frame)
                            if active and conf >= 0.70:
                                p, _ = self.extractor.progress_tracker.extract(sub_frame)
                                if p >= 0.05:
                                    consecutive_active += 1
                                    if consecutive_active >= 2:
                                        self.current_roi = self.static_roi
                                        ui_found = True
                                        logger.info("BobberBar verified at static ROI: %s (conf=%.2f, initial p=%.2f)", self.static_roi, conf, p)
                                        break
                                    time.sleep(0.04)
                                    continue
                                else:
                                    consecutive_active = 0
                            else:
                                consecutive_active = 0
                        elif not is_full_frame:
                            # Pre-cropped frame (e.g. synthetic or mock unit tests)
                            active, conf = self.extractor.track_detector.detect_track(frame)
                            p, _ = self.extractor.progress_tracker.extract(frame)
                            if active and conf >= 0.40 and p >= 0.05:
                                consecutive_active += 1
                                if consecutive_active >= 2:
                                    self.current_roi = None
                                    ui_found = True
                                    break
                            else:
                                consecutive_active = 0
                time.sleep(0.04)

            if not ui_found:
                logger.warning(f"Minigame UI not detected within {wait_timeout}s timeout.")
            else:
                # Pre-flight cursor & window positioning: bring game to front and place cursor inside Screen 3
                try:
                    self.actuator.focus_game_window()
                    self.actuator.ensure_cursor_in_window()
                except Exception as exc:
                    logger.debug(f"Pre-flight cursor/focus error: {exc}")

        # 4. Reset episode tracking counters
        self.step_count = 0
        self.prev_action = 0
        self.progress_zero_count = 0
        self.peak_progress = 0.30
        self.ui_lost_count = 0
        self.in_bar_steps = 0
        self.foreground_lost_count = 0
        self.step_latencies_ms.clear()
        self.start_time = time.perf_counter()
        self.timer.reset()

        # 5. Reset extractors AGAIN before the step loop begins.
        # The detection phase above may have called detect_track() and extract()
        # multiple times, polluting _last_known values and _consecutive_zeros counters
        # with intermediate detection-phase artifacts. This ensures the step loop
        # starts with clean extractor state.
        self.extractor.reset()

        # 6. Grab initial frame and extract starting features
        frame, ts = self.capture.get_latest_frame()
        self._last_full_frame = frame
        if frame is None:
            # Fallback for synthetic/mock runs before first frame arrives
            frame = np.zeros((650, 190, 3), dtype=np.uint8)
            ts = time.perf_counter()
        else:
            frame = self._crop_roi(frame)

        extraction = self.extractor.extract_features(frame, ts, prev_action=self.prev_action)
        self._last_extraction = extraction
        self.progress = extraction.progress

        info = {
            "is_active": extraction.is_active,
            "ui_found": ui_found,
            "progress": extraction.progress,
            "confidence": extraction.confidence,
            "bar_pos": extraction.bar_pos,
            "fish_pos": extraction.fish_pos,
        }
        return extraction.features, info

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        """Execute one 30 Hz control tick in the live game."""
        t_tick_start = time.perf_counter()
        self.step_count += 1
        p_prev = self.progress

        t_dispatch_start = time.perf_counter()
        # 1. Dispatch action to mouse actuator
        self.actuator.set_press(bool(action))
        t_dispatch_end = time.perf_counter()

        # 2. Precision sleep to synchronize 30 Hz loop quantum (33.33 ms)
        self.timer.sleep_until_next_tick()

        # 3. Capture latest frame from DXGI buffer
        t_proc_start = time.perf_counter()
        frame, ts = self.capture.get_latest_frame()
        self._last_full_frame = frame
        if frame is None:
            frame = np.zeros((650, 190, 3), dtype=np.uint8)
            ts = time.perf_counter()
        else:
            frame = self._crop_roi(frame)

        # 4. Extract visual features
        extraction = self.extractor.extract_features(frame, ts, prev_action=int(action))
        self._last_extraction = extraction

        self.progress = extraction.progress
        t_proc_end = time.perf_counter()

        # 5. Measure actual pipeline computation latency (dispatch + capture + extraction)
        tick_latency_ms = ((t_dispatch_end - t_dispatch_start) + (t_proc_end - t_proc_start)) * 1000.0
        self.step_latencies_ms.append(tick_latency_ms)

        # 6. In-bar tracking metrics
        if extraction.in_bar:
            self.in_bar_steps += 1

        self.peak_progress = max(self.peak_progress, self.progress)

        # 7. Check terminal conditions with temporal debounce & physics plausibility
        if self.progress <= 0.01:
            self.progress_zero_count += 1
        else:
            self.progress_zero_count = 0

        # Catch: progress reached full top.
        # Note: In Stardew Valley 1080p UI, the physical progress meter column maxes out at ~567/576 px (0.9844).
        # We check >= 0.96 (or peak_progress >= 0.96) to account for top rounded rim / border antialiasing.
        is_catch = bool((self.progress >= 0.96 or self.peak_progress >= 0.96) and self.step_count >= 15)

        # UI presence debounce: minigame ends when UI vanishes
        if not extraction.is_active:
            self.ui_lost_count += 1
        else:
            self.ui_lost_count = 0

        ui_lost = self.ui_lost_count >= self.ui_lost_threshold_frames

        # Stardew Valley BobberBar.cs (lines 594 & 603) ground truth: the minigame ONLY closes on catch or escape.
        # If UI vanishes after progress was high (peak >= 0.75), this is a confirmed catch.
        # Threshold lowered from 0.85 to 0.75: at 75%+ progress the fish is nearly caught; detection
        # dropouts from white-flash or momentary tracking loss should not count as a LOSS. The game
        # cannot physically transition from 75%+ progress to escape in less than ~1.5s, which is longer
        # than our ui_lost_threshold_frames window (20 frames @ 30 Hz = 0.67s).
        if ui_lost and self.peak_progress >= 0.75:
            is_catch = True

        # Escape: in Stardew Valley, p0=0.30 takes >= 1.67s to drain.
        # Require minimum elapsed steps (>= 25 ticks, ~0.83s) AND sustained zero progress (>= 8 frames)
        # OR UI vanishing after progress is low.
        is_escape = bool(
            not is_catch
            and (
                (self.progress_zero_count >= 8 and self.step_count >= self.min_steps_for_escape)
                or (self.ui_lost_count >= 6 and self.progress < 0.20 and self.step_count >= self.min_steps_for_escape)
            )
        )

        terminated = is_catch or is_escape or ui_lost

        # 8. Check truncation conditions (watchdog / timeout / focus loss)
        timeout = bool(self.step_count >= self.max_steps)
        foreground_lost = False
        if self.require_foreground and isinstance(self.actuator, DirectInputActuator):
            if not self.actuator.is_game_foreground():
                self.foreground_lost_count += 1
                if self.foreground_lost_count >= self.foreground_lost_threshold_frames:
                    foreground_lost = True
                    logger.warning(
                        "Episode truncated: Stardew Valley lost foreground focus for %d consecutive frames.",
                        self.foreground_lost_count,
                    )
            else:
                self.foreground_lost_count = 0

        truncated = timeout or foreground_lost

        # 9. Compute step reward matching simulation
        reward, rew_info = self.reward_calc.compute_step_reward(
            f_norm=extraction.fish_pos,
            b_norm=extraction.bar_pos,
            h_norm=extraction.bar_height,
            v_norm=extraction.bar_vel,
            p_prev=p_prev,
            p_curr=self.progress,
            is_catch=is_catch,
            is_escape=is_escape,
            is_truncated=truncated,
        )

        # 10. Safety: Release mouse if episode ends
        if terminated or truncated:
            self.actuator.release()

        self.prev_action = int(action)

        mean_in_bar = self.in_bar_steps / max(1, self.step_count)
        elapsed_s = time.perf_counter() - self.start_time

        info = {
            "is_catch": is_catch,
            "is_escape": is_escape,
            "ui_lost": ui_lost,
            "timeout": timeout,
            "foreground_lost": foreground_lost,
            "progress": self.progress,
            "peak_progress": self.peak_progress,
            "in_bar": extraction.in_bar,
            "is_active": extraction.is_active,
            "mean_in_bar": mean_in_bar,
            "bar_pos": extraction.bar_pos,
            "bar_height": extraction.bar_height,
            "fish_pos": extraction.fish_pos,
            "confidence": extraction.confidence,
            "latency_ms": tick_latency_ms,
            "elapsed_s": elapsed_s,
            "step_count": self.step_count,
            **rew_info,
        }

        if self.render_mode == "human":
            self.render()

        return extraction.features, reward, terminated, truncated, info

    def render(self, status_text: Optional[str] = None) -> Any:
        """Render environment state (visual preview window if human/rgb_array, or ASCII summary)."""
        if self.render_mode in ("human", "rgb_array"):
            from fisher.ui.preview import draw_preview_overlay
            full = self._last_full_frame
            if full is None:
                full, _ = self.capture.get_latest_frame()

            cap_name = getattr(self.capture, "name", type(self.capture).__name__)
            display = draw_preview_overlay(
                full_frame=full,
                extractor=self.extractor,
                extraction=self._last_extraction,
                current_roi=self.current_roi,
                fps=float(self.control_hz),
                driver_name=cap_name,
                status_text=status_text,
                recorder=self.video_recorder,
            )

            # Record frame if recorder is present and recording
            if self.video_recorder is not None and getattr(self.video_recorder, "is_recording", False):
                meta = {
                    "step": self.step_count,
                    "roi": list(self.current_roi) if self.current_roi else None,
                    "progress": float(self.progress),
                    "bar_pos": float(self._last_extraction.bar_pos) if self._last_extraction else 0.0,
                    "fish_pos": float(self._last_extraction.fish_pos) if self._last_extraction else 0.0,
                    "in_bar": bool(self._last_extraction.in_bar) if self._last_extraction else False,
                    "confidence": float(self._last_extraction.confidence) if self._last_extraction else 0.0,
                }
                self.video_recorder.write_frame(display, metadata=meta)

            if self.render_mode == "human":
                import cv2
                try:
                    if not self._window_initialized:
                        cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)
                        cv2.resizeWindow(self._window_name, 960, 540)
                        self._window_initialized = True
                    cv2.imshow(self._window_name, display)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), ord("Q"), 27):
                        logger.info("User requested preview window exit (q/ESC).")
                except Exception as exc:
                    logger.debug("Live preview display exception: %s", exc)

            return display

        # Default ASCII representation
        if self._last_extraction is None:
            return "No frame extracted yet."
        ext = self._last_extraction
        b_bar = int(ext.bar_pos * 30)
        f_pos = int(ext.fish_pos * 30)
        p_pct = int(ext.progress * 100)
        line = [" "] * 32
        line[min(31, b_bar)] = "["
        line[min(31, b_bar + 1)] = "]"
        line[min(31, f_pos)] = "F"
        bar_viz = "".join(line)
        lat = self.step_latencies_ms[-1] if self.step_latencies_ms else 0.0
        return f"|{bar_viz}| Prog: {p_pct:3d}% InBar: {ext.in_bar} Lat: {lat:.1f}ms"

    def close(self) -> None:
        """Cleanly release mouse, stop capture worker, restore timer period, and close preview windows."""
        self.actuator.emergency_release()
        if self.capture.is_running:
            self.capture.stop()
        self.timer.close()
        if self.video_recorder is not None and getattr(self.video_recorder, "is_recording", False):
            self.video_recorder.stop()
        if self.render_mode == "human" and self._window_initialized:
            try:
                import cv2
                cv2.destroyWindow(self._window_name)
            except Exception:
                pass
            self._window_initialized = False
