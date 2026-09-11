"""Disciplined terminal telemetry dashboard for Fisher RL agent."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from rich.console import Console, RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text


@dataclass
class DashboardState:
    """Telemetry state container for the live dashboard."""

    status: str = "INITIALIZING"
    run_name: str = "fisher_run"
    device: str = "CPU (32 envs)"
    target_env: str = "StardewFishSim-v0"
    killswitch_armed: bool = True
    start_time: float = field(default_factory=time.time)

    # Progress & Throughput
    timesteps_current: int = 0
    timesteps_total: int = 30_000_000
    throughput_fps: float = 0.0
    eta_seconds: float = 0.0

    # RL Metrics
    policy_loss: float = 0.0
    value_loss: float = 0.0
    entropy: float = 0.0
    learning_rate: float = 3.0e-4
    explained_variance: float = 0.0

    # Episode Statistics
    episodes_completed: int = 0
    catch_rate_overall: float = 0.0
    mean_duration_s: float = 0.0
    mean_reward: float = 0.0
    diff_easy_rate: float = 0.0
    diff_mid_rate: float = 0.0
    diff_hard_rate: float = 0.0
    diff_expert_rate: float = 0.0

    # Latency & System Health
    tick_latency_ms: float = 0.0
    tick_jitter_ms: float = 0.0
    capture_latency_ms: float = 0.0
    cv_latency_ms: float = 0.0
    inference_latency_ms: float = 0.0
    input_latency_ms: float = 0.0

    # Live Watchdogs
    client_foreground: bool = True
    in_game_time: str = "06:00 AM"
    stamina_pct: float = 100.0
    inventory_full: bool = False

    # Event log (recent 5 events)
    recent_events: List[str] = field(default_factory=list)

    def elapsed_str(self) -> str:
        elapsed = int(time.time() - self.start_time)
        hrs = elapsed // 3600
        mins = (elapsed % 3600) // 60
        secs = elapsed % 60
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"

    def eta_str(self) -> str:
        if self.eta_seconds <= 0:
            return "--:--:--"
        eta = int(self.eta_seconds)
        hrs = eta // 3600
        mins = (eta % 3600) // 60
        secs = eta % 60
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"


class TelemetryDashboard:
    """Live terminal console dashboard with a structured, high-contrast layout."""

    def __init__(self, console: Optional[Console] = None, refresh_hz: float = 4.0):
        self.console = console or Console()
        self.refresh_hz = refresh_hz
        self.state = DashboardState()
        self._live: Optional[Live] = None

    def start(self) -> None:
        self._live = Live(
            self._render_view(),
            console=self.console,
            refresh_per_second=self.refresh_hz,
            transient=False,
            auto_refresh=True,
        )
        self._live.start()

    def update(self) -> None:
        if self._live:
            self._live.update(self._render_view())

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    def _render_view(self) -> RenderableType:
        root = Layout(name="root")
        root.split(
            Layout(name="header", size=3),
            Layout(name="metrics", size=10),
            Layout(name="performance", size=11),
            Layout(name="footer", size=7),
        )

        root["metrics"].split_row(
            Layout(name="progress", ratio=1),
            Layout(name="policy", ratio=1),
        )

        root["performance"].split_row(
            Layout(name="episodes", ratio=1),
            Layout(name="system", ratio=1),
        )

        root["header"].update(self._build_header())
        root["metrics"]["progress"].update(self._build_progress_panel())
        root["metrics"]["policy"].update(self._build_policy_panel())
        root["performance"]["episodes"].update(self._build_episodes_panel())
        root["performance"]["system"].update(self._build_system_panel())
        root["footer"].update(self._build_footer_panel())

        return root

    def _build_header(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=3)
        grid.add_column(justify="center", ratio=2)
        grid.add_column(justify="right", ratio=3)

        title = Text.assemble(
            ("FISHER ", "bold white"),
            ("TELEMETRY CONSOLE", "bold bright_blue"),
            (f"  [{self.state.run_name}]", "dim white"),
        )
        status_color = "green" if self.state.status in ("TRAINING", "ACTIVE", "READY") else "yellow"
        status = Text.assemble(
            ("STATUS: ", "dim white"),
            (f"{self.state.status}", f"bold {status_color}"),
            (f"  |  ELAPSED: {self.state.elapsed_str()}", "dim white"),
        )
        ks_color = "bold green" if self.state.killswitch_armed else "bold red"
        killswitch = Text.assemble(
            ("KILLSWITCH [F9]: ", "dim white"),
            ("ARMED" if self.state.killswitch_armed else "DISARMED", ks_color),
        )

        grid.add_row(title, status, killswitch)
        return Panel(grid, style="grey70 on grey11", border_style="grey35")

    def _build_progress_panel(self) -> Panel:
        pct = (
            (self.state.timesteps_current / self.state.timesteps_total)
            if self.state.timesteps_total > 0
            else 0.0
        )
        bar = ProgressBar(total=1.0, completed=min(1.0, pct), width=36)

        table = Table.grid(padding=(0, 1), expand=True)
        table.add_column(style="dim white", width=16)
        table.add_column(style="bold white")

        table.add_row("Timesteps:", f"{self.state.timesteps_current:,} / {self.state.timesteps_total:,}")
        table.add_row("Progress:", f"{pct * 100:.1f}%")
        table.add_row("", bar)
        table.add_row("Throughput:", f"{self.state.throughput_fps:,.0f} steps/s")
        table.add_row("ETA:", self.state.eta_str())

        return Panel(
            table,
            title="[bold white]Training Progress & Throughput[/bold white]",
            border_style="grey35",
        )

    def _build_policy_panel(self) -> Panel:
        table = Table.grid(padding=(0, 1), expand=True)
        table.add_column(style="dim white", width=18)
        table.add_column(style="bold white")

        table.add_row("Target Environment:", self.state.target_env)
        table.add_row("Compute Device:", self.state.device)
        table.add_row("Policy Loss:", f"{self.state.policy_loss:+.4f}")
        table.add_row("Value Loss:", f"{self.state.value_loss:.4f}")
        table.add_row("Entropy:", f"{self.state.entropy:.4f}")
        table.add_row("Explained Var:", f"{self.state.explained_variance:.3f}")
        table.add_row("Learning Rate:", f"{self.state.learning_rate:.2e}")

        return Panel(
            table,
            title="[bold white]Policy Optimization Metrics[/bold white]",
            border_style="grey35",
        )

    def _build_episodes_panel(self) -> Panel:
        table = Table.grid(padding=(0, 1), expand=True)
        table.add_column(style="dim white", width=18)
        table.add_column(style="bold white")

        table.add_row("Episodes Run:", f"{self.state.episodes_completed}")
        table.add_row("Overall Catch Rate:", f"{self.state.catch_rate_overall * 100:.1f}%")
        table.add_row("Mean Reward:", f"{self.state.mean_reward:+.2f}")
        table.add_row("Mean Duration:", f"{self.state.mean_duration_s:.1f} s")
        table.add_row(
            "Difficulty ≤ 40:",
            f"{self.state.diff_easy_rate * 100:.1f}% (Easy)",
        )
        table.add_row(
            "Difficulty 41–70:",
            f"{self.state.diff_mid_rate * 100:.1f}% (Mid)",
        )
        table.add_row(
            "Difficulty 71–90:",
            f"{self.state.diff_hard_rate * 100:.1f}% (Hard)",
        )
        table.add_row(
            "Difficulty > 90:",
            f"{self.state.diff_expert_rate * 100:.1f}% (Expert)",
        )

        return Panel(
            table,
            title="[bold white]Episode Performance & Difficulty[/bold white]",
            border_style="grey35",
        )

    def _build_system_panel(self) -> Panel:
        table = Table.grid(padding=(0, 1), expand=True)
        table.add_column(style="dim white", width=20)
        table.add_column(style="bold white")

        jitter_color = "green" if self.state.tick_jitter_ms < 2.0 else "yellow"
        fg_color = "green" if self.state.client_foreground else "red"

        table.add_row("Loop Control Rate:", "30 Hz (33.3 ms period)")
        table.add_row("Tick Latency:", f"{self.state.tick_latency_ms:.2f} ms")
        table.add_row("Scheduler Jitter:", f"[{jitter_color}]{self.state.tick_jitter_ms:.2f} ms[/{jitter_color}]")
        table.add_row("Capture Latency:", f"{self.state.capture_latency_ms:.2f} ms")
        table.add_row("CV Extraction:", f"{self.state.cv_latency_ms:.2f} ms")
        table.add_row("Game Window:", f"[{fg_color}]{'FOREGROUND' if self.state.client_foreground else 'BACKGROUND'}[/{fg_color}]")
        table.add_row("In-Game Clock:", self.state.in_game_time)
        table.add_row("Player Stamina:", f"{self.state.stamina_pct:.0f}%")

        return Panel(
            table,
            title="[bold white]System Health & Watchdogs[/bold white]",
            border_style="grey35",
        )

    def _build_footer_panel(self) -> Panel:
        table = Table(box=None, expand=True, show_header=False, padding=(0, 1))
        table.add_column(style="dim white", width=14)
        table.add_column(style="white")

        events = self.state.recent_events[-4:] if self.state.recent_events else ["[No recent events]"]
        for item in events:
            table.add_row("EVENT", item)

        return Panel(
            table,
            title="[bold white]Recent Telemetry Events[/bold white]",
            border_style="grey35",
        )
