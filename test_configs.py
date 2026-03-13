"""
Standard test suite — run all reference configurations and report results.

Uses the same SimulationManager and SimulationAgentsConfig defaults used by
the backend API, so results are identical to the web UI.

Usage:
    python test_configs.py              # quick summary
    python test_configs.py -v           # verbose (agent log lines)
"""

import json
import sys
import time

sys.path.insert(0, ".")

from backend.api.simulation_manager import SimulationManager
from backend.config.schemas import (
    AgentRoleParams,
    GridScenarioConfig,
    ScoutBehaviorParams,
    SimulationAgentsConfig,
)


def _run(name: str, grid_cfg: GridScenarioConfig, agents_cfg: SimulationAgentsConfig):
    """Run a single simulation to completion using the real SimulationManager."""
    mgr = SimulationManager()
    mgr.initialize_from_grid(grid_cfg, agents_cfg)
    assert mgr.model is not None, "initialize_from_grid failed to create model"
    model = mgr.model

    t0 = time.perf_counter()
    while model.running:
        model.step()
    elapsed = time.perf_counter() - t0

    steps = model.current_step
    done = model.objects_retrieved >= model.total_objects
    tag = "" if done else "  ** INCOMPLETE **"
    print(
        f"[{name:30s}]  {model.objects_retrieved}/{model.total_objects}"
        f"  in {steps:4d} steps  ({elapsed:.2f}s){tag}"
    )
    return steps


# ── Test configurations ─────────────────────────────────────────────────────
# SimulationAgentsConfig() is the single source of truth — identical to what
# the frontend receives from GET /api/defaults and sends back on load.
# Only override the fields that differ from the default 1S/1C/3R composition.

def _default_agents(**overrides) -> SimulationAgentsConfig:
    """Return default SimulationAgentsConfig with optional field overrides."""
    return SimulationAgentsConfig(**overrides)


CONFIGS = [
    # ── seek_coordinator=True (default) ──
    # 1S / 1C / 3R  —  all defaults, unknown map
    ("1S/1C/3R unknown", _default_agents()),
    # 1S / 1C / 3R  —  all defaults, map known
    ("1S/1C/3R map_known", _default_agents(map_known=True)),
    # 0S / 0C / 5R  —  v=3, c=3, unknown map
    (
        "0S/0C/5R unknown",
        _default_agents(
            scouts=AgentRoleParams(count=0),
            coordinators=AgentRoleParams(count=0),
            retrievers=AgentRoleParams(count=5, vision_radius=3, communication_radius=3, carrying_capacity=2),
        ),
    ),
    # 0S / 0C / 5R  —  v=3, c=3, map known
    (
        "0S/0C/5R map_known",
        _default_agents(
            scouts=AgentRoleParams(count=0),
            coordinators=AgentRoleParams(count=0),
            retrievers=AgentRoleParams(count=5, vision_radius=3, communication_radius=3, carrying_capacity=2),
            map_known=True,
        ),
    ),
    # ── seek_coordinator=False ──
    # 1S / 1C / 3R  —  no seek, unknown map
    (
        "1S/1C/3R unknown  no-seek",
        _default_agents(scout_behavior=ScoutBehaviorParams(seek_coordinator=False)),
    ),
    # 1S / 1C / 3R  —  no seek, map known
    (
        "1S/1C/3R map_known no-seek",
        _default_agents(scout_behavior=ScoutBehaviorParams(seek_coordinator=False), map_known=True),
    ),
]


def main():
    verbose = "-v" in sys.argv
    import builtins
    _real_print = builtins.print

    with open("configs/A.json") as f:
        grid_cfg = GridScenarioConfig(**json.load(f))

    meta = grid_cfg.metadata
    _real_print("=" * 80)
    _real_print(
        f"  TEST SUITE — configs/A.json "
        f"({meta.grid_size}×{meta.grid_size}, {meta.num_objects} objects, "
        f"max {meta.max_steps} steps, seed={meta.seed})"
    )
    _real_print("=" * 80)

    results = []
    for name, agents_cfg in CONFIGS:
        if not verbose:
            builtins.print = lambda *a, **kw: None
        steps = _run(name, grid_cfg, agents_cfg)
        if not verbose:
            builtins.print = _real_print
            _real_print(f"  [{name:30s}]  => {steps:4d} steps")
        results.append((name, steps))

    _real_print("-" * 80)
    _real_print(f"  Total steps: {sum(s for _, s in results)}")
    _real_print("=" * 80)


if __name__ == "__main__":
    main()
