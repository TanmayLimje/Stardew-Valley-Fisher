"""Mock screen capture driver for CI and headless testing."""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple
import numpy as np

from fisher.capture.base import CaptureDriver


class MockCaptureDriver(CaptureDriver):
    """Produces synthetic 1080p frames at 60 Hz for testing without hardware."""

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        target_fps: float = 60.0,
    ):
        self.width = width
        self.height = height
        self.target_fps = target_fps
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._frame_count: int = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def get_latest_frame(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            return self._latest_frame, self._latest_timestamp

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def _capture_worker(self) -> None:
        period = 1.0 / self.target_fps
        next_tick = time.perf_counter()

        while self._running:
            # Create a simple synthetic frame (gray background with timestamp marker)
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            # Add a vertical bar representation
            center_y = int(self.height / 2 + 100 * np.sin(self._frame_count * 0.1))
            cv_bar_top = max(0, center_y - 40)
            cv_bar_bottom = min(self.height, center_y + 40)
            frame[cv_bar_top:cv_bar_bottom, 1600:1640] = [0, 220, 50]  # Green bobber bar

            now = time.perf_counter()
            with self._lock:
                self._latest_frame = frame
                self._latest_timestamp = now
                self._frame_count += 1

            next_tick += period
            time_to_wait = next_tick - time.perf_counter()
            if time_to_wait > 0:
                time.sleep(time_to_wait)
            else:
                next_tick = time.perf_counter()
