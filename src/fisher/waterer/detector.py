"""Full-frame farm scanner composing tile classification into a scan result.

Runs :class:`~fisher.extraction.crops.FarmTileClassifier` over the entire
visible tile grid and returns the classified tile map with metadata
(grid phase, unwatered targets, water-body tiles).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import cv2
import numpy as np

from fisher.extraction.crops import FarmTileClassifier
from fisher.extraction.tiles import TileCoord, TileState, estimate_grid_phase


@dataclass
class ScanResult:
    """Output of a full-frame farm scan."""

    tile_map: dict[TileCoord, TileState] = field(default_factory=dict)
    grid_offset: tuple[int, int] = (0, 0)
    unwatered_tiles: list[TileCoord] = field(default_factory=list)
    water_tiles: list[TileCoord] = field(default_factory=list)
    total_tiles_scanned: int = 0


class FarmScanner:
    """Compose ``FarmTileClassifier`` over a captured frame.

    Orchestrates:
        1. Grid phase recovery (when configured as ``auto``)
        2. Per-tile HSV classification
        3. Filtering the player-center tile
        4. Identifying unwatered (DRY_PLANTED) and water-body tiles
    """

    def __init__(
        self,
        classifier: FarmTileClassifier,
        grid_phase_mode: str = "auto",
        tile_size: int = 64,
        grid_offset: tuple[int, int] = (0, 0),
    ) -> None:
        self.classifier = classifier
        self.grid_phase_mode = grid_phase_mode
        self.tile_size = tile_size
        self.grid_offset = grid_offset

    def scan(self, frame: np.ndarray) -> ScanResult:
        """Run full-frame tile classification.

        Args:
            frame: BGR image (H×W×3, uint8) from screen capture.

        Returns:
            ``ScanResult`` with classified tiles, unwatered list, water tiles.
        """
        grid_offset: Optional[tuple[int, int]] = None
        if self.grid_phase_mode == "auto":
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            dry_mask = cv2.inRange(
                hsv,
                self.classifier.hsv_ranges["dry_soil"]["lower"],
                self.classifier.hsv_ranges["dry_soil"]["upper"],
            )
            wet_mask = cv2.inRange(
                hsv,
                self.classifier.hsv_ranges["watered_soil"]["lower"],
                self.classifier.hsv_ranges["watered_soil"]["upper"],
            )
            soil_mask = cv2.bitwise_or(dry_mask, wet_mask)
            grid_offset = estimate_grid_phase(
                soil_mask,
                self.tile_size,
                player_center=self.classifier.player_center,
            )
        else:
            grid_offset = self.grid_offset

        tile_map = self.classifier.classify_frame(frame, grid_offset=grid_offset)

        player_tile = TileCoord(0, 0)
        unwatered = [
            tc for tc, state in tile_map.items()
            if state == TileState.DRY_PLANTED and tc != player_tile
        ]
        water = [tc for tc, state in tile_map.items() if state == TileState.WATER_BODY]

        return ScanResult(
            tile_map=tile_map,
            grid_offset=grid_offset,
            unwatered_tiles=unwatered,
            water_tiles=water,
            total_tiles_scanned=len(tile_map),
        )

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "FarmScanner":
        """Build from a ``waterer`` config section."""
        grid_phase = config.get("grid_phase", "auto")
        if isinstance(grid_phase, (list, tuple)) and len(grid_phase) == 2:
            mode = "fixed"
            offset = (int(grid_phase[0]), int(grid_phase[1]))
        else:
            mode = str(grid_phase)
            offset = (0, 0)

        return cls(
            classifier=FarmTileClassifier.from_config(config),
            grid_phase_mode=mode,
            tile_size=int(config.get("tile_size_px", 64)),
            grid_offset=offset,
        )
