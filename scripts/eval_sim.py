"""Evaluate fishing policy on the nominal simulator benchmark suite.

Measures catch rates across difficulties (d in {5, 20, 40, 60, 80, 110}) and
verifies Phase 1 sim exit gates (>=95% for d<=70, >=80% for d<=110).
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import os
import sys
import time
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from rich.console import Console
from rich.table import Table

from fisher.agent.baselines import BangBangPolicy, RandomPolicy
from fisher.sim.env_sim import CurriculumStage, FishMotionType, SimEnvConfig, StardewFishSimEnv


def run_evaluation(
    policy,
    difficulties: list[float] | None = None,
    episodes_per_diff: int = 20,
    vec_normalize=None,
    level_scaled: bool = True,
    seed: int = 42,
) -> dict:
    """Run evaluation sweep across nominal difficulties."""
    if difficulties is None:
        difficulties = [5.0, 20.0, 40.0, 60.0, 80.0, 110.0]

    env = StardewFishSimEnv(
        SimEnvConfig(
            curriculum_stage=CurriculumStage.NOMINAL,
            enable_domain_rand=False,
        )
    )

    results = []
    archetypes = list(FishMotionType)

    rng = np.random.default_rng(seed)

    was_training = False
    if vec_normalize is not None:
        was_training = vec_normalize.training
        vec_normalize.training = False

    for diff in difficulties:
        # Scale bar height with fishing level matching BobberBar.cs: 96 + level * 8
        bar_h = 96.0 + min(10.0, diff / 10.0) * 8.0 if level_scaled else 96.0

        for ep in range(episodes_per_diff):
            motion_type = archetypes[ep % len(archetypes)]
            ep_seed = int(rng.integers(0, 2**31 - 1))

            obs, _ = env.reset(
                seed=ep_seed,
                options={
                    "curriculum_stage": CurriculumStage.NOMINAL,
                    "difficulty": diff,
                    "motion_type": motion_type,
                },
            )
            env.physics.cfg.bar_height = bar_h

            ep_reward = 0.0
            ep_steps = 0
            ep_in_bar_count = 0
            terminated = False
            truncated = False

            while not (terminated or truncated):
                # Normalize observation if VecNormalize stats are provided
                norm_obs = obs
                if vec_normalize is not None:
                    norm_obs = vec_normalize.normalize_obs(obs[np.newaxis, :])[0]

                action, _ = policy.predict(norm_obs, deterministic=True)
                action = int(np.asarray(action).flat[0])

                obs, reward, terminated, truncated, info = env.step(action)
                ep_reward += reward
                ep_steps += 1
                if info.get("in_bar", False):
                    ep_in_bar_count += 1

            is_catch = bool(info.get("is_catch", False))
            duration_s = ep_steps / 30.0
            in_bar_frac = ep_in_bar_count / max(1, ep_steps)

            results.append(
                {
                    "difficulty": diff,
                    "motion_type": motion_type.name,
                    "is_catch": is_catch,
                    "reward": ep_reward,
                    "duration_s": duration_s,
                    "in_bar_frac": in_bar_frac,
                    "final_progress": info.get("progress", 0.0),
                }
            )

    env.close()
    if vec_normalize is not None:
        vec_normalize.training = was_training
    return {"episodes": results}


def summarize_results(eval_data: dict, console: Console | None = None) -> dict:
    """Summarize and display evaluation statistics and gate checks."""
    episodes = eval_data["episodes"]
    total = len(episodes)
    catches = sum(1 for e in episodes if e["is_catch"])
    overall_cr = (catches / total) * 100.0 if total > 0 else 0.0

    # Bucket results: Easy (<=40), Mid (41-70), Hard (71-90), Expert (>90)
    buckets = {
        "Easy (<=40)": [e for e in episodes if e["difficulty"] <= 40.0],
        "Mid (41-70)": [e for e in episodes if 40.0 < e["difficulty"] <= 70.0],
        "Hard (71-90)": [e for e in episodes if 70.0 < e["difficulty"] <= 90.0],
        "Expert (>90)": [e for e in episodes if e["difficulty"] > 90.0],
    }

    bucket_stats = {}
    for name, ep_list in buckets.items():
        n = len(ep_list)
        c = sum(1 for e in ep_list if e["is_catch"])
        cr = (c / n) * 100.0 if n > 0 else 0.0
        mean_r = np.mean([e["reward"] for e in ep_list]) if n > 0 else 0.0
        mean_dur = np.mean([e["duration_s"] for e in ep_list]) if n > 0 else 0.0
        mean_bar = np.mean([e["in_bar_frac"] for e in ep_list]) * 100.0 if n > 0 else 0.0
        bucket_stats[name] = {
            "count": n,
            "catches": c,
            "catch_rate": cr,
            "mean_reward": float(mean_r),
            "mean_duration": float(mean_dur),
            "mean_in_bar": float(mean_bar),
        }

    # Combined <= 70 and <= 110 stats
    le_70 = [e for e in episodes if e["difficulty"] <= 70.0]
    cr_le_70 = (sum(1 for e in le_70 if e["is_catch"]) / len(le_70) * 100.0) if le_70 else 0.0

    summary = {
        "total_episodes": total,
        "overall_catch_rate": overall_cr,
        "cr_le_70": cr_le_70,
        "bucket_stats": bucket_stats,
    }

    if console:
        table = Table(title="Simulation Nominal Suite Evaluation", border_style="grey35")
        table.add_column("Difficulty Tier", style="bold white", width=16)
        table.add_column("Episodes", justify="right", width=10)
        table.add_column("Catch Rate", justify="right", width=14)
        table.add_column("In-Bar %", justify="right", width=12)
        table.add_column("Mean Duration", justify="right", width=14)
        table.add_column("Mean Reward", justify="right", width=14)

        for name, stats in bucket_stats.items():
            color = "green" if stats["catch_rate"] >= 80.0 else "yellow" if stats["catch_rate"] >= 50.0 else "red"
            table.add_row(
                name,
                str(stats["count"]),
                f"[{color}]{stats['catch_rate']:.1f}%[/{color}]",
                f"{stats['mean_in_bar']:.1f}%",
                f"{stats['mean_duration']:.1f} s",
                f"{stats['mean_reward']:+.2f}",
            )

        color_tot = "green" if overall_cr >= 80.0 else "yellow"
        table.add_section()
        table.add_row(
            "[bold]Overall[/bold]",
            f"[bold]{total}[/bold]",
            f"[{color_tot}][bold]{overall_cr:.1f}%[/bold][/{color_tot}]",
            f"{np.mean([e['in_bar_frac'] for e in episodes]) * 100.0:.1f}%",
            f"{np.mean([e['duration_s'] for e in episodes]):.1f} s",
            f"{np.mean([e['reward'] for e in episodes]):+.2f}",
        )
        console.print("\n", table)

        # Gate audit
        console.print("[bold white]Phase 1 Exit Gate Audit:[/bold white]")
        gate1_pass = cr_le_70 >= 95.0
        gate2_pass = overall_cr >= 80.0

        g1_str = "[bold green]PASS[/bold green]" if gate1_pass else "[bold yellow]FAIL[/bold yellow]"
        g2_str = "[bold green]PASS[/bold green]" if gate2_pass else "[bold yellow]FAIL[/bold yellow]"

        console.print(f"  Gate 1 (Catch Rate >= 95% for d <= 70):  {cr_le_70:.1f}% -> {g1_str}")
        console.print(f"  Gate 2 (Catch Rate >= 80% for d <= 110): {overall_cr:.1f}% -> {g2_str}\n")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate fishing policy on simulation suite")
    parser.add_argument(
        "--policy",
        type=str,
        default="bang-bang",
        help="Policy type: 'bang-bang', 'random', or path to .zip SB3 checkpoint",
    )
    parser.add_argument(
        "--stats",
        type=str,
        default=None,
        help="Path to VecNormalize .pkl stats (if using trained PPO model)",
    )
    parser.add_argument(
        "--episodes",
        type=int,
        default=20,
        help="Number of episodes per difficulty tier (default: 20 -> 120 total)",
    )
    parser.add_argument(
        "--fixed-bar",
        action="store_true",
        help="Use fixed Level 0 bar height (96px) across all tiers instead of player-level scaling",
    )
    args = parser.parse_args()
    console = Console()

    vec_norm = None

    if args.policy == "random":
        console.print("[bold cyan]Evaluating Random Baseline Policy...[/bold cyan]")
        policy = RandomPolicy()
    elif args.policy == "bang-bang":
        console.print("[bold cyan]Evaluating Bang-Bang Pure-Pursuit Baseline Policy...[/bold cyan]")
        policy = BangBangPolicy()
    else:
        # Load SB3 PPO model
        if not os.path.exists(args.policy):
            console.print(f"[bold red]Error: Policy checkpoint '{args.policy}' not found.[/bold red]")
            sys.exit(1)
        console.print(f"[bold cyan]Loading PPO policy from: {args.policy}...[/bold cyan]")
        from stable_baselines3 import PPO
        from stable_baselines3.common.vec_env import VecNormalize

        policy = PPO.load(args.policy)
        # Auto-infer stats file if not explicitly specified
        if args.stats is None:
            candidate_stats = [
                os.path.join(os.path.dirname(args.policy), "vec_normalize_latest.pkl"),
                os.path.join(os.path.dirname(args.policy), "vec_normalize_best.pkl"),
                "models/vec_normalize_latest.pkl",
                "models/vec_normalize_best.pkl",
            ]
            for c in candidate_stats:
                if os.path.exists(c):
                    args.stats = c
                    break

        if args.stats and os.path.exists(args.stats):
            console.print(f"[cyan]Loading normalization statistics from: {args.stats}[/cyan]")
            from stable_baselines3.common.vec_env import DummyVecEnv
            dummy_env = DummyVecEnv([lambda: StardewFishSimEnv()])
            vec_norm = VecNormalize.load(args.stats, dummy_env)
            vec_norm.training = False
            vec_norm.norm_reward = False

    eval_data = run_evaluation(
        policy=policy,
        episodes_per_diff=args.episodes,
        vec_normalize=vec_norm,
        level_scaled=not args.fixed_bar,
    )

    summary = summarize_results(eval_data, console=console)


if __name__ == "__main__":
    main()
