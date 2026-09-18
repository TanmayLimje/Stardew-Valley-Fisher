"""DirectInput mouse actuator for Stardew Valley.

Uses pydirectinput with zero inter-action pause, idempotent press states,
and window foreground guards to prevent input leakage across multi-monitor setups.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from typing import Optional

import pydirectinput
import win32gui

from fisher.input.base import Actuator

logger = logging.getLogger("fisher.input.direct_input")

# CRITICAL: Disable pydirectinput default 100ms sleep and corner failsafe
# (Emergency killswitch is handled by F9 hotkey and ESC keypress)
pydirectinput.PAUSE = 0.0
pydirectinput.FAILSAFE = False


def is_admin_process() -> bool:
    """Check if the current Python process is running with Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


class DirectInputActuator(Actuator):
    """Concrete mouse actuator dispatching DirectInput scan codes to Stardew Valley."""

    def __init__(
        self,
        window_title: str = "Stardew Valley",
        strict_foreground: bool = True,
    ) -> None:
        self.window_title = window_title
        self.strict_foreground = strict_foreground
        self._pressed = False
        self._last_press_time = 0.0
        self._last_release_time = 0.0
        self._held_keys: set[str] = set()
        self._keys_lock = threading.Lock()

        if not is_admin_process():
            logger.warning(
                "Python process is NOT elevated. If Stardew Valley runs as Administrator, "
                "Windows UIPI will silently drop DirectInput events. Consider running as Admin."
            )

    def find_game_window(self) -> Optional[int]:
        """Find the HWND of the Stardew Valley window."""
        target_hwnd = None

        def enum_handler(hwnd: int, extra: list) -> None:
            if win32gui.IsWindowVisible(hwnd):
                text = win32gui.GetWindowText(hwnd)
                if self.window_title.lower() in text.lower():
                    extra.append(hwnd)

        hwnds: list[int] = []
        try:
            win32gui.EnumWindows(enum_handler, hwnds)
            if hwnds:
                target_hwnd = hwnds[0]
        except Exception as exc:
            logger.debug(f"Error enumerating windows: {exc}")

        return target_hwnd

    def focus_game_window(self) -> bool:
        """Bring Stardew Valley window to foreground if not already active."""
        if self.is_game_foreground():
            return True

        hwnd = self.find_game_window()
        if hwnd:
            try:
                import win32con
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.05)
                return True
            except Exception as exc:
                logger.debug(f"Could not bring game window to foreground: {exc}")
        return False

    def ensure_cursor_in_window(self) -> bool:
        """
        Verify that the mouse cursor is located inside Stardew Valley window bounds.
        If the cursor is outside (e.g. resting on Screen 2 terminal), move it to the
        safe center of the game client on Screen 3 to prevent clicks from stealing focus.
        """
        hwnd = self.find_game_window()
        if not hwnd:
            return False

        try:
            rect = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
            l, t, r, b = rect
            if r <= l or b <= t:
                return False

            import win32api
            cur_x, cur_y = win32api.GetCursorPos()
            if l <= cur_x <= r and t <= cur_y <= b:
                return True

            cx = l + (r - l) // 2
            cy = t + (b - t) // 2
            win32api.SetCursorPos((cx, cy))
            logger.info("Repositioned mouse cursor to game window center: (%d, %d)", cx, cy)
            return True
        except Exception as exc:
            logger.debug(f"Error ensuring cursor inside game window: {exc}")
            return False

    def is_game_foreground(self) -> bool:
        """Verify whether Stardew Valley is the active foreground window."""
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return False
            title = win32gui.GetWindowText(hwnd)
            return self.window_title.lower() in title.lower()
        except Exception as exc:
            logger.debug(f"Error querying foreground window: {exc}")
            return False

    def press_down(self) -> None:
        """Hold down LMB if game window is in foreground."""
        if self.strict_foreground and not self.is_game_foreground():
            if self._pressed:
                # If window lost focus while held down, release immediately
                self.emergency_release()
            return

        if not self._pressed:
            try:
                pydirectinput.mouseDown(button="left")
                self._pressed = True
                self._last_press_time = time.perf_counter()
            except Exception as exc:
                logger.error(f"Failed to dispatch mouseDown: {exc}")

    def release(self) -> None:
        """Release LMB."""
        if self._pressed:
            try:
                pydirectinput.mouseUp(button="left")
            except Exception as exc:
                logger.error(f"Failed to dispatch mouseUp: {exc}")
            finally:
                self._pressed = False
                self._last_release_time = time.perf_counter()

    def emergency_release(self) -> None:
        """Force mouse release and release all held keys regardless of cached state."""
        try:
            pydirectinput.mouseUp(button="left")
        except Exception as exc:
            logger.debug(f"Emergency mouseUp dispatch error: {exc}")
        finally:
            self._pressed = False
            self._last_release_time = time.perf_counter()
        self.release_all_keys()

    def set_press(self, pressed: bool) -> None:
        """Idempotently set mouse press state."""
        if pressed:
            if not self._pressed:
                self.press_down()
        else:
            if self._pressed:
                self.release()

    def send_escape(self) -> None:
        """Send ESC scan code to trigger native emergencyShutDown in BobberBar.cs."""
        try:
            pydirectinput.press("esc")
            logger.info("Dispatched ESC to trigger native emergencyShutDown")
        except Exception as exc:
            logger.error(f"Failed to send ESC: {exc}")

    def click(self, button: str = "left", duration: float = 0.05) -> None:
        """Helper to perform a timed click (e.g. for cast, hook, or dialogs)."""
        if self.strict_foreground and not self.is_game_foreground():
            logger.warning("Click dropped: game window not in foreground.")
            return

        try:
            pydirectinput.mouseDown(button=button)
            time.sleep(duration)
            pydirectinput.mouseUp(button=button)
        except Exception as exc:
            logger.error(f"Click failed for button {button}: {exc}")

    def right_click(self, duration: float = 0.05) -> None:
        """Helper to perform right click (e.g. eating food or interacting)."""
        self.click(button="right", duration=duration)

    @property
    def is_pressed(self) -> bool:
        """Current LMB press state."""
        return self._pressed

    # ------------------------------------------------------------------
    # Keyboard & cursor (waterer WASD navigation / tool aiming)
    # ------------------------------------------------------------------

    def key_down(self, key: str) -> None:
        """Hold a keyboard key down via DirectInput scan code.

        Guarded by strict_foreground to prevent input leakage to other monitors.
        """
        if self.strict_foreground and not self.is_game_foreground():
            return
        try:
            pydirectinput.keyDown(key)
            with self._keys_lock:
                self._held_keys.add(key)
        except Exception as exc:
            logger.error(f"Failed to dispatch keyDown({key}): {exc}")

    def key_up(self, key: str) -> None:
        """Release a keyboard key via DirectInput scan code."""
        try:
            pydirectinput.keyUp(key)
        except Exception as exc:
            logger.error(f"Failed to dispatch keyUp({key}): {exc}")
        finally:
            with self._keys_lock:
                self._held_keys.discard(key)

    def move_cursor(self, x: int, y: int) -> None:
        """Park the OS cursor on a target tile's screen position (tool aiming).

        Uses win32api.SetCursorPos (same precedent as ensure_cursor_in_window).
        """
        if self.strict_foreground and not self.is_game_foreground():
            return
        try:
            import win32api
            win32api.SetCursorPos((x, y))
        except Exception as exc:
            logger.error(f"Failed to move cursor to ({x}, {y}): {exc}")

    def release_all_keys(self) -> None:
        """Release all currently held keyboard keys.

        Thread-safe: called from the SafetySupervisor daemon thread so the
        F9 killswitch meets the < 200 ms release gate even while the main
        loop is blocked in a WASD hold sleep.
        """
        with self._keys_lock:
            for key in list(self._held_keys):
                try:
                    pydirectinput.keyUp(key)
                except Exception:
                    pass
            self._held_keys.clear()

