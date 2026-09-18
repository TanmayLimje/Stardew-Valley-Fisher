"""Synthetic farm scene generator and stateful simulator for the waterer.

Two layers:

1. :func:`generate_farm_frame` — stateless BGR frame painter calibrated so the
   synthetic colors land inside :class:`~fisher.extraction.crops.FarmTileClassifier`
   HSV ranges.
2. :class:`FarmSceneSimulator` — stateful scene used by ``fisher --water --mock``
   and the test suite. It mirrors the live camera: tiles are rendered relative
   to the navigator's current position, and tiles darken as they are watered.

Only used for offline testing — the live path never imports this module.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from fisher.extraction.tiles import TileCoord


# BGR colors calibrated to produce correct HSV values for detection:
#   DRY SOIL:   BGR=(50,70,100)   → HSV=(12,128,100) — in [10-25, 60-140, 80-140]
#   WATERED:    BGR=(20,25,30)    → HSV=(15,85,30)   — in [10-30, 40-100, 30-70]
#   CROP:       BGR=(20,80,20)    → HSV=(60,191,80)  — in [35-85, 80-255, 60-200]
#   WATER:      BGR=(100,60,20)   → HSV=(105,204,100) — in [90-130, 80-255, 80-200]
#   BACKGROUND: BGR=(160,180,200) → sandy, outside all masks
BGR_DRY_SOIL = (50, 70, 100)
BGR_WATERED = (20, 25, 30)
BGR_CROP = (20, 80, 20)
BGR_WATER = (100, 60, 20)
BGR_BACKGROUND = (160, 180, 200)


def generate_farm_frame(
    width: int = 1920,
    height: int = 1080,
    tile_size: int = 64,
    grid_offset: tuple[int, int] = (0, 0),
    dry_planted_tiles: Optional[list[TileCoord]] = None,
    watered_tiles: Optional[list[TileCoord]] = None,
    water_body_tiles: Optional[list[TileCoord]] = None,
    dry_empty_tiles: Optional[list[TileCoord]] = None,
    player_center: tuple[int, int] = (960, 540),
    player_tile: TileCoord = TileCoord(0, 0),
) -> np.ndarray:
    """Generate a synthetic farm frame in world coordinates.

    Scene tiles are painted at player-relative screen positions: a tile at
    world coordinate ``t`` is drawn at ``player_center + (t - player_tile) * tile_size``.
    This matches the live camera (which locks the farmer to screen center), so
    scans of the returned frame yield player-relative coordinates.

    Args:
        width: Frame width in pixels.
        height: Frame height in pixels.
        tile_size: Tile grid size in pixels.
        grid_offset: Sub-tile phase (dx, dy) to shift the tile grid.
        dry_planted_tiles: World tiles rendered as dry soil + green crop.
        watered_tiles: World tiles rendered as watered (dark) soil.
        water_body_tiles: World tiles rendered as blue water body.
        dry_empty_tiles: World tiles rendered as dry soil without crops.
        player_center: Player's screen position (center of the grid).
        player_tile: Player's world tile — the camera anchor.

    Returns:
        BGR image (H×W×3, uint8).
    """
    frame = np.full((height, width, 3), dtype=np.uint8, fill_value=BGR_BACKGROUND)

    px, py = player_center
    ox, oy = grid_offset
    ts = tile_size

    def _tile_rect(tile: TileCoord) -> tuple[int, int, int, int]:
        """Get the (x0, y0, x1, y1) screen rect for a world tile."""
        cx = px + (tile.col - player_tile.col) * ts + ox
        cy = py + (tile.row - player_tile.row) * ts + oy
        x0 = cx - ts // 2
        y0 = cy - ts // 2
        return (max(0, x0), max(0, y0), min(width, x0 + ts), min(height, y0 + ts))

    def _fill_tile(tile: TileCoord, bgr: tuple[int, int, int]) -> None:
        x0, y0, x1, y1 = _tile_rect(tile)
        if x1 > x0 and y1 > y0:
            frame[y0:y1, x0:x1] = bgr

    for tile in (dry_empty_tiles or []):
        _fill_tile(tile, BGR_DRY_SOIL)

    for tile in (dry_planted_tiles or []):
        x0, y0, x1, y1 = _tile_rect(tile)
        if x1 > x0 and y1 > y0:
            split = y0 + int((y1 - y0) * 0.4)
            frame[y0:split, x0:x1] = BGR_CROP
            frame[split:y1, x0:x1] = BGR_DRY_SOIL

    for tile in (watered_tiles or []):
        _fill_tile(tile, BGR_WATERED)

    for tile in (water_body_tiles or []):
        _fill_tile(tile, BGR_WATER)

    return frame


class FarmSceneSimulator:
    """Stateful synthetic farm scene mirroring the live camera and watering.

    The scene tracks which world tiles have been watered. ``capture()`` returns
    a frame rendered relative to the bound navigator's current position, so the
    mock pipeline exercises the exact same player-relative coordinate math as
    the live pipeline.
    """

    def __init__(
        self,
        tile_size: int = 64,
        player_center: tuple[int, int] = (960, 540),
        grid_offset: tuple[int, int] = (0, 0),
        watered_tiles: Optional[set[TileCoord]] = None,
    ) -> None:
        self.tile_size = tile_size
        self.player_center = player_center
        self.grid_offset = grid_offset
        self.watered_tiles: set[TileCoord] = watered_tiles if watered_tiles is not None else set()

        self.dry_planted_tiles: list[TileCoord] = []
        self.water_body_tiles: list[TileCoord] = []

        self._navigator = None

    def seed(
        self,
        dry_planted_tiles: Optional[list[TileCoord]] = None,
        water_body_tiles: Optional[list[TileCoord]] = None,
    ) -> None:
        """Populate the scene's world tiles."""
        self.dry_planted_tiles = list(dry_planted_tiles or [])
        self.water_body_tiles = list(water_body_tiles or [])

    def bind_navigator(self, navigator) -> None:
        """Bind a :class:`~fisher.waterer.navigator.TileNavigator` as camera anchor."""
        self._navigator = navigator

    def capture(self) -> np.ndarray:
        """Render the scene from the bound navigator's current position."""
        player = self._navigator.current_pos if self._navigator is not None else TileCoord(0, 0)
        return generate_farm_frame(
            tile_size=self.tile_size,
            grid_offset=self.grid_offset,
            dry_planted_tiles=[
                t for t in self.dry_planted_tiles if t not in self.watered_tiles
            ],
            watered_tiles=list(self.watered_tiles),
            water_body_tiles=self.water_body_tiles,
            player_center=self.player_center,
            player_tile=player,
        )
