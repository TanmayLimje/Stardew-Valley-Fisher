"""Multi-monitor query and dynamic window-to-display binding for Windows."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class DisplayInfo:
    index: int
    device_name: str
    rect: Tuple[int, int, int, int]  # (left, top, right, bottom)
    width: int
    height: int
    is_primary: bool

    def contains_point(self, x: int, y: int) -> bool:
        left, top, right, bottom = self.rect
        return left <= x < right and top <= y < bottom


def get_connected_displays() -> List[DisplayInfo]:
    """Enumerate all active displays on Windows."""
    if sys.platform != "win32":
        return [
            DisplayInfo(
                index=0,
                device_name="MOCK_DISPLAY",
                rect=(0, 0, 1920, 1080),
                width=1920,
                height=1080,
                is_primary=True,
            )
        ]

    try:
        import win32api
        import win32con

        monitors = win32api.EnumDisplayMonitors()
        displays: List[DisplayInfo] = []

        for idx, (hmon, _, _) in enumerate(monitors):
            info = win32api.GetMonitorInfo(hmon)
            monitor_rect = info["Monitor"]  # (left, top, right, bottom)
            device_name = info.get("Device", f"\\\\.\\DISPLAY{idx+1}")
            flags = info.get("Flags", 0)
            is_primary = bool(flags & win32con.MONITORINFOF_PRIMARY)
            width = monitor_rect[2] - monitor_rect[0]
            height = monitor_rect[3] - monitor_rect[1]

            displays.append(
                DisplayInfo(
                    index=idx,
                    device_name=device_name,
                    rect=monitor_rect,
                    width=width,
                    height=height,
                    is_primary=is_primary,
                )
            )
        return displays
    except Exception:
        return []


def find_window_hwnd(window_title: str) -> Optional[int]:
    """Find top-level window handle by fuzzy case-insensitive substring match.

    Searches all visible top-level windows for any whose title contains
    ``window_title`` (case-insensitive). Falls back to exact ``FindWindow``
    for efficiency, then tries ``EnumWindows`` with substring matching.
    This handles SMAPI, Steam overlay, or versioned titles like
    "Stardew Valley 1.6.15".
    """
    if sys.platform != "win32":
        return None
    try:
        import win32gui

        # Fast path: exact title match
        hwnd = win32gui.FindWindow(None, window_title)
        if hwnd and win32gui.IsWindow(hwnd):
            return hwnd

        # Slow path: fuzzy substring search across all top-level windows
        needle = window_title.lower()
        matches: list[int] = []

        def _enum_cb(hwnd: int, results: list) -> None:
            if win32gui.IsWindowVisible(hwnd):
                try:
                    text = win32gui.GetWindowText(hwnd)
                    if text and needle in text.lower():
                        results.append(hwnd)
                except Exception:
                    pass

        win32gui.EnumWindows(_enum_cb, matches)
        if matches:
            return matches[0]
    except Exception:
        pass
    return None


def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """Return (left, top, right, bottom) of window."""
    if sys.platform != "win32":
        return None
    try:
        import win32gui

        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return None


def get_window_monitor_index(window_title: str) -> Optional[int]:
    """Identify which display index (0, 1, 2...) hosts the specified window."""
    if sys.platform != "win32":
        return 0

    hwnd = find_window_hwnd(window_title)
    if not hwnd:
        return None

    try:
        import win32api
        import win32con

        hmon_target = win32api.MonitorFromWindow(
            hwnd, win32con.MONITOR_DEFAULTTONEAREST
        )
        monitors = win32api.EnumDisplayMonitors()
        for idx, (hmon, _, _) in enumerate(monitors):
            if int(hmon) == int(hmon_target):
                return idx
    except Exception:
        pass

    return None
