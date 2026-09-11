# Fisher — Execution Log & Progress Record

This document serves as the persistent status log for the **Fisher** project. It tracks completed phases, architectural milestones, verified benchmarks, and provides full context for subsequent development sessions.

---

## Hardware & Multi-Screen Topology

Verified via `win32api.EnumDisplayMonitors` and Windows Forms display query:

| Display Identifier | Resolution | Virtual Bounding Box | Physical Role | System Responsibility |
|---|---|---|---|---|
| `\\.\DISPLAY1` | 1536×864 (DPI scaled) | `[-1920, 0, -384, 864]` | Laptop Display (Left) | **Screen 1**: Dedicated Real-Time Telemetry CLI (`fisher --dashboard-demo`, Rich live UI) |
| `\\.\DISPLAY5` | 1920×1080 | `[0, 0, 1920, 1080]` | Primary External Monitor (Center) | **Screen 2**: Antigravity IDE (code inspection, testing, git) |
| `\\.\DISPLAY6` | 1920×1080 | `[1920, 0, 3840, 1080]` | Secondary External Monitor (Right) | **Screen 3**: Stardew Valley client (borderless 1920×1080, UI zoom 100%) |

**Multi-Monitor Handling:**
- DXGI Desktop Duplication (`bettercam`) dynamically resolves the monitor containing `"Stardew Valley"` using `win32api.MonitorFromWindow` via `fisher.utils.display.get_window_monitor_index`.
- Screen coordinates and ROI (`x0: 1520, y0: 220, x1: 1900, y1: 980`) are evaluated relative to the game's client monitor bounds.
- Foreground validation (`win32gui.GetForegroundWindow`) guards against input leaks into IDE or telemetry terminals.

---

## Phase 0 — Bootstrap, Precision Timing & Telemetry Scaffolding

- **Status:** `[COMPLETED]` (2026-09-12)
- **Primary Goal:** Scaffolding project dependencies, locking 1 ms OS timer resolution, multi-display detection, headless mock interfaces for CI, and terminal telemetry console.

### Key Deliverables & Achievements

1. **Packaging & Dependency Baseline:**
   - [pyproject.toml](pyproject.toml): Configured build backend and dependencies:
     - Core RL: `gymnasium==1.3.0`, `stable-baselines3==2.9.0`, `torch==2.14.0`.
     - Vision & Capture: `bettercam==1.0.0`, `opencv-python==5.0.0.93`.
     - Actuation & System: `pydirectinput==1.0.4`, `pywin32==312`, `keyboard==0.13.5`.
     - Telemetry & Tooling: `rich==14.3.4`, `pyyaml==6.0.3`, `pytest==9.1.1`.
   - Installed in editable mode (`pip install -e .`), providing the global CLI command `fisher`.

2. **Configuration System:**
   - [configs/default.yaml](configs/default.yaml): Master single-file configuration with deep-merge overlay support for environment overrides (`configs/capture_1080p.yaml`).
   - [src/fisher/config.py](src/fisher/config.py): Typed loader with section accessors (`game`, `capture`, `ui`, `control`, `reward`, `ppo`, `sim`, `lifecycle`, `safety`).

3. **High-Precision Timing Subsystem (< 0.15 ms Jitter):**
   - [src/fisher/utils/timing.py](src/fisher/utils/timing.py):
     - Wrapped Windows Multimedia Timer (`winmm.timeBeginPeriod(1)` / `winmm.timeEndPeriod(1)`) to bypass the default 15.6 ms OS scheduling quantum.
     - Implemented hybrid sleep: OS sleep for `(dt - 1.5ms)` followed by a tight `time.perf_counter()` spinlock.
     - **Benchmark Result:** 30 Hz control loop achieved **p50 jitter = 0.002 ms, p99 jitter = 0.043 ms** (Gate requirement was < 2.0 ms).

