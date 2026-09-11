"""Heuristic baseline policies for evaluation and benchmark comparison."""

from __future__ import annotations

import numpy as np


class BasePolicy:
    """Abstract interface for policy evaluation."""

    def predict(self, obs: np.ndarray, deterministic: bool = True) -> tuple[int, None]:
        raise NotImplementedError


class RandomPolicy(BasePolicy):
    """Uniform random action selection (lower performance bound)."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = np.random.default_rng(seed)

    def predict(self, obs: np.ndarray, deterministic: bool = True) -> tuple[int, None]:
        action = int(self.rng.integers(0, 2))
        return action, None


class BangBangPolicy(BasePolicy):
    """Pure-pursuit script: HOLD if bar center is below fish, else RELEASE.
    
    Includes a small deadband and predictive velocity compensation to mitigate
    bounciness and inertia.
    """

    def __init__(self, deadband: float = 0.02, vel_compensation: float = 0.05) -> None:
        self.deadband = deadband
        self.vel_compensation = vel_compensation

    def predict(self, obs: np.ndarray, deterministic: bool = True) -> tuple[int, None]:
        # Observation format:
        # obs[0] = 2*b - 1 -> b = (obs[0] + 1) / 2
        # obs[1] = v (normalized velocity [-1, 1], positive = upward)
        # obs[2] = 2*f - 1 -> f = (obs[2] + 1) / 2
        b = (float(obs[0]) + 1.0) / 2.0
        v = float(obs[1])
        f = (float(obs[2]) + 1.0) / 2.0

        # Anticipated bar center based on velocity
        b_pred = b + self.vel_compensation * v

        if b_pred < f - self.deadband:
            action = 1  # HOLD (accelerate upward)
        elif b_pred > f + self.deadband:
            action = 0  # RELEASE (fall downward)
        else:
            # Within deadband: hold current trend or default release
            action = 1 if v < 0 else 0

        return action, None
