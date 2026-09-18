"""Configuration loader and schema definition for Fisher."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


@dataclass
class FisherConfig:
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def game(self) -> Dict[str, Any]:
        return self.raw.get("game", {})

    @property
    def capture(self) -> Dict[str, Any]:
        return self.raw.get("capture", {})

    @property
    def ui(self) -> Dict[str, Any]:
        return self.raw.get("ui", {})

    @property
    def control(self) -> Dict[str, Any]:
        return self.raw.get("control", {})

    @property
    def reward(self) -> Dict[str, Any]:
        return self.raw.get("reward", {})

    @property
    def ppo(self) -> Dict[str, Any]:
        return self.raw.get("ppo", {})

    @property
    def sim(self) -> Dict[str, Any]:
        return self.raw.get("sim", {})

    @property
    def lifecycle(self) -> Dict[str, Any]:
        return self.raw.get("lifecycle", {})

    @property
    def safety(self) -> Dict[str, Any]:
        return self.raw.get("safety", {})

    @property
    def waterer(self) -> Dict[str, Any]:
        return self.raw.get("waterer", {})


def get_default_config_path() -> Path:
    """Resolve default config file path."""
    current_dir = Path(__file__).resolve().parent
    repo_root = current_dir.parent.parent
    candidate = repo_root / "configs" / "default.yaml"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"Default config not found at: {candidate}")


def load_config(
    base_path: Optional[str | Path] = None,
    overlay_path: Optional[str | Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> FisherConfig:
    """Load configuration from base YAML, apply optional overlay YAML, then overrides."""
    if base_path is None:
        base_path = get_default_config_path()
    else:
        base_path = Path(base_path)

    if not base_path.is_file():
        raise FileNotFoundError(f"Configuration file does not exist: {base_path}")

    with open(base_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if overlay_path:
        overlay_path = Path(overlay_path)
        if overlay_path.is_file():
            with open(overlay_path, "r", encoding="utf-8") as f:
                overlay_data = yaml.safe_load(f) or {}
                data = _deep_merge(data, overlay_data)

    if overrides:
        data = _deep_merge(data, overrides)

    return FisherConfig(raw=data)
