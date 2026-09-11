"""60 Hz live and simulated minigame recorder logging telemetry tuples to JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Optional
import numpy as np


# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fisher.capture import create_capture_driver
from fisher.config import load_config
from fisher.extraction.extractor import FeatureExtractor
from tests.fixtures.synthetic_generator import SyntheticFrameGenerator, SyntheticMinigameState


def record_session(
    output_path: Optional[str] = None,
    max_duration_s: float = 10.0,
    synthetic_mode: bool = False,
    target_hz: float = 60.0,
) -> str:
    cfg = load_config()
    period = 1.0 / target_hz

    out_dir = Path("reports/recordings")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not output_path:
        ts_str = time.strftime("%Y%m%d_%H%M%S")
        output_path = str(out_dir / f"recording_{ts_str}.jsonl")

    print(f"\n=======================================================")
    print(f"Starting 60 Hz Minigame Recorder -> {output_path}")
    print(f"Duration: {max_duration_s:.1f}s | Mode: {'Synthetic' if synthetic_mode else 'Live Capture'}")
    print(f"=======================================================")

    bounds = None
    if isinstance(cfg.capture, dict) and "track_bounds" in cfg.capture:
        from fisher.extraction.types import TrackBounds
        bounds = TrackBounds(**cfg.capture["track_bounds"])
    extractor = FeatureExtractor(bounds=bounds)
    driver = None
    gen = None

    if synthetic_mode:
        gen = SyntheticFrameGenerator()
    else:
        driver = create_capture_driver(cfg)
        driver.start()
        time.sleep(0.2)

    records = []
    start_time = time.perf_counter()
    next_tick = start_time
    tick = 0

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            while time.perf_counter() - start_time < max_duration_s:
                now = time.perf_counter()
                elapsed = now - start_time

                frame = None
                frame_ts = now

                if synthetic_mode:
                    # Dynamic moving fish and bar for demonstration
                    b = 0.30 + 0.20 * np.sin(elapsed * 2.0)
                    fish_p = 0.40 + 0.30 * np.sin(elapsed * 3.5)
                    prog = min(1.0, 0.30 + elapsed * 0.05)
                    state = SyntheticMinigameState(
                        bar_pos=float(b),
                        fish_pos=float(fish_p),
                        progress=float(prog),
                    )
                    frame, _ = gen.generate_roi_frame(state)
                else:
                    assert driver is not None
                    frame, frame_ts = driver.get_latest_frame()

                if frame is not None:
                    res = extractor.extract_features(frame, timestamp=frame_ts)
                    row = {
                        "tick": tick,
                        "timestamp": frame_ts,
                        "elapsed_s": round(elapsed, 4),
                        "is_active": res.is_active,
                        "bar_pos": round(res.bar_pos, 4),
                        "bar_height": round(res.bar_height, 4),
                        "bar_vel": round(res.bar_vel, 4),
                        "fish_pos": round(res.fish_pos, 4),
                        "fish_vel": round(res.fish_vel, 4),
                        "progress": round(res.progress, 4),
                        "in_bar": bool(res.in_bar),
                        "confidence": round(res.confidence, 4),
                    }
                    f.write(json.dumps(row) + "\n")
                    records.append(row)
                    tick += 1

                    # Live terminal feedback every 60 ticks (~1 second)
                    if tick % 60 == 0:
                        if res.is_active:
                            in_bar_str = "[green]IN-BAR[/green]" if res.in_bar else "[red]OUT[/red]"
                            print(f"[{elapsed:4.1f}s] MINIGAME ACTIVE | Bar: {res.bar_pos:.2f} | Fish: {res.fish_pos:.2f} | Prog: {res.progress*100:.0f}% | {in_bar_str}")
                        else:
                            print(f"[{elapsed:4.1f}s] Waiting for fishing minigame (UI inactive)...")

                next_tick += period
                sleep_t = next_tick - time.perf_counter()
                if sleep_t > 0.001:
                    time.sleep(sleep_t - 0.0005)
                elif sleep_t < -period:
                    next_tick = time.perf_counter()
    except KeyboardInterrupt:
        print("\n[INFO] Recording stopped by user (Ctrl+C). Saving captured frames...")
    finally:
        if driver is not None:
            driver.stop()

    actual_duration = max(0.1, time.perf_counter() - start_time)
    print(f"[SUCCESS] Recorded {len(records)} frames to '{output_path}' ({len(records)/actual_duration:.1f} FPS)")
    return output_path



if __name__ == "__main__":
    import numpy as np

    parser = argparse.ArgumentParser(description="Fisher Telemetry Recorder")
    parser.add_argument("--output", type=str, default=None, help="Output JSONL path")
    parser.add_argument("--duration", type=float, default=5.0, help="Recording duration in seconds")
    parser.add_argument("--synthetic", action="store_true", help="Record using synthetic generator")
    args = parser.parse_args()

    record_session(
        output_path=args.output,
        max_duration_s=args.duration,
        synthetic_mode=args.synthetic,
    )
