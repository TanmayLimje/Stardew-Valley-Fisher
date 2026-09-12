# Phase 3 — Live Client Transfer & Evaluation Guide

> **Welcome to Phase 3!** In Phase 1, we built a virtual 1D simulator and trained a neural network (PPO agent) to master the fishing minigame. In Phase 2, we built lightning-fast computer vision to track the green bobber bar and fish on screen in under 2 milliseconds.
>
> Now in **Phase 3**, we connect the trained neural brain to your physical mouse and play the **real *Stardew Valley* game client**!

---

## 1. What Happens in Phase 3? (The Big Picture)

Think of Phase 1 as an astronaut training in a flight simulator. Phase 3 is the astronaut's first real test flight:

```
 ┌────────────────┐         ┌───────────────────┐         ┌────────────────┐
 │ Screen 3       │         │ Computer Vision   │         │ PPO Brain      │
 │ Live Stardew   │ ──────> │ Tracks Green Bar  │ ──────> │ Decides: Hold  │
 │ Fishing Game   │  (DXGI) │ & Fish Centroid   │         │ or Release LMB │
 └────────────────┘         └───────────────────┘         └───────┬────────┘
         ▲                                                        │
         │                DirectInput Mouse Actuator              │
         └────────────────────────────────────────────────────────┘
```

1. **Watch (60 Hz):** The agent grabs the fishing minigame area on Screen 3 without lag using DirectX Desktop Duplication (`BetterCam`).
2. **See (OpenCV):** The computer vision calculates the exact position and velocity of both the green bar and the fish.
3. **Decide (30 Hz):** The trained neural network looks at the 9 numbers (positions, speeds, catch progress) and decides: **HOLD** or **RELEASE** the mouse button.
4. **Click (DirectInput):** It gently taps or holds the Left Mouse Button (LMB) just like a human player.

---

## 2. Pre-Flight Checklist (Do These Before Running!)

Before starting the live evaluation, ensure your game and computer are set up correctly:

| Requirement | Setting | Why It Matters |
|---|---|---|
| **Monitor Placement** | **Screen 3** (`\\.\DISPLAY6`) | Dedicated display for the game client. |
| **Game Window Mode** | **Borderless Windowed** (1920×1080) | Allows seamless DirectX screen capture. |
| **Game UI Zoom** | **100% Zoom** | Coordinates (`720, 150, 910, 800`) are calibrated for 100% scale. |
| **Terminal Privileges** | **Run as Administrator** | Windows UIPI blocks synthetic mouse clicks if the game runs elevated and the terminal doesn't. |
| **In-Game Setup** | Standing at river/lake, rod in hand | Ready to cast when the test starts. |
| **Safety Net** | **F9 Key** or **ESC** | Press anytime to immediately release the mouse and abort. |

---

## 3. Step-by-Step: How to Run Live Evaluation

Open **PowerShell** or **Windows Terminal** as **Administrator** and navigate to the project directory:

```powershell
cd D:\projects\fisher
```

### Step 1: Verify Hardware & Game Detection
First, verify that your multi-monitor layout is recognized:

```powershell
fisher --check-monitors
```
*Expected result:* You should see 3 connected displays (`DISPLAY1`, `DISPLAY5`, `DISPLAY6`), and green confirmation if *Stardew Valley* is open.

---

### Step 2: Run a Headless Dry-Run (Safe Practice Mode)
Before testing with the live game, you can run a 2-episode mock test. This runs the full pipeline with virtual hardware to confirm your Python environment is 100% healthy:

```powershell
fisher --eval-live --eval-mock --eval-episodes 2
```
*Expected result:* Both episodes complete with loop latency $< 2\text{ ms}$ and a green summary report.

---

### Step 3: Run the Real Live Evaluation!
Bring up *Stardew Valley* on Screen 3, stand by the water with your fishing rod equipped, and launch the evaluation:

```powershell
fisher --eval-live --eval-episodes 5
```

> [!TIP]
> **Dynamic Screen Localization:** The agent automatically scans the entire Screen 3 display to locate the minigame widget wherever your character stands and regardless of whether they face Left, Right, Up, or Down.
>
> If you are playing with an Xbox / PlayStation gamepad or prefer that window focus shifts don't abort your game, add `--no-require-foreground`:
> ```powershell
> fisher --eval-live --eval-episodes 5 --no-require-foreground
> ```

#### What you will see:
1. The terminal will say:
   ```
   Episode 1/5:
   > Cast your fishing rod. Waiting for minigame UI to appear on Screen 3...
   ```
2. **You cast your rod** and click when the fish bites ("!" alert).
3. The moment the mini-game bar appears, the agent automatically locks onto its position and engages:
   ```
   Minigame detected! Control loop engaged @ 30 Hz...
   ```
