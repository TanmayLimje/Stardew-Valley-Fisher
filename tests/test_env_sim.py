"""Contract and integration tests for StardewFishSimEnv Gymnasium environment."""

import gymnasium as gym
from gymnasium.utils.env_checker import check_env
import numpy as np
import pytest

from fisher.sim.env_sim import CurriculumStage, SimEnvConfig, StardewFishSimEnv


def test_gymnasium_env_contract():
    """Verify StardewFishSimEnv satisfies the Gymnasium API contract."""
    env = StardewFishSimEnv()
    # check_env asserts observation_space, action_space, step/reset contracts
    check_env(env.unwrapped)
    env.close()


def test_gymnasium_registered_make():
    """Verify StardewFishSim-v0 is registered and instantiable via gym.make."""
    env = gym.make("StardewFishSim-v0")
    obs, info = env.reset(seed=42)
    assert obs.shape == (9,)
    assert obs.dtype == np.float32
    assert -1.0 <= obs.all() <= 1.0

    action = env.action_space.sample()
    next_obs, reward, terminated, truncated, info = env.step(action)
    assert next_obs.shape == (9,)
    assert isinstance(reward, float)
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert "progress" in info
    env.close()


def test_seed_determinism():
    """Verify resetting with identical seeds produces identical rollouts."""
    env1 = StardewFishSimEnv(SimEnvConfig(enable_domain_rand=False, curriculum_stage=CurriculumStage.NOMINAL))
    env2 = StardewFishSimEnv(SimEnvConfig(enable_domain_rand=False, curriculum_stage=CurriculumStage.NOMINAL))

    obs1, _ = env1.reset(seed=12345)
    obs2, _ = env2.reset(seed=12345)
    np.testing.assert_allclose(obs1, obs2, atol=1e-5)

    for a in [1, 1, 0, 0, 1, 0, 1]:
        o1, r1, d1, t1, _ = env1.step(a)
        o2, r2, d2, t2, _ = env2.step(a)
        np.testing.assert_allclose(o1, o2, atol=1e-5)
        assert abs(r1 - r2) < 1e-5
        assert d1 == d2
        assert t1 == t2

    env1.close()
    env2.close()


def test_curriculum_stages():
    """Verify curriculum stages properly configure difficulty ranges."""
    env = StardewFishSimEnv()

    # Stage A: d in [5, 35]
    _, info_a = env.reset(seed=1, options={"curriculum_stage": CurriculumStage.A})
    assert 5.0 <= info_a["difficulty"] <= 35.0

    # Stage B: d in [20, 70]
    _, info_b = env.reset(seed=1, options={"curriculum_stage": CurriculumStage.B})
    assert 20.0 <= info_b["difficulty"] <= 70.0

    # Stage C: d in [5, 110]
    _, info_c = env.reset(seed=1, options={"curriculum_stage": CurriculumStage.C})
    assert 5.0 <= info_c["difficulty"] <= 110.0

    env.close()
