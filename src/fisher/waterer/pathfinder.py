"""Path planners for the waterer: greedy nearest-neighbor and serpentine row scan.

The auto strategy selects serpentine when the targets form a roughly
rectangular patch (fill ratio > 60 %, aspect ratio < 3:1), otherwise greedy.
"""

from __future__ import annotations

from fisher.extraction.tiles import TileCoord


def plan_greedy(tiles: list[TileCoord], start: TileCoord = TileCoord(0, 0)) -> list[TileCoord]:
    """Visit tiles in nearest-first order (Manhattan distance).

    General-purpose path for irregular tile arrangements. O(n²) but n is
    typically < 200 visible tiles, so negligible.

    Args:
        tiles: Tiles to visit.
        start: Starting position (player tile).

    Returns:
        Ordered list of tiles to visit.
    """
    if not tiles:
        return []
    remaining = list(tiles)
    path: list[TileCoord] = []
    current = start
    while remaining:
        nearest = min(
            remaining,
            key=lambda t: abs(t.row - current.row) + abs(t.col - current.col),
        )
        path.append(nearest)
        remaining.remove(nearest)
        current = nearest
    return path


def plan_serpentine(tiles: list[TileCoord]) -> list[TileCoord]:
    """Sort tiles into boustrophedon (serpentine) row order.

    Optimal for rectangular crop patches — minimizes backtracking by scanning
    rows alternately left→right and right→left.

    Args:
        tiles: Tiles to visit.

    Returns:
        Tiles sorted in serpentine row-scan order.
    """
    if not tiles:
        return []
    by_row: dict[int, list[TileCoord]] = {}
    for t in tiles:
        by_row.setdefault(t.row, []).append(t)

    path: list[TileCoord] = []
    for i, row in enumerate(sorted(by_row.keys())):
        row_tiles = sorted(by_row[row], key=lambda t: t.col, reverse=(i % 2 == 1))
        path.extend(row_tiles)
    return path


def plan_path(
    tiles: list[TileCoord],
    start: TileCoord = TileCoord(0, 0),
    strategy: str = "auto",
) -> list[TileCoord]:
    """Select and execute a path planning strategy.

    Args:
        tiles: Tiles to visit.
        start: Player's current tile position.
        strategy: ``"auto"`` | ``"serpentine"`` | ``"greedy"``.

    Returns:
        Ordered list of tiles to visit.
    """
    if not tiles:
        return []

    if strategy == "serpentine":
        return plan_serpentine(tiles)
    if strategy == "greedy":
        return plan_greedy(tiles, start)

    if _is_rectangular(tiles):
        return plan_serpentine(tiles)
    return plan_greedy(tiles, start)


def _is_rectangular(
    tiles: list[TileCoord],
    fill_threshold: float = 0.6,
    max_aspect_ratio: float = 3.0,
) -> bool:
    """Heuristic: True when tiles form a compact rectangular patch.

    A patch qualifies when its bounding box is densely filled (default > 60 %)
    and not a long thin line (default aspect ratio < 3:1). Thin lines are
    handled better by greedy traversal.
    """
    if len(tiles) < 4:
        return False

    rows = [t.row for t in tiles]
    cols = [t.col for t in tiles]
    row_span = max(rows) - min(rows) + 1
    col_span = max(cols) - min(cols) + 1
    bbox_area = row_span * col_span
    if bbox_area == 0:
        return False

    fill_ratio = len(tiles) / bbox_area
    aspect_ratio = max(row_span, col_span) / min(row_span, col_span)
    return fill_ratio >= fill_threshold and aspect_ratio <= max_aspect_ratio