4. The agent will smoothly micro-adjust the green bar to keep the fish centered until the fish is caught!
5. When the minigame ends, the agent automatically releases the mouse and reports the outcome:
   ```
   Episode 1 finished: CATCH | Duration: 6.42s | In-Bar: 97.4% | Progress: 100.0%
   ```
6. Click through the caught fish dialog, cast again, and repeat for the episodes!

---

### Step 4 (Optional): Compare Against the Rule-Based Baseline
Want to see how much smarter the RL neural network is compared to a simple script? Run the Bang-Bang baseline (which just holds LMB if the fish is above the bar):

```powershell
fisher --eval-live --eval-baseline --eval-episodes 20
```
*Note:* You'll notice the scripted baseline bounces violently against the boundaries and loses erratic fish, while the PPO neural network anticipates bounces and applies smooth braking.

---

## 4. Emergency Killswitch & Safety Guards

Your safety and game integrity are guaranteed by multiple guardrails:

- **Global Killswitch (F9):** Press **`F9`** on your keyboard at any moment. The agent will instantly release the left mouse button and terminate in $< 200\text{ ms}$.
- **Game Cancel Primitive (ESC):** Pressing `ESC` triggers Stardew Valley's native `emergencyShutDown`, closing the fishing minigame with zero penalty.
- **Foreground Safety Lock:** If you Alt-Tab away from *Stardew Valley* (e.g. to browse the web or edit code), the actuator **immediately releases the mouse** so clicks will never leak onto your desktop or other screens.

---

## 5. How to Read Your Results

At the end of the session, a structured table is printed in your terminal and saved as a JSON report in `reports/live_eval/`:

```
                       Live Transfer Evaluation Summary
┌────────────────────┬─────────────────────┬────────────────────┬─────────────┐
│ Metric             │ Achieved Live Value │ Phase 3 Gate / Sim │   Status    │
├────────────────────┼─────────────────────┼────────────────────┼─────────────┤
│ Total Episodes     │                  20 │              >= 20 │     OK      │
│ Live Catch Rate    │               90.0% │           >= 80.0% │    PASS     │
│ Mean In-Bar Ratio  │               94.2% │           >= 80.0% │     OK      │
│ Mean Duration      │              6.80 s │           < 20.0 s │     OK      │
│ Sim-to-Real Gap    │            -0.8 pts │           < 5.0 pts│   OPTIMAL   │
│ Loop Latency (p99) │             2.81 ms │      p99 < 25.0 ms │    PASS     │
└────────────────────┴─────────────────────┴────────────────────┴─────────────┘
```

### Key Metrics Explained:
- **Live Catch Rate (`CR_live`):** The percentage of hooked fish successfully reeled in (Target: $\ge 80\%$).
- **Mean In-Bar Ratio:** What percentage of the time the green bar stayed directly on top of the fish (typically $90\%+$).
- **Sim-to-Real Gap:** The difference between simulator catch rate ($89.2\%$) and real-game catch rate:
  - **$< 5.0\text{ pts}$ (Optimal):** Policy transferred flawlessly from simulation to real life! Ready for Phase 4 (full autonomy).
  - **$5.0 - 20.0\text{ pts}$ (Recalibrate):** Small timing or friction mismatch. We can tune simulation constants from the recorded JSONL telemetry and re-train in 10 minutes.
  - **$> 20.0\text{ pts}$ (Debug):** Perception issue (e.g. game resolution was not 100% UI zoom or wrong monitor).
- **Loop Latency (p99):** Time taken to process each frame and dispatch mouse clicks (Target: $< 25.0\text{ ms}$; Fisher achieves $\sim 2.8\text{ ms}$).

---

## 6. Common Questions & Troubleshooting

### Q: The terminal says "Running in standard user mode [WARN]".
- **Fix:** Right-click your Windows Terminal or PowerShell icon and select **"Run as Administrator"**. If Stardew Valley runs with admin rights, Windows blocks regular programs from sending mouse clicks to it.

### Q: The agent doesn't react when the minigame starts.
- **Check 1:** Is the game on **Screen 3** (`\\.\DISPLAY6`)? Run `fisher --check-monitors` to verify.
- **Check 2:** Is the game resolution **1920×1080** with **100% UI zoom**? (In Stardew options: Zoom Level 100%, UI Scale 100%).
- **Check 3:** Are you fishing in the Pelican Town river? If you fish in a radically different UI layout, run `fisher --calibrate` to adjust the bounding boxes.

### Q: Where are the logs stored?
- Every tick of every episode is saved in `reports/live_eval/` as synchronized JSONL files containing timestamps, mouse actions, bar positions, and latencies.

---

**You are now ready to fish! Proceed with Step 1 and Step 3 above.**
