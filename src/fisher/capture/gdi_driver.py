"""Win32 GDI / BitBlt fallback capture driver."""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Optional, Tuple
import numpy as np

from fisher.capture.base import CaptureDriver
from fisher.utils.display import get_connected_displays, get_window_monitor_index

logger = logging.getLogger(__name__)


class GdiCaptureDriver(CaptureDriver):
    """Win32 GDI BitBlt capture driver for fallback capture across multi-monitor virtual space."""

    def __init__(
        self,
        monitor_idx: Optional[int] = None,
        roi: Optional[Tuple[int, int, int, int]] = None,
        target_fps: float = 60.0,
        auto_bind_window: Optional[str] = "Stardew Valley",
    ) -> None:
        self.monitor_idx = monitor_idx
        self.roi = roi
        self.target_fps = target_fps
        self.auto_bind_window = auto_bind_window

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._frame_count: int = 0
        self._dropped_frames: int = 0
        self._measured_fps: float = 0.0
        self._last_error: Optional[str] = None

    def _resolve_capture_rect(self) -> Tuple[int, int, int, int]:
        """Convert monitor-local ROI to virtual screen coordinates (left, top, width, height)."""
        displays = get_connected_displays()
        mon_idx = self.monitor_idx

        if mon_idx is None and self.auto_bind_window:
            mon_idx = get_window_monitor_index(self.auto_bind_window)
        if mon_idx is None or mon_idx >= len(displays):
            # Default to Screen 3 (index 2) or primary
            mon_idx = 2 if len(displays) > 2 else 0

        mon = displays[mon_idx]
        mon_left, mon_top, mon_right, mon_bottom = mon.rect

        if self.roi:
            rx0, ry0, rx1, ry1 = self.roi
            cap_left = mon_left + rx0
            cap_top = mon_top + ry0
            cap_width = rx1 - rx0
            cap_height = ry1 - ry0
        else:
            cap_left = mon_left
            cap_top = mon_top
            cap_width = mon.width
            cap_height = mon.height

        return cap_left, cap_top, cap_width, cap_height

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_worker, name="GdiCaptureThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def get_latest_frame(self) -> Tuple[Optional[np.ndarray], float]:
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
    def last_error(self) -> Optional[str]:
        return self._last_error

    def _capture_worker(self) -> None:
        if sys.platform != "win32":
            logger.warning("GdiCaptureDriver only supported on Windows")
            return

        try:
            import win32gui
            import win32ui
            import win32con
        except ImportError:
            self._last_error = "pywin32 not installed"
            return

        period = 1.0 / self.target_fps
        next_tick = time.perf_counter()
        fps_start = time.perf_counter()
        fps_count = 0

        while self._running:
            try:
                left, top, width, height = self._resolve_capture_rect()
                hdesktop = win32gui.GetDesktopWindow()
                hdc = win32gui.GetWindowDC(hdesktop)
                mdc = win32ui.CreateDCFromHandle(hdc)
                cdc = mdc.CreateCompatibleDC()

                bmp = win32ui.CreateBitmap()
                bmp.CreateCompatibleBitmap(mdc, width, height)
                cdc.SelectObject(bmp)
                cdc.BitBlt((0, 0), (width, height), mdc, (left, top), win32con.SRCCOPY)

                bits = bmp.GetBitmapBits(True)
                frame_bgra = np.frombuffer(bits, dtype=np.uint8).reshape((height, width, 4))
                frame_bgr = frame_bgra[:, :, :3].copy()

                now = time.perf_counter()
                with self._lock:
                    self._latest_frame = frame_bgr
                    self._latest_timestamp = now
                    self._frame_count += 1
                    fps_count += 1

                # Clean up GDI objects
                win32gui.DeleteObject(bmp.GetHandle())
                cdc.DeleteDC()
                mdc.DeleteDC()
                win32gui.ReleaseDC(hdesktop, hdc)

                dur = now - fps_start
                if dur >= 1.0:
                    self._measured_fps = fps_count / dur
                    fps_start = now
                    fps_count = 0

            except Exception as exc:
                self._last_error = str(exc)
                self._dropped_frames += 1
                time.sleep(0.02)

            next_tick += period
            time_to_wait = next_tick - time.perf_counter()
            if time_to_wait > 0.001:
                time.sleep(time_to_wait - 0.0005)
                while time.perf_counter() < next_tick:
                    pass
            elif time_to_wait < -period:
                next_tick = time.perf_counter()