4. **Multi-Display & Window Detection:**
   - [src/fisher/utils/display.py](src/fisher/utils/display.py):
     - `get_connected_displays()`: Enumerates all active displays, device names, resolutions, and primary status.
     - `get_window_monitor_index(window_title)`: Automatically binds capture to Screen 3 (`\\.\DISPLAY6`) without manual configuration.

5. **Hardware Abstractions (Headless & Mock Drivers):**
   - [src/fisher/capture/base.py](src/fisher/capture/base.py) & [src/fisher/capture/mock_driver.py](src/fisher/capture/mock_driver.py):
     - Thread-safe 60 Hz frame generator with timestamping and atomic latest-frame slot.
   - [src/fisher/input/base.py](src/fisher/input/base.py) & [src/fisher/input/mock_actuator.py](src/fisher/input/mock_actuator.py):
     - Abstract mouse actuator with state tracking and transition timestamp logging.

6. **Dedicated Terminal Telemetry Dashboard:**
   - [src/fisher/ui/dashboard.py](src/fisher/ui/dashboard.py):
     - Fixed-grid `rich.live.Live` console dashboard formatted for the 1536×864 laptop screen.
     - Telemetry panels: Training Progress & Throughput, PPO Loss & Value Metrics, Difficulty Bucket Catch Rates, 30 Hz Latency Budget, and Real-time Event Log.
   - [src/fisher/cli.py](src/fisher/cli.py):
     - `fisher --check-monitors`: Validates display arrangement and checks for game window.
     - `fisher --jitter-test`: Benchmarks Windows OS scheduler precision.
     - `fisher --dry-run`: 300-tick end-to-end simulation with mock drivers.
     - `fisher --dashboard-demo`: Live interactive dashboard demonstration.

7. **Verification & Testing:**
   - `pytest -v`: **7 passed in 2.34s** (`test_phase0.py`).
   - `fisher --dry-run`: **300 ticks passed** (`p50 latency = 0.058 ms`, `p99 latency = 0.169 ms`, `p99 jitter = 0.043 ms`, 0 dropped frames).
   - `fisher --dashboard-demo`: Verified interactive rendering on the laptop screen.

---

## Roadmap & Next Phases

### Phase 1 — Simulator & Decompiled C# Ground Truth
- Ground 1D physics directly in decompiled `StardewValley.Menus.BobberBar.cs`:
  - Gravity: `g_out = 0.25f` px/tick² (1.584 track/s²), `g_in = 0.15f` (0.6× nominal).
  - Restitution: `e_bot = 2/3`, `e_top = 2/3`, boundary pinning logic.
  - Fish Kinematics: 5 archetypes (`Mixed`, `Dart`, `Smooth`, `Sinker`, `Floater`) with difficulty-parameterized velocity chase.
- Build `StardewFishSim-v0` (`gymnasium.Env`) with domain randomization and latency injection.
- Pre-train PPO policy via Stable-Baselines3.

### Phase 2 — Capture, Calibration & Robust CV Extractor
- DXGI capture thread via `bettercam` targeting Screen 3 (`\\.\DISPLAY6`).
- ROI calibration tool (`scripts/calibrate.py` -> `configs/capture_1080p.yaml`).
- OpenCV feature extractor (contrast-invariant horizontal saliency scan for fish centroid, red-green progress tracking).
- Live 60 Hz recording script (`scripts/record.py`) to validate against simulator.

### Phase 3 — Live Environment Integration & Transfer Evaluation
- Connect live capture + extractor + policy + DirectInput actuator in `env/live_env.py`.
- 20-episode frozen policy live eval (`scripts/eval_live.py`).
- Sim-to-real gap triage & offline recalibration.

### Phase 4 — End-to-End Autonomy, Lifecycle Guards & Hardening
- Full FSM lifecycle: auto-casting, bite detection ("!" cue + bobber dip), hook, minigame control, loot dismissal.
- Safety supervisor: global F9 killswitch (< 200 ms response), stamina food auto-consumption, 1:30 AM night cutoff.
- 30-minute unattended soak test.
