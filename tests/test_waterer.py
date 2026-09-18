"""Comprehensive test suite for the Auto Waterer.

Covers:
- Tile coordinate math (roundtrip, player center)
- CV detection (dry soil, watered soil, crop sprites, water body)
- Grid phase recovery (auto and fixed)
- Path planning (greedy, serpentine, auto heuristic)
- Navigation (WASD dispatch, abort-safe key release)
- Targeting (adjacent stand tile, cursor aim delta)
- FSM lifecycle (full cycle, refill + resume, refill failure)
- Safety (killswitch key release, safety callback, programmatic abort)
"""

from __future__ import annotations

import threading
import time

import numpy as np

from fisher.extraction.crops import FarmTileClassifier
from fisher.extraction.tiles import (
    TileCoord,
    TileState,
    estimate_grid_phase,
    screen_to_tile,
    tile_to_screen,
)
from fisher.input.mock_actuator import MockActuator
from fisher.orchestration.safety import SafetySupervisor
from fisher.waterer.assistant import WateringAssistant, adjacent_stand_tile
from fisher.waterer.detector import FarmScanner
from fisher.waterer.farm_sim import FarmSceneSimulator
from fisher.waterer.navigator import TileNavigator
from fisher.waterer.pathfinder import plan_greedy, plan_path, plan_serpentine
from tests.fixtures.farm_generator import generate_farm_frame


# ─── Shared fixtures / helpers ──────────────────────────────────────

BASE_CONFIG = {
    "water_capacity": 40,
    "tile_size_px": 64,
    "player_center": [960, 540],
    "tile_walk_ms": 10,
    "watering_click_ms": 10,
    "watering_anim_ms": 10,
    "refill_click_ms": 10,
    "refill_anim_ms": 10,
    "cursor_aim": True,
    "max_scan_passes": 1,
    "scan_settle_ms": 10,
    "pond_direction": "right",
    "pond_max_tiles": 20,
    "path_strategy": "greedy",
    "map_edge_margin_tiles": 15,
    "detection_thresholds": {
        "dry_soil_pct": 30,
        "watered_soil_pct": 25,
        "crop_sprite_pct": 10,
        "water_body_pct": 40,
    },
}


def _build_assistant(
    dry_planted_tiles: list[TileCoord],
    water_body_tiles: list[TileCoord] | None = None,
    capacity: int = 40,
    path_strategy: str = "greedy",
) -> tuple[WateringAssistant, MockActuator, FarmSceneSimulator]:
    """Build an assistant wired to a stateful synthetic farm scene."""
    actuator = MockActuator()
    scene = FarmSceneSimulator()
    scene.seed(
        dry_planted_tiles=dry_planted_tiles,
        water_body_tiles=water_body_tiles or [],
    )
    scanner = FarmScanner.from_config(BASE_CONFIG)
    config = dict(BASE_CONFIG)
    config["water_capacity"] = capacity
    config["path_strategy"] = path_strategy
    assistant = WateringAssistant(
        actuator=actuator,
        capture_fn=scene.capture,
        scanner=scanner,
        safety=SafetySupervisor(),
        config=config,
        watered_tiles=scene.watered_tiles,
    )
    scene.bind_navigator(assistant.navigator)
    return assistant, actuator, scene


# ─── Tile coordinate math ───────────────────────────────────────────


class TestTileCoordMath:
    """Tests #1–#2: screen↔tile coordinate conversion."""

    def test_tile_coord_roundtrip(self):
        """screen_to_tile ↔ tile_to_screen are inverses."""
        center = (960, 540)
        offset = (10, -5)
        for row in range(-5, 6):
            for col in range(-5, 6):
                tile = TileCoord(row, col)
                px_x, px_y = tile_to_screen(tile, center, 64, offset)
                recovered = screen_to_tile(px_x, px_y, center, 64, offset)
                assert recovered == tile, f"Roundtrip failed: {tile} → ({px_x},{px_y}) → {recovered}"

    def test_tile_grid_player_center(self):
        """Player pixel (960, 540) maps to TileCoord(0, 0)."""
        tile = screen_to_tile(960, 540, player_center=(960, 540))
        assert tile == TileCoord(0, 0)

        px = tile_to_screen(TileCoord(0, 0), player_center=(960, 540))
        assert px == (960, 540)


# ─── CV tile detection ──────────────────────────────────────────────


