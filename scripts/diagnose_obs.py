"""Diagnose live evaluation telemetry for observation pipeline issues.

Analyzes JSONL telemetry files produced by eval_live.py and flags:
- Frozen bar/fish positions (stale extractor values)
- Physically impossible progress drops (faster than game drain rate)
- Monotonic action outputs (policy stuck on all-hold or all-release)
- Track detection failures (is_active=False during active minigame)

Usage:
    python scripts/diagnose_obs.py [reports/live_eval/eval_live_*.jsonl ...]
    python scripts/diagnose_obs.py --latest 10   # Analyze last 10 session files
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports" / "live_eval"


def analyze_episode(filepath: Path) -> dict:
    """Analyze a single episode JSONL file for observation pipeline bugs."""
    with open(filepath) as f:
        steps = [json.loads(line) for line in f if line.strip()]

    if not steps:
        return {"file": filepath.name, "steps": 0, "issues": ["EMPTY FILE"]}

    n = len(steps)
    issues = []

    # --- Frozen values detection ---
    bar_vals = [round(r["bar_pos"], 4) for r in steps]
    fish_vals = [round(r["fish_pos"], 4) for r in steps]
    unique_bar = len(set(bar_vals))
    unique_fish = len(set(fish_vals))

    if unique_bar <= 2 and n > 5:
        issues.append(f"BAR FROZEN at {bar_vals[0]} ({unique_bar} unique across {n} steps)")
    if unique_fish <= 2 and n > 5:
        issues.append(f"FISH FROZEN at {fish_vals[0]} ({unique_fish} unique across {n} steps)")

    # Check for specific known-bad default values
    if bar_vals[0] == 0.085 and fish_vals[0] == 0.1:
        issues.append("VALUES ARE EXTRACTOR DEFAULTS -> track was never detected (is_active=False)")
    elif unique_bar <= 2 and bar_vals[0] > 0.5:
        issues.append(f"BAR STUCK AT STALE VALUE {bar_vals[0]} (likely leaked from previous episode)")

    # --- Progress physics violations ---
    prog_list = [r["progress"] for r in steps]
    max_drop = 0.0
    drop_step = -1
    for i in range(1, len(prog_list)):
        drop = prog_list[i - 1] - prog_list[i]
        if drop > max_drop:
            max_drop = drop
            drop_step = i + 1

    if max_drop > 0.02:  # Physics max drain: 0.006/step
        issues.append(
            f"IMPOSSIBLE PROGRESS DROP {max_drop:.3f} at step {drop_step} "
            f"(max physics drain is 0.006/step)"
        )

    # --- Monotonic action output ---
    actions = [r["action"] for r in steps]
    act0 = actions.count(0)
    act1 = actions.count(1)
    if act0 == n and n > 5:
        issues.append(f"ALL RELEASE ({n} steps) -> policy sees bar above fish, never holds")
    elif act1 == n and n > 5:
        issues.append(f"ALL HOLD ({n} steps) -> policy sees bar below fish, always holds")

    # --- Track detection failures ---
    if "is_active" in steps[0]:
        inactive_count = sum(1 for r in steps if not r.get("is_active", True))
        if inactive_count > 0:
            issues.append(
                f"TRACK NOT DETECTED on {inactive_count}/{n} steps "
                f"({inactive_count / n * 100:.0f}% inactive)"
            )

    # --- In-bar ratio ---
    in_bar_count = sum(1 for r in steps if r["in_bar"])
    in_bar_pct = in_bar_count / n * 100

    # --- Duration ---
    duration = steps[-1].get("t", 0.0) if steps else 0.0

    return {
        "file": filepath.name,
        "steps": n,
        "duration_s": round(duration, 2),
        "bar_range": f"{min(bar_vals):.3f}-{max(bar_vals):.3f}",
        "fish_range": f"{min(fish_vals):.3f}-{max(fish_vals):.3f}",
        "unique_bar": unique_bar,
        "unique_fish": unique_fish,
        "prog_start": round(prog_list[0], 3),
        "prog_end": round(prog_list[-1], 3),
        "max_prog_drop": round(max_drop, 3),
        "in_bar_pct": round(in_bar_pct, 1),
        "action_hold_pct": round(act1 / n * 100, 1) if n else 0,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description="Diagnose live eval telemetry for obs pipeline bugs")
    parser.add_argument("files", nargs="*", help="JSONL telemetry files to analyze")
    parser.add_argument("--latest", type=int, default=0, help="Analyze the N most recent JSONL files")
    parser.add_argument("--all", action="store_true", help="Analyze all JSONL files in reports/live_eval")
    args = parser.parse_args()

    files = []
    if args.files:
        files = [Path(f) for f in args.files if Path(f).exists()]
    elif args.latest > 0 or args.all:
        all_jsonl = sorted(REPORTS_DIR.glob("eval_live_*.jsonl"))
        # Filter to substantial files only (> 500 bytes)
        substantial = [f for f in all_jsonl if f.stat().st_size > 500]
        if args.all:
            files = substantial
        else:
            files = substantial[-args.latest:]
    else:
        # Default: last 15
        all_jsonl = sorted(REPORTS_DIR.glob("eval_live_*.jsonl"))
        substantial = [f for f in all_jsonl if f.stat().st_size > 500]
        files = substantial[-15:]

    if not files:
        print("No telemetry files found. Run 'fisher --eval-live' first.")
        sys.exit(1)

    print(f"Analyzing {len(files)} telemetry files...\n")

    total_issues = 0
    categories = {"healthy": 0, "frozen": 0, "prog_drop": 0, "all_release": 0, "all_hold": 0, "inactive": 0}

    for filepath in files:
        result = analyze_episode(filepath)

        # Categorize
        issue_str = " ".join(result["issues"])
        if not result["issues"]:
            categories["healthy"] += 1
        if "FROZEN" in issue_str or "STALE" in issue_str or "DEFAULTS" in issue_str:
            categories["frozen"] += 1
        if "IMPOSSIBLE PROGRESS" in issue_str:
            categories["prog_drop"] += 1
        if "ALL RELEASE" in issue_str:
            categories["all_release"] += 1
        if "ALL HOLD" in issue_str:
            categories["all_hold"] += 1
        if "TRACK NOT DETECTED" in issue_str:
            categories["inactive"] += 1

        # Print
        status = "OK" if not result["issues"] else "ISSUES"
        print(f"[{status:6s}] {result['file']}: "
              f"{result['steps']:4d} steps, {result['duration_s']:5.1f}s | "
              f"bar={result['bar_range']} ({result['unique_bar']} uniq) | "
              f"fish={result['fish_range']} ({result['unique_fish']} uniq) | "
              f"prog={result['prog_start']}->{result['prog_end']} | "
              f"inbar={result['in_bar_pct']:4.0f}% | hold={result['action_hold_pct']:4.0f}%")
        for issue in result["issues"]:
            print(f"         ** {issue}")
        total_issues += len(result["issues"])

    print(f"\n{'=' * 80}")
    print(f"SUMMARY: {len(files)} episodes analyzed, {total_issues} issues found")
    print(f"  Healthy:         {categories['healthy']}")
    print(f"  Frozen obs:      {categories['frozen']}")
    print(f"  Progress drops:  {categories['prog_drop']}")
    print(f"  All-release:     {categories['all_release']}")
    print(f"  All-hold:        {categories['all_hold']}")
    print(f"  Track inactive:  {categories['inactive']}")


if __name__ == "__main__":
    main()
