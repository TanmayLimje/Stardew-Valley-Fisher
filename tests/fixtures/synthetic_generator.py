"""Synthetic 1080p game frame generator based on decompiled BobberBar.cs drawing logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import cv2
import numpy as np

from fisher.extraction.types import TrackBounds


@dataclass
class SyntheticMinigameState:
    """Ground truth state parameters for synthetic frame generation."""
    bar_pos: float = 0.20          # b in [0, 1] (0 = bottom, 1 = top)
    bar_half_height: float = 0.0845  # h in [0, 1] (96 px / 568 px / 2)
    fish_pos: float = 0.25         # f in [0, 1]
    progress: float = 0.30         # p in [0, 1]
    is_active: bool = True         # Whether minigame UI is visible
    in_bar_flash: bool = False     # Whether bar is flashing white
    sparkles: bool = False         # Whether sparkles are present
    weather: str = "clear"         # "clear" | "night" | "rain"
    stamina_pct: float = 85.0      # Player stamina percentage
    is_night_cutoff: bool = False  # In-game clock >= 1:30 AM
    bite_cue: bool = False         # Exclamation mark '!' bite cue visible


def get_red_to_green_bgr(p: float) -> Tuple[int, int, int]:
    """Calculate BGR color matching Stardew Valley Utility.getRedToGreenLerpColor(distanceFromCatching)."""
    p = float(np.clip(p, 0.0, 1.0))
    if p < 0.5:
        # Interpolate Red (0, 0, 240) to Yellow (0, 230, 240)
        t = p * 2.0
        b = int(0)
        g = int(230.0 * t)
        r = 240
    else:
        # Interpolate Yellow (0, 230, 240) to Green (0, 220, 0)
        t = (p - 0.5) * 2.0
        b = int(0)
        g = 230
        r = int(240.0 * (1.0 - t))
    return (b, g, r)


class SyntheticFrameGenerator:
    """Generates ground-truth labeled 1080p and ROI frames for testing vision extraction."""

    def __init__(
        self,
        roi_width: int = 380,
        roi_height: int = 760,
        track_bounds: Optional[TrackBounds] = None,
    ) -> None:
        self.roi_w = roi_width
        self.roi_h = roi_height
        self.track_bounds = track_bounds or TrackBounds(
            x0=72,
            y0=70,
            x1=116,
            y1=638,
            height_px=568,
        )

    def generate_roi_frame(self, state: SyntheticMinigameState) -> Tuple[np.ndarray, dict]:
        """
        Generate a synthetic ROI frame (380x760) matching BobberBar.cs.
        Returns:
            (frame_bgr, ground_truth_dict)
        """
        # Base background (water / outdoor scene)
        if state.weather == "night":
            bg_color = [35, 20, 10]  # dark night water
        else:
            bg_color = [120, 75, 30]  # daytime lake water

        frame = np.full((self.roi_h, self.roi_w, 3), bg_color, dtype=np.uint8)

        # Add subtle water noise texture
        noise = np.random.randint(-10, 10, frame.shape, dtype=np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        tb = self.track_bounds
        track_span = float(tb.y1 - tb.y0)

        if not state.is_active:
            # Minigame not active; return scene with no UI
            return frame, {"is_active": False}

        # 1. Draw outer frame (wooden border around minigame)
        cv2.rectangle(frame, (tb.x0 - 8, tb.y0 - 12), (tb.x1 + 45, tb.y1 + 12), (30, 45, 90), -1)  # brown wood
        cv2.rectangle(frame, (tb.x0 - 8, tb.y0 - 12), (tb.x1 + 45, tb.y1 + 12), (10, 20, 45), 2)

        # 2. Draw track interior (dark slate/charcoal channel)
        cv2.rectangle(frame, (tb.x0, tb.y0), (tb.x1, tb.y1), (25, 20, 20), -1)
        cv2.rectangle(frame, (tb.x0, tb.y0), (tb.x1, tb.y1), (10, 10, 10), 1)

        # 3. Draw Bobber Bar (green paddle or white flash)
        # Convert state.bar_pos and state.bar_half_height to screen Y
        # y_norm = 1.0 - (y_screen - y0) / span  ==>  y_screen = y0 + (1.0 - y_norm) * span
        b_screen = tb.y0 + (1.0 - state.bar_pos) * track_span
        h_screen = state.bar_half_height * track_span
        bar_top = int(np.clip(b_screen - h_screen, tb.y0, tb.y1))
        bar_bot = int(np.clip(b_screen + h_screen, tb.y0, tb.y1))

        # Check if in-bar contact causes white flash
        in_bar = abs(state.fish_pos - state.bar_pos) <= state.bar_half_height
        is_flashing = state.in_bar_flash or in_bar

        bar_color = (245, 245, 245) if is_flashing else (45, 215, 60)  # white vs bright green
        cv2.rectangle(frame, (tb.x0 + 2, bar_top), (tb.x1 - 2, bar_bot), bar_color, -1)
        # Bar edge highlights
        edge_color = (255, 255, 255) if is_flashing else (80, 250, 100)
        cv2.rectangle(frame, (tb.x0 + 2, bar_top), (tb.x1 - 2, bar_bot), edge_color, 2)

        # 4. Draw Fish Icon
        f_screen = tb.y0 + (1.0 - state.fish_pos) * track_span
        fish_cx = (tb.x0 + tb.x1) // 2
        fish_cy = int(np.clip(f_screen, tb.y0 + 12, tb.y1 - 12))

        # Draw fish body (40x40 sprite footprint)
        fish_body_color = (20, 110, 220)  # Orange-red fish (standard carp/bass/salmon)
        cv2.ellipse(frame, (fish_cx, fish_cy), (14, 12), 0, 0, 360, fish_body_color, -1)
        cv2.ellipse(frame, (fish_cx, fish_cy), (14, 12), 0, 0, 360, (15, 45, 90), 2)  # dark outline
        # Eye & fin
        cv2.circle(frame, (fish_cx - 4, fish_cy - 3), 3, (255, 255, 255), -1)
        cv2.circle(frame, (fish_cx - 4, fish_cy - 3), 1, (0, 0, 0), -1)
        cv2.rectangle(frame, (fish_cx + 8, fish_cy - 4), (fish_cx + 14, fish_cy + 4), (15, 75, 180), -1)

        # 5. Draw Sparkles if enabled
        if state.sparkles:
            for _ in range(12):
                sx = np.random.randint(tb.x0, tb.x1)
                sy = int(np.random.normal(fish_cy, 20))
                if tb.y0 <= sy <= tb.y1:
                    frame[sy, sx] = [255, 255, 255]

        # 6. Draw Catch Progress Bar
        # Col centered at tb.x1 + 32, width 12 px, height 580 px
        prog_x0 = tb.x1 + 26
        prog_x1 = tb.x1 + 38
        prog_y0 = tb.y0 - 4
        prog_y1 = tb.y1 + 4
        prog_height = prog_y1 - prog_y0

        # Background channel of progress meter (dark backing)
        cv2.rectangle(frame, (prog_x0, prog_y0), (prog_x1, prog_y1), (30, 45, 90), -1)
        cv2.rectangle(frame, (prog_x0, prog_y0), (prog_x1, prog_y1), (15, 20, 40), 1)

        # Filled portion: height = prog_height * progress, drawn from bottom upwards
        fill_pixels = int(prog_height * state.progress)
        if fill_pixels > 0:
            fill_top = prog_y1 - fill_pixels
            fill_color = get_red_to_green_bgr(state.progress)
            cv2.rectangle(frame, (prog_x0 + 1, fill_top), (prog_x1 - 1, prog_y1 - 1), fill_color, -1)


        # Rain streaks if rainy
        if state.weather == "rain":
            for _ in range(80):
                rx = np.random.randint(0, self.roi_w)
                ry = np.random.randint(0, self.roi_h - 15)
                cv2.line(frame, (rx, ry), (rx - 2, ry + 12), (180, 160, 140), 1)

        gt = {
            "is_active": True,
            "bar_pos": state.bar_pos,
            "bar_half_height": state.bar_half_height,
            "fish_pos": state.fish_pos,
            "progress": state.progress,
            "in_bar": in_bar,
        }
        return frame, gt

    def generate_full_1080p_frame(self, state: SyntheticMinigameState) -> Tuple[np.ndarray, dict]:
        """Generate complete 1920x1080 frame with minigame ROI, stamina, clock, and bite cue."""
        frame = np.full((1080, 1920, 3), [100, 60, 25], dtype=np.uint8)

        # Draw ROI minigame at (1520, 220)
        roi_frame, gt = self.generate_roi_frame(state)
        frame[220 : 220 + self.roi_h, 1520 : 1520 + self.roi_w] = roi_frame

        # Draw Stamina Bar at bottom-right (1880, 920, 1908, 1050)
        stamina_h = 1050 - 920  # 130 px
        cv2.rectangle(frame, (1880, 920), (1908, 1050), (20, 20, 20), -1)
        fill_stamina_px = int((state.stamina_pct / 100.0) * stamina_h)
        if fill_stamina_px > 0:
            stamina_color = (40, 200, 40) if state.stamina_pct > 20 else (30, 30, 220)
            cv2.rectangle(frame, (1882, 1050 - fill_stamina_px), (1906, 1048), stamina_color, -1)

        # Draw Clock HUD at top-right (1720, 15, 1910, 120)
        cv2.rectangle(frame, (1720, 15), (1910, 120), (35, 30, 30), -1)
        clock_text_color = (0, 0, 240) if state.is_night_cutoff else (220, 220, 220)
        time_str = "1:40 am" if state.is_night_cutoff else "10:30 am"
        cv2.putText(frame, time_str, (1740, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, clock_text_color, 2)

        # Draw Bite Alert '!' cue if active (800, 400, 1120, 680)
        if state.bite_cue:
            cv2.circle(frame, (960, 520), 22, (255, 255, 255), -1)
            cv2.putText(frame, "!", (952, 530), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 240), 3)

        gt["stamina_pct"] = state.stamina_pct
        gt["is_night_cutoff"] = state.is_night_cutoff
        gt["bite_cue"] = state.bite_cue
        return frame, gt