class TestTileDetection:
    """Tests #3–#6: HSV-based tile classification on synthetic frames."""

    def _make_classifier(self) -> FarmTileClassifier:
        return FarmTileClassifier(tile_size=64, player_center=(960, 540))

    def test_dry_soil_detection(self):
        """Synthetic brown tile classified as DRY_EMPTY or DRY_PLANTED."""
        frame = generate_farm_frame(dry_empty_tiles=[TileCoord(-1, -1)])
        tile_map = self._make_classifier().classify_frame(frame)
        assert tile_map.get(TileCoord(-1, -1)) in (
            TileState.DRY_EMPTY,
            TileState.DRY_PLANTED,
        )

    def test_watered_soil_detection(self):
        """Synthetic dark tile classified as WATERED."""
        frame = generate_farm_frame(watered_tiles=[TileCoord(-1, 0)])
        tile_map = self._make_classifier().classify_frame(frame)
        assert tile_map.get(TileCoord(-1, 0)) == TileState.WATERED

    def test_crop_sprite_detection(self):
        """Tile with green pixels above soil → DRY_PLANTED."""
        frame = generate_farm_frame(dry_planted_tiles=[TileCoord(-1, 1)])
        tile_map = self._make_classifier().classify_frame(frame)
        assert tile_map.get(TileCoord(-1, 1)) == TileState.DRY_PLANTED

    def test_water_body_detection(self):
        """Blue tile region classified as WATER_BODY."""
        frame = generate_farm_frame(water_body_tiles=[TileCoord(0, 5)])
        tile_map = self._make_classifier().classify_frame(frame)
        assert tile_map.get(TileCoord(0, 5)) == TileState.WATER_BODY


# ─── Grid phase recovery ────────────────────────────────────────────


class TestGridPhase:
    """Test #14: sub-tile grid offset recovery."""

    def test_grid_phase_recovery(self):
        """Synthetic tilled grid with known offset → recovered within ±2 px."""
        known_offset = (17, -11)
        tile_size = 64
        # Checkerboard gives boundary edges on both axes.
        tiles = [
            TileCoord(r, c)
            for r in range(-5, 6)
            for c in range(-5, 6)
            if (r + c) % 2 == 0
        ]
        frame = generate_farm_frame(
            dry_empty_tiles=tiles,
            grid_offset=known_offset,
        )
        import cv2
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        dry_mask = cv2.inRange(hsv, np.array([10, 60, 80]), np.array([25, 140, 140]))

        recovered = estimate_grid_phase(dry_mask, tile_size=tile_size)

        assert recovered != (0, 0), "Grid phase recovery returned fallback (0,0)"
        assert abs(recovered[0] - known_offset[0]) <= 2, \
            f"dx error: expected {known_offset[0]}, got {recovered[0]}"
        assert abs(recovered[1] - known_offset[1]) <= 2, \
            f"dy error: expected {known_offset[1]}, got {recovered[1]}"

    def test_fixed_grid_phase_from_config(self):
        """A fixed [dx, dy] config bypasses auto recovery and is used verbatim."""
        fixed = (5, -3)
        scanner = FarmScanner.from_config({**BASE_CONFIG, "grid_phase": list(fixed)})
        frame = generate_farm_frame(
            dry_planted_tiles=[TileCoord(-1, 0)],
            grid_offset=fixed,
        )
        scan = scanner.scan(frame)
        assert scan.grid_offset == fixed
        assert TileCoord(-1, 0) in scan.unwatered_tiles


# ─── Mock scene realism ─────────────────────────────────────────────


class TestFarmSceneSimulator:
    """The mock scene must mirror the live camera (player-relative frames)."""

    def test_scene_shifts_with_player(self):
        """Moving the navigator shifts surveyed coordinates exactly like the camera."""
        scene = FarmSceneSimulator()
        scene.seed(water_body_tiles=[TileCoord(0, 5)])
        nav = TileNavigator(MockActuator(), threading.Event(), tile_walk_ms=10)
        scene.bind_navigator(nav)
        scanner = FarmScanner.from_config(BASE_CONFIG)

        scan_before = scanner.scan(scene.capture())
        assert TileCoord(0, 5) in scan_before.water_tiles

        assert nav.move_one_tile((0, 1))
        assert nav.move_one_tile((0, 1))

        scan_after = scanner.scan(scene.capture())
        assert TileCoord(0, 3) in scan_after.water_tiles


# ─── Path planning ──────────────────────────────────────────────────


