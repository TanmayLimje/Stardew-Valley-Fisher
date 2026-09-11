"""Unit tests and mathematical audit for the reward formulation."""

import pytest
from fisher.env.rewards import RewardCalculator, RewardConfig, compute_soft_overlap


def test_soft_overlap_values():
    """Verify soft overlap returns 1.0 when inside bar and decays linearly outside."""
    b = 0.5
    h = 0.1

    # Exact center
    assert compute_soft_overlap(f=0.5, b=b, h=h) == 1.0

    # At bar boundary
    assert compute_soft_overlap(f=0.6, b=b, h=h) == 1.0
    assert compute_soft_overlap(f=0.4, b=b, h=h) == 1.0

    # Half bar-width outside (dist = 1.5 * h) -> val = 1 - 0.5 = 0.5
    assert abs(compute_soft_overlap(f=0.65, b=b, h=h) - 0.5) < 1e-6

    # Full bar-width outside (dist = 2 * h) -> val = 0.0
    assert compute_soft_overlap(f=0.70, b=b, h=h) == 0.0

    # Far away
    assert compute_soft_overlap(f=0.95, b=b, h=h) == 0.0


def test_reward_audit_anti_stall_dominance():
    """Verify Appendix C anti-stall audit:
    Catching decisively dominates stalling, and stalling with escape yields low or negative return.
    """
    calc = RewardCalculator(RewardConfig())

    # 1. Fast perfect catch (175 steps, 100% in bar, p 0.30 -> 1.0)
    total_fast = 0.0
    p = 0.30
    for _ in range(175):
        p_next = min(1.0, p + (0.70 / 175))
        is_catch = p_next >= 1.0
        r, _ = calc.compute_step_reward(
            f_norm=0.5, b_norm=0.5, h_norm=0.1, v_norm=0.0,
            p_prev=p, p_curr=p_next, is_catch=is_catch
        )
        total_fast += r
        p = p_next

    # 2. Grinding catch (690 steps, 70% in bar, p 0.30 -> 1.0)
    total_grind = 0.0
    p = 0.30
    for i in range(690):
        in_bar = (i % 10) < 7
        p_next = min(1.0, p + (0.70 / 690))
        is_catch = p_next >= 1.0
        f_pos = 0.5 if in_bar else 0.8  # in bar or outside
        r, _ = calc.compute_step_reward(
            f_norm=f_pos, b_norm=0.5, h_norm=0.1, v_norm=0.1,
            p_prev=p, p_curr=p_next, is_catch=is_catch
        )
        total_grind += r
        p = p_next

    # 3. Stalling 900 steps then escaping (70% in bar, p 0.30 -> 0.0 at end)
    total_stall = 0.0
    p = 0.30
    for i in range(900):
        in_bar = (i % 10) < 7
        # Stall hovers around 0.3, then drops to 0 at end
        p_next = 0.30 if i < 850 else max(0.0, 0.30 - (i - 850) * 0.006)
        is_escape = (i == 899)
        f_pos = 0.5 if in_bar else 0.8
        r, _ = calc.compute_step_reward(
            f_norm=f_pos, b_norm=0.5, h_norm=0.1, v_norm=0.1,
            p_prev=p, p_curr=p_next, is_escape=is_escape
        )
        total_stall += r
        p = p_next

    # Verification: Both catch scenarios should be strong positive (> +12)
    assert total_fast > 14.0
    assert total_grind > 14.0
    # Stalling then escaping should be severely penalized compared to catching (> 10 pts lower)
    assert total_stall < 4.0
    assert total_fast - total_stall > 10.0
    assert total_grind - total_stall > 10.0
