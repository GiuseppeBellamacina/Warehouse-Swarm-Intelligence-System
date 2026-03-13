"""
Standard test suite — run all reference configurations and report results.

Uses the same SimulationManager and SimulationAgentsConfig defaults used by
the backend API, so results are identical to the web UI.

Generates SVG charts (same visual style as the frontend BenchmarkPanel) and
saves them to experiments/<map>/.

Usage:
    python test_configs.py              # quick summary
    python test_configs.py -v           # verbose (agent log lines)
"""

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, ".")

from backend.api.simulation_manager import SimulationManager
from backend.config.schemas import (
    AgentRoleParams,
    GridScenarioConfig,
    ScoutBehaviorParams,
    SimulationAgentsConfig,
)


# ── SVG chart renderer (mirrors frontend BenchmarkPanel) ─────────────────────

CHART_COLORS = [
    "#3b82f6",  # blue
    "#10b981",  # emerald
    "#f59e0b",  # amber
    "#ef4444",  # red
    "#8b5cf6",  # violet
    "#ec4899",  # pink
    "#06b6d4",  # cyan
    "#f97316",  # orange
]

THEME = {
    "bg": "#111318",
    "title": "#e5e7eb",
    "axisLabel": "#9ca3af",
    "tickLabel": "#6b7280",
    "grid": "#374151",
    "axis": "#4b5563",
    "legend": "#d1d5db",
}


def _pick_color(i: int) -> str:
    return CHART_COLORS[i % len(CHART_COLORS)]