class TestPathPlanning:
    """Tests #7–#8: greedy and serpentine path planners."""

    def test_pathfinder_greedy(self):
        """Greedy path visits all tiles, starts from player, no duplicates."""
        tiles = [
            TileCoord(-2, -1), TileCoord(-1, 0), TileCoord(0, 1),
            TileCoord(1, -2), TileCoord(2, 2),
        ]
        path = plan_greedy(tiles, start=TileCoord(0, 0))
        assert len(path) == len(tiles)
        assert len(set(path)) == len(path)
        assert set(path) == set(tiles)

    def test_pathfinder_serpentine(self):
        """Serpentine produces boustrophedon row ordering."""
        tiles = [
            TileCoord(0, c) for c in range(3)
        ] + [
            TileCoord(1, c) for c in range(3)
        ] + [
            TileCoord(2, c) for c in range(3)
        ]
        path = plan_serpentine(tiles)
        assert len(path) == 9
        assert [t.col for t in path[:3]] == [0, 1, 2]
        assert [t.col for t in path[3:6]] == [2, 1, 0]
        assert [t.col for t in path[6:9]] == [0, 1, 2]

    def test_auto_prefers_greedy_for_thin_line(self):
        """A 1×10 line is not rectangular (aspect > 3:1) → greedy is selected."""
        line = [TileCoord(0, c) for c in range(10)]
        start = TileCoord(0, 0)
        assert plan_path(line, start=start, strategy="auto") == plan_greedy(line, start)

    def test_auto_prefers_serpentine_for_block(self):
        """A dense 3×3 block qualifies as rectangular → serpentine is selected."""
        block = [TileCoord(r, c) for r in range(3) for c in range(3)]
        assert plan_path(block, strategy="auto") == plan_serpentine(block)


# ─── Navigation & targeting ─────────────────────────────────────────


class TestNavigation:
    """Tests #9, #13: WASD dispatch and abort-safe key release."""

    def test_navigator_wasd(self):
        """TileNavigator dispatches correct WASD keys for movement."""
        actuator = MockActuator()
        nav = TileNavigator(actuator, threading.Event(), tile_walk_ms=10)

        assert nav.move_one_tile((0, 1))
        assert nav.current_pos == TileCoord(0, 1)
        assert nav.facing == "d"

        assert nav.move_one_tile((1, 0))
        assert nav.current_pos == TileCoord(1, 1)
        assert nav.facing == "s"

        key_events = [e for e, _ in actuator.history if e.startswith("KEY_")]
        assert "KEY_DOWN:d" in key_events
        assert "KEY_UP:d" in key_events
        assert "KEY_DOWN:s" in key_events
        assert "KEY_UP:s" in key_events

    def test_navigator_abort_releases_key(self):
        """Abort mid-hold releases the key immediately and reports incomplete."""
        actuator = MockActuator()
        abort = threading.Event()
        nav = TileNavigator(actuator, abort, tile_walk_ms=500)

        result: dict[str, bool] = {}

        def _long_move() -> None:
            result["completed"] = nav.move_one_tile((0, 1))

        thread = threading.Thread(target=_long_move)
        thread.start()
        time.sleep(0.05)

        abort.set()
        thread.join(timeout=2.0)

        assert result["completed"] is False
        assert len(actuator._held_keys) == 0, "WASD key left held after abort"
        assert "KEY_UP:d" in [e for e, _ in actuator.history]

    def test_adjacent_stand_tile(self):
        """Stand tile is a cardinal neighbor closest to the farmer."""
        assert adjacent_stand_tile(TileCoord(-1, 0), TileCoord(0, 0)) == TileCoord(0, 0)
        assert adjacent_stand_tile(TileCoord(0, 3), TileCoord(0, 0)) == TileCoord(0, 2)
        assert adjacent_stand_tile(TileCoord(5, 0), TileCoord(0, 0)) == TileCoord(4, 0)


# ─── FSM lifecycle ──────────────────────────────────────────────────


