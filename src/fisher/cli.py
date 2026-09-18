"""Main CLI dispatcher for Fisher."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


from rich.console import Console
from rich.table import Table

from fisher.capture.mock_driver import MockCaptureDriver
from fisher.config import load_config
from fisher.input.mock_actuator import MockActuator
from fisher.ui.dashboard import TelemetryDashboard
from fisher.utils.display import find_window_hwnd, get_connected_displays, get_window_monitor_index
from fisher.utils.timing import check_admin, hybrid_sleep, measure_jitter, precise_clock_scope


def cmd_check_monitors(console: Console) -> None:
    """List connected displays and check for Stardew Valley window."""
    console.print("\n[bold white]Display Topology Query[/bold white]")
    displays = get_connected_displays()

    if not displays:
        console.print("[yellow]No display devices reported by system.[/yellow]")
        return

    table = Table(title="Connected Windows Displays", border_style="grey35")
    table.add_column("Index", style="bold white", width=6)
    table.add_column("Device Name", style="cyan", width=16)
    table.add_column("Resolution", width=14)
    table.add_column("Rect (L, T, R, B)", width=24)
    table.add_column("Primary", width=10)

    for d in displays:
        table.add_row(
            str(d.index),
            d.device_name,
            f"{d.width}x{d.height}",
            f"{d.rect[0]}, {d.rect[1]}, {d.rect[2]}, {d.rect[3]}",
            "[green]Yes[/green]" if d.is_primary else "[dim]No[/dim]",
        )
    console.print(table)

    # Check Stardew Valley window
    game_title = "Stardew Valley"
    hwnd = find_window_hwnd(game_title)
    if hwnd:
        mon_idx = get_window_monitor_index(game_title)
        console.print(
            f"[green][OK] '{game_title}' window detected![/green] HWND: {hwnd} on Monitor Index: {mon_idx}"
        )
    else:
        console.print(
            f"[dim]'{game_title}' window is not currently open (normal for offline/test mode).[/dim]"
        )

    # Check admin privilege
    is_admin = check_admin()
    if is_admin:
        console.print("[green][OK] Running with Administrator privileges.[/green]\n")
    else:
        console.print(
            "[yellow][WARN] Running in standard user mode (Administrator recommended for live DirectInput in Phase 2).[/yellow]\n"
        )


def cmd_dry_run(console: Console, iterations: int = 300) -> None:
    """Run an end-to-end simulated 30 Hz loop using mock hardware."""
    console.print(
        f"\n[bold white]Starting Phase 0 Dry Run ({iterations} ticks @ 30 Hz)...[/bold white]"
    )

    config = load_config()
    target_hz = config.control.get("hz", 30)
    period = 1.0 / target_hz

    capture = MockCaptureDriver(target_fps=60.0)
    actuator = MockActuator()

    capture.start()
    time.sleep(0.05)  # allow capture thread to spin up

    jitters = []
    latencies = []

    with precise_clock_scope(1):
        next_tick = time.perf_counter()
        for i in range(iterations):
            tick_start = time.perf_counter()

            # 1. Capture step
            frame, capture_ts = capture.get_latest_frame()
            assert frame is not None, "Mock capture must supply frame"

            # 2. Simulated inference & actuation
            if i % 10 < 5:
                actuator.press_down()
            else:
                actuator.release()

            tick_duration = (time.perf_counter() - tick_start) * 1000.0
            latencies.append(tick_duration)

            # 3. Precise 30 Hz sync
            next_tick += period
            time_to_wait = next_tick - time.perf_counter()
            hybrid_sleep(time_to_wait)

            now = time.perf_counter()
            actual_error = abs((now - next_tick) * 1000.0)
            jitters.append(actual_error)

    capture.stop()

    jitters.sort()
    latencies.sort()

    p50_jit = jitters[int(len(jitters) * 0.50)]
    p99_jit = jitters[min(int(len(jitters) * 0.99), len(jitters) - 1)]
    p50_lat = latencies[int(len(latencies) * 0.50)]
    p99_lat = latencies[min(int(len(latencies) * 0.99), len(latencies) - 1)]

    console.print(f"[green][PASS] Dry run completed {iterations} ticks successfully.[/green]")
    console.print(f"  - Loop Tick Processing Time: p50 = {p50_lat:.3f} ms, p99 = {p99_lat:.3f} ms")
    console.print(f"  - Windows Scheduler Jitter:  p50 = {p50_jit:.3f} ms, p99 = {p99_jit:.3f} ms")
    console.print(f"  - Actuator dispatches logged: {len(actuator.history)}")
    console.print(f"  - Frames generated: {capture.frame_count}")

    if p99_jit < 2.0:
        console.print("[bold green][PASS] Scheduler jitter passed gate (< 2.0 ms p99).[/bold green]\n")
    else:
        console.print(f"[yellow][WARN] Scheduler jitter elevated ({p99_jit:.2f} ms).[/yellow]\n")


def cmd_dashboard_demo(console: Console, duration_sec: float = 6.0) -> None:
    """Display interactive live telemetry dashboard demo."""
    dashboard = TelemetryDashboard(console=console, refresh_hz=4.0)
    dashboard.state.run_name = "phase0_scaffold_demo"
    dashboard.state.status = "ACTIVE"
    dashboard.start()

    start = time.time()
    step = 0
    try:
        while time.time() - start < duration_sec:
            step += 1
            dashboard.state.timesteps_current = min(
                30_000_000, int(step * 75_000)
            )
            dashboard.state.throughput_fps = 18_200.0 + (step % 5) * 150
            dashboard.state.policy_loss = -0.0150 + (step * 0.0005)
            dashboard.state.value_loss = 0.0080 - (step * 0.0001)
            dashboard.state.entropy = 0.0045 - (step * 0.00005)
            dashboard.state.catch_rate_overall = min(0.92, 0.65 + step * 0.01)
            dashboard.state.episodes_completed = step * 3
            dashboard.state.diff_easy_rate = 0.98
            dashboard.state.diff_mid_rate = min(0.92, 0.70 + step * 0.01)
            dashboard.state.diff_hard_rate = min(0.80, 0.50 + step * 0.01)
            dashboard.state.diff_expert_rate = min(0.68, 0.40 + step * 0.01)
            dashboard.state.tick_jitter_ms = 0.85 + (step % 3) * 0.15
            dashboard.state.tick_latency_ms = 4.2 + (step % 2) * 0.4

            if step == 2:
                dashboard.state.recent_events.append("Curriculum Phase A initialized")
            elif step == 5:
                dashboard.state.recent_events.append("Checkpoint saved: ppo_fisher_step150k.zip")
            elif step == 10:
                dashboard.state.recent_events.append("Sim difficulty sweep completed (d=40 CR: 95%)")

            dashboard.update()
            time.sleep(0.25)
    finally:
        dashboard.stop()
    console.print("[green][PASS] Telemetry dashboard demo complete.[/green]\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fisher — Autonomous RL Agent for Stardew Valley")
    parser.add_argument(
        "--check-monitors",
        action="store_true",
        help="Query connected monitors and detect Stardew Valley target window",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute simulated 30 Hz loop with mock capture and actuator drivers",
    )
    parser.add_argument(
        "--dashboard-demo",
        action="store_true",
        help="Run live telemetry dashboard demonstration for the laptop monitor",
    )
    parser.add_argument(
        "--jitter-test",
        action="store_true",
        help="Run 100-tick OS scheduler precision benchmark",
    )
    parser.add_argument(
        "--train-sim",
        action="store_true",
        help="Train PPO policy in Stardew Valley fishing simulation",
    )
    parser.add_argument(
        "--eval-sim",
        type=str,
        nargs="?",
        const="models/ppo_fisher_latest.zip",
        help="Evaluate policy checkpoint or baseline in simulation (e.g. 'models/ppo_fisher_best.zip')",
    )
    parser.add_argument(
        "--eval-live",
        action="store_true",
        help="Run Phase 3 live evaluation harness on Stardew Valley client",
    )
    parser.add_argument(
        "--eval-episodes",
        type=int,
        default=20,
        help="Number of episodes to evaluate for --eval-sim or --eval-live (default: 20)",
    )
    parser.add_argument(
        "--eval-mock",
        action="store_true",
        help="Run evaluation with mock drivers (headless automated testing without physical game)",
    )
    parser.add_argument(
        "--eval-baseline",
        action="store_true",
        help="Evaluate Bang-Bang pure-pursuit baseline instead of PPO policy",
    )
    parser.add_argument(
        "--policy-path",
        type=str,
        default="models/ppo_fisher_best.zip",
        help="Path to PPO model weights for live or sim evaluation",
    )
    parser.add_argument(
        "--require-foreground",
        action="store_true",
        help="Enforce strict foreground window focus requirement (default: disabled, safe auto-cursor positioning used)",
    )
    parser.add_argument(
        "--no-require-foreground",
        action="store_true",
        help="Disable foreground window focus requirement",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=90.0,
        help="Timeout in seconds when waiting for minigame UI to appear (default: 90.0s)",
    )
    parser.add_argument(
        "--timesteps",
        type=int,
        default=1_500_000,
        help="Total timesteps for --train-sim",
    )
    parser.add_argument(
        "--bench-latency",
        action="store_true",
        help="Run end-to-end pipeline latency benchmark (capture -> extract -> infer -> dispatch)",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Run interactive/automated ROI & track calibration tool",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Run 60 Hz minigame telemetry recorder",
    )
    parser.add_argument(
        "--record-synthetic",
        action="store_true",
        help="Run 60 Hz minigame recorder using synthetic frame generator",
    )
    parser.add_argument(
        "--record-duration",
        type=float,
        default=60.0,
        help="Duration in seconds for --record (default: 60.0s; press Ctrl+C anytime to stop early)",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Display real-time visual preview of Screen 3 capture and BobberBar detection (standalone or with --eval-live)",
    )
    parser.add_argument(
        "--record-video",
        type=str,
        nargs="?",
        const="",
        default=None,
        help="Record real-time preview display (with bounding boxes and metrics HUD) to an MP4 video file",
    )
    parser.add_argument(
        "--assist",
        action="store_true",
        help="Start the on-demand fishing assistant (watches for minigame, AI takes control automatically)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock drivers (headless, no live game required)",
    )
    parser.add_argument(
        "--water",
        action="store_true",
        help="Start the auto watering assistant (waters visible crops, refills at pond)",
    )
    parser.add_argument(
        "--capacity",
        type=int,
        default=None,
        help="Override watering can capacity (basic=40, copper=55, steel=70, gold=85, iridium=100)",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=None,
        help="Override capture monitor output index (0 = primary/center screen, 1 = right screen)",
    )
    parser.add_argument(
        "--analyze-video",
        type=str,
        default=None,
        help="Analyze recorded preview MP4 video for minigame detection errors, false positives, and tracking stability",
    )

    args = parser.parse_args()
    console = Console()

    rec_vid = args.record_video is not None
    vid_path = args.record_video if (args.record_video and args.record_video != "") else None

    if args.check_monitors:
        cmd_check_monitors(console)
    elif args.dry_run:
        cmd_dry_run(console)
    elif args.dashboard_demo:
        cmd_dashboard_demo(console)
    elif args.jitter_test:
        results = measure_jitter(target_hz=30.0, iterations=100)
        console.print(f"Jitter Results (30 Hz): p50={results['p50_ms']:.3f}ms, p99={results['p99_ms']:.3f}ms, max={results['max_ms']:.3f}ms")
    elif args.bench_latency:
        from scripts.bench_latency import run_latency_benchmark
        run_latency_benchmark()
    elif args.calibrate:
        from scripts.calibrate import run_calibration
        run_calibration()
    elif args.record:
        from scripts.record import record_session
        record_session(max_duration_s=args.record_duration, synthetic_mode=False)
    elif args.record_synthetic:
        from scripts.record import record_session
        record_session(max_duration_s=args.record_duration, synthetic_mode=True)
    elif args.train_sim:
        import subprocess
        cmd = [sys.executable, "scripts/train_sim.py", "--timesteps", str(args.timesteps)]
        subprocess.run(cmd)
    elif args.eval_sim:
        import subprocess
        cmd = [sys.executable, "scripts/eval_sim.py", "--policy", args.eval_sim, "--episodes", str(args.eval_episodes)]
        subprocess.run(cmd)
    elif args.eval_live:
        from scripts.eval_live import run_live_evaluation
        require_fg = args.require_foreground and not args.no_require_foreground
        run_live_evaluation(
            episodes=args.eval_episodes,
            policy_path=args.policy_path,
            use_baseline=args.eval_baseline,
            mock_mode=args.eval_mock,
            require_foreground=require_fg,
            wait_timeout=args.wait_timeout,
            preview=args.preview,
            record_video=rec_vid,
            output_video_path=vid_path,
        )
    elif args.water:
        from fisher.waterer.assistant import WateringAssistant
        config = load_config()
        if args.monitor is not None:
            config.capture["monitor_idx"] = args.monitor
        assistant = WateringAssistant.from_config(
            config=config,
            mock_mode=args.mock,
            preview=args.preview,
            capacity_override=args.capacity,
        )
        console.print("\n[bold cyan]Starting Stardew Valley Auto-Waterer...[/bold cyan] (Press [bold red]F9[/bold red] to stop)")
        stats = assistant.run()
        console.print(f"\n[bold green]Auto Waterer Completed[/bold green] — State: [yellow]{stats.final_state}[/yellow]")
        console.print(
            f"Tiles watered: [cyan]{stats.tiles_watered}[/cyan] "
            f"(confirmed: [cyan]{stats.tiles_confirmed}[/cyan]) | "
            f"Skipped: [cyan]{stats.tiles_skipped}[/cyan] | "
            f"Refills: [cyan]{stats.refills}[/cyan] | "
            f"Scans: [cyan]{stats.scan_passes}[/cyan] | "
            f"Duration: [cyan]{stats.duration_s:.1f}s[/cyan]\n"
        )
    elif args.assist:
        from fisher.orchestration.assistant import FishingAssistant
        config = load_config()
        assistant = FishingAssistant.from_config(
            config=config,
            policy_path=args.policy_path,
            preview=args.preview,
            mock_mode=getattr(args, 'mock', False),
        )
        assistant.run()
    elif args.preview:
        from fisher.ui.preview import run_preview
        run_preview(record_video=rec_vid, output_video_path=vid_path)
    elif args.analyze_video:
        from scripts.analyze_preview_video import analyze_video
        analyze_video(args.analyze_video)
    else:
        config = load_config()
        console.print(
            f"[bold white]Fisher v0.1.0[/bold white] — Loaded configuration from {config.game.get('window_title')}"
        )
        console.print("Run with --help to see available commands, or --assist / --eval-live / --preview.")


if __name__ == "__main__":
    main()

