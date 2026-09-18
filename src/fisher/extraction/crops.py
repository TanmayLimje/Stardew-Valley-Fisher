"""HSV-based tile state classifier for Stardew Valley farm scenes.

Classifies every visible 64×64 tile into one of: UNTILLED, DRY_EMPTY,
DRY_PLANTED, WATERED, WATER_BODY, OTHER using configurable HSV thresholds.

WARNING: The HSV thresholds below are initial estimates. Stardew's day/night
global tint swings the V channel hard — these MUST be calibrated against real
screenshots at multiple times of day before live use (see waterer_plan.md §4.1).
"""

from __future__ import annotations

from typing import Any, Dict

import cv2
import numpy as np

from fisher.extraction.tiles import TileCoord, TileState


# Default HSV ranges (placeholder — calibrate with real frames).
DEFAULT_HSV_RANGES: Dict[str, Dict[str, np.ndarray]] = {
    "dry_soil": {
        "lower": np.array([10, 60, 80]),
        "upper": np.array([25, 140, 140]),
    },
    "watered_soil": {
        "lower": np.array([10, 40, 30]),
        "upper": np.array([30, 100, 70]),
    },
    "crop_sprite": {
        "lower": np.array([35, 80, 60]),
        "upper": np.array([85, 255, 200]),
    },
    "water_body": {
        "lower": np.array([90, 80, 80]),
        "upper": np.array([130, 255, 200]),
    },
}

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "dry_soil_pct": 30,
    "watered_soil_pct": 25,
    "crop_sprite_pct": 10,
    "water_body_pct": 40,
}


class FarmTileClassifier:
    """Classify tiles in a captured frame into soil states and features.

    Pipeline per frame:
        1. Convert BGR → HSV
        2. Apply HSV masks for each soil/feature state
        3. Quantize to the ``tile_size`` grid (with grid phase correction)
        4. For each tilled tile, check for green crop sprite presence
        5. Return the classified tile map
    """

    def __init__(
        self,
        tile_size: int = 64,
        player_center: tuple[int, int] = (960, 540),
        thresholds: Dict[str, float] | None = None,
        hsv_ranges: Dict[str, Dict[str, np.ndarray]] | None = None,
    ) -> None:
        self.tile_size = tile_size
        self.player_center = player_center

        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)

        self.hsv_ranges = hsv_ranges or dict(DEFAULT_HSV_RANGES)

    def classify_frame(
        self,
        frame: np.ndarray,
        grid_offset: tuple[int, int] = (0, 0),
    ) -> dict[TileCoord, TileState]:
        """Analyze a full-screen frame and return per-tile classifications.

        Args:
            frame: BGR image (H×W×3, uint8) — full screen capture.
            grid_offset: Sub-tile phase (dx, dy) from ``estimate_grid_phase``.

        Returns:
            Dictionary mapping player-relative ``TileCoord`` to ``TileState``.
        """
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        masks = self._compute_masks(hsv)

        tile_map: dict[TileCoord, TileState] = {}
        ts = self.tile_size
        px, py = self.player_center
        ox, oy = grid_offset
        half = ts // 2

        # Tile (0, 0) is centered at (px + ox, py + oy); its top-left pixel is
        # (px + ox - half, py + oy - half). Extend the grid in both directions
        # to cover the frame.
        col_min = -(px + ox - half) // ts - 1
        col_max = (w - px - ox - half) // ts + 1
        row_min = -(py + oy - half) // ts - 1
        row_max = (h - py - oy - half) // ts + 1

        for row in range(row_min, row_max + 1):
            for col in range(col_min, col_max + 1):
                tx = px + ox + col * ts - half
                ty = py + oy + row * ts - half

                x0 = max(0, tx)
                y0 = max(0, ty)
                x1 = min(w, tx + ts)
                y1 = min(h, ty + ts)
                if x1 - x0 < half or y1 - y0 < half:
                    continue  # Skip tiles that are mostly off-screen.

                tile_region = {
                    name: mask[y0:y1, x0:x1]
                    for name, mask in masks.items()
                }
                state = self._classify_tile(tile_region, x1 - x0, y1 - y0)
                tile_map[TileCoord(row=row, col=col)] = state

        return tile_map

    def _compute_masks(self, hsv: np.ndarray) -> dict[str, np.ndarray]:
        """Generate binary HSV masks for each feature type."""
        return {
            name: cv2.inRange(hsv, bounds["lower"], bounds["upper"])
            for name, bounds in self.hsv_ranges.items()
        }

    def _classify_tile(
        self,
        tile_masks: dict[str, np.ndarray],
        tile_w: int,
        tile_h: int,
    ) -> TileState:
        """Classify a single tile based on pixel percentages in each mask.

        Classification priority (highest to lowest):
            1. WATER_BODY — blue water pixels dominate
            2. WATERED — dark soil (already watered, skip)
            3. DRY_PLANTED — dry soil + green crop sprite → needs watering
            4. DRY_EMPTY — dry soil without crop
            5. OTHER — no dominant feature
        """
        total_px = tile_w * tile_h
        if total_px == 0:
            return TileState.OTHER

        water_pct = 100.0 * np.count_nonzero(tile_masks["water_body"]) / total_px
        if water_pct >= self.thresholds["water_body_pct"]:
            return TileState.WATER_BODY

        watered_pct = 100.0 * np.count_nonzero(tile_masks["watered_soil"]) / total_px
        if watered_pct >= self.thresholds["watered_soil_pct"]:
            return TileState.WATERED

        dry_pct = 100.0 * np.count_nonzero(tile_masks["dry_soil"]) / total_px
        crop_pct = 100.0 * np.count_nonzero(tile_masks["crop_sprite"]) / total_px

        if dry_pct >= self.thresholds["dry_soil_pct"]:
            if crop_pct >= self.thresholds["crop_sprite_pct"]:
                return TileState.DRY_PLANTED
            return TileState.DRY_EMPTY

        return TileState.OTHER

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "FarmTileClassifier":
        """Build from a ``waterer`` config section."""
        return cls(
            tile_size=int(config.get("tile_size_px", 64)),
            player_center=tuple(config.get("player_center", [960, 540])),
            thresholds=config.get("detection_thresholds"),
        )
