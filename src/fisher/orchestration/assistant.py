"""On-demand fishing minigame assistant.

Watches Screen 3 at low frequency (idle scan). When the BobberBar minigame UI
is detected, takes over mouse control with the trained PPO policy for the
duration of the minigame (~5-20s), then releases control and returns to idle.

Architecture:
    IDLE (10 Hz scan) → RL_ACTIVE (30 Hz policy loop) → IDLE

The player casts, waits for the bite, and hooks the fish themselves. The
assistant only intervenes during the minigame, then hands control back.
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from fisher.capture.base import CaptureDriver
from fisher.capture import create_capture_driver
from fisher.config import FisherConfig, load_config
from fisher.env.live_env import LiveFishingEnv
from fisher.env.rewards import RewardCalculator, RewardConfig
from fisher.extraction.extractor import FeatureExtractor
from fisher.extraction.types import TrackBounds
from fisher.input.base import Actuator
from fisher.input import create_actuator
from fisher.orchestration.safety import SafetySupervisor

logger = logging.getLogger("fisher.orchestration.assistant")


class AssistantState(enum.Enum):
    """Two-state machine for the on-demand assistant."""
    IDLE = "IDLE"
    RL_ACTIVE = "RL_ACTIVE"
    STOPPED = "STOPPED"


@dataclass
class MinigameResult:
    """Outcome of a single minigame episode."""
    caught: bool = False
    escaped: bool = False
    truncated: bool = False
    duration_s: float = 0.0
    steps: int = 0
    peak_progress: float = 0.0
    mean_in_bar: float = 0.0
    termination_reason: str = ""


@dataclass
class AssistantStats:
    """Session-level statistics for the assistant."""
    session_start: float = 0.0
    minigames_played: int = 0
    catches: int = 0
    escapes: int = 0
    truncations: int = 0
    results: list[MinigameResult] = field(default_factory=list)


class FishingAssistant:
    """On-demand fishing minigame assistant.

    Watches Screen 3 at low frequency. When the BobberBar minigame UI is
    detected, takes over mouse control with the trained PPO policy for the
    duration of the minigame (~5-20s), then releases control.

    Usage::

        assistant = FishingAssistant.from_config(config)
        assistant.run()  # Blocks until F9 or Ctrl+C
    """

    def __init__(
        self,
        config: FisherConfig,
        capture_driver: CaptureDriver,
        actuator: Actuator,
        extractor: FeatureExtractor,
        policy: Any,  # SB3 BaseAlgorithm or compatible .predict() interface
        obs_normalizer: Any = None,  # VecNormalize stats
        supervisor: Optional[SafetySupervisor] = None,
        idle_scan_hz: float = 10.0,
        detection_confirm_frames: int = 2,
        transition_delay_ms: float = 200.0,
        show_notifications: bool = True,
        preview: bool = False,
        require_foreground: bool = False,
    ) -> None:
        self.config = config
        self.capture = capture_driver
        self.actuator = actuator
        self.extractor = extractor
        self.policy = policy
        self.obs_normalizer = obs_normalizer
        self.supervisor = supervisor or SafetySupervisor(
            killswitch_key=config.safety.get("killswitch_key", "f9"),
            hard_abort_key=config.safety.get("hard_abort", "ctrl+f9"),
        )

        self.idle_scan_hz = idle_scan_hz
        self.idle_period_s = 1.0 / idle_scan_hz
        self.detection_confirm_frames = detection_confirm_frames
        self.transition_delay_ms = transition_delay_ms
        self.show_notifications = show_notifications
        self.preview = preview
        self.require_foreground = require_foreground

        self.state = AssistantState.IDLE
        self.stats = AssistantStats()

        # Console output (Rich if available, fallback to print)
        self._console = None
        if show_notifications:
            try:
                from rich.console import Console
                self._console = Console()
            except ImportError:
                pass

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: Optional[FisherConfig] = None,
        policy_path: str = "models/ppo_fisher_best.zip",
        stats_path: str = "models/vec_normalize_best.pkl",
        preview: bool = False,
        mock_mode: bool = False,
    ) -> "FishingAssistant":
        """Build an assistant from config, loading the trained PPO policy."""
        config = config or load_config()

        # Capture driver
        if mock_mode:
            from fisher.capture.mock_driver import MockCaptureDriver
            capture = MockCaptureDriver()
        else:
            capture = create_capture_driver(config)

        # Actuator
        if mock_mode:
            from fisher.input.mock_actuator import MockActuator
            actuator = MockActuator()
        else:
            actuator = create_actuator(config, strict_foreground=False)

        # Feature extractor
        tb_dict = config.capture.get("track_bounds")
        track_bounds = None
        if tb_dict:
            track_bounds = TrackBounds(
                x0=tb_dict["x0"], y0=tb_dict["y0"],
                x1=tb_dict["x1"], y1=tb_dict["y1"],
                height_px=tb_dict.get("height_px", 568),
            )
        extractor = FeatureExtractor(bounds=track_bounds)

        # Load policy
        policy = None
        obs_normalizer = None
        policy_file = Path(policy_path)
        if policy_file.exists():
            try:
                from stable_baselines3 import PPO
                policy = PPO.load(str(policy_file), device="cpu")
                logger.info("Loaded PPO policy from %s", policy_file)
            except Exception as exc:
                logger.warning("Failed to load PPO policy: %s", exc)

        stats_file = Path(stats_path)
        if stats_file.exists():
            try:
                import pickle
                with open(stats_file, "rb") as f:
                    obs_normalizer = pickle.load(f)
                logger.info("Loaded VecNormalize stats from %s", stats_file)
            except Exception as exc:
                logger.warning("Failed to load obs normalizer: %s", exc)

        # Assistant config section
        asst_cfg = config.raw.get("assistant", {})

        return cls(
            config=config,
            capture_driver=capture,
            actuator=actuator,
            extractor=extractor,
            policy=policy,
            obs_normalizer=obs_normalizer,
            idle_scan_hz=float(asst_cfg.get("idle_scan_hz", 10.0)),
            detection_confirm_frames=int(asst_cfg.get("detection_confirm_frames", 2)),
            transition_delay_ms=float(asst_cfg.get("transition_delay_ms", 200.0)),
            show_notifications=bool(asst_cfg.get("show_notifications", True)),
            preview=preview,
            require_foreground=bool(asst_cfg.get("require_foreground", False)),
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> AssistantStats:
        """Main blocking loop: IDLE scanning → RL_ACTIVE → back to IDLE.

        Returns session stats when stopped (F9, Ctrl+C, or max episodes).
        """
        self.stats = AssistantStats(session_start=time.perf_counter())
        self.state = AssistantState.IDLE

        # Start subsystems
        if not self.capture.is_running:
            self.capture.start()
        self.supervisor.start()

        self._notify("🎣 Fisher Assistant started — watching for minigame...", style="bold green")
        self._notify(f"   Press {self.config.safety.get('killswitch_key', 'F9').upper()} to stop.", style="dim")

        max_episodes = int(self.config.safety.get("max_episodes", 100))

        try:
            while self.state != AssistantState.STOPPED:
                # Check killswitch at top of every iteration
                if self.supervisor.is_abort_requested():
                    self._notify(
                        f"⛔ Killswitch activated ({self.supervisor.abort_reason}). Stopping.",
                        style="bold red",
                    )
                    break

                if self.stats.minigames_played >= max_episodes:
                    self._notify(f"📊 Max episodes ({max_episodes}) reached. Stopping.", style="bold yellow")
                    break

                if self.state == AssistantState.IDLE:
                    detected = self._idle_scan()
                    if detected:
                        self.state = AssistantState.RL_ACTIVE
                        result = self._play_minigame()
                        self._record_result(result)
                        self.state = AssistantState.IDLE

                elif self.state == AssistantState.RL_ACTIVE:
                    # Should not reach here — _play_minigame handles this inline
                    self.state = AssistantState.IDLE

        except KeyboardInterrupt:
            self._notify("⛔ Ctrl+C — stopping assistant.", style="bold red")
        finally:
            self.state = AssistantState.STOPPED
            self.actuator.release()
            self.supervisor.stop()
            self._print_summary()

        return self.stats

    # ------------------------------------------------------------------
    # IDLE state: low-frequency scan
    # ------------------------------------------------------------------

    def _idle_scan(self, max_frames: Optional[int] = None) -> bool:
        """Scan at idle_scan_hz for the BobberBar minigame UI.

        Returns True when the minigame is confirmed (detection_confirm_frames
        consecutive positive detections).
        """
        consecutive_detections = 0
        self.extractor.reset()
        frames_scanned = 0

        while not self.supervisor.is_abort_requested():
            scan_start = time.perf_counter()

            frame, ts = self.capture.get_latest_frame()
            if frame is None:
                time.sleep(self.idle_period_s)
                continue

            if max_frames is not None and frames_scanned >= max_frames:
                return False
            frames_scanned += 1

            # Crop to static ROI if we have a full-screen frame
            roi_dict = self.config.capture.get("roi")
            if roi_dict and (frame.shape[1] > 600 or frame.shape[0] > 800):
                x0, y0 = int(roi_dict["x0"]), int(roi_dict["y0"])
                x1, y1 = int(roi_dict["x1"]), int(roi_dict["y1"])
                sub_frame = frame[y0:y1, x0:x1]
            else:
                sub_frame = frame

            # Check for minigame track presence
            is_active, confidence = self.extractor.track_detector.detect_track(sub_frame)

            if is_active and confidence >= 0.50:
                # Also check progress to filter false positives
                p, _ = self.extractor.progress_tracker.extract(sub_frame)
                if p >= 0.15:
                    consecutive_detections += 1
                    if consecutive_detections >= self.detection_confirm_frames:
                        logger.info(
                            "Minigame detected (conf=%.2f, p=%.2f, %d consecutive frames)",
                            confidence, p, consecutive_detections,
                        )
                        return True
                else:
                    consecutive_detections = 0
            else:
                consecutive_detections = 0

            # Pace the idle loop
            elapsed = time.perf_counter() - scan_start
            remaining = self.idle_period_s - elapsed
            if remaining > 0:
                time.sleep(remaining)

        return False

    # ------------------------------------------------------------------
    # RL_ACTIVE state: play one minigame episode
    # ------------------------------------------------------------------

    def _play_minigame(self) -> MinigameResult:
        """Hand control to LiveFishingEnv for one minigame episode."""
        self._notify("🎯 Minigame detected — AI taking control...", style="bold cyan")

        # Brief transition delay to let the UI settle
        delay_s = self.transition_delay_ms / 1000.0
        if delay_s > 0:
            time.sleep(delay_s)

        # Build a LiveFishingEnv wired to our existing capture/actuator/extractor
        env = LiveFishingEnv(
            config=self.config,
            capture_driver=self.capture,
            actuator=self.actuator,
            extractor=self.extractor,
            control_hz=int(self.config.control.get("hz", 30)),
            max_duration_s=float(self.config.reward.get("t_max_s", 30)),
            auto_start_capture=False,  # Already running
            wait_for_ui_on_reset=False,  # We already detected the UI
            require_foreground=self.require_foreground,
            render_mode="human" if self.preview else None,
        )

        result = MinigameResult()
        ep_start = time.perf_counter()

        try:
            obs, info = env.reset()
            terminated = False
            truncated = False

            while not terminated and not truncated:
                # Check killswitch during minigame
                if self.supervisor.is_abort_requested():
                    result.truncated = True
                    result.termination_reason = "killswitch"
                    break

                # Normalize observation if we have stats
                obs_input = obs
                if self.obs_normalizer is not None:
                    try:
                        obs_input = self.obs_normalizer.normalize_obs(obs.reshape(1, -1)).flatten()
                    except Exception:
                        pass

                # Policy inference
                if self.policy is not None:
                    action, _ = self.policy.predict(obs_input, deterministic=True)
                    action = int(action)
                else:
                    # Fallback: bang-bang baseline if no policy loaded
                    fish_pos = float(obs[2] + 1.0) / 2.0  # un-normalize f
                    bar_pos = float(obs[0] + 1.0) / 2.0   # un-normalize b
                    action = 1 if bar_pos < fish_pos else 0

                obs, reward, terminated, truncated, info = env.step(action)
                result.steps += 1

            # Determine outcome
            result.duration_s = time.perf_counter() - ep_start
            result.peak_progress = info.get("peak_progress", 0.0)
            result.mean_in_bar = info.get("mean_in_bar", 0.0)

            if info.get("is_catch", False):
                result.caught = True
                result.termination_reason = "catch"
            elif info.get("is_escape", False):
                result.escaped = True
                result.termination_reason = "escape"
            elif truncated:
                result.truncated = True
                if not result.termination_reason:
                    result.termination_reason = "truncated"

        except Exception as exc:
            logger.error("Error during minigame: %s", exc, exc_info=True)
            result.truncated = True
            result.termination_reason = f"error: {exc}"
        finally:
            # ALWAYS release mouse when leaving RL_ACTIVE
            self.actuator.release()
            # Close env without stopping our shared capture driver
            env.actuator.release()
            env.timer.close()
            # Reset extractor for clean idle scanning
            self.extractor.reset()

        # Notify outcome
        if result.caught:
            self._notify(
                f"✅ Caught! ({result.duration_s:.1f}s, {result.steps} steps, "
                f"peak={result.peak_progress:.0%}, in-bar={result.mean_in_bar:.0%})",
                style="bold green",
            )
        elif result.escaped:
            self._notify(
                f"❌ Escaped ({result.duration_s:.1f}s, peak={result.peak_progress:.0%})",
                style="bold red",
            )
        else:
            self._notify(
                f"⚠️ Truncated: {result.termination_reason} ({result.duration_s:.1f}s)",
                style="yellow",
            )

        return result

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def _record_result(self, result: MinigameResult) -> None:
        """Record a minigame result into session stats."""
        self.stats.minigames_played += 1
        self.stats.results.append(result)
        if result.caught:
            self.stats.catches += 1
        elif result.escaped:
            self.stats.escapes += 1
        else:
            self.stats.truncations += 1

    def _print_summary(self) -> None:
        """Print a summary table of the session."""
        total = self.stats.minigames_played
        if total == 0:
            self._notify("No minigames played this session.", style="dim")
            return

        elapsed = time.perf_counter() - self.stats.session_start
        cr = self.stats.catches / total * 100 if total > 0 else 0.0

        self._notify("", style="")
        self._notify("━" * 50, style="dim")
        self._notify("📊 Fisher Assistant — Session Summary", style="bold white")
        self._notify(f"   Duration:    {elapsed:.0f}s", style="")
        self._notify(f"   Minigames:   {total}", style="")
        self._notify(f"   Catches:     {self.stats.catches} ({cr:.0f}%)", style="green")
        self._notify(f"   Escapes:     {self.stats.escapes}", style="red")
        self._notify(f"   Truncations: {self.stats.truncations}", style="yellow")
        self._notify("━" * 50, style="dim")

    def _notify(self, message: str, style: str = "") -> None:
        """Print a notification to the console."""
        if not self.show_notifications:
            return
        if self._console is not None:
            self._console.print(f"[{style}]{message}[/{style}]" if style else message)
        else:
            print(message)