def _svg_line_chart(
    title: str,
    series: list[dict],
    y_label: str,
    x_label: str = "Step",
    width: int = 620,
    height: int = 320,
) -> str:
    """Generate an SVG line chart string identical to the frontend SVGChart."""
    t = THEME
    pad = {"top": 40, "right": 20, "bottom": 52, "left": 58}
    w = width - pad["left"] - pad["right"]
    h = height - pad["top"] - pad["bottom"]

    x_min, x_max, y_min, y_max = float("inf"), float("-inf"), float("inf"), float("-inf")
    for s in series:
        for d in s["data"]:
            x_min = min(x_min, d["x"])
            x_max = max(x_max, d["x"])
            y_min = min(y_min, d["y"])
            y_max = max(y_max, d["y"])
    if not (x_min < float("inf")):
        x_min, x_max, y_min, y_max = 0, 1, 0, 1
    y_range = (y_max - y_min) or 1
    y_min = max(0, y_min - y_range * 0.05)
    y_max = y_max + y_range * 0.05
    x_range = (x_max - x_min) or 1

    def sx(x):
        return pad["left"] + ((x - x_min) / x_range) * w

    def sy(y):
        return pad["top"] + h - ((y - y_min) / ((y_max - y_min) or 1)) * h

    y_ticks = [y_min + (y_max - y_min) * i / 4 for i in range(5)]
    x_ticks = [round(x_min + x_range * i / 4) for i in range(5)]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" style="background:{t["bg"]};border-radius:8px">',
        # Title
        f'<text x="{width / 2}" y="22" text-anchor="middle" fill="{t["title"]}" '
        f'font-size="13" font-weight="700">{title}</text>',
        # Y axis label
        f'<text x="14" y="{pad["top"] + h / 2}" text-anchor="middle" fill="{t["axisLabel"]}" '
        f'font-size="10" transform="rotate(-90,14,{pad["top"] + h / 2})">{y_label}</text>',
        # X axis label
        f'<text x="{pad["left"] + w / 2}" y="{height - 6}" text-anchor="middle" '
        f'fill="{t["axisLabel"]}" font-size="10">{x_label}</text>',
    ]

    # Grid + tick labels
    for v in y_ticks:
        lbl = str(int(v)) if v == int(v) else f"{v:.1f}"
        parts.append(
            f'<line x1="{pad["left"]}" x2="{pad["left"] + w}" y1="{sy(v):.1f}" '
            f'y2="{sy(v):.1f}" stroke="{t["grid"]}" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{pad["left"] - 6}" y="{sy(v) + 3:.1f}" text-anchor="end" '
            f'fill="{t["tickLabel"]}" font-size="9">{lbl}</text>'
        )
    for v in x_ticks:
        parts.append(
            f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="{pad["top"]}" '
            f'y2="{pad["top"] + h}" stroke="{t["grid"]}" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{sx(v):.1f}" y="{pad["top"] + h + 14}" text-anchor="middle" '
            f'fill="{t["tickLabel"]}" font-size="9">{v}</text>'
        )

    # Axes
    parts.append(
        f'<line x1="{pad["left"]}" x2="{pad["left"]}" y1="{pad["top"]}" '
        f'y2="{pad["top"] + h}" stroke="{t["axis"]}" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{pad["left"]}" x2="{pad["left"] + w}" y1="{pad["top"] + h}" '
        f'y2="{pad["top"] + h}" stroke="{t["axis"]}" stroke-width="1"/>'
    )

    # Lines
    for s in series:
        if len(s["data"]) < 2:
            continue
        pts = " ".join(f"{sx(d['x']):.1f},{sy(d['y']):.1f}" for d in s["data"])
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{s["color"]}" '
            f'stroke-width="1.5" stroke-linejoin="round"/>'
        )

    # Legend
    for i, s in enumerate(series):
        lx = pad["left"] + 8
        ly = pad["top"] + 10 + i * 14
        parts.append(
            f'<line x1="{lx}" x2="{lx + 16}" y1="{ly}" y2="{ly}" '
            f'stroke="{s["color"]}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{lx + 20}" y="{ly + 3}" fill="{t["legend"]}" '
            f'font-size="9">{s["label"]}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _svg_bar_chart(
    title: str,
    labels: list[str],
    values: list[int],
    colors: list[str],
    y_label: str = "Steps",
    width: int = 620,
    height: int = 320,
) -> str:
    """Generate an SVG bar chart for comparing total steps across configs."""
    t = THEME
    pad = {"top": 40, "right": 20, "bottom": 80, "left": 58}
    w = width - pad["left"] - pad["right"]
    h = height - pad["top"] - pad["bottom"]

    y_max = max(values) * 1.1 if values else 1

    def sy(y):
        return pad["top"] + h - (y / y_max) * h

    bar_gap = 8
    bar_w = (w - bar_gap * (len(labels) + 1)) / max(len(labels), 1)

    y_ticks = [y_max * i / 4 for i in range(5)]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" style="background:{t["bg"]};border-radius:8px">',
        f'<text x="{width / 2}" y="22" text-anchor="middle" fill="{t["title"]}" '
        f'font-size="13" font-weight="700">{title}</text>',
        f'<text x="14" y="{pad["top"] + h / 2}" text-anchor="middle" fill="{t["axisLabel"]}" '
        f'font-size="10" transform="rotate(-90,14,{pad["top"] + h / 2})">{y_label}</text>',
    ]

    # Grid
    for v in y_ticks:
        lbl = str(int(v))
        parts.append(
            f'<line x1="{pad["left"]}" x2="{pad["left"] + w}" y1="{sy(v):.1f}" '
            f'y2="{sy(v):.1f}" stroke="{t["grid"]}" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{pad["left"] - 6}" y="{sy(v) + 3:.1f}" text-anchor="end" '
            f'fill="{t["tickLabel"]}" font-size="9">{lbl}</text>'
        )

    # Axes
    parts.append(
        f'<line x1="{pad["left"]}" x2="{pad["left"]}" y1="{pad["top"]}" '
        f'y2="{pad["top"] + h}" stroke="{t["axis"]}" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{pad["left"]}" x2="{pad["left"] + w}" y1="{pad["top"] + h}" '
        f'y2="{pad["top"] + h}" stroke="{t["axis"]}" stroke-width="1"/>'
    )

    # Bars
    for i, (lbl, val, col) in enumerate(zip(labels, values, colors)):
        bx = pad["left"] + bar_gap + i * (bar_w + bar_gap)
        by = sy(val)
        bh = pad["top"] + h - by
        parts.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
            f'fill="{col}" rx="3"/>'
        )
        # Value on top
        parts.append(
            f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle" '
            f'fill="{t["legend"]}" font-size="10" font-weight="600">{val}</text>'
        )
        # Label below (rotated)
        parts.append(
            f'<text x="{bx + bar_w / 2:.1f}" y="{pad["top"] + h + 12}" '
            f'text-anchor="end" fill="{t["tickLabel"]}" font-size="8" '
            f'transform="rotate(-35,{bx + bar_w / 2:.1f},{pad["top"] + h + 12})">{lbl}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _svg_table(
    title: str,
    headers: list[str],
    rows: list[list[str]],
    colors: list[str],
    width: int = 900,
) -> str:
    """Generate an SVG table similar to the frontend exportTableAsPNG."""
    t = THEME
    row_h = 28
    pad_x = 12
    pad_y = 8

    # Estimate column widths (roughly 7px per char at font-size 11)
    col_widths = []
    for ci in range(len(headers)):
        max_w = len(headers[ci]) * 7 + pad_x * 2
        for row in rows:
            if ci < len(row):
                cw = len(row[ci]) * 7 + pad_x * 2
                max_w = max(max_w, cw)
        col_widths.append(max_w)

    total_w = max(sum(col_widths), width)
    # Scale col_widths to fill total_w
    scale = total_w / sum(col_widths)
    col_widths = [int(cw * scale) for cw in col_widths]
    total_w = sum(col_widths)

    total_h = (len(rows) + 1) * row_h + pad_y * 2 + 30  # extra for title

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h}" '
        f'width="{total_w}" height="{total_h}" style="background:#1f2937;border-radius:8px">',
        # Title
        f'<text x="{total_w / 2}" y="20" text-anchor="middle" fill="{t["title"]}" '
        f'font-size="13" font-weight="700">{title}</text>',
    ]

    base_y = 30

    # Header background
    parts.append(
        f'<rect x="0" y="{base_y + pad_y}" width="{total_w}" height="{row_h}" '
        f'fill="#111827"/>'
    )

    # Header text
    x = 0
    for ci, hdr in enumerate(headers):
        parts.append(
            f'<text x="{x + pad_x}" y="{base_y + pad_y + row_h * 0.65}" '
            f'fill="#9ca3af" font-size="11" font-weight="bold" '
            f'font-family="sans-serif">{hdr}</text>'
        )
        x += col_widths[ci]

    # Header divider
    parts.append(
        f'<line x1="0" x2="{total_w}" y1="{base_y + pad_y + row_h}" '
        f'y2="{base_y + pad_y + row_h}" stroke="#374151" stroke-width="1"/>'
    )

    # Data rows
    for ri, row in enumerate(rows):
        y = base_y + pad_y + (ri + 1) * row_h
        # Zebra
        if ri % 2 == 1:
            parts.append(
                f'<rect x="0" y="{y}" width="{total_w}" height="{row_h}" fill="#111827"/>'
            )
        # Row divider
        parts.append(
            f'<line x1="0" x2="{total_w}" y1="{y + row_h}" y2="{y + row_h}" '
            f'stroke="#1f2937" stroke-width="1"/>'
        )
        x = 0
        for ci, cell in enumerate(row):
            if ci == 0:
                # Color dot + label
                col = colors[ri] if ri < len(colors) else "#6b7280"
                parts.append(
                    f'<circle cx="{x + pad_x + 4}" cy="{y + row_h * 0.5}" r="4" fill="{col}"/>'
                )
                parts.append(
                    f'<text x="{x + pad_x + 14}" y="{y + row_h * 0.65}" '
                    f'fill="#d1d5db" font-size="11" font-family="monospace">{cell}</text>'
                )
            else:
                fill = "#6b7280" if ci >= 6 else "#d1d5db"
                parts.append(
                    f'<text x="{x + pad_x}" y="{y + row_h * 0.65}" '
                    f'fill="{fill}" font-size="11" font-family="monospace">{cell}</text>'
                )
            x += col_widths[ci]

    parts.append("</svg>")
    return "\n".join(parts)


