"""PPO training pipeline on StardewFishSim-v0 with curriculum and nominal evaluation.

Pre-trains neural policy purely in simulation grounded in game source code.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Callable
import numpy as np
import torch
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from rich.console import Console
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv, VecNormalize

from fisher.sim.env_sim import CurriculumStage, SimEnvConfig, StardewFishSimEnv
from scripts.eval_sim import run_evaluation, summarize_results


def linear_schedule(initial_value: float, final_value: float = 1e-6) -> Callable[[float], float]:
    """Linear schedule function for learning rate annealing."""
    def func(progress_remaining: float) -> float:
        return final_value + progress_remaining * (initial_value - final_value)
    return func


class CurriculumAndEvalCallback(BaseCallback):
    """Manages training curriculum progression, periodic nominal evaluation, and checkpointing."""

    def __init__(
        self,
        eval_freq: int = 100_000,
        eval_episodes: int = 20,
        stage_a_steps: int = 250_000,
        stage_b_steps: int = 600_000,
        save_dir: str = "models",
        console: Console | None = None,
        verbose: int = 1,
    ) -> None:
        super().__init__(verbose)
        self.eval_freq = eval_freq
        self.eval_episodes = eval_episodes
        self.stage_a_steps = stage_a_steps
        self.stage_b_steps = stage_b_steps
        self.save_dir = save_dir
        self.console = console or Console()

        self.current_stage = CurriculumStage.A
        self.best_cr = -1.0
        self.last_eval_step = 0
        os.makedirs(save_dir, exist_ok=True)

    def _on_step(self) -> bool:
        step = self.num_timesteps

        # 1. Update Curriculum Stage
        new_stage = self.current_stage
        if step < self.stage_a_steps:
            new_stage = CurriculumStage.A
        elif step < self.stage_a_steps + self.stage_b_steps:
            new_stage = CurriculumStage.B
        else:
            new_stage = CurriculumStage.C

        if new_stage != self.current_stage:
            self.current_stage = new_stage
            self.console.print(
                f"\n[bold yellow]>>> Curriculum Transition: Advancing to Stage {new_stage.value} at step {step:,}[/bold yellow]"
            )
            # Propagate to all vectorized environments
            self.training_env.env_method("set_curriculum_stage", new_stage)

        # 2. Periodic Nominal Suite Evaluation
        if step - self.last_eval_step >= self.eval_freq:
            self.last_eval_step = step
            self.console.print(f"\n[bold cyan]--- Running Nominal Suite Evaluation at Step {step:,} ---[/bold cyan]")

            # Run evaluation with current policy
            eval_data = run_evaluation(
                policy=self.model,
                episodes_per_diff=self.eval_episodes,
                vec_normalize=self.training_env if isinstance(self.training_env, VecNormalize) else None,
            )
            summary = summarize_results(eval_data, console=self.console)

            cr_overall = summary["overall_catch_rate"]
            cr_le70 = summary["cr_le_70"]

            # Save latest checkpoint
            latest_path = os.path.join(self.save_dir, "ppo_fisher_latest.zip")
            self.model.save(latest_path)
            if isinstance(self.training_env, VecNormalize):
                self.training_env.save(os.path.join(self.save_dir, "vec_normalize_latest.pkl"))

            # Save best checkpoint
            if cr_overall > self.best_cr:
                self.best_cr = cr_overall
                best_path = os.path.join(self.save_dir, "ppo_fisher_best.zip")
                self.model.save(best_path)
                if isinstance(self.training_env, VecNormalize):
                    self.training_env.save(os.path.join(self.save_dir, "vec_normalize_best.pkl"))
                self.console.print(f"[bold green]New best model saved! (Overall Catch Rate: {cr_overall:.1f}%)[/bold green]")

            # Check if exit gate criteria are achieved
            if cr_le70 >= 95.0 and cr_overall >= 80.0:
                self.console.print(
                    f"\n[bold green]*** PHASE 1 EXIT GATES ACHIEVED AT STEP {step:,}! (d<=70: {cr_le70:.1f}%, overall: {cr_overall:.1f}%) ***[/bold green]"
                )

        return True


def make_env_fn(env_seed: int) -> Callable[[], StardewFishSimEnv]:
    """Create a seeded environment factory."""
    def _init() -> StardewFishSimEnv:
        env = StardewFishSimEnv(SimEnvConfig(curriculum_stage=CurriculumStage.A))
        env.reset(seed=env_seed)
        return env
    return _init


def main() -> None:
    parser = argparse.ArgumentParser(description="Train PPO policy in Stardew Valley fishing simulation")
    parser.add_argument("--timesteps", type=int, default=2_000_000, help="Total training timesteps")
    parser.add_argument("--envs", type=int, default=16, help="Number of vectorized parallel environments")
    parser.add_argument("--vec-env", type=str, default="dummy", choices=["dummy", "subproc"], help="Vectorized env type")
    parser.add_argument("--eval-freq", type=int, default=150_000, help="Timesteps between nominal evaluations")
    parser.add_argument("--save-dir", type=str, default="models", help="Directory to save model checkpoints")
    parser.add_argument("--resume", type=str, default=None, help="Path to existing model checkpoint to resume training")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    console = Console()
    console.print(f"\n[bold white]Fisher Sim-to-Real PPO Pre-Training[/bold white]")
    console.print(f"  Target timesteps:  {args.timesteps:,}")
    console.print(f"  Vectorized envs:   {args.envs} ({args.vec_env})")
    console.print(f"  Evaluation freq:   {args.eval_freq:,}")
    console.print(f"  Checkpoints dir:   {args.save_dir}")
    if args.resume:
        console.print(f"  Resuming from:     {args.resume}")
    console.print("")

    # Set seeds
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    # 1. Create vectorized environments
    env_fns = [make_env_fn(args.seed + i) for i in range(args.envs)]
    if args.vec_env == "subproc":
        vec_env = SubprocVecEnv(env_fns)
    else:
        vec_env = DummyVecEnv(env_fns)

    # Check for saved VecNormalize stats if resuming
    stats_path = os.path.join(args.save_dir, "vec_normalize_latest.pkl")
    if args.resume and os.path.exists(stats_path):
        console.print(f"[cyan]Restoring VecNormalize stats from: {stats_path}[/cyan]")
        vec_env = VecNormalize.load(stats_path, vec_env)
        vec_env.training = True
        vec_env.norm_reward = False
    else:
        vec_env = VecNormalize(
            vec_env,
            norm_obs=True,
            norm_reward=False,
            clip_obs=10.0,
        )

    # 2. PPO Policy Architecture & Hyperparameters
    if args.resume and os.path.exists(args.resume):
        console.print(f"[cyan]Loading existing policy model from: {args.resume}[/cyan]")
        model = PPO.load(args.resume, env=vec_env, learning_rate=linear_schedule(2.0e-4, 1.0e-5))
    else:
        policy_kwargs = dict(
            net_arch=dict(pi=[64, 64], vf=[64, 64]),
            activation_fn=torch.nn.Tanh,
        )
        model = PPO(
            policy="MlpPolicy",
            env=vec_env,
            learning_rate=linear_schedule(3.0e-4, 1.0e-5),
            n_steps=512,
            batch_size=512,
            n_epochs=10,
            gamma=0.999,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.01,
            vf_coef=0.5,
            max_grad_norm=0.5,
            policy_kwargs=policy_kwargs,
            verbose=1,
            seed=args.seed,
        )

    # 3. Setup Callback
    if args.resume:
        # If resuming, directly engage Stage C
        stage_a = 0
        stage_b = 0
    else:
        stage_a = int(args.timesteps * 0.20)
        stage_b = int(args.timesteps * 0.40)

    cb = CurriculumAndEvalCallback(
        eval_freq=args.eval_freq,
        eval_episodes=10,
        stage_a_steps=stage_a,
        stage_b_steps=stage_b,
        save_dir=args.save_dir,
        console=console,
    )

    # 4. Train
    start_time = time.time()
    try:
        model.learn(total_timesteps=args.timesteps, callback=cb)
    except KeyboardInterrupt:
        console.print("[yellow]Training interrupted by user. Saving current checkpoint...[/yellow]")
    finally:
        elapsed = time.time() - start_time
        console.print(f"\n[bold green]Training completed in {elapsed / 60.0:.2f} minutes.[/bold green]")

        final_path = os.path.join(args.save_dir, "ppo_fisher_latest.zip")
        model.save(final_path)
        vec_env.save(os.path.join(args.save_dir, "vec_normalize_latest.pkl"))
        console.print(f"Saved final checkpoint to: {final_path}")

        vec_env.close()

    # 5. Final Comprehensive Evaluation
    console.print("\n[bold white]================ FINAL BENCHMARK EVALUATION ================ [/bold white]")
    eval_data = run_evaluation(
        policy=model,
        episodes_per_diff=25,  # 150 episodes
        vec_normalize=vec_env,
    )
    summarize_results(eval_data, console=console)


if __name__ == "__main__":
    main()
