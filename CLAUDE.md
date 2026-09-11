# Claude Code Instructions

> **IMPORTANT:** This repository follows a strict multi-agent universal operating standard.
> 
> **You MUST read and follow the instructions in [`AGENTS.md`](file:///d:/projects/fisher/AGENTS.md) and [`log.md`](file:///d:/projects/fisher/log.md) before inspecting or modifying any code.**

## Quick Directives:
1. **Pre-flight:** Read [`AGENTS.md`](file:///d:/projects/fisher/AGENTS.md), read [`log.md`](file:///d:/projects/fisher/log.md) (check recent changes & handoff notes), then read [`plan.md`](file:///d:/projects/fisher/plan.md).
2. **Baseline check:** Always run `pytest -v` to confirm existing tests pass before writing code.
3. **Execution standard:** No vibe coding, no fake tests, respect the 3-screen topology (Screen 1: Telemetry, Screen 2: IDE, Screen 3: Game Client).
4. **Post-task requirement:** Append a structured session entry to [`log.md`](file:///d:/projects/fisher/log.md) documenting code changes, tests run with outputs, gate results, and handoff notes for the next agent.
