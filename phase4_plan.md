# Phase 4 — End-to-End Autonomy, Lifecycle Guards & Hardening

> **FOR THE INCOMING AGENT:** Read [`AGENTS.md`](file:///d:/projects/fisher/AGENTS.md) and [`log.md`](file:///d:/projects/fisher/log.md) first, then this document. Do NOT start implementing until you have run `pytest -v` and confirmed the green baseline (currently **62 passed, 2 warnings**).

---

## Current State (Phase 3 — COMPLETE)

All Phase 3 exit gates are satisfied. Do not re-litigate them.

| Metric | Achieved | Gate | Status |
|---|---|---|---|
| Live Catch Rate (20 eps) | **90.0%** | ≥ 80% | ✅ PASS |
| Sim-to-Real Gap | **−0.8 pts** | < 5 pts | ✅ OPTIMAL |
| p99 Loop Latency | **6.47 ms** | < 25 ms | ✅ PASS |
| Test Suite | **62 passed** | 0 regressions | ✅ GREEN |

The last two commits (pushed to `main`, commit `ae4f4f2`) fixed:
- `ui_lost_threshold_frames` 15 → 20 (outer termination now greater than inner debounce of 12 frames)
- Catch-rescue `peak_progress >= 0.85` → `>= 0.75`
- Added `termination_reason` field to per-episode JSONL + console

---

## Phase 4 Objective

Build the full autonomous fishing loop that wraps the trained PPO policy so the agent can run unattended for 30+ minutes: cast → wait bite → hook → RL play → collect loot → repeat, with stamina management, night-cycle cutoff, inventory guard, and a < 200 ms killswitch.

**Exit gate:** 30-minute unattended soak test — ≥ 20 consecutive successful episodes, zero unrecovered hangs, stamina automatically managed, clean session report written.

---

## Pre-Phase-4 Prerequisite Fix (Do This First, 5 Minutes)

### False-Start Detection Gate: `p >= 0.01` → `p >= 0.20`

In [`src/fisher/env/live_env.py`](file:///d:/projects/fisher/src/fisher/env/live_env.py) around line 236, the dynamic localization path in `reset()` requires `p >= 0.01` before locking `current_roi`. The default Stardew Valley initial progress is `p0 = 0.30`. This means a frame where the tracker returns the default fallback value (`p=0.30`) can pass the `>= 0.01` gate and mis-trigger the minigame start on a rod-cast UI flash (observed as Ep 20 in the last validation run: duration 0.67 s, peak = 0.30, mean_in_bar = 0.0).

**Fix:**
```python
# In reset(), dynamic localization path (~line 236):
# BEFORE:
if p >= 0.01:
# AFTER:
if p >= 0.20:
```

This ensures the detection phase only locks onto a genuine, non-default progress value. After fixing, run `pytest -v` to confirm 62 passed.

---

## New Files to Create

### 1. `src/fisher/orchestration/__init__.py`
Empty init file to make the package importable.

### 2. `src/fisher/orchestration/fsm.py`

Full state machine implementing the lifecycle described in `plan.md §7`.

**States (in order):**

| State | Entry Action | Exit Condition | Timeout / Failure |
|---|---|---|---|
| `INIT` | Load config, policy, warm capture, `timeBeginPeriod(1)`, elevation check | All checks pass | 5 s → ABORT |
| `CAL_CHECK` | Grab one frame, validate ROI colors (green bar region, progress meter column) | ROIs sane | → ABORT (no input sent) |
| `LIFECYCLE_CHECK` | Check stamina gauge, clock HUD, inventory-full dialog | Guards nominal | Stamina low → EAT_FOOD; Full/Night → ABORT |
| `EAT_FOOD` | Press food hotbar slot key, RMB to eat, dismiss prompt | Stamina restored | 3 s → ABORT |
| `CASTING` | Select rod hotbar slot, hold LMB 0.6 s (power cast), release | Bobber landing confirmed (or fixed 1.5 s wait) | 3 retries → ABORT |
| `WAIT_BITE` | Monitor bobber ROI for bite cues (2-of-2: dip + `!` template) | Bite confirmed → HOOK | 25 s → recast |
| `HOOK` | Single LMB click | Minigame UI appears (up to 2 s) | No UI → recast |
| `RL_ACTIVE` | Hand control to `LiveFishingEnv.step()` at 30 Hz | `terminated` or `truncated` | `T_max`, watchdogs |
| `RESOLVE` | Log episode outcome | Catch → LOOT; Escape → LIFECYCLE_CHECK | 3 s → ABORT |
| `LOOT` | Click catch dialog dismiss button | Dialog gone | 5 s → ABORT |
| `ABORT` | Release LMB, send ESC if minigame active, write session report, restore console focus | — | Terminal |

**Implementation notes:**
- Use Python `Enum` for states, not bare strings.
- Each state is a method on the FSM class; the `run()` method loops calling the current state handler.
- The FSM checks `keyboard.is_pressed('f9')` at the top of every state handler (not just in RL_ACTIVE).
- Use `time.perf_counter()` for all timing, never `time.time()`.
- All magic numbers (0.6 s cast hold, 25 s bite timeout, 3 retries, 5 s loot timeout) must come from `configs/default.yaml` under a `lifecycle:` key — no hardcoding.

### 3. `src/fisher/orchestration/session.py`

Session-level accounting across all episodes in a single run:

```python
@dataclass
class SessionStats:
    start_time: float
    episodes: int = 0
    catches: int = 0
    escapes: int = 0
    aborts: int = 0
    eat_food_events: int = 0
    total_catch_duration_s: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)
```

- Writes a structured JSON report to `reports/session/session_<timestamp>.json` on exit.
- Also writes a one-line CSV row to `reports/session/sessions.csv` for longitudinal tracking.

### 4. `src/fisher/orchestration/safety.py`

Independent supervisor running in a daemon thread alongside the FSM:

```python
class SafetySupervisor:
    def start(self) -> None: ...  # launches daemon thread
    def stop(self) -> None: ...
    def is_abort_requested(self) -> bool: ...
```

**Responsibilities:**
- **F9 killswitch:** `keyboard.is_pressed('f9')` polling at 50 Hz → sets `_abort_requested = True`. The FSM checks `supervisor.is_abort_requested()` at the top of every state.
- **Ctrl+F9:** Hard `os._exit(1)` — unconditional process termination.
- **Watchdogs (all set `_abort_requested = True`):**
  - Game window lost foreground for > N consecutive frames (configurable, default 90 = 3 s at 30 Hz).
  - Capture FPS < 45 (averaged over last 2 s).
  - Extractor exception count > 5 in last 10 frames.
- **Killswitch drill at startup:** before the first episode, test F9 response time using a mock signal — must complete in < 200 ms. Log the result. Do not abort if the drill passes; only log.

### 5. `src/fisher/vision/lifecycle.py`

CV detectors for lifecycle events. All use the same ROI conventions as the existing extractor (absolute screen coordinates for 1080p, loaded from `configs/default.yaml`).

```python
class StaminaDetector:
    def get_fill_ratio(self, frame: np.ndarray) -> float:
        """Returns stamina bar fill [0.0, 1.0]. Below threshold → eat food."""

class ClockDetector:
    def is_past_cutoff(self, frame: np.ndarray) -> bool:
        """Returns True if in-game time >= 1:30 AM (configurable)."""
        # v1: use episode budget cap (default 25 episodes/day) as proxy.
        # v2: template-match clock digit sprites.

class InventoryDetector:
    def is_full(self, frame: np.ndarray) -> bool:
        """Returns True if 'Inventory Full' dialog is visible."""

class LootDialogDetector:
    def is_visible(self, frame: np.ndarray) -> bool:
        """Returns True if catch/loot dialog is on screen."""
    def get_dismiss_point(self, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        """Returns screen coordinates of the OK/dismiss button, or None."""

class BobberLandDetector:
    def wait_for_landing(self, capture: CaptureDriver, timeout_s: float = 2.0) -> bool:
        """
        v1: fixed sleep for cast_hold_s + 0.9 s (bobber flight time), return True.
        v2: motion detection in pond ROI (frame differencing threshold).
        """
```

**Important:** For v1, the `ClockDetector` should use the **episode budget cap** method (track episodes elapsed since session start, abort after 25) rather than actual clock reading — this avoids the need for clock template sprites and is reliable enough for a 30-minute session at ~90 s/episode average.

### 6. `scripts/run.py`

Single entry point for the full autonomous loop:

```bash
python scripts/run.py \
    --model models/ppo_fisher_best.zip \
    --stats models/vec_normalize_best.pkl \
    --episodes 100 \
    --preview
```

Internals:
1. Parse args, load config.
2. Instantiate: `BetterCamCaptureDriver`, `DirectInputActuator`, `FeatureExtractor`, `LiveFishingEnv`, `SafetySupervisor`, `FishingFSM`, `SessionStats`.
3. Call `supervisor.start()`.
4. Call `fsm.run()` — blocks until done.
5. Write session report, print summary table (same style as `eval_live.py`).

---

## Files to Modify

### [`configs/default.yaml`](file:///d:/projects/fisher/configs/default.yaml)

Add a `lifecycle:` top-level section:

```yaml
lifecycle:
  # Stamina management
  stamina_roi: {x0: 1700, y0: 1000, x1: 1900, y1: 1030}  # screen coords, 1080p
  stamina_threshold: 0.15       # eat food when fill ratio below this
  food_hotbar_slot: "1"         # keyboard key for food hotbar slot

  # Night cycle guard
  max_episodes_per_day: 25      # proxy for 1:30 AM cutoff (v1)
  # clock_roi: {x0: 1820, y0: 10, x1: 1920, y1: 50}  # for v2 clock reading

  # Cast parameters
  cast_hold_s: 0.6              # LMB hold duration for power cast
  cast_land_wait_s: 0.9         # fixed wait after release for bobber to land (v1)
  cast_max_retries: 3

  # Bite detection
  bite_timeout_s: 25.0          # recast if no bite confirmed in this window
  # pond_roi: {x0: 800, y0: 400, x1: 1400, y1: 800}  # for bobber motion detection

  # Loot dialog
  loot_timeout_s: 5.0           # abort if dialog not dismissed in this time
  # loot_dialog_roi: {x0: 600, y0: 300, x1: 1300, y1: 780}

  # Session limits
  max_episodes: 100
  max_session_s: 1800           # 30 minutes
```

### [`src/fisher/cli.py`](file:///d:/projects/fisher/src/fisher/cli.py)

Add `fisher --run` CLI alias that calls `scripts/run.py` logic:
```python
# In cli.py, add to the argument parser:
elif args.run:
    from scripts.run import main as run_main
    run_main()
```

---

## Tests to Write

### [`tests/test_phase4.py`](file:///d:/projects/fisher/tests/test_phase4.py)

All tests must use `MockCaptureDriver` and `MockActuator` — no live game required for CI.

| Test | What it verifies |
|---|---|
| `test_fsm_cast_to_rl_active` | FSM transitions CASTING → WAIT_BITE → HOOK → RL_ACTIVE with mock drivers |
| `test_fsm_loot_dismissal` | LOOT state dismisses dialog within 5 s timeout |
| `test_fsm_abort_on_night_cutoff` | FSM → ABORT when episode budget cap hit |
| `test_fsm_eat_food_on_low_stamina` | FSM → EAT_FOOD when StaminaDetector < 15%, resumes CASTING after |
| `test_fsm_inventory_full_abort` | FSM → ABORT on InventoryDetector hit |
| `test_killswitch_latency` | Safety supervisor responds to simulated F9 in < 200 ms |
| `test_session_report_written` | Session report JSON exists with valid schema after 3-episode mock run |
| `test_cast_retry_on_miss` | FSM retries cast up to 3 times if bobber landing not confirmed |
| `test_fsm_full_episode_cycle` | Complete mock episode from INIT to LOOT to LIFECYCLE_CHECK, back to CASTING |

**Exit gate for tests:** `pytest -v` must show **≥ 71 passed** (62 existing + 9 new), zero regressions.

---

## Verification Plan

### Step 1 — Automated Tests
```bash
pytest -v                        # must be >= 71 passed, 0 failures
pytest -v tests/test_phase4.py   # new suite in isolation
```

### Step 2 — Killswitch Drill (Manual)
```bash
python scripts/run.py --mock --episodes 3
```
While running: press F9 → verify console prints abort message, mouse released, loop stopped in < 200 ms. Repeat 5×. Document exact measured latencies.

### Step 3 — Lifecycle Guard Smoke Tests (Manual, Live Game)
- **Stamina guard:** Lower stamina below 15%, run `python scripts/run.py`. Verify "EAT_FOOD" state fires, food consumed, session continues.
- **Night cutoff:** Set `max_episodes_per_day: 2` in config temporarily, run 2 episodes, verify graceful abort and report written.
- **Inventory guard:** Fill inventory, run `python scripts/run.py`. Verify ABORT state fires with `inventory_full` reason.

### Step 4 — Soak Test (30 Minutes Unattended)
```bash
python scripts/run.py --model models/ppo_fisher_best.zip --stats models/vec_normalize_best.pkl --episodes 40
```

**Pass criteria (all must be met):**
- ≥ 20 consecutive successful catches.
- ≥ 1 `EAT_FOOD` event (verify stamina guard is exercised).
- Zero FSM states stuck > 30 s (watchdog fires).
- `reports/session/session_<timestamp>.json` written with correct schema.
- No unhandled Python exceptions in stdout/stderr.

**Record exact numbers** in `log.md` entry: total episodes, catches, escapes, eat-food events, p50/p99 latency, total wall-clock time.

---

## Log Entry Requirements

After completing Phase 4, you MUST append a structured entry to [`log.md`](file:///d:/projects/fisher/log.md) per the protocol in [`AGENTS.md`](file:///d:/projects/fisher/AGENTS.md). Include:
- Exact `pytest -v` output (N passed, N warnings, time).
- Soak test results: episodes, catches, eat-food events, wall-clock duration.
- Killswitch drill: measured p50/p99 response times across 5 repetitions.
- Any deviations from this plan and rationale.

---

## What NOT to Do

- Do **not** modify any sim training code (`src/fisher/sim/`, `scripts/train_sim.py`) — Phase 3 is complete and the policy is frozen.
- Do **not** modify `src/fisher/extraction/` or `src/fisher/env/live_env.py` beyond the prerequisite `p >= 0.20` fix documented above.
- Do **not** attempt live fine-tuning or retraining — the sim-to-real gap is −0.8 pts (negative), meaning the live agent already exceeds the sim baseline. There is no gradient work left.
- Do **not** add magic numbers to source code — all values go in `configs/default.yaml` under `lifecycle:`.
