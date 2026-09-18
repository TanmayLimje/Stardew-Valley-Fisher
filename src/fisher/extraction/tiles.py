"""Tile coordinate system and screen↔grid conversion for Stardew Valley.

Stardew uses a fixed 64×64 pixel tile grid at 1920×1080 / 100% UI zoom.
The camera is locked to the player character, who is centered on screen
(except near map borders where the camera clamps).

All coordinates are player-relative: ``TileCoord(0, 0)`` is the player's tile.
"""

from __future__ import annotations

import enum
from typing import NamedTuple

import numpy as np


class TileState(enum.Enum):
    """Classification of a single tile on the farm."""

    UNTILLED = "untilled"
    DRY_EMPTY = "dry_empty"        # Tilled soil, no crop sprite
    DRY_PLANTED = "dry_planted"    # Tilled soil + green crop sprite → needs watering
    WATERED = "watered"            # Already watered (dark soil)
    WATER_BODY = "water_body"      # Pond / river — refill target
    OTHER = "other"                # Fences, buildings, paths, etc.


class TileCoord(NamedTuple):
    """Grid-relative tile position. Player = (0, 0).

    Positive row = south (down), positive col = east (right).
    """

    row: int
    col: int


def screen_to_tile(
    px_x: int,
    px_y: int,
    player_center: tuple[int, int] = (960, 540),
    tile_size: int = 64,
    grid_offset: tuple[int, int] = (0, 0),
) -> TileCoord:
    """Convert screen pixel to player-relative tile coordinate.

    Args:
        px_x: Screen x-coordinate (pixels).
        px_y: Screen y-coordinate (pixels).
        player_center: Player's pixel position on screen (camera-locked center).
        tile_size: Tile side length in pixels (64 at 1080p/100% zoom).
        grid_offset: Sub-tile phase (dx, dy) recovered by ``estimate_grid_phase``.
            Without it, quantized cells straddle two world tiles whenever the
            player stands off tile alignment — which is most of the time.

    Returns:
        TileCoord relative to the player (0, 0).
    """
    col = round((px_x - player_center[0] - grid_offset[0]) / tile_size)
    row = round((px_y - player_center[1] - grid_offset[1]) / tile_size)
    return TileCoord(row=row, col=col)


def tile_to_screen(
    tile: TileCoord,
    player_center: tuple[int, int] = (960, 540),
    tile_size: int = 64,
    grid_offset: tuple[int, int] = (0, 0),
) -> tuple[int, int]:
    """Convert tile coordinate to screen pixel center (for cursor aiming).

    Args:
        tile: Player-relative tile coordinate.
        player_center: Player's pixel position on screen.
        tile_size: Tile side length in pixels.
        grid_offset: Sub-tile phase (dx, dy).

    Returns:
        (px_x, px_y) screen pixel at the center of the target tile.
    """
    px_x = player_center[0] + tile.col * tile_size + grid_offset[0]
    px_y = player_center[1] + tile.row * tile_size + grid_offset[1]
    return (px_x, px_y)


def estimate_grid_phase(
    soil_mask: np.ndarray,
    tile_size: int = 64,
    player_center: tuple[int, int] = (960, 540),
) -> tuple[int, int]:
    """Recover the sub-tile grid offset from tilled-soil edges.

    Tilled plots form a regular lattice. The world lattice is not screen
    aligned (the farmer moves continuously), so each scan re-derives the
    phase by correlating soil-mask projection gradients against candidate
    tile-boundary positions.

    Args:
        soil_mask: Binary mask (H×W, uint8) where tilled soil pixels = 255.
        tile_size: Expected tile size in pixels.
        player_center: Player's screen position (camera center), default (960, 540).

    Returns:
        (dx, dy) sub-tile pixel offset. Falls back to (0, 0) if too little
        tilled soil is visible (< 2% of frame).
    """
    h, w = soil_mask.shape[:2]

    soil_ratio = np.count_nonzero(soil_mask) / (h * w)
    if soil_ratio < 0.02:
        return (0, 0)

    col_proj = np.sum(soil_mask > 0, axis=0).astype(np.float64)  # shape (W,)
    row_proj = np.sum(soil_mask > 0, axis=1).astype(np.float64)  # shape (H,)

    def best_phase(projection: np.ndarray) -> int:
        """Find the screen boundary phase that maximizes edge alignment."""
        n = len(projection)
        if n < tile_size * 2:
            return 0
        grad = np.abs(np.diff(projection))

        best_score = -1.0
        best_offset = 0
        for offset in range(tile_size):
            indices = np.arange(offset, n, tile_size)
            # Boundary at `idx` corresponds to transition between idx-1 and idx.
            score = sum(grad[idx - 1] for idx in indices if 1 <= idx <= len(grad))
            if score > best_score:
                best_score = score
                best_offset = offset
        return best_offset

    edge_x = best_phase(col_proj)
    edge_y = best_phase(row_proj)

    half = tile_size // 2
    # Invert boundary phase to player-relative tile offset:
    # tile center = boundary + half; offset = (center - player_center) % tile_size
    dx = (edge_x + half - player_center[0]) % tile_size
    dy = (edge_y + half - player_center[1]) % tile_size

    # Normalize to [-tile_size/2, +tile_size/2) range.
    if dx > half:
        dx -= tile_size
    if dy > half:
        dy -= tile_size

    return (dx, dy)
