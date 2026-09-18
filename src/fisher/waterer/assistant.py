"""Autonomous crop watering assistant — FSM orchestrator.

State machine::

    INIT → SCANNING → PLANNING → WATERING → REFILLING → DONE / ABORT

Movement is handled by :class:`~fisher.waterer.navigator.TileNavigator`
(WASD); tool aiming parks the OS cursor on the target tile
(``cursor_aim: true``), which overrides WASD facing in Stardew 1.6.x and
avoids the sub-tile drift of facing taps.

Coordinate model: tile coordinates from a scan are relative to the player at
scan time (``TileCoord(0, 0)``), and ``TileNavigator.current_pos`` tracks
movement since that scan. Aim deltas are therefore always computed as
``target − current_pos`` — never as absolute screen positions captured before
the farmer moved.

Usage::

    assistant = WateringAssistant.from_config(config)
    stats = assistant.run()  # Blocks until done or F9
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import numpy as np

from fisher.config import FisherConfig, load_config
from fisher.extraction.tiles import TileCoord, TileState, tile_to_screen
from fisher.input.base import Actuator
from fisher.orchestration.safety import SafetySupervisor
from fisher.utils.timing import interruptible_sleep
from fisher.waterer.detector import FarmScanner, ScanResult
from fisher.waterer.navigator import TileNavigator, direction_key
from fisher.waterer.pathfinder import plan_path

logger = logging.getLogger("fisher.waterer.assistant")

# Cardinal neighbor offsets in priority order (N, S, W, E). The farmer waters
# a target from the neighbor that minimizes walking distance.
_STAND_OFFSETS: tuple[tuple[int, int], ...] = ((-1, 0), (1, 0), (0, -1), (0, 1))

# A pond is considered reachable when a water tile is within this many tiles.
_POND_REACH_TILES = 2


class WatererState(enum.Enum):
    """FSM states for the watering assistant."""

    INIT = "INIT"
    SCANNING = "SCANNING"
    PLANNING = "PLANNING"
    WATERING = "WATERING"
    REFILLING = "REFILLING"
    DONE = "DONE"
    ABORT = "ABORT"


# Direction name → (dr, dc) step vector for pond walking.
DIRECTION_VECTORS: dict[str, tuple[int, int]] = {
    "right": (0, 1),
    "left": (0, -1),
    "up": (-1, 0),
    "down": (1, 0),
}


def adjacent_stand_tile(target: TileCoord, current: TileCoord) -> TileCoord:
    """Pick the cardinal neighbor of ``target`` closest to ``current``.

    Stardew cannot target the tile the farmer is standing on, so the assistant
    walks to a neighboring tile and aims the cursor at the target from there.
    """
    candidates = [
        TileCoord(target.row + dr, target.col + dc) for dr, dc in _STAND_OFFSETS
    ]
    return min(
        candidates,
        key=lambda t: abs(t.row - current.row) + abs(t.col - current.col),
    )


@dataclass
class WateringStats:
    """Summary statistics for a watering session."""

    tiles_watered: int = 0
    tiles_skipped: int = 0
    tiles_confirmed: int = 0
    refills: int = 0
    scan_passes: int = 0
    duration_s: float = 0.0
    final_state: str = "INIT"


class WateringAssistant:
    """Autonomous crop watering assistant.

    Detects unwatered crop tiles via CV, navigates tile-by-tile with WASD,
    waters via cursor-aimed LMB clicks, and refills at a pond when the can
    is empty.
    """

    def __init__(
        self,
        actuator: Actuator,
        capture_fn: Callable[[], Optional[np.ndarray]],
        scanner: FarmScanner,
        safety: SafetySupervisor,
        config: Dict[str, Any],
        preview: bool = False,
        capture_driver: Optional[Any] = None,
        watered_tiles: Optional[set[TileCoord]] = None,
    ) -> None:
        self.actuator = actuator
        self.capture_fn = capture_fn
        self.scanner = scanner
        self.safety = safety
        self.config = config
        self.preview = preview
        self.capture_driver = capture_driver

        self.state = WatererState.INIT
        self.stats = WateringStats()
        self.abort_event = threading.Event()
        self.watered_tiles: set[TileCoord] = (
            watered_tiles if watered_tiles is not None else set()
        )

        self.water_capacity = int(config.get("water_capacity", 40))
        self.water_remaining = self.water_capacity
        self.tile_size = int(config.get("tile_size_px", 64))
        self.player_center = tuple(config.get("player_center", [960, 540]))
        self.watering_click_ms = float(config.get("watering_click_ms", 150))
        self.watering_anim_ms = float(config.get("watering_anim_ms", 450))
        self.refill_click_ms = float(config.get("refill_click_ms", 500))
        self.refill_anim_ms = float(config.get("refill_anim_ms", 1000))
        self.cursor_aim = bool(config.get("cursor_aim", True))
        self.max_scan_passes = int(config.get("max_scan_passes", 3))
        self.scan_settle_ms = float(config.get("scan_settle_ms", 500))
        self.pond_direction = str(config.get("pond_direction", "right"))
        self.pond_max_tiles = int(config.get("pond_max_tiles", 15))
        self.path_strategy = str(config.get("path_strategy", "auto"))
        self.map_edge_margin_tiles = int(config.get("map_edge_margin_tiles", 15))

        self.navigator = TileNavigator(
            actuator,
            self.abort_event,
            tile_walk_ms=int(config.get("tile_walk_ms", 250)),
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> WateringStats:
        """Run the watering session until done or aborted.

        Returns:
            ``WateringStats`` summary of the session.
        """
        start_time = time.perf_counter()
        self.safety.start()

        try:
            if self._wait_for_foreground():
                self._run_scan_passes()
            elif self.state != WatererState.ABORT:
                self.state = WatererState.ABORT
        except Exception as exc:
            logger.error("Waterer error: %s", exc, exc_info=True)
            self.state = WatererState.ABORT
        finally:
            self._shutdown()

        self.stats.duration_s = time.perf_counter() - start_time
        self.stats.final_state = self.state.value
        self._print_summary()
        return self.stats

    def _run_scan_passes(self) -> None:
        """SCAN → PLAN → WATER cycle, repeated up to ``max_scan_passes``."""
        for scan_pass in range(self.max_scan_passes):
            if self._aborted():
                self.state = WatererState.ABORT
                return

            self.state = WatererState.SCANNING
            logger.info("Scan pass %d/%d", scan_pass + 1, self.max_scan_passes)

            scan = self._scan()
            if scan is None:
                self.state = WatererState.ABORT
                return

            if not scan.unwatered_tiles:
                logger.info(
                    "No unwatered crops found (%d tiles scanned).", scan.total_tiles_scanned
                )
                self._hold_preview()
                self.state = WatererState.DONE
                return

            targets = self._select_targets(scan)
            if not targets:
                self.state = WatererState.DONE
                return

            self.state = WatererState.PLANNING
            path = plan_path(
                targets,
                start=self.navigator.current_pos,
                strategy=self.path_strategy,
            )
            logger.info(
                "Planned %d target(s), %d skipped (strategy=%s).",
                len(path), self.stats.tiles_skipped, self.path_strategy,
            )

            self.state = WatererState.WATERING
            self._water_path(path, scan)
            if self.state == WatererState.ABORT:
                return

        self.state = WatererState.DONE

    # ------------------------------------------------------------------
    # Scanning & planning
    # ------------------------------------------------------------------

    def _scan(self) -> Optional[ScanResult]:
        """Settle, capture, and classify one frame. Resets the navigator origin."""
        # Coordinates in this scan are player-relative; the navigator origin
        # must coincide with the player at capture time.
        self.navigator.reset_position()

        if not interruptible_sleep(self.scan_settle_ms / 1000.0, self.abort_event):
            return None

        frame = self._grab_frame()
        if frame is None:
            return None

        scan = self.scanner.scan(frame)
        self.stats.scan_passes += 1
        self._update_preview(scan)
        return scan

    def _select_targets(self, scan: ScanResult) -> list[TileCoord]:
        """Filter scan targets through the navigation sanity guard."""
        limit = self.map_edge_margin_tiles
        targets: list[TileCoord] = []
        for tile in scan.unwatered_tiles:
            if abs(tile.row) + abs(tile.col) > limit:
                self.stats.tiles_skipped += 1
                logger.warning(
                    "Skipping %s: beyond navigation guard (%d tiles).", tile, limit
                )
                continue
            targets.append(tile)
        return targets

    # ------------------------------------------------------------------
    # Watering
    # ------------------------------------------------------------------

    def _water_path(self, path: list[TileCoord], scan: ScanResult) -> None:
        """Water every tile in ``path``, refilling when the can runs dry."""
        for index, target in enumerate(path, start=1):
            if self._aborted():
                self.state = WatererState.ABORT
                return

            self._update_preview(scan, path=path, current_target=target)
            logger.info("Watering %d/%d: %s", index, len(path), target)

            if self.water_remaining <= 0:
                if not self._refill():
                    self.state = WatererState.ABORT
                    return

            stand = adjacent_stand_tile(target, self.navigator.current_pos)
            if not self.navigator.navigate_to(stand):
                self.state = WatererState.ABORT
                return

            if not self._water_tile(target, scan.grid_offset):
                self.state = WatererState.ABORT
                return

            self.stats.tiles_watered += 1
            self.water_remaining -= 1

    def _water_tile(self, target: TileCoord, grid_offset: tuple[int, int]) -> bool:
        """Aim at ``target`` and water it. Returns False if aborted."""
        if self._aborted():
            return False

        # Aim delta is relative to the farmer's *current* position — the world
        # has scrolled with the player since the scan was taken.
        delta = TileCoord(
            target.row - self.navigator.current_pos.row,
            target.col - self.navigator.current_pos.col,
        )
        if not self._aim(delta, grid_offset):
            return False

        self.actuator.click(button="left", duration=self.watering_click_ms / 1000.0)
        # Register the tile before confirmation so the mock scene darkens it
        # too; live confirmation reads real pixels (telemetry only).
        self.watered_tiles.add(target)

        if not interruptible_sleep(self.watering_anim_ms / 1000.0, self.abort_event):
            return False

        self._confirm_watered(target)
        return True

    def _confirm_watered(self, target: TileCoord) -> None:
        """Best-effort CV confirmation that the soil darkened.

        Count-based tracking is the source of truth (mature crops occlude
        their soil), so a negative confirmation is logged, never fatal.
        """
        rel = TileCoord(
            target.row - self.navigator.current_pos.row,
            target.col - self.navigator.current_pos.col,
        )
        try:
            frame = self._grab_frame(timeout_s=1.0)
            if frame is None:
                return
            scan = self.scanner.scan(frame)
        except Exception as exc:
            logger.debug("Watering confirmation scan failed: %s", exc)
            return

        state = scan.tile_map.get(rel)
        if state == TileState.WATERED:
            self.stats.tiles_confirmed += 1
        else:
            logger.warning(
                "Tile %s not confirmed watered (state=%s); count-based tracking continues.",
                rel, state,
            )

    def _aim(self, delta: TileCoord, grid_offset: tuple[int, int]) -> bool:
        """Point the tool at ``delta`` (player-relative tile)."""
        if not self._foreground_ok():
            logger.error("Game window lost foreground — aborting.")
            self.state = WatererState.ABORT
            return False

        if self.cursor_aim:
            px, py = tile_to_screen(
                delta,
                player_center=self.player_center,
                tile_size=self.tile_size,
                grid_offset=grid_offset,
            )
            self.actuator.move_cursor(px, py)
        else:
            self.navigator.face_direction(direction_key(delta.row, delta.col))
        return True

    # ------------------------------------------------------------------
    # Refilling
    # ------------------------------------------------------------------

    def _refill(self) -> bool:
        """Walk toward the pond and refill the watering can.

        Returns True on a successful refill, False if aborted or no pond was
        reachable within ``pond_max_tiles``.
        """
        self.state = WatererState.REFILLING
        logger.info("Watering can empty — walking %s to pond.", self.pond_direction)

        direction = DIRECTION_VECTORS.get(self.pond_direction, (0, 1))
        for _ in range(self.pond_max_tiles):
            if self._aborted():
                return False
            if not self._foreground_ok():
                self.state = WatererState.ABORT
                return False
            if not self.navigator.move_one_tile(direction):
                return False

            frame = self._grab_frame(timeout_s=1.0)
            if frame is None:
                return False
            scan = self.scanner.scan(frame)

            water_tile = self._nearest_water(scan, max_distance=_POND_REACH_TILES)
            if water_tile is None:
                continue

            # Fresh scan coordinates are player-relative, so the water tile
            # itself is the aim delta.
            if not self._aim(water_tile, scan.grid_offset):
                return False

            self.actuator.click(button="left", duration=self.refill_click_ms / 1000.0)
            if not interruptible_sleep(self.refill_anim_ms / 1000.0, self.abort_event):
                return False

            self.water_remaining = self.water_capacity
            self.stats.refills += 1
            self.state = WatererState.WATERING
            logger.info("Refilled watering can (%d uses).", self.water_capacity)
            return True

        logger.error("No reachable pond within %d tiles.", self.pond_max_tiles)
        self.state = WatererState.ABORT
        return False

    @staticmethod
    def _nearest_water(scan: ScanResult, max_distance: int) -> Optional[TileCoord]:
        """Return the closest water tile within ``max_distance`` (Manhattan)."""
        best: Optional[TileCoord] = None
        best_distance = max_distance + 1
        for tile in scan.water_tiles:
            distance = abs(tile.row) + abs(tile.col)
            if distance <= max_distance and distance < best_distance:
                best = tile
                best_distance = distance
        return best

    # ------------------------------------------------------------------
    # Safety, capture & shutdown helpers
    # ------------------------------------------------------------------

    def _aborted(self) -> bool:
        """True when F9 (or a programmatic abort) has been requested."""
        if self.safety.is_abort_requested():
            if not self.abort_event.is_set():
                logger.warning("Abort requested: %s", self.safety.abort_reason)
                self.actuator.emergency_release()
            self.abort_event.set()
        return self.abort_event.is_set()

    def _foreground_ok(self) -> bool:
        """Foreground guard for actuators that support it (mock: always True)."""
        checker = getattr(self.actuator, "is_game_foreground", None)
        if checker is None:
            return True
        try:
            return bool(checker())
        except Exception:
            return True

    def _wait_for_foreground(self, timeout_s: float = 5.0) -> bool:
        """Wait for the game window to be focused before dispatching inputs."""
        checker = getattr(self.actuator, "is_game_foreground", None)
        if checker is None:
            return True

        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            if self._aborted():
                return False
            if self._foreground_ok():
                return True
            if not interruptible_sleep(0.1, self.abort_event):
                return False

        logger.error(
            "Game window not in foreground after %.0fs — focus Stardew Valley and retry.",
            timeout_s,
        )
        self.state = WatererState.ABORT
        return False

    def _grab_frame(self, timeout_s: float = 3.0) -> Optional[np.ndarray]:
        """Wait for a capture frame, tolerating capture-thread startup."""
        deadline = time.perf_counter() + timeout_s
        while True:
            frame = self.capture_fn()
            if frame is not None:
                return frame
            if time.perf_counter() >= deadline:
                logger.error("Capture produced no frame within %.1fs.", timeout_s)
                return None
            if not interruptible_sleep(0.05, self.abort_event):
                return None

    def _shutdown(self) -> None:
        """Release every input and resource (always runs)."""
        if self.preview:
            try:
                import cv2
                cv2.destroyAllWindows()
            except Exception:
                pass

        if self.capture_driver is not None:
            try:
                self.capture_driver.stop()
            except Exception:
                pass

        self.actuator.emergency_release()
        self.safety.stop()

    def _print_summary(self) -> None:
        """Log the session summary."""
        logger.info(
            "Waterer session complete: state=%s, tiles_watered=%d, confirmed=%d, "
            "skipped=%d, refills=%d, scans=%d, duration=%.1fs",
            self.stats.final_state,
            self.stats.tiles_watered,
            self.stats.tiles_confirmed,
            self.stats.tiles_skipped,
            self.stats.refills,
            self.stats.scan_passes,
            self.stats.duration_s,
        )

    # ------------------------------------------------------------------
    # Preview overlay (debug only)
    # ------------------------------------------------------------------

    def _update_preview(
        self,
        scan: ScanResult,
        path: Optional[list[TileCoord]] = None,
        current_target: Optional[TileCoord] = None,
    ) -> None:
        """Render the debug CV overlay when ``preview=True``."""
        if not self.preview:
            return
        try:
            import cv2

            frame = self.capture_fn()
            if frame is None:
                return
            vis = frame.copy()
            ts = self.tile_size
            half = ts // 2

            color_map = {
                TileState.DRY_PLANTED: (0, 0, 255),    # Red
                TileState.DRY_EMPTY: (0, 165, 255),    # Orange
                TileState.WATERED: (0, 200, 0),        # Green
                TileState.WATER_BODY: (255, 150, 0),   # Blue
            }
            for tile, state in scan.tile_map.items():
                color = color_map.get(state)
                if not color:
                    continue
                cx, cy = tile_to_screen(
                    tile,
                    player_center=self.player_center,
                    tile_size=ts,
                    grid_offset=scan.grid_offset,
                )
                cv2.rectangle(
                    vis,
                    (cx - half + 2, cy - half + 2),
                    (cx + half - 2, cy + half - 2),
                    color,
                    2,
                )

            if path and len(path) > 1:
                points = [
                    tile_to_screen(
                        t,
                        player_center=self.player_center,
                        tile_size=ts,
                        grid_offset=scan.grid_offset,
                    )
                    for t in path
                ]
                for i in range(len(points) - 1):
                    cv2.line(vis, points[i], points[i + 1], (0, 255, 255), 2)

            if current_target:
                tx, ty = tile_to_screen(
                    current_target,
                    player_center=self.player_center,
                    tile_size=ts,
                    grid_offset=scan.grid_offset,
                )
                cv2.rectangle(
                    vis,
                    (tx - half, ty - half),
                    (tx + half, ty + half),
                    (0, 255, 255),
                    3,
                )

            cv2.rectangle(vis, (10, 10), (520, 110), (20, 20, 20), -1)
            cv2.rectangle(vis, (10, 10), (520, 110), (100, 100, 100), 1)
            hud_lines = [
                f"STATE: {self.state.value}",
                f"WATER: {self.water_remaining}/{self.water_capacity} | WATERED: {self.stats.tiles_watered}",
                f"GRID PHASE: dx={scan.grid_offset[0]}, dy={scan.grid_offset[1]}",
            ]
            for i, line in enumerate(hud_lines):
                cv2.putText(
                    vis,
                    line,
                    (20, 35 + i * 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )

            cv2.imshow("Fisher Waterer Preview", vis)
            cv2.waitKey(1)
        except Exception as exc:
            logger.debug("Preview rendering error: %s", exc)

    def _hold_preview(self, duration_ms: int = 1500) -> None:
        """Keep the preview window visible briefly when no crops are found."""
        if not self.preview:
            return
        try:
            import cv2
            cv2.waitKey(duration_ms)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: Optional[FisherConfig] = None,
        mock_mode: bool = False,
        preview: bool = False,
        capacity_override: Optional[int] = None,
    ) -> "WateringAssistant":
        """Build a configured assistant with the shared Fisher infrastructure.

        Args:
            config: FisherConfig instance (loads default if None).
            mock_mode: Use the synthetic farm scene and a mock actuator.
            preview: Show the CV overlay window.
            capacity_override: Override the watering can capacity.

        Returns:
            A ``WateringAssistant`` ready for ``.run()``.
        """
        if config is None:
            config = load_config()

        waterer_cfg = dict(config.waterer)
        if capacity_override is not None:
            waterer_cfg["water_capacity"] = capacity_override

        capture_driver: Optional[Any] = None
        scene = None

        if mock_mode:
            from fisher.input.mock_actuator import MockActuator
            from fisher.waterer.farm_sim import FarmSceneSimulator

            actuator: Actuator = MockActuator()
            grid_phase = waterer_cfg.get("grid_phase", "auto")
            fixed_offset = (
                (int(grid_phase[0]), int(grid_phase[1]))
                if isinstance(grid_phase, (list, tuple)) and len(grid_phase) == 2
                else (0, 0)
            )
            scene = FarmSceneSimulator(
                tile_size=waterer_cfg.get("tile_size_px", 64),
                player_center=tuple(waterer_cfg.get("player_center", [960, 540])),
                grid_offset=fixed_offset,
            )
            scene.seed(
                dry_planted_tiles=[
                    TileCoord(-1, 0), TileCoord(-1, 1), TileCoord(0, 1), TileCoord(1, 1),
                ],
                water_body_tiles=[TileCoord(0, 5), TileCoord(1, 5)],
            )
            capture_fn: Callable[[], Optional[np.ndarray]] = scene.capture
        else:
            from fisher.capture import create_capture_driver
            from fisher.input.direct_input import DirectInputActuator

            actuator = DirectInputActuator(
                window_title=config.game.get("window_title", "Stardew Valley"),
                strict_foreground=True,
            )
            capture_driver = create_capture_driver(config, full_frame=True)
            capture_driver.start()

            def capture_fn() -> Optional[np.ndarray]:
                frame, _ = capture_driver.get_latest_frame()
                return frame

        scanner = FarmScanner.from_config(waterer_cfg)
        safety = SafetySupervisor(
            killswitch_key=config.safety.get("killswitch_key", "f9"),
            hard_abort_key=config.safety.get("hard_abort", "ctrl+f9"),
            on_abort=actuator.emergency_release,
        )

        instance = cls(
            actuator=actuator,
            capture_fn=capture_fn,
            scanner=scanner,
            safety=safety,
            config=waterer_cfg,
            preview=preview,
            capture_driver=capture_driver,
            watered_tiles=scene.watered_tiles if scene is not None else None,
        )
        if scene is not None:
            scene.bind_navigator(instance.navigator)
        return instance
