"""Gymnasium environment for the Stardew Valley fishing minigame simulation.

Grounded in decompiled BobberBar.cs with domain randomization and latency injection.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import numpy as np
import gymnasium as gym
from gymnasium import spaces

from fisher.env.rewards import RewardCalculator, RewardConfig
from fisher.sim.fish import FishConfig, FishMotionType, SimulatedFish
from fisher.sim.physics import BarPhysicsConfig, BobberBarPhysics


class CurriculumStage(str, Enum):
    """Curriculum stages for training and evaluation."""

    A = "A"        # Easy: d <= 35, large bar (h >= 0.18), delay <= 2, no obs noise
    B = "B"        # Mid: d <= 70, nominal domain randomization
    C = "C"        # Full DR: d <= 110, full latency injection and observation noise
    NOMINAL = "NOMINAL"  # Deterministic nominal evaluation (no DR, delay=1 tick)


@dataclass
class SimEnvConfig:
    """Environment configuration options."""

    control_hz: int = 30           # 30 Hz control rate (2 physics ticks per step)
    physics_hz: int = 60           # 60 Hz internal physics clock
    max_duration_s: float = 30.0   # Episode timeout (900 steps @ 30 Hz)
    initial_p: float = 0.30        # Default starting progress (BobberBar.cs line 130)
    curriculum_stage: CurriculumStage = CurriculumStage.C
    fixed_difficulty: float | None = None
    fixed_motion_type: FishMotionType | None = None
    enable_domain_rand: bool = True
    action_delay_ticks: int = 1    # Nominal 1-tick delay for MonoGame input poll
    obs_delay_ticks: int = 1       # Nominal 1-tick delay


class StardewFishSimEnv(gym.Env):
    """Gymnasium environment simulating Stardew Valley fishing minigame."""

    metadata = {"render_modes": ["ansi"], "render_fps": 30}

    def __init__(self, config: SimEnvConfig | None = None, reward_config: RewardConfig | None = None) -> None:
        super().__init__()
        self.cfg = config or SimEnvConfig()
        self.reward_calc = RewardCalculator(reward_config)

        # Action: Discrete(2) -> 0: RELEASE, 1: HOLD
        self.action_space = spaces.Discrete(2)

        # Observation: 9 features normalized to [-1, 1]
        # [b, v, f, f_dot, h, p, I, delta, a_prev]
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(9,), dtype=np.float32
        )

        self.physics = BobberBarPhysics()
        self.fish = SimulatedFish()

        self.ticks_per_step = int(self.cfg.physics_hz // self.cfg.control_hz)
        self.max_steps = int(self.cfg.max_duration_s * self.cfg.control_hz)

        # Ring buffers for latency simulation (in 60 Hz ticks)
        self._action_buffer: deque[int] = deque()
        self._obs_buffer: deque[np.ndarray] = deque()

        # Episode state
        self.progress = self.cfg.initial_p
        self.prev_action = 0
        self.step_count = 0
        self.obs_noise_std = 0.0
        self.obs_bias = 0.0

    def set_curriculum_stage(self, stage: CurriculumStage) -> None:
        """Update the curriculum stage for subsequent resets."""
        self.cfg.curriculum_stage = stage

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict | None = None,
    ) -> tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        options = options or {}

        # 1. Determine curriculum parameters and domain randomization
        stage = options.get("curriculum_stage", self.cfg.curriculum_stage)
        diff_override = options.get("difficulty", self.cfg.fixed_difficulty)
        motion_override = options.get("motion_type", self.cfg.fixed_motion_type)

        if stage == CurriculumStage.NOMINAL:
            difficulty = diff_override if diff_override is not None else 40.0
            motion_type = (
                motion_override if motion_override is not None else FishMotionType.MIXED
            )
            bar_height = 96.0
            gravity = 0.25
            in_bar_factor = 0.6
            restitution = 2.0 / 3.0
            p0 = 0.30
            act_delay = 1
            obs_delay = 1
            self.obs_noise_std = 0.0
            self.obs_bias = 0.0
        elif stage == CurriculumStage.A:
            difficulty = (
                diff_override
                if diff_override is not None
                else float(self.np_random.uniform(5.0, 35.0))
            )
            motion_type = (
                motion_override
                if motion_override is not None
                else FishMotionType(self.np_random.integers(0, 3))
            )
            bar_height = float(self.np_random.uniform(112.0, 144.0))  # large bar
            gravity = 0.25
            in_bar_factor = 0.6
            restitution = 2.0 / 3.0
            p0 = 0.30
            act_delay = int(self.np_random.integers(1, 3))
            obs_delay = int(self.np_random.integers(1, 3))
            self.obs_noise_std = 0.0
            self.obs_bias = 0.0
        elif stage == CurriculumStage.B:
            difficulty = (
                diff_override
                if diff_override is not None
                else float(self.np_random.uniform(20.0, 70.0))
            )
            motion_type = (
                motion_override
                if motion_override is not None
                else FishMotionType(self.np_random.integers(0, 5))
            )
            bar_height = float(self.np_random.uniform(88.0, 120.0))
            gravity = float(self.np_random.uniform(0.23, 0.27))
            in_bar_factor = float(self.np_random.uniform(0.55, 0.65))
            restitution = float(self.np_random.uniform(0.60, 0.73))
            p0 = float(self.np_random.uniform(0.28, 0.32))
            act_delay = int(self.np_random.integers(1, 4))
            obs_delay = int(self.np_random.integers(1, 4))
            self.obs_noise_std = float(self.np_random.uniform(0.0, 0.01))
            self.obs_bias = float(self.np_random.uniform(-0.005, 0.005))
        else:  # Stage C: full domain randomization
            difficulty = (
                diff_override
                if diff_override is not None
                else float(self.np_random.uniform(25.0, 110.0))
            )
            motion_type = (
                motion_override
                if motion_override is not None
                else FishMotionType(self.np_random.integers(0, 5))
            )
            # bar_height in range ~50 to 125 px (h in ~0.08 to 0.22)
            bar_height = float(self.np_random.uniform(60.0, 128.0))
            gravity = float(self.np_random.uniform(0.22, 0.28))
            in_bar_factor = float(self.np_random.uniform(0.52, 0.68))
            restitution = float(self.np_random.uniform(0.58, 0.75))
            p0 = float(self.np_random.uniform(0.25, 0.35))
            act_delay = int(self.np_random.integers(1, 5))
            obs_delay = int(self.np_random.integers(1, 5))
            self.obs_noise_std = float(self.np_random.uniform(0.0, 0.02))
            self.obs_bias = float(self.np_random.uniform(-0.01, 0.01))

        # 2. Configure physics and fish
        self.physics.cfg = BarPhysicsConfig(
            bar_height=bar_height,
            gravity=gravity,
            in_bar_factor=in_bar_factor,
            restitution_bottom=restitution,
            restitution_top=restitution,
        )
        self.physics.reset()

        self.fish.cfg = FishConfig(
            difficulty=difficulty,
            motion_type=motion_type,
        )
        fish_seed = int(self.np_random.integers(0, 2**31 - 1))
        self.fish.rng = np.random.default_rng(fish_seed)
        self.fish.reset()

        self.progress = p0
        self.prev_action = 0
        self.step_count = 0

        # Pre-fill latency buffers
        self._action_buffer.clear()
        for _ in range(max(1, act_delay)):
            self._action_buffer.append(0)

        self._obs_buffer.clear()
        initial_obs = self._build_raw_observation()
        for _ in range(max(1, obs_delay)):
            self._obs_buffer.append(initial_obs)

        return self._get_delayed_observation(), {
            "difficulty": difficulty,
            "motion_type": motion_type.name,
            "bar_height": bar_height,
        }

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict]:
        self.step_count += 1
        p_prev = self.progress

        # Push action into latency queue and pull delayed action for physics ticks
        self._action_buffer.append(int(action))

        # Execute 2 internal 60 Hz physics sub-steps per 30 Hz control step
        in_bar = False
        for _ in range(self.ticks_per_step):
            act_to_apply = self._action_buffer.popleft() if self._action_buffer else int(action)
            # Re-enqueue if buffer empty to maintain pipeline
            if not self._action_buffer:
                self._action_buffer.append(act_to_apply)

            # Check in_bar using exact game logic
            in_bar = self.fish.is_in_bar(self.physics.bar_pos, self.physics.cfg.bar_height)

            # Bar step
            self.physics.step(button_pressed=bool(act_to_apply), in_bar=in_bar)

            # Fish step
            self.fish.step()

            # Progress integration (lines 525, 575)
            # +0.002 in-bar, -0.003 out-of-bar
            if in_bar:
                self.progress += 0.002
            else:
                self.progress -= 0.003
            self.progress = float(np.clip(self.progress, 0.0, 1.0))

        # Store raw observation in obs latency buffer
        raw_obs = self._build_raw_observation()
        self._obs_buffer.append(raw_obs)
        delayed_obs = self._get_delayed_observation()

        # Terminal conditions
        is_catch = self.progress >= 1.0
        is_escape = self.progress <= 0.0
        terminated = bool(is_catch or is_escape)
        truncated = bool(self.step_count >= self.max_steps)

        # Compute reward
        b_norm = self.physics.bar_center_norm
        h_norm = self.physics.bar_half_height_norm
        v_norm = self.physics.bar_velocity_norm
        f_norm = self.fish.position_norm

        reward, reward_info = self.reward_calc.compute_step_reward(
            f_norm=f_norm,
            b_norm=b_norm,
            h_norm=h_norm,
            v_norm=v_norm,
            p_prev=p_prev,
            p_curr=self.progress,
            is_catch=is_catch,
            is_escape=is_escape,
            is_truncated=truncated,
        )

        self.prev_action = int(action)

        info = {
            "is_catch": is_catch,
            "is_escape": is_escape,
            "progress": self.progress,
            "in_bar": in_bar,
            "difficulty": self.fish.cfg.difficulty,
            "motion_type": self.fish.cfg.motion_type.name,
            **reward_info,
        }
        return delayed_obs, reward, terminated, truncated, info

    def _build_raw_observation(self) -> np.ndarray:
        """Construct the 9-dimensional observation vector per Table 4.1."""
        b = self.physics.bar_center_norm
        v = self.physics.bar_velocity_norm
        f = self.fish.position_norm
        f_dot = self.fish.velocity_norm
        h = self.physics.bar_half_height_norm
        p = self.progress
        in_bar = 1.0 if abs(f - b) <= h else 0.0
        delta = f - b

        # Features mapped to [-1, 1] per Table 4.1:
        # 1. b: 2b - 1
        # 2. v: v (already normalized to [-1, 1])
        # 3. f: 2f - 1
        # 4. f_dot: f_dot (already normalized)
        # 5. h: 2h - 1
        # 6. p: 2p - 1
        # 7. in_bar: in_bar in {0, 1}
        # 8. delta: 2 * delta
        # 9. a_prev: a_prev in {0, 1}
        obs = np.array(
            [
                2.0 * b - 1.0,
                v,
                2.0 * f - 1.0,
                f_dot,
                2.0 * h - 1.0,
                2.0 * p - 1.0,
                in_bar,
                np.clip(2.0 * delta, -1.0, 1.0),
                float(self.prev_action),
            ],
            dtype=np.float32,
        )
        return obs

    def _get_delayed_observation(self) -> np.ndarray:
        """Pop delayed observation and apply observation noise/bias if configured."""
        raw_obs = (
            self._obs_buffer.popleft() if self._obs_buffer else self._build_raw_observation()
        )
        if not self._obs_buffer:
            self._obs_buffer.append(raw_obs)

        obs = raw_obs.copy()
        if self.obs_noise_std > 0.0 or self.obs_bias != 0.0:
            noise = self.np_random.normal(0.0, self.obs_noise_std, size=obs.shape).astype(np.float32)
            # Apply bias & noise primarily to perception features: b (0), f (2), delta (7)
            obs[0] = np.clip(obs[0] + self.obs_bias + noise[0], -1.0, 1.0)
            obs[2] = np.clip(obs[2] + self.obs_bias + noise[2], -1.0, 1.0)
            obs[7] = np.clip(obs[7] + noise[7], -1.0, 1.0)

        return np.clip(obs, -1.0, 1.0).astype(np.float32)


# Register environment with Gymnasium
gym.register(
    id="StardewFishSim-v0",
    entry_point="fisher.sim.env_sim:StardewFishSimEnv",
    max_episode_steps=900,
)
