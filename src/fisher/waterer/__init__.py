"""Auto Waterer — autonomous crop watering for Stardew Valley.

Usage::

    from fisher.waterer import WateringAssistant

    assistant = WateringAssistant.from_config()
    assistant.run()  # Blocks until done or F9

CLI::

    fisher --water            # Water all visible crops
    fisher --water --mock     # Headless dry run
    fisher --water --preview  # With CV overlay
"""

from fisher.extraction.crops import FarmTileClassifier
from fisher.extraction.tiles import (
    TileCoord,
    TileState,
    estimate_grid_phase,
    screen_to_tile,
    tile_to_screen,
)
from fisher.waterer.assistant import WateringAssistant, WatererState
from fisher.waterer.detector import FarmScanner, ScanResult
from fisher.waterer.navigator import TileNavigator
from fisher.waterer.pathfinder import plan_greedy, plan_path, plan_serpentine

__all__ = [
    "WateringAssistant",
    "WatererState",
    "FarmTileClassifier",
    "FarmScanner",
    "ScanResult",
    "TileNavigator",
    "plan_path",
    "plan_greedy",
    "plan_serpentine",
    "TileCoord",
    "TileState",
    "estimate_grid_phase",
    "screen_to_tile",
    "tile_to_screen",
]
