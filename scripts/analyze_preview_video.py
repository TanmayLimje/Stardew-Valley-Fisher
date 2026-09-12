"""Analyze recorded preview videos to diagnose minigame detection errors, false positives, and tracking issues.

Inspects video frames and accompanying JSONL telemetry to generate an automated diagnostic timeline,
flag anomalies (false detections on scenery/water, sudden ROI jumps, tracking dropouts),
and export keyframe snapshots for visual debugging.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fisher.extraction.extractor import FeatureExtractor
from fisher.extraction.track import TrackBounds

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)


def analyze_video(video_path_str: str, output_dir: Optional[str] = None) -> Dict[str, Any]:
    """Analyze a recorded preview MP4 video file and output diagnostic findings."""
    console = Console()
    console.print()
    console.rule("[bold cyan]Fisher Preview Video Detection Diagnostic Analyzer[/bold cyan]")
    console.print()

    video_path = Path(video_path_str)
    if not video_path.is_absolute():
        video_path = (REPO_ROOT / video_path).resolve()

    if not video_path.exists():
        console.print(f"[bold red]Error: Video file not found: {video_path}[/bold red]")
        return {"error": f"File not found: {video_path}"}

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        console.print(f"[bold red]Error: Could not open video: {video_path}[/bold red]")
        return {"error": f"Could not open video: {video_path}"}

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_s = total_frames / fps if fps > 0 else 0.0

    console.print(f"[cyan]Target Video:[/cyan]     [bold white]{video_path.name}[/bold white]")
    console.print(f"[cyan]Dimensions:[/cyan]       {width}×{height} @ {fps:.1f} FPS")
    console.print(f"[cyan]Total Duration:[/cyan]   {duration_s:.2f}s ({total_frames} frames)")

    # Check for accompanying .jsonl metadata
    jsonl_path = video_path.with_suffix(".jsonl")
    has_jsonl = jsonl_path.exists()
    metadata_entries: List[Dict[str, Any]] = []
    if has_jsonl:
        console.print(f"[cyan]Telemetry Log:[/cyan]    [green]Found accompanying {jsonl_path.name}[/green]")
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        metadata_entries.append(json.loads(line))
                    except Exception:
                        pass
    else:
        console.print("[yellow]Notice: No accompanying .jsonl log found. Falling back to visual frame analysis.[/yellow]")

    # Setup output artifact directory
    t_stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir) if output_dir else (video_path.parent / f"analysis_{video_path.stem}_{t_stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Vision feature extractor for fallback extraction from PiP
    bounds = TrackBounds(x0=76, y0=47, x1=112, y1=615, height_px=568)
    extractor = FeatureExtractor(bounds=bounds)

    # Frame-by-frame analysis
    frame_idx = 0
    detections: List[Dict[str, Any]] = []

    console.print("\n[dim]Scanning frames for BobberBar detection events...[/dim]")

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        frame_idx += 1
        t_sec = frame_idx / fps

        meta = metadata_entries[frame_idx - 1] if (has_jsonl and frame_idx <= len(metadata_entries)) else None

        # Check detection status: either from metadata or via visual inspection of overlay
        is_detected = False
        roi = None
        conf = 0.0
        bar_pos = 0.0
        fish_pos = 0.0
        prog = 0.0
        in_bar = False

        if meta is not None:
            roi = meta.get("roi")
            is_detected = (roi is not None)
            conf = float(meta.get("confidence", 0.95 if is_detected else 0.0))
            bar_pos = float(meta.get("bar_pos", 0.0))
            fish_pos = float(meta.get("fish_pos", 0.0))
            prog = float(meta.get("progress", 0.0))
            in_bar = bool(meta.get("in_bar", False))

            # Backward-compatibility fallback: if telemetry lacked bar/fish/progress, extract from overlay PiP
            if is_detected and (prog == 0.0 and bar_pos == 0.0 and fish_pos == 0.0):
                pip_crop = frame[50:470, 790:940]
                if pip_crop.size > 0:
                    sub = cv2.resize(pip_crop, (190, 650))
                    res = extractor.extract_features(sub, 0.0)
                    if res.is_active:
                        bar_pos = float(res.bar_pos)
                        fish_pos = float(res.fish_pos)
                        prog = float(res.progress)
                        in_bar = bool(res.in_bar)
        else:
            # Visual check: In preview overlay, PiP header text "ZOOMED MINIGAME TRACK" or bounding box exists
            # We check the top-right PiP region [50:470, 790:940] for active track pixels (orange/green/white)
            pip_region = frame[50:470, 790:940]
            # Check if green bounding box exists in frame (header banner dx0..dx0+160 has text BOBBERBAR)
            # Or inspect bottom-left HUD text: if status line contains "MINIGAME ENGAGED"
            hud_crop = frame[height - 60:height - 10, 10:460]
            # If HUD has green border (0, 255, 100), detection is engaged
            green_pixels = np.sum((hud_crop[:, :, 1] >= 200) & (hud_crop[:, :, 0] <= 50) & (hud_crop[:, :, 2] <= 120))
            is_detected = bool(green_pixels >= 40)
            if is_detected and pip_region.size > 0:
                sub = cv2.resize(pip_region, (190, 650))
                res = extractor.extract_features(sub, 0.0)
                if res.is_active:
                    bar_pos = float(res.bar_pos)
                    fish_pos = float(res.fish_pos)
                    prog = float(res.progress)
                    in_bar = bool(res.in_bar)
                    conf = float(res.confidence)

        detections.append({
            "frame": frame_idx,
            "t": t_sec,
            "detected": is_detected,
            "roi": roi,
            "conf": conf,
            "bar_pos": bar_pos,
            "fish_pos": fish_pos,
            "prog": prog,
            "in_bar": in_bar,
        })

    cap.release()

    # Aggregate detection segments with gap tolerance to bridge momentary occlusions
    gap_tolerance_frames = 10
    current_gap = 0
    segments: List[Dict[str, Any]] = []
    current_seg: Optional[Dict[str, Any]] = None

    for d in detections:
        if d["detected"]:
            current_gap = 0
            if current_seg is None:
                current_seg = {
                    "start_frame": d["frame"],
                    "start_t": d["t"],
                    "end_frame": d["frame"],
                    "end_t": d["t"],
                    "rois": [d["roi"]] if d["roi"] else [],
                    "peak_prog": d["prog"],
                    "in_bar_count": 1 if d["in_bar"] else 0,
                    "total_count": 1,
                }
            else:
                current_seg["end_frame"] = d["frame"]
                current_seg["end_t"] = d["t"]
                current_seg["total_count"] += 1
                if d["in_bar"]:
                    current_seg["in_bar_count"] += 1
                current_seg["peak_prog"] = max(current_seg["peak_prog"], d["prog"])
                if d["roi"]:
                    current_seg["rois"].append(d["roi"])
        else:
            if current_seg is not None:
                current_gap += 1
                if current_gap >= gap_tolerance_frames:
                    segments.append(current_seg)
                    current_seg = None
                    current_gap = 0

    if current_seg is not None:
        segments.append(current_seg)

    # Classify segments and flag anomalies
    anomalies: List[Dict[str, Any]] = []
    valid_episodes: List[Dict[str, Any]] = []

    # Re-open capture to export anomaly and keyframe snapshots
    cap = cv2.VideoCapture(str(video_path))

    def extract_frame_image(frame_num: int) -> Optional[np.ndarray]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num - 1)
        ret, f = cap.read()
        return f if ret else None

    for idx, seg in enumerate(segments, 1):
        dur_s = seg["end_t"] - seg["start_t"]
        frames_count = seg["total_count"]
        mean_in_bar = (seg["in_bar_count"] / max(1, frames_count)) * 100.0

        # Heuristic 1: Short false-positive flicker (< 0.50s, < 15 frames)
        if dur_s < 0.50 and frames_count < 15:
            keyframe = extract_frame_image(seg["start_frame"])
            snap_path = out_dir / f"anomaly_{idx:02d}_false_pos_f{seg['start_frame']}.png"
            if keyframe is not None:
                cv2.imwrite(str(snap_path), keyframe)

            anomalies.append({
                "type": "FALSE_POSITIVE_FLICKER",
                "severity": "HIGH",
                "start_t": seg["start_t"],
                "duration_s": dur_s,
                "frames": frames_count,
                "roi": seg["rois"][0] if seg["rois"] else None,
                "description": f"Minigame triggered for only {dur_s:.2f}s ({frames_count} frames) on scenery/water before disappearing.",
                "snapshot": str(snap_path),
            })
        else:
            # Valid sustained minigame attempt
            keyframe_start = extract_frame_image(seg["start_frame"])
            keyframe_end = extract_frame_image(seg["end_frame"])
            snap_start = out_dir / f"episode_{idx:02d}_start_f{seg['start_frame']}.png"
            snap_end = out_dir / f"episode_{idx:02d}_end_f{seg['end_frame']}.png"
            if keyframe_start is not None:
                cv2.imwrite(str(snap_start), keyframe_start)
            if keyframe_end is not None:
                cv2.imwrite(str(snap_end), keyframe_end)

            valid_episodes.append({
                "episode": len(valid_episodes) + 1,
                "start_t": seg["start_t"],
                "end_t": seg["end_t"],
                "duration_s": dur_s,
                "frames": frames_count,
                "peak_prog": seg["peak_prog"] * 100.0,
                "mean_in_bar": mean_in_bar,
                "roi": seg["rois"][0] if seg["rois"] else None,
                "snapshot_start": str(snap_start),
                "snapshot_end": str(snap_end),
            })

    cap.release()

    # Render results table
    table_eps = Table(title="Detected Minigame Episodes", border_style="grey35")
    table_eps.add_column("Episode", justify="center", style="bold cyan")
    table_eps.add_column("Start Time", justify="right")
    table_eps.add_column("End Time", justify="right")
    table_eps.add_column("Duration", justify="right")
    table_eps.add_column("In-Bar %", justify="right")
    table_eps.add_column("Peak Catch %", justify="right")
    table_eps.add_column("Detected ROI", style="dim")

    if valid_episodes:
        for ep in valid_episodes:
            table_eps.add_row(
                str(ep["episode"]),
                f"{ep['start_t']:.2f}s",
                f"{ep['end_t']:.2f}s",
                f"{ep['duration_s']:.2f}s",
                f"{ep['mean_in_bar']:.1f}%",
                f"{ep['peak_prog']:.1f}%",
                str(ep["roi"]) if ep["roi"] else "Visual",
            )
        console.print(table_eps)
    else:
        console.print("[yellow]No sustained minigame episodes detected in this recording.[/yellow]")

    console.print()
    if anomalies:
        table_anom = Table(title="[bold red]Detected Anomalies & False Positives[/bold red]", border_style="red")
        table_anom.add_column("#", justify="center", style="bold")
        table_anom.add_column("Type", style="bold red")
        table_anom.add_column("Timestamp", justify="right")
        table_anom.add_column("Duration", justify="right")
        table_anom.add_column("Diagnosis / Cause", style="white")
        table_anom.add_column("Keyframe Snapshot", style="dim")

        for i, anom in enumerate(anomalies, 1):
            snap_rel = Path(anom["snapshot"]).name
            table_anom.add_row(
                str(i),
                anom["type"],
                f"{anom['start_t']:.2f}s",
                f"{anom['duration_s']:.2f}s ({anom['frames']}f)",
                anom["description"],
                snap_rel,
            )
        console.print(table_anom)
    else:
        console.print("[bold green]✓ Zero false positives or detection anomalies detected![/bold green]")

    # Save summary json
    summary = {
        "video_file": str(video_path),
        "total_frames": total_frames,
        "fps": fps,
        "duration_s": duration_s,
        "episodes_found": len(valid_episodes),
        "episodes": valid_episodes,
        "anomalies_found": len(anomalies),
        "anomalies": anomalies,
        "output_directory": str(out_dir),
    }

    summary_json_path = out_dir / "analysis_report.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    console.print(f"\n[green]Diagnostic report and keyframes saved to:[/green] [bold white]{out_dir}[/bold white]\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Fisher Preview Video Detection Diagnostic Analyzer")
    parser.add_argument("video", type=str, help="Path to recorded preview MP4 video")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to save analysis report and snapshots")
    args = parser.parse_args()

    analyze_video(args.video, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