# ── Snapshot collection ──────────────────────────────────────────────────────

def _run(name: str, grid_cfg: GridScenarioConfig, agents_cfg: SimulationAgentsConfig):
    """Run a single simulation, collect per-step snapshots."""
    mgr = SimulationManager()
    mgr.initialize_from_grid(grid_cfg, agents_cfg)
    assert mgr.model is not None, "initialize_from_grid failed to create model"
    model = mgr.model

    snapshots: list[dict] = []
    t0 = time.perf_counter()
    while model.running:
        model.step()
        snapshots.append({
            "step": model.current_step,
            "objects_retrieved": model.objects_retrieved,
            "total_objects": model.total_objects,
            "average_energy": float(
                np.mean([getattr(a, "energy", 0) for a in model.agents])
            ) if model.agents else 0.0,
            "active_agents": len([a for a in model.agents if getattr(a, "energy", 0) > 0]),
            "messages_sent": model.comm_manager.messages_sent,
        })
    elapsed = time.perf_counter() - t0

    steps = model.current_step
    done = model.objects_retrieved >= model.total_objects
    tag = "" if done else "  ** INCOMPLETE **"
    print(
        f"[{name:30s}]  {model.objects_retrieved}/{model.total_objects}"
        f"  in {steps:4d} steps  ({elapsed:.2f}s){tag}"
    )
    return steps, snapshots


