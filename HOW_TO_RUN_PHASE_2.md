# How to Run Phase 2 — Live Screen Capture, Calibration & Recording

This guide walks you through setting up *Stardew Valley* on Screen 3, validating the multi-monitor display binding, calibrating the fishing minigame region of interest (ROI), and recording live 60 Hz minigame telemetry traces.

---

## 1. Prerequisites & Display Topology Checklist

Before running capture commands, verify your physical monitors match the project topology:

| Physical Monitor | Resolution & Scaling | Assigned System Role |
|---|---|---|
| **Screen 1 (Laptop / Left)** | 1536×864 (DPI Scaled) | Telemetry dashboard terminal (`fisher --dashboard-demo`) |
| **Screen 2 (Center External)** | 1920×1080 (100% Scale) | Primary desktop: VS Code, Antigravity IDE, command terminals |
| **Screen 3 (Right External)** | 1920×1080 (100% Scale) | Dedicated game monitor: *Stardew Valley* borderless window |

### Game Settings Verification:
1. Open **Stardew Valley**.
2. Go to **Options** (controller tab in game menu):
   - **Window Mode:** `Borderless Windowed` or `Windowed` maximized on **Screen 3**.
   - **Resolution:** `1920x1080`.
   - **UI Scale:** `100%` (Zoom level: `100%`).
3. Position your character at any fishing spot (e.g. Mountain Lake, River, or Ocean).

> [!IMPORTANT]
> **Administrator Privileges:**
> Always open your terminal with **Run as Administrator**. Windows User Interface Privilege Isolation (UIPI) drops synthetic input and capture events if the game client runs at a higher integrity level than your command prompt.

---

## 2. Step 1: Validate Monitor Enumeration & Window Detection

Open an **Administrator PowerShell** terminal in `D:\projects\fisher` and run:

```powershell
fisher --check-monitors
```

### Expected Output:
- Displays table showing:
  - Index `0`: `\\.\DISPLAY1` (Laptop display)
  - Index `1`: `\\.\DISPLAY5` (Center primary display)
  - Index `2`: `\\.\DISPLAY6` (Right display)
- Detection banner:
  ```text
  [OK] 'Stardew Valley' window detected! HWND: <number> on Monitor Index: 2
  [OK] Running with Administrator privileges.
  ```

If the window is detected on Index `2`, the system is ready for DXGI Desktop Duplication on Screen 3.

---

## 3. Step 2: Calibrate the Fishing ROI & Track Geometry

With *Stardew Valley* running on Screen 3, execute the automated calibration utility:

```powershell
fisher --calibrate
```

### What this does:
1. Binds BetterCam DXGI output duplication to Screen 3 (`Device[0] Output[1]`).
2. Grabs the target frame and crops the candidate fishing ROI (`[1520, 220, 1900, 980]`).
3. Validates the 568 px vertical track channel (`TrackBounds: x0=72, y0=70, x1=116, y1=638`).
4. Generates or updates [`configs/capture_1080p.yaml`](file:///d:/projects/fisher/configs/capture_1080p.yaml).

### Running Calibration with a Saved Screenshot:
If you want to calibrate against an existing screenshot without having the game actively open:

```powershell
python scripts/calibrate.py --image path\to\screenshot.png
```

---

## 4. Step 3: Test Real-Time End-to-End Latency

Verify that the complete pipeline (Capture $\to$ CV Extraction $\to$ PPO Neural Network Inference $\to$ Mouse Actuation) operates within the real-time budget ($< 25\text{ ms}$ p99):

```powershell
fisher --bench-latency
```

### Expected Benchmark Output:
```text
Stage Latency Breakdown (milliseconds over 1000 samples):
Stage                  | Mean     | p50      | p90      | p95      | p99      | Max     
------------------------------------------------------------------------------
1. Capture (Buffer)    | 0.000    | 0.000    | 0.000    | 0.000    | 0.000    | 0.000   
2. Feature Extraction  | 1.500    | 1.378    | 2.027    | 2.245    | 2.584    | 5.859   
3. Policy Inference    | 0.327    | 0.284    | 0.486    | 0.555    | 0.736    | 1.616   
4. Actuator Dispatch   | 0.001    | 0.001    | 0.001    | 0.001    | 0.001    | 0.014   
------------------------------------------------------------------------------
Total E2E Pipeline     | 1.828    | 1.680    | 2.455    | 2.742    | 3.210    | 7.480   

Phase 2 Exit Gate (p99 < 25.0 ms): [PASS] (Achieved p99 = 3.210 ms, p50 = 1.680 ms)
```

---

## 5. Step 4: Record Live Fishing Minigame Telemetry (60 Hz)

When you are ready to fish in *Stardew Valley*, launch the live recorder. By default it runs for 60 seconds (or you can specify longer), and **you can press `Ctrl+C` anytime** when you finish catching the fish:

```powershell
# Records up to 60s of gameplay (press Ctrl+C when finished)
fisher --record

# Or specify a custom duration (e.g., 90 seconds):
fisher --record --record-duration 90.0
```

### Workflow:
1. Run `fisher --record` in your terminal.
2. Switch focus to *Stardew Valley*.
3. Cast your fishing rod and wait for the bite ("!" alert).
4. Left-click to hook the fish and play the minigame!
5. While recording, the terminal prints live feedback every 1 second:
   ```text
   [ 4.2s] Waiting for fishing minigame (UI inactive)...
   [12.0s] MINIGAME ACTIVE | Bar: 0.35 | Fish: 0.42 | Prog: 48% | IN-BAR
   [13.0s] MINIGAME ACTIVE | Bar: 0.38 | Fish: 0.40 | Prog: 54% | IN-BAR
   ```
6. When the catch dialog appears, press **`Ctrl+C`** in the terminal to stop recording early.
7. Telemetry will be saved to `reports/recordings/recording_<timestamp>.jsonl`.


### Testing Without the Game (Synthetic Demo):
To test the recording pipeline without launching *Stardew Valley*:

```powershell
fisher --record-synthetic --record-duration 5.0
```

---

## 6. Step 5: Run the Automated Verification Suite

Run all automated unit tests and golden-image benchmarks to ensure complete system health:

```powershell
pytest -v
```

Expected result: **42 passed** (capture drivers, CV extractors, lifecycle detectors, physics invariants, fish archetypes, and reward anti-stall guarantees).

---

## 7. Troubleshooting & FAQ

### Issue: BetterCam returns `_ctypes.COMError: Access is denied`
- **Cause:** Windows Desktop Duplication is blocked when the workstation is locked, when monitors are sleeping, or during a UAC secure desktop prompt.
- **Fix:** Ensure Windows is unlocked, your displays are active, and *Stardew Valley* is in the foreground on Screen 3.
- **Alternative:** You can switch the capture driver to Windows GDI fallback by editing [`configs/default.yaml`](file:///d:/projects/fisher/configs/default.yaml):
  ```yaml
  capture:
    driver: gdi   # fallback from bettercam to Win32 GDI BitBlt
  ```

### Issue: Game window is on the wrong display index
- If *Stardew Valley* is reported on Monitor Index `1` instead of `2`:
  1. Press `Shift + Win + Right Arrow` while focusing *Stardew Valley* to move it to Screen 3.
  2. Re-run `fisher --check-monitors` to verify it is hosted on `DISPLAY6` (Index `2`).

### Issue: Live minigame is not detected (`is_active: false`)
- Verify in game options that **UI Scale is 100%** and **Zoom Level is 100%**. Custom UI zoom shifts track coordinates relative to the 1080p frame.
- Re-run `fisher --calibrate` while the fishing minigame is open on screen.
