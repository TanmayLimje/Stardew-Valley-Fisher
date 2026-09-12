"""Input actuators for Fisher."""

from typing import Optional

from fisher.config import FisherConfig, load_config
from fisher.input.base import Actuator
from fisher.input.direct_input import DirectInputActuator, is_admin_process
from fisher.input.mock_actuator import MockActuator


def create_actuator(
    config: Optional[FisherConfig] = None,
    mock: bool = False,
    strict_foreground: bool = True,
) -> Actuator:
    """Factory creating DirectInputActuator or MockActuator based on configuration/flag."""
    if mock:
        return MockActuator()

    cfg = config or load_config()
    window_title = cfg.game.get("window_title", "Stardew Valley")
    return DirectInputActuator(
        window_title=window_title,
        strict_foreground=strict_foreground,
    )


__all__ = [
    "Actuator",
    "MockActuator",
    "DirectInputActuator",
    "create_actuator",
    "is_admin_process",
]