# ── Test configurations ─────────────────────────────────────────────────────

def _default_agents(**overrides) -> SimulationAgentsConfig:
    """Return default SimulationAgentsConfig with optional field overrides."""
    return SimulationAgentsConfig(**overrides)


CONFIGS = [
    # ── seek_coordinator=True (default) ──
    ("1S/1C/3R unknown", _default_agents()),
    ("1S/1C/3R map_known", _default_agents(map_known=True)),
    (
        "0S/0C/5R unknown",
        _default_agents(
            scouts=AgentRoleParams(count=0),
            coordinators=AgentRoleParams(count=0),
            retrievers=AgentRoleParams(count=5, vision_radius=3, communication_radius=3, carrying_capacity=2),
        ),
    ),
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
    (
        "1S/1C/3R unknown  no-seek",
        _default_agents(scout_behavior=ScoutBehaviorParams(seek_coordinator=False)),
    ),
    (
        "1S/1C/3R map_known no-seek",
        _default_agents(scout_behavior=ScoutBehaviorParams(seek_coordinator=False), map_known=True),
    ),
]

GRID_FILES = ["configs/A.json", "configs/B.json"]


# ── Chart generation ─────────────────────────────────────────────────────────

CHART_DEFS = [
    {
        "key": "retrieval",
        "title": "Objects Retrieved vs Step",
        "y_label": "Objects Retrieved",
        "extract": lambda sn: sn["objects_retrieved"],
    },
    {
        "key": "energy",
        "title": "Average Agent Energy vs Step",
        "y_label": "Avg Energy",
        "extract": lambda sn: sn["average_energy"],
    },
    {
        "key": "efficiency",
        "title": "Retrieval Efficiency vs Step",
        "y_label": "Obj / 100 steps",
        "extract": lambda sn: (sn["objects_retrieved"] / sn["step"] * 100) if sn["step"] > 0 else 0,
    },
    {
        "key": "messages",
        "title": "Messages Sent vs Step",
        "y_label": "Messages",
        "extract": lambda sn: sn["messages_sent"],
    },
]


