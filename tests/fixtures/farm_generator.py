"""Synthetic farm-scene fixtures for the waterer test suite.

The implementation lives in :mod:`fisher.waterer.farm_sim` so that
``fisher --water --mock`` never imports test code. This module re-exports it
for the test suite (and any external fixture consumers).
"""

from fisher.waterer.farm_sim import (
    FarmSceneSimulator,
    generate_farm_frame,
)

__all__ = [
    "FarmSceneSimulator",
    "generate_farm_frame",
]
