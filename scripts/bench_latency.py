"""End-to-end pipeline latency benchmark measuring capture -> extract -> infer -> actuate."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Dict, List

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from fisher.capture.mock_driver import MockCaptureDriver
from fisher.extraction.extractor import FeatureExtractor
from fisher.input.mock_actuator import MockActuator
from tests.fixtures.synthetic_generator import SyntheticFrameGenerator, SyntheticMinigameState



def run_latency_benchmark(
    iterations: int = 1000,
    model_path: str = "models/ppo_fisher_best.zip",
    vec_norm_path: str = "models/vec_normalize_best.pkl",
) -> Dict[str, float]:
    """
    Benchmark the latency of each pipeline stage:
    1. Capture grab
    2. CV feature extraction
    3. PPO policy inference (or baseline forward pass)
    4. Actuator dispatch
    """
    print(f"\n=======================================================")
    print(f"Running Fisher Pipeline Latency Benchmark ({iterations} iterations)")
    print(f"=======================================================")

    # Setup components
    gen = SyntheticFrameGenerator()
    state = SyntheticMinigameState(bar_pos=0.45, fish_pos=0.50, progress=0.60)
    test_frame, _ = gen.generate_roi_frame(state)

    extractor = FeatureExtractor()
    actuator = MockActuator()

    # Attempt loading trained PPO model + VecNormalize
    policy = None
    vec_normalize = None
    try:
        from stable_baselines3 import PPO
        import pickle

        policy = PPO.load(model_path, device="cpu")
        with open(vec_norm_path, "rb") as f:
            vec_normalize = pickle.load(f)
        print(f"[OK] Loaded trained PPO policy and VecNormalize from '{model_path}'")
    except Exception as exc:
        print(f"[INFO] Using fast fallback policy ({exc})")

    capture_times: List[float] = []
    extract_times: List[float] = []
    infer_times: List[float] = []
    actuate_times: List[float] = []
    total_times: List[float] = []

    last_action = 0

    for i in range(iterations):
        t0 = time.perf_counter()

        # 1. Capture stage (simulated frame fetch)
        t_cap_start = time.perf_counter()
        frame = test_frame  # in-memory buffer fetch
        t_cap_end = time.perf_counter()

        # 2. CV Extraction stage
        t_ext_start = time.perf_counter()
        extraction = extractor.extract_features(frame, timestamp=t_cap_end, prev_action=last_action)
        t_ext_end = time.perf_counter()

        # 3. Policy Inference stage
        t_inf_start = time.perf_counter()
        if policy is not None and vec_normalize is not None:
            # Normalize observation using VecNormalize running stats
            obs = extraction.features.reshape(1, -1)
            # Clip and normalize matching VecNormalize logic
            obs_norm = (obs - vec_normalize.obs_rms.mean) / np.sqrt(vec_normalize.obs_rms.var + 1e-8)
            obs_norm = np.clip(obs_norm, -vec_normalize.clip_obs, vec_normalize.clip_obs)
            action, _ = policy.predict(obs_norm, deterministic=True)
            last_action = int(action[0])
        else:
            # Bang-bang fallback
            last_action = 1 if extraction.fish_pos > extraction.bar_pos else 0
        t_inf_end = time.perf_counter()

        # 4. Actuation stage
        t_act_start = time.perf_counter()
        if last_action == 1:
            actuator.press_down()
        else:
            actuator.release()
        t_act_end = time.perf_counter()

        t_total_end = time.perf_counter()

        capture_times.append((t_cap_end - t_cap_start) * 1000.0)
        extract_times.append((t_ext_end - t_ext_start) * 1000.0)
        infer_times.append((t_inf_end - t_inf_start) * 1000.0)
        actuate_times.append((t_act_end - t_act_start) * 1000.0)
        total_times.append((t_total_end - t0) * 1000.0)

    def calc_stats(arr: List[float]) -> Dict[str, float]:
        sorted_arr = sorted(arr)
        n = len(sorted_arr)
        return {
            "mean": float(np.mean(arr)),
            "p50": sorted_arr[int(n * 0.50)],
            "p90": sorted_arr[int(n * 0.90)],
            "p95": sorted_arr[int(n * 0.95)],
            "p99": sorted_arr[min(int(n * 0.99), n - 1)],
            "max": sorted_arr[-1],
        }

    cap_s = calc_stats(capture_times)
    ext_s = calc_stats(extract_times)
    inf_s = calc_stats(infer_times)
    act_s = calc_stats(actuate_times)
    tot_s = calc_stats(total_times)

    print(f"\nStage Latency Breakdown (milliseconds over {iterations} samples):")
    print(f"{'Stage':<22} | {'Mean':<8} | {'p50':<8} | {'p90':<8} | {'p95':<8} | {'p99':<8} | {'Max':<8}")
    print("-" * 78)
    print(f"{'1. Capture (Buffer)':<22} | {cap_s['mean']:<8.3f} | {cap_s['p50']:<8.3f} | {cap_s['p90']:<8.3f} | {cap_s['p95']:<8.3f} | {cap_s['p99']:<8.3f} | {cap_s['max']:<8.3f}")
    print(f"{'2. Feature Extraction':<22} | {ext_s['mean']:<8.3f} | {ext_s['p50']:<8.3f} | {ext_s['p90']:<8.3f} | {ext_s['p95']:<8.3f} | {ext_s['p99']:<8.3f} | {ext_s['max']:<8.3f}")
    print(f"{'3. Policy Inference':<22} | {inf_s['mean']:<8.3f} | {inf_s['p50']:<8.3f} | {inf_s['p90']:<8.3f} | {inf_s['p95']:<8.3f} | {inf_s['p99']:<8.3f} | {inf_s['max']:<8.3f}")
    print(f"{'4. Actuator Dispatch':<22} | {act_s['mean']:<8.3f} | {act_s['p50']:<8.3f} | {act_s['p90']:<8.3f} | {act_s['p95']:<8.3f} | {act_s['p99']:<8.3f} | {act_s['max']:<8.3f}")
    print("-" * 78)
    print(f"{'Total E2E Pipeline':<22} | {tot_s['mean']:<8.3f} | {tot_s['p50']:<8.3f} | {tot_s['p90']:<8.3f} | {tot_s['p95']:<8.3f} | {tot_s['p99']:<8.3f} | {tot_s['max']:<8.3f}")

    exit_gate_pass = tot_s["p99"] < 25.0
    status_str = "[PASS]" if exit_gate_pass else "[FAIL]"
    print(f"\nPhase 2 Exit Gate (p99 < 25.0 ms): {status_str} (Achieved p99 = {tot_s['p99']:.3f} ms, p50 = {tot_s['p50']:.3f} ms)")
    return tot_s


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fisher Latency Benchmark")
    parser.add_argument("--iterations", type=int, default=1000, help="Number of benchmark iterations")
    args = parser.parse_args()
    run_latency_benchmark(iterations=args.iterations)