def _downsample(data: list[dict], max_pts: int = 300) -> list[dict]:
    if len(data) <= max_pts:
        return data
    step = max(1, len(data) // max_pts)
    out = [data[i] for i in range(0, len(data), step)]
    if out[-1] is not data[-1]:
        out.append(data[-1])
    return out


def _generate_charts(map_name: str, results: list[tuple[str, int, list[dict]]], out_dir: str):
    """Generate SVG charts + table for one map and save to out_dir."""
    os.makedirs(out_dir, exist_ok=True)

    # 1) Line charts (one per metric, all configs overlaid)
    for cdef in CHART_DEFS:
        series = []
        for i, (name, _steps, snapshots) in enumerate(results):
            ds = _downsample(snapshots)
            series.append({
                "label": name,
                "color": _pick_color(i),
                "data": [{"x": sn["step"], "y": cdef["extract"](sn)} for sn in ds],
            })
        svg = _svg_line_chart(
            f"{cdef['title']}  —  {map_name}",
            series,
            cdef["y_label"],
        )
        path = os.path.join(out_dir, f"{cdef['key']}.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

    # 2) Bar chart — total steps comparison
    labels = [name for name, _, _ in results]
    values = [steps for _, steps, _ in results]
    colors = [_pick_color(i) for i in range(len(results))]
    svg = _svg_bar_chart(f"Total Steps Comparison  —  {map_name}", labels, values, colors)
    with open(os.path.join(out_dir, "steps_comparison.svg"), "w", encoding="utf-8") as f:
        f.write(svg)

    # 3) Summary table
    headers = ["Config", "Steps", "Retrieved", "Completion", "Efficiency", "Avg Energy", "Messages"]
    rows = []
    for name, steps, snapshots in results:
        last = snapshots[-1] if snapshots else {}
        retrieved = last.get("objects_retrieved", 0)
        total = last.get("total_objects", 0)
        pct = f"{(retrieved / total * 100):.1f}%" if total else "—"
        eff = f"{(retrieved / steps * 100):.2f}" if steps > 0 else "—"
        avg_e = f"{last.get('average_energy', 0):.0f}"
        msgs = str(last.get("messages_sent", 0))
        rows.append([name, str(steps), f"{retrieved}/{total}", pct, eff, avg_e, msgs])
    svg = _svg_table(f"Summary  —  {map_name}", headers, rows, colors)
    with open(os.path.join(out_dir, "summary_table.svg"), "w", encoding="utf-8") as f:
        f.write(svg)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    verbose = "-v" in sys.argv
    import builtins
    _real_print = builtins.print

    for grid_file in GRID_FILES:
        with open(grid_file) as f:
            grid_cfg = GridScenarioConfig(**json.load(f))

        map_name = os.path.splitext(os.path.basename(grid_file))[0]  # "A" or "B"
        meta = grid_cfg.metadata

        _real_print("=" * 80)
        _real_print(
            f"  TEST SUITE — {grid_file} "
            f"({meta.grid_size}×{meta.grid_size}, {meta.num_objects} objects, "
            f"max {meta.max_steps} steps, seed={meta.seed})"
        )
        _real_print("=" * 80)

        results: list[tuple[str, int, list[dict]]] = []
        for name, agents_cfg in CONFIGS:
            if not verbose:
                builtins.print = lambda *a, **kw: None
            steps, snapshots = _run(name, grid_cfg, agents_cfg)
            if not verbose:
                builtins.print = _real_print
                _real_print(f"  [{name:30s}]  => {steps:4d} steps")
            results.append((name, steps, snapshots))

        _real_print("-" * 80)
        _real_print(f"  Total steps: {sum(s for _, s, _ in results)}")

        # Generate charts
        out_dir = os.path.join("docs", "benchmarks", map_name)
        _generate_charts(map_name, results, out_dir)
        _real_print(f"  Charts saved to {out_dir}/")
        _real_print("=" * 80)
        _real_print()


if __name__ == "__main__":
    main()
