"""Simplified safety supervisor for the on-demand fishing assistant.

Provides F9 killswitch polling and basic watchdog checks. Unlike the full
autonomous FSM safety system (Phase 4 original), this supervisor only handles:
- F9 killswitch → release mouse, stop assistant
- Ctrl+F9 → hard os._exit(1)
- Foreground guard → pause (return to IDLE), not full abort
- Capture health → release mouse and retry on FPS drop
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("fisher.orchestration.safety")


class SafetySupervisor:
    """F9 killswitch + basic watchdogs for the on-demand assistant.

    Runs a daemon thread polling for the killswitch key at ~50 Hz.
    The assistant checks ``is_abort_requested()`` at the top of every loop iteration.

    ``on_abort`` is invoked **directly from the polling thread** the instant an
    abort is detected. Actuators pass ``emergency_release`` here so held keys
    and mouse buttons are released cross-thread within one poll period even if
    the main loop is blocked in a long animation-lock sleep.
    """

    def __init__(
        self,
        killswitch_key: str = "f9",
        hard_abort_key: str = "ctrl+f9",
        poll_hz: float = 50.0,
        on_abort: Optional[Callable[[], None]] = None,
    ) -> None:
        self.killswitch_key = killswitch_key
        self.hard_abort_key = hard_abort_key
        self._poll_period = 1.0 / poll_hz
        self._on_abort = on_abort

        self._abort_requested = threading.Event()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._abort_reason: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the killswitch polling daemon thread."""
        if self._running:
            return
        if not self._abort_requested.is_set():
            self._abort_reason = ""
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="safety-supervisor"
        )
        self._thread.start()
        logger.info("Safety supervisor started (killswitch=%s)", self.killswitch_key)

    def stop(self) -> None:
        """Stop the daemon thread."""
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def is_abort_requested(self) -> bool:
        """Check whether an abort has been requested (non-blocking)."""
        return self._abort_requested.is_set()

    def request_abort(self, reason: str = "manual") -> None:
        """Programmatically request abort from any thread."""
        self._abort_reason = reason
        if not self._abort_requested.is_set():
            self._abort_requested.set()
            self._fire_on_abort()
        logger.warning("Abort requested: %s", reason)

    @property
    def abort_reason(self) -> str:
        return self._abort_reason

    def _fire_on_abort(self) -> None:
        """Invoke the abort callback once, swallowing callback failures."""
        if self._on_abort is None:
            return
        try:
            self._on_abort()
        except Exception as exc:
            logger.debug("Abort callback failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Poll killswitch key at ~50 Hz. Daemon thread target."""
        try:
            import keyboard
        except ImportError:
            logger.warning(
                "keyboard library not available; killswitch polling disabled. "
                "Install with: pip install keyboard"
            )
            return

        while self._running:
            try:
                # Hard abort: Ctrl+F9 → release inputs, then immediate process exit
                if keyboard.is_pressed(self.hard_abort_key):
                    logger.critical("HARD ABORT: %s pressed — terminating process.", self.hard_abort_key)
                    if not self._abort_requested.is_set():
                        self._abort_reason = f"hard abort ({self.hard_abort_key})"
                        self._abort_requested.set()
                        self._fire_on_abort()
                    os._exit(1)

                # Soft abort: F9 → set flag for clean shutdown
                if keyboard.is_pressed(self.killswitch_key):
                    if not self._abort_requested.is_set():
                        self._abort_reason = f"killswitch ({self.killswitch_key})"
                        self._abort_requested.set()
                        self._fire_on_abort()
                    logger.warning("Killswitch %s pressed — requesting clean abort.", self.killswitch_key)
                    # Debounce: wait for key release before continuing to poll
                    while self._running and keyboard.is_pressed(self.killswitch_key):
                        time.sleep(0.02)
                    return  # Stop polling after killswitch fires

            except Exception as exc:
                logger.debug("Killswitch poll exception: %s", exc)

            time.sleep(self._poll_period)
