"""BetterCam DXGI Desktop Duplication capture driver for Windows."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple
import numpy as np

from fisher.capture.base import CaptureDriver
from fisher.utils.display import get_window_monitor_index

logger = logging.getLogger(__name__)


class BetterCamCaptureDriver(CaptureDriver):
    """60 Hz DXGI Desktop Duplication capture driver using BetterCam."""

    def __init__(
        self,
        device_idx: int = 0,
        output_idx: Optional[int] = 1,
        roi: Optional[Tuple[int, int, int, int]] = None,
        target_fps: float = 60.0,
        auto_bind_window: Optional[str] = "Stardew Valley",
    ) -> None:
        """
        Args:
            device_idx: DXGI adapter index (0 = discrete GPU, e.g. RTX 4060).
            output_idx: Monitor output index on adapter (1 = Screen 3 / DISPLAY6).
            roi: Optional (x0, y0, x1, y1) crop region within target display.
            target_fps: Target acquisition rate (nominal 60.0 Hz).
            auto_bind_window: If set, query window title to resolve target display.
        """
        self.device_idx = device_idx
        self.output_idx = output_idx
        self.roi = roi
        self.target_fps = target_fps
        self.auto_bind_window = auto_bind_window

        self._camera = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._prev_timestamp: float = 0.0
        self._frame_count: int = 0
        self._dropped_frames: int = 0
        self._monotonic_violations: int = 0

        # Rolling FPS calculation
        self._fps_window_start: float = 0.0
        self._fps_window_count: int = 0
        self._measured_fps: float = 0.0

        self._last_error: Optional[str] = None

    def _resolve_monitor(self) -> Tuple[int, int]:
        """Resolve device and output indices, matching multi-monitor topology."""
        if self.auto_bind_window:
            mon_idx = get_window_monitor_index(self.auto_bind_window)
            if mon_idx is not None:
                # Screen 3 is display index 2 (DISPLAY6) -> Device 0 Output 1
                if mon_idx == 2:
                    return (0, 1)
                elif mon_idx == 1:
                    return (0, 0)
                elif mon_idx == 0:
                    return (1, 0)
        out_idx = self.output_idx if self.output_idx is not None else 1
        return (self.device_idx, out_idx)

    def _init_camera(self) -> bool:
        """Instantiate bettercam camera object."""
        try:
            import bettercam

            dev_idx, out_idx = self._resolve_monitor()
            logger.info("Initializing BetterCam on Device[%d] Output[%d]", dev_idx, out_idx)
            self._camera = bettercam.create(
                device_idx=dev_idx,
                output_idx=out_idx,
                region=self.roi,
                output_color="BGR",
                max_buffer_len=1,
            )
            self._last_error = None
            return True
        except Exception as exc:
            self._last_error = f"BetterCam init failed: {exc}"
            logger.warning("%s (desktop composition or active session may be unavailable)", self._last_error)
            self._camera = None
            return False

    def start(self) -> None:
        """Start the capture worker thread."""
        if self._running:
            return

        if not self._init_camera():
            logger.warning("BetterCam camera could not be created at start(); will retry in worker.")

        self._running = True
        self._thread = threading.Thread(target=self._capture_worker, name="BetterCamCaptureThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop capture worker and release camera."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None

    def get_latest_frame(self) -> Tuple[Optional[np.ndarray], float]:
        """Atomically return the most recent frame and timestamp."""
        with self._lock:
            return self._latest_frame, self._latest_timestamp

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @property
    def measured_fps(self) -> float:
        return self._measured_fps

    @property
    def monotonic_violations(self) -> int:
        return self._monotonic_violations

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def _capture_worker(self) -> None:
        """Dedicated 60 Hz acquisition loop with atomic latest-frame swap."""
        period = 1.0 / self.target_fps
        next_tick = time.perf_counter()
        self._fps_window_start = time.perf_counter()
        self._fps_window_count = 0

        retry_backoff = 1.0

        while self._running:
            if self._camera is None:
                time.sleep(retry_backoff)
                if not self._init_camera():
                    retry_backoff = min(5.0, retry_backoff * 1.5)
                    continue
                retry_backoff = 1.0

            try:
                frame = self._camera.grab()
                now = time.perf_counter()

                if frame is not None:
                    if self.roi is not None and (
                        frame.shape[1] != (self.roi[2] - self.roi[0])
                        or frame.shape[0] != (self.roi[3] - self.roi[1])
                    ):
                        x0, y0, x1, y1 = self.roi
                        frame = frame[y0:y1, x0:x1]

                    with self._lock:
                        if self._latest_timestamp > 0.0 and now <= self._latest_timestamp:
                            self._monotonic_violations += 1
                        self._latest_frame = frame
                        self._latest_timestamp = now
                        self._frame_count += 1
                        self._fps_window_count += 1

                    window_duration = now - self._fps_window_start
                    if window_duration >= 1.0:
                        self._measured_fps = self._fps_window_count / window_duration
                        self._fps_window_start = now
                        self._fps_window_count = 0
                else:
                    self._dropped_frames += 1

            except Exception as exc:
                self._last_error = f"BetterCam grab error: {exc}"
                logger.debug("BetterCam grab error: %s", exc)
                time.sleep(0.05)

            next_tick += period
            time_to_wait = next_tick - time.perf_counter()
            if time_to_wait > 0.001:
                time.sleep(time_to_wait - 0.0008)
                while time.perf_counter() < next_tick:
                    pass
            elif time_to_wait < -period:
                next_tick = time.perf_counter()
