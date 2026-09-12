"""High-precision OS timer and scheduler utilities for Windows."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from contextlib import contextmanager
from typing import Dict, Generator


def is_windows() -> bool:
    return sys.platform == "win32"


def check_admin() -> bool:
    """Return True if the current process has administrative privileges."""
    if not is_windows():
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


class WindowsTimerPrecision:
    """Manages Windows Multimedia Timer resolution via winmm.timeBeginPeriod."""

    _instance: WindowsTimerPrecision | None = None

    def __init__(self, target_period_ms: int = 1):
        self.target_period_ms = target_period_ms
        self._active = False
        self._winmm = None
        if is_windows():
            try:
                self._winmm = ctypes.windll.winmm
            except Exception:
                self._winmm = None

    def acquire(self) -> bool:
        if self._active or self._winmm is None:
            return False
        ret = self._winmm.timeBeginPeriod(self.target_period_ms)
        if ret == 0:  # TIMERR_NOERROR
            self._active = True
            return True
        return False

    def release(self) -> bool:
        if not self._active or self._winmm is None:
            return False
        ret = self._winmm.timeEndPeriod(self.target_period_ms)
        self._active = False
        return ret == 0

    @classmethod
    def get_singleton(cls, target_period_ms: int = 1) -> WindowsTimerPrecision:
        if cls._instance is None:
            cls._instance = cls(target_period_ms)
        return cls._instance


@contextmanager
def precise_clock_scope(period_ms: int = 1) -> Generator[None, None, None]:
    """Context manager setting 1 ms timer period for the duration of the block."""
    timer = WindowsTimerPrecision.get_singleton(period_ms)
    acquired = timer.acquire()
    try:
        yield
    finally:
        if acquired:
            timer.release()


def hybrid_sleep(seconds: float) -> None:
    """Sleep accurately using OS sleep for the bulk duration, spinlocking the final ~1.5 ms."""
    if seconds <= 0:
        return
    start = time.perf_counter()
    target = start + seconds

    # Sleep coarse interval if duration is large enough
    margin = 0.0015  # 1.5 ms margin
    if seconds > margin:
        time.sleep(seconds - margin)

    # Spinlock the remainder for sub-millisecond precision
    while time.perf_counter() < target:
        pass


def measure_jitter(target_hz: float = 30.0, iterations: int = 100) -> Dict[str, float]:
    """Measure loop timing jitter at a given target frequency (e.g. 30 Hz = 33.33 ms)."""
    period = 1.0 / target_hz
    jitters_ms = []

    with precise_clock_scope(1):
        next_tick = time.perf_counter()
        for _ in range(iterations):
            next_tick += period
            time_to_wait = next_tick - time.perf_counter()
            hybrid_sleep(time_to_wait)
            now = time.perf_counter()
            actual_error = abs((now - next_tick) * 1000.0)
            jitters_ms.append(actual_error)

    jitters_ms.sort()
    p50 = jitters_ms[int(len(jitters_ms) * 0.50)]
    p90 = jitters_ms[int(len(jitters_ms) * 0.90)]
    p99 = jitters_ms[min(int(len(jitters_ms) * 0.99), len(jitters_ms) - 1)]
    max_jitter = jitters_ms[-1]

    return {
        "target_hz": target_hz,
        "target_period_ms": period * 1000.0,
        "iterations": iterations,
        "p50_ms": p50,
        "p90_ms": p90,
        "p99_ms": p99,
        "max_ms": max_jitter,
    }


class HighPrecisionTimer:
    """Tick-synchronized high-precision timer for real-time control loops (e.g. 30 Hz)."""

    def __init__(self, target_hz: float = 30.0, timer_period_ms: int = 1):
        self.target_hz = target_hz
        self.period_s = 1.0 / target_hz
        self.timer_period_ms = timer_period_ms
        self._win_timer = WindowsTimerPrecision.get_singleton(timer_period_ms)
        self._win_timer.acquire()
        self._next_tick = time.perf_counter()

    def reset(self) -> None:
        """Reset the tick anchor to the current clock time."""
        self._next_tick = time.perf_counter()

    def sleep_until_next_tick(self) -> float:
        """Sleep until the next scheduled tick using hybrid sleep-spinlock."""
        self._next_tick += self.period_s
        now = time.perf_counter()
        remaining = self._next_tick - now
        if remaining > 0:
            hybrid_sleep(remaining)
        else:
            # Timing overrun: resync next tick to current time to avoid cumulative drift
            self._next_tick = time.perf_counter()
        return time.perf_counter()

    def close(self) -> None:
        """Release multimedia timer resolution."""
        if self._win_timer is not None:
            self._win_timer.release()