class TestWateringFSM:
    """Tests #10–#11: full watering cycle, refill, and failure handling."""

    def test_watering_fsm_full_cycle(self):
        """SCAN → PLAN → WATER → DONE with exact counts and cursor telemetry."""
        assistant, actuator, _ = _build_assistant(
            dry_planted_tiles=[TileCoord(-1, 0), TileCoord(-1, 1), TileCoord(0, 1)],
        )

        stats = assistant.run()

        assert stats.final_state == "DONE"
        assert stats.scan_passes == 1
        assert stats.tiles_watered == 3
        assert stats.tiles_confirmed == 3
        assert stats.tiles_skipped == 0
        assert stats.refills == 0

        events = [e for e, _ in actuator.history]
        assert events.count("CLICK:left") == 3
        # Cursor aiming: every aim delta is one tile from the screen center.
        cursor_events = [e for e in events if e.startswith("MOVE_CURSOR:")]
        assert len(cursor_events) == 3
        for event in cursor_events:
            x, y = (int(v) for v in event.split(":")[1].split(","))
            assert abs(x - 960) <= 64, f"Cursor x {x} not adjacent to player"
            assert abs(y - 540) <= 64, f"Cursor y {y} not adjacent to player"

    def test_refill_and_resume(self):
        """WATER → REFILL → WATER when capacity hits 0; session completes."""
        assistant, _, _ = _build_assistant(
            dry_planted_tiles=[TileCoord(-1, c) for c in range(-2, 3)],
            water_body_tiles=[TileCoord(0, 5), TileCoord(1, 5)],
            capacity=2,
        )

        stats = assistant.run()

        assert stats.final_state == "DONE"
        assert stats.tiles_watered == 5
        assert stats.refills == 2
        assert assistant.water_remaining == 1  # 2 refills (4 uses) − 5 waterings

    def test_refill_failure_aborts(self):
        """No reachable pond → ABORT, no refills recorded, inputs released."""
        assistant, actuator, _ = _build_assistant(
            dry_planted_tiles=[TileCoord(-1, 0), TileCoord(-1, 1)],
            water_body_tiles=[],
            capacity=1,
        )

        stats = assistant.run()

        assert stats.final_state == "ABORT"
        assert stats.tiles_watered == 1
        assert stats.refills == 0
        assert len(actuator._held_keys) == 0
        assert not actuator.is_pressed

    def test_programmatic_abort_stops_session(self):
        """A pre-requested abort stops immediately and releases held keys."""
        assistant, actuator, _ = _build_assistant(
            dry_planted_tiles=[TileCoord(-1, 0)],
        )
        actuator.key_down("w")  # Simulate a key held at F9 time.
        assistant.safety.request_abort("test")

        stats = assistant.run()

        assert stats.final_state == "ABORT"
        assert stats.tiles_watered == 0
        assert len(actuator._held_keys) == 0

    def test_navigation_guard_skips_distant_targets(self):
        """Targets beyond map_edge_margin_tiles are skipped, not watered."""
        assistant, _, _ = _build_assistant(
            dry_planted_tiles=[TileCoord(-1, 0), TileCoord(0, 12)],
        )
        assistant.map_edge_margin_tiles = 4

        stats = assistant.run()

        assert stats.final_state == "DONE"
        assert stats.tiles_watered == 1
        assert stats.tiles_skipped == 1


# ─── Safety & keyboard extension ────────────────────────────────────


class TestSafety:
    """Tests #12–#13: killswitch key release and keyboard extension."""

    def test_killswitch_releases_keys(self):
        """F9 during watering → all WASD + LMB released within < 200 ms."""
        actuator = MockActuator()
        abort = threading.Event()
        nav = TileNavigator(actuator, abort, tile_walk_ms=500)

        move_done = threading.Event()

        def _long_move() -> None:
            nav.move_one_tile((0, 1))
            move_done.set()

        thread = threading.Thread(target=_long_move)
        thread.start()
        time.sleep(0.05)

        start = time.perf_counter()
        abort.set()
        actuator.release_all_keys()
        release_time = (time.perf_counter() - start) * 1000.0

        assert move_done.wait(timeout=2.0)
        assert release_time < 200, f"Key release took {release_time:.1f} ms > 200 ms"
        assert len(actuator._held_keys) == 0, "Keys still held after release"

    def test_safety_callback_releases_on_abort(self):
        """SafetySupervisor fires on_abort cross-thread via request_abort."""
        actuator = MockActuator()
        safety = SafetySupervisor(on_abort=actuator.emergency_release)
        actuator.key_down("w")
        assert "w" in actuator._held_keys

        safety.request_abort("test")

        assert safety.is_abort_requested()
        assert safety.abort_reason == "test"
        assert len(actuator._held_keys) == 0
        assert not actuator.is_pressed

    def test_keyboard_extension(self):
        """key_down, key_up, key_press dispatch correctly on mock actuator."""
        actuator = MockActuator()

        actuator.key_down("w")
        assert "w" in actuator._held_keys
        actuator.key_up("w")
        assert "w" not in actuator._held_keys

        actuator.key_press("d", duration=0.01)
        assert "d" not in actuator._held_keys

        actuator.move_cursor(100, 200)

        events = [e for e, _ in actuator.history]
        assert "KEY_DOWN:w" in events
        assert "KEY_UP:w" in events
        assert "KEY_DOWN:d" in events
        assert "KEY_UP:d" in events
        assert "MOVE_CURSOR:100,200" in events

        actuator.key_down("a")
        actuator.key_down("s")
        actuator.release_all_keys()
        assert len(actuator._held_keys) == 0
