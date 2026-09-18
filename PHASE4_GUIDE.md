# Phase 4 — On-Demand Fishing Assistant Player's Guide

> **Play Stardew Valley normally while Fisher catches every fish for you!**
> 
> You do the walking, farming, talking, rod casting, and hook clicking. The moment the BobberBar minigame appears, Fisher takes over mouse control, tracks the fish with the neural network, reels it in, and immediately hands control back to you.

---

## 1. Quick Start Commands

Run from an **Administrator PowerShell** terminal in `D:\projects\fisher`:

```powershell
# Standard Mode (Recommended for normal gameplay):
fisher --assist

# Preview Mode (Displays live computer-vision tracking HUD on Screen 2):
fisher --assist --preview

# Offline Practice / Mock Test (Tests everything without opening the game):
fisher --assist --mock
```

---

## 2. Pre-Flight Setup Checklist

Before you start fishing, verify the following:

| Setting | Required Value | Why It Matters |
|---|---|---|
| **Game Screen** | **Screen 3** (`\\.\DISPLAY6`) | The DXGI capture engine looks for the game window on Screen 3. |
| **Window Mode** | **Borderless Windowed** (1920×1080) | Zero-copy DirectX Desktop Duplication requires borderless mode. |
| **Game UI Zoom** | **100% Zoom** | Computer vision tracking bounding boxes are pixel-calibrated for 100% scale. |
| **PowerShell Elevation** | **Run as Administrator** | Windows UIPI security prevents synthetic mouse clicks from reaching the game unless the terminal is elevated. |
| **Safety Hotkey** | **F9** | Press anytime to instantly disconnect the bot and release the mouse. |

---

## 3. How to Play (Step-by-Step)

```
       [YOU PLAY NORMALLY]                          [AI ASSISTANT]
  1. Walk to water & cast rod
  2. Wait for bite "!" cue
  3. Click LMB to hook fish
                                          4. BobberBar appears!
                                             AI takes over mouse in < 200 ms
                                             Keeps fish centered at 30 Hz
                                          5. Fish caught (100% progress)!
                                             AI releases mouse immediately
  6. Click to dismiss catch dialog
  7. Repeat as many times as you like!
```

### Detailed Walkthrough:
1. **Launch Stardew Valley:** Make sure it is borderless 1080p on Screen 3.
2. **Open Terminal as Administrator:**
   - Right-click PowerShell -> *Run as administrator*.
   - Navigate: `cd D:\projects\fisher`
3. **Start the Assistant:**
   ```powershell
   fisher --assist
   ```
   You will see:
   ```
   [Assistant] Started in IDLE mode. Watching Screen 3 at 10.0 Hz.
   [Assistant] Play Stardew Valley normally. When you hook a fish, AI takes over!
   ```
4. **Cast your Fishing Rod:** Go to any river, lake, or ocean in Stardew Valley and cast.
5. **Hook the Fish:** When the exclamation mark `"!"` appears and the bobber splashes, click LMB to hook it.
6. **Watch the AI Catch It:** The BobberBar minigame UI pops up. Within $< 200\text{ ms}$, Fisher detects the widget, switches to `RL_ACTIVE`, and begins pulsing the left mouse button at 30 Hz using the trained PPO policy to keep the green paddle over the fish.
7. **Resume Play:** Once the green progress meter hits 100% (or the fish escapes), Fisher instantly releases mouse LMB and drops back to passive `IDLE` mode. Click OK on the item popup and continue playing!

---

## 4. Safety Controls & Hotkeys

- **`F9` (Emergency Killswitch):** Instantly releases the mouse LMB, disengages control in $< 200\text{ ms}$, and cleanly stops the assistant.
- **`Ctrl+F9` or `Ctrl+C`:** Hard exit the assistant process.
- **`[Q]` or `ESC` (in Preview window):** Closes the OpenCV preview window without aborting the assistant.

---

## 5. Session Statistics

When you terminate the assistant (`Ctrl+C` or `F9`), a summary of your fishing session is printed:

```
[Assistant] Session Summary:
  - Total Minigames Played: 8
  - Catches: 8
  - Escapes: 0
  - Win Rate: 100.0%
  - Average Minigame Duration: 7.4s
```
