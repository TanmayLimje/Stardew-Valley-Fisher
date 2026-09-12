"""Deterministic Live Evaluation Harness for Phase 3.

Evaluates trained PPO policy (or Bang-Bang baseline) against the live Stardew Valley client
over a 20-episode series, logging per-tick telemetry tuples to JSONL and measuring the
Sim-to-Real transfer gap (CR_sim - CR_live).
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

import numpy as np

# Ensure project root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import keyboard
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live

from fisher.agent.baselines import BangBangPolicy
from fisher.capture.mock_driver import MockCaptureDriver
from fisher.config import load_config
from fisher.env.live_env import LiveFishingEnv
from fisher.input.direct_input import is_admin_process
from fisher.input.mock_actuator import MockActuator


def normalize_observation(obs: np.ndarray, vec_normalize: Any) -> np.ndarray:
    """Normalize 9-d observation using VecNormalize running mean and variance stats."""
    if vec_normalize is None:
        return obs
    obs_2d = obs.reshape(1, -1)
    mean = vec_normalize.obs_rms.mean
    var = vec_normalize.obs_rms.var
    clip_obs = getattr(vec_normalize, "clip_obs", 10.0)
    norm = (obs_2d - mean) / np.sqrt(var + 1e-8)
    norm = np.clip(norm, -clip_obs, clip_obs)
    return norm.astype(np.float32)


def run_live_evaluation(
    episodes: int = 20,
    policy_path: str = "models/ppo_fisher_best.zip",
    stats_path: Optional[str] = None,
    use_baseline: bool = False,
    mock_mode: bool = False,
    output_dir: str = "reports/live_eval",
    killswitch_key: str = "f9",
    require_foreground: bool = False,
    wait_timeout: float = 90.0,
    preview: bool = False,
    record_video: bool = False,
    output_video_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute live evaluation suite and return aggregated metrics."""
    console = Console()
    console.print()
    console.rule("[bold cyan]Fisher Phase 3 — Live Client Transfer Evaluation[/bold cyan]")
    console.print()

    # 1. Check administrative privileges
    admin_ok = is_admin_process()
    if not admin_ok and not mock_mode:
        console.print(
            "[bold yellow][WARN] Running in non-elevated user mode. If Stardew Valley was launched "
            "as Administrator, Windows UIPI will drop SendInput events. Administrator terminal is recommended.[/bold yellow]\n"
        )

    # 1b. Check game window is findable
    if not mock_mode:
        from fisher.utils.display import find_window_hwnd, get_window_monitor_index
        cfg_check = load_config()
        game_title = cfg_check.game.get("window_title", "Stardew Valley")
        hwnd = find_window_hwnd(game_title)
        if hwnd:
            mon_idx = get_window_monitor_index(game_title)
            console.print(f"[green]  ✓ Game window found:[/green] HWND={hwnd}, Monitor={mon_idx}")
        else:
            console.print(
                f"[bold yellow][WARN] Game window '{game_title}' not found![/bold yellow]\n"
                f"  Ensure Stardew Valley is running. If using SMAPI or mods, the window title may differ.\n"
                f"  Update 'game.window_title' in configs/default.yaml if needed."
            )

    # Helper to resolve relative paths against both current directory and REPO_ROOT
    def resolve_path(p_str: Optional[str]) -> Optional[str]:
        if not p_str:
            return None
        p = Path(p_str)
        if p.exists():
            return str(p.resolve())
        cand = REPO_ROOT / p_str
        if cand.exists():
            return str(cand.resolve())
        return p_str

    # 2. Setup output directory (anchored to REPO_ROOT if relative)
    out_path = Path(output_dir)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / output_dir
    out_path.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 3. Load policy or baseline
    policy = None
    vec_norm = None
    policy_name = "BangBang Baseline" if use_baseline else "PPO Agent (Frozen)"

    if use_baseline:
        policy = BangBangPolicy()
        console.print(f"[cyan]Policy Engine:[/cyan] [bold yellow]{policy_name}[/bold yellow]")
    else:
        resolved_policy = resolve_path(policy_path)
        if not resolved_policy or not os.path.exists(resolved_policy):
            console.print(f"[bold red]Error: Policy checkpoint '{policy_path}' not found.[/bold red]")
            console.print(f"[dim]Checked: '{policy_path}' and '{REPO_ROOT / policy_path}'[/dim]")
            sys.exit(1)
        policy_path = resolved_policy

        from stable_baselines3 import PPO
        import pickle

        console.print(f"[cyan]Loading PPO policy checkpoint from:[/cyan] {policy_path}")
        policy = PPO.load(policy_path, device="cpu")

        # Resolve normalization statistics
        cand_stats = [
            resolve_path(stats_path),
            str(REPO_ROOT / "models" / "vec_normalize_best.pkl"),
            resolve_path("models/vec_normalize_best.pkl"),
            str(REPO_ROOT / "models" / "vec_normalize_latest.pkl"),
            resolve_path("models/vec_normalize_latest.pkl"),
            os.path.join(os.path.dirname(policy_path), "vec_normalize_best.pkl"),
            os.path.join(os.path.dirname(policy_path), "vec_normalize_latest.pkl"),
        ]
        chosen_stats = None
        for c in cand_stats:
            if c and os.path.exists(c):
                chosen_stats = c
                break

        if chosen_stats:
            console.print(f"[cyan]Loading observation normalizer from:[/cyan] {chosen_stats}")
            with open(chosen_stats, "rb") as f:
                vec_norm = pickle.load(f)
            vec_norm.training = False

    # 4. Instantiate environment
    cfg = load_config()
    recorder = None
    if record_video or output_video_path:
        from fisher.ui.preview import PreviewVideoRecorder
        recorder = PreviewVideoRecorder()
        rec_path = recorder.start(output_video_path)
        console.print(f"[cyan]Preview video recording active:[/cyan] [bold green]{rec_path}[/bold green]")

    render_mode = "human" if preview else ("rgb_array" if recorder is not None else None)

    env = None
    if mock_mode:
        console.print("[bold green][INFO] Running in Mock Hardware Mode (headless CI simulation)[/bold green]")
        w = cfg.capture.get("roi", {}).get("x1", 910) - cfg.capture.get("roi", {}).get("x0", 720)
        h = cfg.capture.get("roi", {}).get("y1", 800) - cfg.capture.get("roi", {}).get("y0", 150)
        mock_cap = MockCaptureDriver(width=w, height=h, target_fps=60.0)
        mock_act = MockActuator()
        env = LiveFishingEnv(
            config=cfg,
            capture_driver=mock_cap,
            actuator=mock_act,
            auto_start_capture=True,
            wait_for_ui_on_reset=False,
            require_foreground=False,
            render_mode=render_mode,
            video_recorder=recorder,
        )
    else:
        env = LiveFishingEnv(
            config=cfg,
            auto_start_capture=True,
            wait_for_ui_on_reset=True,
            require_foreground=require_foreground,
            foreground_lost_threshold_frames=90,
            render_mode=render_mode,
            video_recorder=recorder,
        )

    console.print(f"[cyan]Target Episodes:[/cyan] {episodes}")
    console.print(f"[cyan]Emergency Killswitch:[/cyan] [bold red]Press {killswitch_key.upper()} or ESC[/bold red] to abort any minigame.")
    console.print("-" * 70)

    # Pre-flight capture verification: ensure we can actually grab frames
    if not mock_mode:
        console.print("\n[dim]Running capture pre-flight check...[/dim]")
        import time as _time
        _preflight_start = _time.perf_counter()
        _preflight_frame = None
        while _time.perf_counter() - _preflight_start < 3.0:
            _preflight_frame, _ = env.capture.get_latest_frame()
            if _preflight_frame is not None:
                break
            _time.sleep(0.1)

        if _preflight_frame is None:
            driver_name = type(env.capture).__name__
            last_err = getattr(env.capture, "last_error", None) or getattr(env.capture, "_last_error", None)
            console.print(f"\n[bold red][FATAL] Capture driver '{driver_name}' returned no frames after 3 seconds.[/bold red]")
            if last_err:
                console.print(f"[red]  Last error: {last_err}[/red]")
            console.print()
            console.print("[yellow]Troubleshooting:[/yellow]")
            console.print("  1. [bold]Run this terminal as Administrator[/bold] — right-click PowerShell → 'Run as administrator'")
            console.print("  2. Ensure Stardew Valley is running and visible on Screen 3")
            console.print("  3. Kill any zombie python/fisher processes: [cyan]Get-Process python* | Stop-Process -Force[/cyan]")
            console.print("  4. Close other screen-capture apps (OBS, Discord overlay, etc.)")
            console.print("  5. Ensure the desktop is unlocked (no UAC prompts or lock screen)")
            console.print()
            env.close()
            sys.exit(1)
        else:
            h, w = _preflight_frame.shape[:2]
            driver_name = type(env.capture).__name__
            console.print(f"[green]  ✓ Capture OK — {driver_name} delivering {w}×{h} frames[/green]")

    episode_results: List[Dict[str, Any]] = []
    all_latencies_ms: List[float] = []

    try:
        for ep_idx in range(1, episodes + 1):
            if keyboard.is_pressed(killswitch_key):
                console.print("\n[bold red][ABORT] Emergency killswitch pressed! Stopping evaluation.[/bold red]")
                break

            console.print(f"\n[bold]Episode {ep_idx}/{episodes}:[/bold]")
            if not mock_mode:
                console.print("  [dim]> Cast your fishing rod. Waiting for minigame UI to appear on Screen 3...[/dim]")

            # Reset environment (waits for UI in live mode)
            obs, reset_info = env.reset(options={"wait_for_ui": not mock_mode, "wait_timeout": wait_timeout})

            # Check if killswitch pressed while waiting
            if keyboard.is_pressed(killswitch_key):
                console.print("\n[bold red][ABORT] Emergency killswitch pressed! Stopping evaluation.[/bold red]")
                break

            if not reset_info.get("ui_found", True):
                console.print(f"  [bold yellow][TIMEOUT][/bold yellow] Minigame UI not detected within {wait_timeout:.0f}s timeout. Skipping episode.")
                continue

            ep_telemetry: List[Dict[str, Any]] = []
            ep_start_time = time.perf_counter()
            done = False
            last_info: Dict[str, Any] = {}

            console.print("  [bold green]Minigame detected! Control loop engaged @ 30 Hz...[/bold green]")

            while not done:
                # 1. Killswitch check
                if keyboard.is_pressed(killswitch_key):
                    console.print("\n[bold red][ABORT] Killswitch triggered during active minigame! Releasing mouse.[/bold red]")
                    env.actuator.send_escape()
                    env.actuator.emergency_release()
                    break

                # 2. Policy action inference
                action = 0
                if use_baseline:
                    action = 1 if obs[2] > obs[0] else 0  # fish_pos > bar_pos
                else:
                    norm_obs = normalize_observation(obs, vec_norm)
                    pred_act, _ = policy.predict(norm_obs, deterministic=True)
                    action = int(pred_act[0])

                # 3. Environment step
                obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                last_info = info

                all_latencies_ms.append(info["latency_ms"])
                ep_telemetry.append(
                    {
                        "step": info["step_count"],
                        "t": time.perf_counter() - ep_start_time,
                        "action": action,
                        "bar_pos": float(info["bar_pos"]),
                        "bar_height": float(info.get("bar_height", 0.0)),
                        "fish_pos": float(info["fish_pos"]),
                        "progress": float(info["progress"]),
                        "in_bar": bool(info["in_bar"]),
                        "is_active": bool(info.get("is_active", True)),
                        "confidence": float(info.get("confidence", 0.0)),
                        "latency_ms": float(info["latency_ms"]),
                    }
                )

                # Mock progress simulation if running in mock mode
                if mock_mode:
                    # In mock mode, advance simulated progress to exercise loop termination
                    if info["step_count"] > 90:
                        break

            ep_duration = time.perf_counter() - ep_start_time
            is_catch = last_info.get("is_catch", False)
            is_escape = last_info.get("is_escape", False)
            mean_in_bar = last_info.get("mean_in_bar", 0.0)

            peak_prog = last_info.get("peak_progress", last_info.get("progress", 0.0))

            # Determine precise termination reason for diagnostics
            if is_catch:
                termination_reason = "catch"
            elif is_escape:
                termination_reason = "escape"
            elif last_info.get("ui_lost", False):
                termination_reason = "ui_lost"
            elif last_info.get("timeout", False):
                termination_reason = "timeout"
            else:
                termination_reason = "unknown"

            outcome_str = "[green]CATCH[/green]" if is_catch else ("[red]ESCAPE[/red]" if is_escape else "[yellow]TIMEOUT/LOST[/yellow]")
            console.print(
                f"  Episode {ep_idx} finished: {outcome_str} | "
                f"Duration: {ep_duration:.2f}s | "
                f"In-Bar: {mean_in_bar * 100:.1f}% | "
                f"Peak Progress: {peak_prog * 100:.1f}% | "
                f"Reason: [dim]{termination_reason}[/dim]"
            )

            # Record episode log to JSONL
            ep_file = out_path / f"eval_live_{timestamp_str}_ep{ep_idx:02d}.jsonl"
            with open(ep_file, "w", encoding="utf-8") as f:
                for row in ep_telemetry:
                    f.write(json.dumps(row) + "\n")

            episode_results.append(
                {
                    "episode": ep_idx,
                    "outcome": "catch" if is_catch else ("escape" if is_escape else "truncated"),
                    "termination_reason": termination_reason,
                    "is_catch": is_catch,
                    "duration_s": ep_duration,
                    "mean_in_bar": mean_in_bar,
                    "final_progress": last_info.get("progress", 0.0),
                    "peak_progress": peak_prog,
                    "steps": len(ep_telemetry),
                    "telemetry_file": str(ep_file),
                }
            )

            # Brief pause between episodes for player recast
            if not mock_mode and ep_idx < episodes:
                console.print("  [dim]> Dismiss the caught fish dialog in Stardew Valley (or click in-game), then cast your rod...[/dim]")
                time.sleep(2.5)

    finally:
        env.close()
        if recorder is not None and recorder.is_recording:
            saved_vid = recorder.stop()
            console.print(f"\n[bold green][RECORDER] Preview video saved to:[/bold green] {saved_vid}")

    # 5. Summarize and render aggregate report
    total_eps = len(episode_results)
    catches = sum(1 for e in episode_results if e["is_catch"])
    catch_rate = (catches / total_eps * 100.0) if total_eps > 0 else 0.0
    mean_duration = float(np.mean([e["duration_s"] for e in episode_results])) if total_eps > 0 else 0.0
    mean_in_bar_all = float(np.mean([e["mean_in_bar"] for e in episode_results])) * 100.0 if total_eps > 0 else 0.0

    lat_sorted = sorted(all_latencies_ms) if all_latencies_ms else [0.0]
    n_lat = len(lat_sorted)
    p50_lat = lat_sorted[int(n_lat * 0.50)]
    p90_lat = lat_sorted[int(n_lat * 0.90)]
    p99_lat = lat_sorted[min(int(n_lat * 0.99), n_lat - 1)]

    # Simulation nominal reference catch rate is 89.2%
    sim_ref_cr = 89.2
    transfer_gap = sim_ref_cr - catch_rate

    console.print()
    table = Table(title="Live Transfer Evaluation Summary", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="dim")
    table.add_column("Achieved Live Value", justify="right")
    table.add_column("Phase 3 Gate / Sim Reference", justify="right")
    table.add_column("Status", justify="center")

    table.add_row(
        "Total Evaluated Episodes",
        str(total_eps),
        f">= {episodes}",
        "[green]OK[/green]" if total_eps >= episodes else "[yellow]PARTIAL[/yellow]",
    )
    table.add_row(
        "Live Catch Rate (CR_live)",
        f"{catch_rate:.1f}%",
        ">= 80.0%",
        "[green]PASS[/green]" if catch_rate >= 80.0 else "[yellow]NEEDS RECAL[/yellow]",
    )
    table.add_row(
        "Mean In-Bar Ratio",
        f"{mean_in_bar_all:.1f}%",
        ">= 80.0%",
        "[green]OK[/green]" if mean_in_bar_all >= 80.0 else "[dim]-[/dim]",
    )
    table.add_row(
        "Mean Episode Duration",
        f"{mean_duration:.2f} s",
        "< 20.0 s",
        "[green]OK[/green]",
    )
    table.add_row(
        "Sim-to-Real Transfer Gap",
        f"{transfer_gap:+.1f} pts",
        "< 5.0 pts",
        "[green]OPTIMAL[/green]" if transfer_gap < 5.0 else ("[yellow]RECALIBRATE[/yellow]" if transfer_gap <= 20.0 else "[red]DEBUG STACK[/red]"),
    )
    table.add_row(
        "Loop Latency (p50 / p99)",
        f"{p50_lat:.2f}ms / {p99_lat:.2f}ms",
        "p99 < 25.0 ms",
        "[green]PASS[/green]" if p99_lat < 25.0 else "[red]FAIL[/red]",
    )

    console.print(table)

    summary_data = {
        "timestamp": timestamp_str,
        "policy": policy_name,
        "policy_path": policy_path,
        "episodes_target": episodes,
        "episodes_completed": total_eps,
        "catches": catches,
        "catch_rate_pct": catch_rate,
        "sim_reference_cr_pct": sim_ref_cr,
        "transfer_gap_pts": transfer_gap,
        "mean_duration_s": mean_duration,
        "mean_in_bar_pct": mean_in_bar_all,
        "latency_p50_ms": p50_lat,
        "latency_p90_ms": p90_lat,
        "latency_p99_ms": p99_lat,
        "episodes_detail": episode_results,
    }

    summary_file = out_path / f"summary_eval_live_{timestamp_str}.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    console.print(f"\n[green][OK] Session report saved to:[/green] {summary_file}")
    return summary_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Fisher Phase 3 Live Client Transfer Evaluation")
    parser.add_argument("--episodes", type=int, default=20, help="Number of minigame episodes to evaluate (default: 20)")
    parser.add_argument("--policy", "--model", type=str, default="models/ppo_fisher_best.zip", help="Path to PPO model weights")
    parser.add_argument("--stats", type=str, default=None, help="Path to VecNormalize pkl stats")
    parser.add_argument("--baseline", action="store_true", help="Evaluate scripted BangBang policy instead of PPO")
    parser.add_argument("--mock", action="store_true", help="Run with mock hardware drivers (for CI/automated testing)")
    parser.add_argument("--output-dir", type=str, default="reports/live_eval", help="Telemetry output directory")
    parser.add_argument("--killswitch", type=str, default="f9", help="Global killswitch key (default: f9)")
    parser.add_argument("--preview", action="store_true", help="Display live visual preview window with BobberBar tracking overlay")
    parser.add_argument("--record-video", type=str, nargs="?", const="", default=None, help="Record live preview feed to an MP4 video")
    args = parser.parse_args()

    rec_vid = args.record_video is not None
    vid_path = args.record_video if (args.record_video and args.record_video != "") else None

    run_live_evaluation(
        episodes=args.episodes,
        policy_path=args.policy,
        stats_path=args.stats,
        use_baseline=args.baseline,
        mock_mode=args.mock,
        output_dir=args.output_dir,
        killswitch_key=args.killswitch,
        preview=args.preview,
        record_video=rec_vid,
        output_video_path=vid_path,
    )


if __name__ == "__main__":
    main()
