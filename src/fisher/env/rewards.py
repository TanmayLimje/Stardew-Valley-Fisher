"""Shared reward formulation for simulation and live environments.

Implements potential-based progress shaping (Ng et al. 1999) with proven
anti-stall mathematical guarantees (Appendix C).
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass
class RewardConfig:
    """Reward weight hyperparameters from configs/default.yaml."""

    w_o: float = 0.02      # In-bar soft overlap bonus
    w_p: float = 5.0       # Potential progress shaping coefficient
    w_v: float = 0.002     # Bar oscillation/velocity penalty
    w_s: float = 0.005     # Per-step survival/time cost
    r_win: float = 10.0    # Terminal catch bonus
    r_lose: float = 5.0    # Terminal escape penalty
    gamma: float = 0.999   # Discount factor


def compute_soft_overlap(f: float, b: float, h: float) -> float:
    """Compute soft in-bar overlap signal o_t in [0, 1].
    
    Returns 1.0 when fish is within [b - h, b + h], decaying linearly
    to 0.0 at distance 2 * h away from center.
    """
    dist = abs(f - b)
    if dist <= h:
        return 1.0
    if dist >= 2.0 * h - 1e-9:
        return 0.0
    val = 1.0 - (dist - h) / max(h, 1e-6)
    return float(np.clip(val, 0.0, 1.0))


class RewardCalculator:
    """Calculates step rewards and terminal bonuses consistently across sim and live."""

    def __init__(self, config: RewardConfig | None = None) -> None:
        self.cfg = config or RewardConfig()

    def compute_step_reward(
        self,
        f_norm: float,
        b_norm: float,
        h_norm: float,
        v_norm: float,
        p_prev: float,
        p_curr: float,
        is_catch: bool = False,
        is_escape: bool = False,
        is_truncated: bool = False,
    ) -> tuple[float, dict[str, float]]:
        """Compute the scalar reward r_t and component breakdown.
        
        Args:
            f_norm: Normalized fish position [0, 1].
            b_norm: Normalized bar center [0, 1].
            h_norm: Normalized bar half-height [0, 1].
            v_norm: Normalized bar velocity [-1, 1].
            p_prev: Progress at step t [0, 1].
            p_curr: Progress at step t+1 [0, 1].
            is_catch: True if progress reached >= 1.0.
            is_escape: True if progress depleted <= 0.0.
            is_truncated: True if terminated by watchdog or timeout (receives 0 terminal bonus).
            
        Returns:
            Tuple of (total_reward, dict_of_components).
        """
        # 1. Soft in-bar overlap: w_o * o_t
        overlap = compute_soft_overlap(f_norm, b_norm, h_norm)
        r_overlap = self.cfg.w_o * overlap

        # 2. Potential-based progress shaping: w_p * (gamma * p_{t+1} - p_t)
        r_progress = self.cfg.w_p * (self.cfg.gamma * p_curr - p_prev)

        # 3. Oscillation penalty: -w_v * v_norm^2
        r_osc = -self.cfg.w_v * (v_norm ** 2)

        # 4. Time cost: -w_s
        r_time = -self.cfg.w_s

        # 5. Terminal bonuses
        r_term = 0.0
        if not is_truncated:
            if is_catch:
                r_term = self.cfg.r_win
            elif is_escape:
                r_term = -self.cfg.r_lose

        total = r_overlap + r_progress + r_osc + r_time + r_term

        info = {
            "r_overlap": float(r_overlap),
            "r_progress": float(r_progress),
            "r_osc": float(r_osc),
            "r_time": float(r_time),
            "r_term": float(r_term),
            "overlap": float(overlap),
        }
        return float(total), info
