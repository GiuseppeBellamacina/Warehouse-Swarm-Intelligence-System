"""
Multi-seed benchmark using MAPD logistics-grid instances.

Runs the 18 pre-generated instances from Logistic-Warehouse-main/instances/
with diversified seeding and produces statistical plots (mean ± std) and
JSON results.

Usage:
    python evaluation_logistic.py                          # all instances, 30 seeds
    python evaluation_logistic.py --seeds 10               # fewer seeds (faster)
    python evaluation_logistic.py --instances 50x50        # filter by grid size
    python evaluation_logistic.py --instances medium       # filter by density
    python evaluation_logistic.py --instances border       # filter by distribution
    python evaluation_logistic.py --instances 75x75 few    # combine filters
    python evaluation_logistic.py --workers 4              # limit parallelism
    python evaluation_logistic.py --mode unknown           # only map_known=False
    python evaluation_logistic.py --mode known             # only map_known=True
    python evaluation_logistic.py --config cfg.json        # use JSON config file
    python evaluation_logistic.py --config cfg.json --seeds 5  # config + CLI override
    python evaluation_logistic.py --no-plots               # skip charts, just JSON + summary
    python evaluation_logistic.py --no-json                # skip JSON export
    python evaluation_logistic.py -v                       # verbose agent logs

See docs/BENCHMARK.md for full documentation.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from tqdm import tqdm

sys.path.insert(0, ".")

from backend.api.simulation_manager import SimulationManager
from backend.config.schemas import (
    CoordinatorParams,
    GridScenarioConfig,
    RetrieverParams,
    ScoutParams,
    SimulationAgentsConfig,
)

# ── Constants ────────────────────────────────────────────────────────────────

INSTANCES_DIR = Path("configs/logistics")

# Total energy budget per grid size (floor(0.8 * medium_traversable_cells))
# From README: medium traversable counts are 1726, 3841, 7192
_TOTAL_ENERGY = {
    50: 1380,
    75: 3072,
    100: 5753,
}

NUM_AGENTS = 10  # fixed across all configs

# ── Chart styling (reused from evaluation.py) ────────────────────────────────

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


# ── Helpers ──────────────────────────────────────────────────────────────────


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _font(size: int, mono: bool = False):
    names = (
        ["consola.ttf", "Consolas.ttf", "DejaVuSansMono.ttf"]
        if mono
        else ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"]
    )
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return int(bb[2] - bb[0])


def _paste_rotated(
    img: Image.Image, text: str, font, fill: tuple, cx: int, cy: int, angle: int = 90
) -> None:
    tmp = Image.new("RGBA", (800, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    bb = d.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text((-bb[0], -bb[1]), text, fill=(*fill, 255), font=font)
    cropped = tmp.crop((0, 0, tw, th))
    rotated = cropped.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    x, y = cx - rotated.width // 2, cy - rotated.height // 2
    img.paste(rotated, (x, y), rotated)


def _pick_color(i: int) -> str:
    return CHART_COLORS[i % len(CHART_COLORS)]


# ── Instance loading ─────────────────────────────────────────────────────────


def compute_energy_per_agent(grid_size: int, num_agents: int = NUM_AGENTS) -> int:
    """Compute per-agent energy budget from README formula."""
    total = _TOTAL_ENERGY.get(grid_size)
    if total is None:
        raise ValueError(f"No energy budget defined for grid_size={grid_size}")
    return math.ceil(total / num_agents)


def load_mapd_instance(path: str | Path) -> GridScenarioConfig:
    """Load a MAPD instance JSON and return a GridScenarioConfig."""
    with open(path) as f:
        data = json.load(f)

    # Inject max_steps if missing (grid_size * 10)
    meta = data.get("metadata", {})
    grid_size = meta.get("grid_size", 50)
    if "max_steps" not in meta:
        meta["max_steps"] = grid_size * 10

    # Keep only fields GridScenarioConfig expects
    cfg_data = {
        "metadata": {
            "grid_size": meta["grid_size"],
            "num_warehouses": meta["num_warehouses"],
            "num_objects": meta["num_objects"],
            "max_steps": meta["max_steps"],
            "seed": meta.get("seed"),
        },
        "grid": data["grid"],
        "warehouses": data["warehouses"],
        "objects": data["objects"],
    }
    return GridScenarioConfig(**cfg_data)


def list_instances(filters: list[str] | None = None) -> list[Path]:
    """List instance files, optionally filtered by substrings."""
    files = sorted(INSTANCES_DIR.glob("mapd_*.json"))
    if filters:
        filtered = []
        for f in files:
            name = f.stem
            if all(flt in name for flt in filters):
                filtered.append(f)
        return filtered
    return files


# ── Agent configurations ─────────────────────────────────────────────────────


def build_configs(
    grid_size: int, modes: list[str], unlimited_energy: bool = False
) -> list[tuple[str, SimulationAgentsConfig]]:
    """
    Build the 2 (or 4) agent configurations for a given grid size.

    Configs:
      - 0S/0C/10R: 10 retrievers only
      - 2S/2C/6R: 2 scouts + 2 coordinators + 6 retrievers

    Both with vision_radius=6, communication_radius=4, energy=computed.
    Tested with map_known=False and/or map_known=True depending on modes.
    If unlimited_energy is True, agents get 999999 energy (effectively infinite).
    """
    energy = 999999 if unlimited_energy else compute_energy_per_agent(grid_size, NUM_AGENTS)

    configs: list[tuple[str, SimulationAgentsConfig]] = []

    for mode in modes:
        map_known = mode == "known"
        suffix = "known" if map_known else "unknown"

        # Config 1: 10 retrievers only
        configs.append(
            (
                f"0S/0C/10R {suffix}",
                SimulationAgentsConfig(
                    scouts=ScoutParams(count=0),
                    coordinators=CoordinatorParams(count=0),
                    retrievers=RetrieverParams(
                        count=10,
                        vision_radius=6,
                        communication_radius=4,
                        max_energy=float(energy),
                        carrying_capacity=2,
                    ),
                    map_known=map_known,
                ),
            )
        )

        # Config 2: 2S/2C/6R
        configs.append(
            (
                f"2S/2C/6R {suffix}",
                SimulationAgentsConfig(
                    scouts=ScoutParams(
                        count=2,
                        vision_radius=6,
                        communication_radius=4,
                        max_energy=float(energy),
                        speed=2.0,
                    ),
                    coordinators=CoordinatorParams(
                        count=2,
                        vision_radius=6,
                        communication_radius=4,
                        max_energy=float(energy),
                    ),
                    retrievers=RetrieverParams(
                        count=6,
                        vision_radius=6,
                        communication_radius=4,
                        max_energy=float(energy),
                        carrying_capacity=2,
                    ),
                    map_known=map_known,
                ),
            )
        )

    return configs


# ── Single-run worker (picklable for multiprocessing) ────────────────────────


def _run_single(args: tuple) -> dict[str, Any]:
    """Run one simulation. Designed to be called from Pool.map()."""
    instance_path, config_name, agents_cfg_dict, seed, verbose = args

    # Suppress prints unless verbose
    import builtins

    _real_print = builtins.print
    if not verbose:
        builtins.print = lambda *a, **kw: None

    try:
        grid_cfg = load_mapd_instance(instance_path)
        grid_cfg = copy.deepcopy(grid_cfg)
        grid_cfg.metadata.seed = seed

        mgr = SimulationManager()
        agents_cfg = SimulationAgentsConfig(**agents_cfg_dict)
        mgr.initialize_from_grid(grid_cfg, agents_cfg)
        model = mgr.model

        # Collect per-step data for line charts
        step_data: list[dict] = []
        while model.running:
            model.step()
            step_data.append(
                {
                    "step": model.current_step,
                    "objects_retrieved": model.objects_retrieved,
                    "average_energy": float(
                        np.mean([getattr(a, "energy", 0) for a in model.agents])
                    ),
                    "messages_sent": model.comm_manager.messages_sent,
                }
            )

        steps = model.current_step
        retrieved = model.objects_retrieved
        total = model.total_objects
        avg_energy = float(np.mean([getattr(a, "energy", 0) for a in model.agents]))
        messages = model.comm_manager.messages_sent
        active = len([a for a in model.agents if getattr(a, "energy", 0) > 0])
        completed = retrieved >= total

        return {
            "instance": str(instance_path),
            "config": config_name,
            "seed": seed,
            "steps": steps,
            "objects_retrieved": retrieved,
            "total_objects": total,
            "avg_energy_final": avg_energy,
            "messages_sent": messages,
            "active_agents": active,
            "completed": completed,
            "step_data": step_data,
        }
    finally:
        builtins.print = _real_print


# ── Multi-seed orchestrator ──────────────────────────────────────────────────


def run_multiseed_benchmark(
    instances: list[Path],
    modes: list[str],
    num_seeds: int = 30,
    num_workers: int | None = None,
    verbose: bool = False,
    unlimited_energy: bool = False,
) -> dict[str, dict[str, list[dict]]]:
    """
    Run all instances × configs × seeds in parallel.

    Returns: {instance_name: {config_name: [result_dicts]}}
    """
    if num_workers is None:
        num_workers = max(1, mp.cpu_count() - 1)

    # Build work items
    work: list[tuple] = []
    for inst_path in instances:
        grid_size = json.loads(inst_path.read_text())["metadata"]["grid_size"]
        configs = build_configs(grid_size, modes, unlimited_energy=unlimited_energy)
        for cfg_name, agents_cfg in configs:
            agents_dict = agents_cfg.model_dump()
            # 10R configs are fully deterministic (no random calls) → single seed
            is_deterministic = cfg_name.startswith("0S/0C/10R")
            seeds_for_cfg = 1 if is_deterministic else num_seeds
            for seed in range(seeds_for_cfg):
                work.append((str(inst_path), cfg_name, agents_dict, seed, verbose))

    total = len(work)
    # Count deterministic vs stochastic configs
    n_det = sum(
        1 for name, _ in build_configs(50, modes, unlimited_energy) if name.startswith("0S/0C/10R")
    )
    n_stoch = len(build_configs(50, modes, unlimited_energy)) - n_det
    print(
        f"  Total runs: {total} "
        f"({len(instances)} inst × [{n_det} determ ×1 + {n_stoch} stoch ×{num_seeds} seeds])"
    )
    print(f"  Workers: {num_workers}")
    print()

    t0 = time.perf_counter()

    # Run with multiprocessing
    results_flat: list[dict] = []
    pbar = tqdm(total=total, desc="  Benchmark", unit="run", ncols=80)
    if num_workers <= 1:
        for item in work:
            r = _run_single(item)
            results_flat.append(r)
            pbar.update(1)
    else:
        with mp.Pool(num_workers) as pool:
            for r in pool.imap_unordered(_run_single, work):
                results_flat.append(r)
                pbar.update(1)
    pbar.close()

    elapsed = time.perf_counter() - t0
    print(f"  Completed in {elapsed:.1f}s ({elapsed/total:.2f}s per run avg)")

    # Organize results: {instance_name: {config: [results]}}
    organized: dict[str, dict[str, list[dict]]] = {}
    for r in results_flat:
        inst_name = Path(r["instance"]).stem
        cfg = r["config"]
        organized.setdefault(inst_name, {}).setdefault(cfg, []).append(r)

    return organized


# ── Plot: Bar chart (mean ± std) ─────────────────────────────────────────────


def _save_bar_chart_mean_std(
    path: str,
    title: str,
    labels: list[str],
    means: list[float],
    stds: list[float],
    colors: list[str],
    y_label: str = "Steps",
    width: int = 700,
    height: int = 400,
    scale: int = 2,
) -> None:
    """Bar chart with error bars (mean ± stddev)."""
    S = scale
    W, H = width * S, height * S
    img = Image.new("RGB", (W, H), _hex(THEME["bg"]))
    draw = ImageDraw.Draw(img)
    ft, fs, fl, fb = _font(13 * S), _font(10 * S), _font(8 * S), _font(10 * S)

    pt, pr, pb, pl = 40 * S, 20 * S, 100 * S, 68 * S
    cw, ch = W - pl - pr, H - pt - pb

    ym = max(m + s for m, s in zip(means, stds)) * 1.15 if means else 1

    def sy(v: float) -> int:
        return pt + ch - int(v / ym * ch)

    gap = 12 * S
    n = max(len(labels), 1)
    bw = (cw - gap * (n + 1)) // n

    # Title
    tw_t = _tw(draw, title, ft)
    draw.text(((W - tw_t) // 2, 6 * S), title, fill=_hex(THEME["title"]), font=ft)

    # Y label
    _paste_rotated(img, y_label, fs, _hex(THEME["axisLabel"]), 10 * S, pt + ch // 2)
    draw = ImageDraw.Draw(img)

    # Grid + ticks
    gc, tc = _hex(THEME["grid"]), _hex(THEME["tickLabel"])
    for i in range(5):
        v = ym * i / 4
        yy = sy(v)
        draw.line([(pl, yy), (pl + cw, yy)], fill=gc, width=1)
        lbl = str(int(round(v)))
        lw = _tw(draw, lbl, fl)
        draw.text((pl - lw - 4 * S, yy - 4 * S), lbl, fill=tc, font=fl)

    # Axes
    ac = _hex(THEME["axis"])
    draw.line([(pl, pt), (pl, pt + ch)], fill=ac, width=S)
    draw.line([(pl, pt + ch), (pl + cw, pt + ch)], fill=ac, width=S)

    # Bars
    for i, (lbl, mean, std, col) in enumerate(zip(labels, means, stds, colors)):
        bx = pl + gap + i * (bw + gap)
        by = sy(mean)
        draw.rectangle([(bx, by), (bx + bw, pt + ch)], fill=_hex(col))

        # Error bar (vertical line ± std)
        center_x = bx + bw // 2
        top = sy(mean + std)
        bot = sy(max(0, mean - std))
        draw.line([(center_x, top), (center_x, bot)], fill=(255, 255, 255), width=S)
        draw.line([(center_x - 4 * S, top), (center_x + 4 * S, top)], fill=(255, 255, 255), width=S)
        draw.line([(center_x - 4 * S, bot), (center_x + 4 * S, bot)], fill=(255, 255, 255), width=S)

        # Value on top
        vtxt = f"{mean:.0f}"
        vw = _tw(draw, vtxt, fb)
        draw.text((bx + (bw - vw) // 2, top - 16 * S), vtxt, fill=_hex(THEME["legend"]), font=fb)

        # Label below (rotated)
        _paste_rotated(
            img, lbl, fl, _hex(THEME["tickLabel"]), bx + bw // 2, pt + ch + pb // 2, angle=35
        )
        draw = ImageDraw.Draw(img)

    img.save(path, "PNG")


# ── Plot: Box plot ───────────────────────────────────────────────────────────


def _save_box_plot(
    path: str,
    title: str,
    labels: list[str],
    data_arrays: list[list[float]],
    colors: list[str],
    y_label: str = "Steps",
    width: int = 700,
    height: int = 400,
    scale: int = 2,
) -> None:
    """Box plot showing distribution per config."""
    S = scale
    W, H = width * S, height * S
    img = Image.new("RGB", (W, H), _hex(THEME["bg"]))
    draw = ImageDraw.Draw(img)
    ft, fs, fl = _font(13 * S), _font(10 * S), _font(8 * S)

    pt, pr, pb, pl = 40 * S, 20 * S, 100 * S, 68 * S
    cw, ch = W - pl - pr, H - pt - pb

    # Compute stats
    all_vals = [v for arr in data_arrays for v in arr]
    ym = max(all_vals) * 1.1 if all_vals else 1
    yn = 0

    def sy(v: float) -> int:
        return pt + ch - int((v - yn) / (ym - yn) * ch)

    gap = 16 * S
    n = max(len(labels), 1)
    bw = (cw - gap * (n + 1)) // n

    # Title
    tw_t = _tw(draw, title, ft)
    draw.text(((W - tw_t) // 2, 6 * S), title, fill=_hex(THEME["title"]), font=ft)

    # Y label
    _paste_rotated(img, y_label, fs, _hex(THEME["axisLabel"]), 10 * S, pt + ch // 2)
    draw = ImageDraw.Draw(img)

    # Grid
    gc, tc = _hex(THEME["grid"]), _hex(THEME["tickLabel"])
    for i in range(5):
        v = yn + (ym - yn) * i / 4
        yy = sy(v)
        draw.line([(pl, yy), (pl + cw, yy)], fill=gc, width=1)
        lbl = str(int(round(v)))
        lw = _tw(draw, lbl, fl)
        draw.text((pl - lw - 4 * S, yy - 4 * S), lbl, fill=tc, font=fl)

    # Axes
    ac = _hex(THEME["axis"])
    draw.line([(pl, pt), (pl, pt + ch)], fill=ac, width=S)
    draw.line([(pl, pt + ch), (pl + cw, pt + ch)], fill=ac, width=S)

    # Draw boxes
    for i, (lbl, arr, col) in enumerate(zip(labels, data_arrays, colors)):
        if not arr:
            continue
        sorted_arr = sorted(arr)
        q1 = np.percentile(sorted_arr, 25)
        q2 = np.percentile(sorted_arr, 50)
        q3 = np.percentile(sorted_arr, 75)
        iqr = q3 - q1
        whisker_lo = max(min(sorted_arr), q1 - 1.5 * iqr)
        whisker_hi = min(max(sorted_arr), q3 + 1.5 * iqr)

        bx = pl + gap + i * (bw + gap)
        cx = bx + bw // 2

        # Whiskers
        draw.line([(cx, sy(whisker_hi)), (cx, sy(q3))], fill=_hex(col), width=S)
        draw.line([(cx, sy(q1)), (cx, sy(whisker_lo))], fill=_hex(col), width=S)
        draw.line(
            [(bx + bw // 4, sy(whisker_hi)), (bx + 3 * bw // 4, sy(whisker_hi))],
            fill=_hex(col),
            width=S,
        )
        draw.line(
            [(bx + bw // 4, sy(whisker_lo)), (bx + 3 * bw // 4, sy(whisker_lo))],
            fill=_hex(col),
            width=S,
        )

        # Box
        box_top = sy(q3)
        box_bot = sy(q1)
        if box_bot > box_top:
            draw.rectangle(
                [(bx + 2, box_top), (bx + bw - 2, box_bot)], outline=_hex(col), width=S + 1
            )
            # Fill with darker shade
            r, g, b = _hex(col)
            fill_col = (r // 3, g // 3, b // 3)
            if box_bot - box_top > 2:
                draw.rectangle([(bx + 3, box_top + 1), (bx + bw - 3, box_bot - 1)], fill=fill_col)
        else:
            # Zero-height box (all values identical) — draw a single line
            draw.line([(bx + 2, box_top), (bx + bw - 2, box_top)], fill=_hex(col), width=S + 1)

        # Median line
        med_y = sy(q2)
        draw.line([(bx + 2, med_y), (bx + bw - 2, med_y)], fill=(255, 255, 255), width=S + 1)

        # Label
        _paste_rotated(
            img, lbl, fl, _hex(THEME["tickLabel"]), bx + bw // 2, pt + ch + pb // 2, angle=35
        )
        draw = ImageDraw.Draw(img)

    img.save(path, "PNG")


# ── Plot: Line chart with confidence interval band ───────────────────────────


def _save_line_chart_ci(
    path: str,
    title: str,
    series: list[dict],
    y_label: str,
    x_label: str = "Step",
    width: int = 700,
    height: int = 380,
    scale: int = 2,
) -> None:
    """
    Line chart with shaded confidence band.

    Each series: {label, color, x, mean, std}
    where x, mean, std are lists of the same length.
    """
    S = scale
    W, H = width * S, height * S
    img = Image.new("RGBA", (W, H), (*_hex(THEME["bg"]), 255))
    draw = ImageDraw.Draw(img)
    ft, fs, fl = _font(13 * S), _font(10 * S), _font(9 * S)

    pt, pr, pb, pl = 40 * S, 20 * S, 52 * S, 58 * S
    cw, ch = W - pl - pr, H - pt - pb

    # Data bounds
    xn = min(min(sr["x"]) for sr in series if sr["x"])
    xx = max(max(sr["x"]) for sr in series if sr["x"])
    yn = 0.0
    yx = max(max(m + s for m, s in zip(sr["mean"], sr["std"])) for sr in series if sr["mean"])
    yx *= 1.05
    xr = (xx - xn) or 1

    def sx(x: float) -> int:
        return pl + int((x - xn) / xr * cw)

    def sy(y: float) -> int:
        return pt + ch - int((y - yn) / ((yx - yn) or 1) * ch)

    # Title
    tw_t = _tw(draw, title, ft)
    draw.text(((W - tw_t) // 2, 6 * S), title, fill=_hex(THEME["title"]), font=ft)

    # Y label
    _paste_rotated(img, y_label, fs, _hex(THEME["axisLabel"]), 10 * S, pt + ch // 2)
    draw = ImageDraw.Draw(img)

    # X label
    xw = _tw(draw, x_label, fs)
    draw.text(((W - xw) // 2, H - 12 * S), x_label, fill=_hex(THEME["axisLabel"]), font=fs)

    # Grid + ticks
    gc, tc = _hex(THEME["grid"]), _hex(THEME["tickLabel"])
    for i in range(5):
        v = yn + (yx - yn) * i / 4
        yy = sy(v)
        draw.line([(pl, yy), (pl + cw, yy)], fill=gc, width=1)
        lbl = str(int(round(v)))
        lw = _tw(draw, lbl, fl)
        draw.text((pl - lw - 4 * S, yy - 5 * S), lbl, fill=tc, font=fl)
    for i in range(5):
        v = round(xn + xr * i / 4)
        vx = sx(v)
        draw.line([(vx, pt), (vx, pt + ch)], fill=gc, width=1)
        lbl = str(int(v))
        lw = _tw(draw, lbl, fl)
        draw.text((vx - lw // 2, pt + ch + 3 * S), lbl, fill=tc, font=fl)

    # Axes
    ac = _hex(THEME["axis"])
    draw.line([(pl, pt), (pl, pt + ch)], fill=ac, width=S)
    draw.line([(pl, pt + ch), (pl + cw, pt + ch)], fill=ac, width=S)

    # Confidence bands (semi-transparent fill)
    for sr in series:
        if len(sr["x"]) < 2:
            continue
        r, g, b = _hex(sr["color"])
        band_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        band_draw = ImageDraw.Draw(band_img)

        # Build polygon points: upper bound forward, lower bound backward
        upper = [(sx(x), sy(min(m + s, yx))) for x, m, s in zip(sr["x"], sr["mean"], sr["std"])]
        lower = [(sx(x), sy(max(m - s, 0))) for x, m, s in zip(sr["x"], sr["mean"], sr["std"])]
        polygon = upper + lower[::-1]
        if len(polygon) >= 3:
            band_draw.polygon(polygon, fill=(r, g, b, 40))
        img = Image.alpha_composite(img, band_img)
        draw = ImageDraw.Draw(img)

    # Mean lines
    for sr in series:
        if len(sr["x"]) < 2:
            continue
        pts = [(sx(x), sy(m)) for x, m in zip(sr["x"], sr["mean"])]
        draw.line(pts, fill=_hex(sr["color"]), width=S + 1)

    # Legend
    leg_x, leg_y = pl + 12 * S, pt + 8 * S
    lc = _hex(THEME["legend"])
    for i, sr in enumerate(series):
        lx = leg_x
        ly = leg_y + i * 14 * S
        draw.line([(lx, ly), (lx + 16 * S, ly)], fill=_hex(sr["color"]), width=S + 1)
        draw.text((lx + 20 * S, ly - 5 * S), sr["label"], fill=lc, font=fl)

    img = img.convert("RGB")
    img.save(path, "PNG")


# ── Plot: Summary table ──────────────────────────────────────────────────────


def _save_table(
    path: str,
    title: str,
    headers: list[str],
    rows: list[list[str]],
    colors: list[str],
    width: int = 900,
    scale: int = 2,
) -> None:
    """Draw a summary table with Pillow and save as PNG."""
    S = scale
    row_h = 28 * S
    pad_x, pad_y = 12 * S, 8 * S
    title_h = 30 * S
    ft, fh = _font(13 * S), _font(11 * S)
    fm = _font(11 * S, mono=True)

    # Measure column widths
    tmp = Image.new("RGB", (1, 1))
    td = ImageDraw.Draw(tmp)
    col_w: list[int] = []
    for ci in range(len(headers)):
        m = _tw(td, headers[ci], fh) + pad_x * 2
        for row in rows:
            if ci < len(row):
                m = max(m, _tw(td, row[ci], fm) + pad_x * 2)
        col_w.append(m)
    total_w = max(sum(col_w), width * S)
    ratio = total_w / sum(col_w)
    col_w = [int(c * ratio) for c in col_w]
    total_w = sum(col_w)
    total_h = (len(rows) + 1) * row_h + pad_y * 2 + title_h

    img = Image.new("RGB", (total_w, total_h), _hex("#1f2937"))
    draw = ImageDraw.Draw(img)

    # Title
    tw_t = _tw(draw, title, ft)
    draw.text(((total_w - tw_t) // 2, 6 * S), title, fill=_hex(THEME["title"]), font=ft)

    by = title_h
    # Header bg
    draw.rectangle([(0, by + pad_y), (total_w, by + pad_y + row_h)], fill=_hex("#111827"))
    x = 0
    for ci, hdr in enumerate(headers):
        draw.text((x + pad_x, by + pad_y + row_h // 4), hdr, fill=_hex("#9ca3af"), font=fh)
        x += col_w[ci]
    draw.line(
        [(0, by + pad_y + row_h), (total_w, by + pad_y + row_h)], fill=_hex("#374151"), width=S
    )

    # Data rows
    for ri, row in enumerate(rows):
        y = by + pad_y + (ri + 1) * row_h
        if ri % 2 == 1:
            draw.rectangle([(0, y), (total_w, y + row_h)], fill=_hex("#111827"))
        draw.line([(0, y + row_h), (total_w, y + row_h)], fill=_hex("#1f2937"), width=1)
        x = 0
        for ci, cell in enumerate(row):
            col = colors[ri] if ci == 0 and ri < len(colors) else "#e5e7eb"
            draw.text((x + pad_x, y + row_h // 4), cell, fill=_hex(col), font=fm)
            x += col_w[ci]

    img.save(path, "PNG")


# ── Plot generation from results ─────────────────────────────────────────────


def _downsample_step_data(
    all_runs: list[dict], max_pts: int = 200, metric: str = "objects_retrieved"
) -> tuple[list[float], list[float], list[float]]:
    """
    Compute mean ± std of a metric across runs at uniform x points.

    metric can be: "objects_retrieved", "average_energy", "messages_sent", "efficiency"
    Returns (x_values, means, stds).
    """
    if not all_runs:
        return [], [], []

    # Find the longest run
    max_steps = max(len(r["step_data"]) for r in all_runs)
    if max_steps == 0:
        return [], [], []

    # Sample at uniform intervals
    step_size = max(1, max_steps // max_pts)
    x_indices = list(range(0, max_steps, step_size))
    if x_indices[-1] != max_steps - 1:
        x_indices.append(max_steps - 1)

    x_vals: list[float] = []
    means: list[float] = []
    stds: list[float] = []

    for idx in x_indices:
        values = []
        for r in all_runs:
            sd = r["step_data"]
            actual_idx = min(idx, len(sd) - 1)
            entry = sd[actual_idx]
            if metric == "efficiency":
                step_num = entry["step"]
                val = (entry["objects_retrieved"] / step_num * 100) if step_num > 0 else 0
            else:
                val = entry.get(metric, 0)
            values.append(val)
        x_val = all_runs[0]["step_data"][min(idx, len(all_runs[0]["step_data"]) - 1)]["step"]
        x_vals.append(x_val)
        means.append(float(np.mean(values)))
        stds.append(float(np.std(values)))

    return x_vals, means, stds


def generate_plots(
    results: dict[str, dict[str, list[dict]]],
    out_base: str = "docs/benchmarks/logistic",
) -> None:
    """Generate all plots from benchmark results."""
    os.makedirs(out_base, exist_ok=True)

    # Per-instance plots
    for inst_name, cfg_results in results.items():
        # Parse instance name for directory structure
        # e.g. "mapd_50x50_few_random_objects25_seed42"
        parts = inst_name.split("_")
        grid_str = parts[1] if len(parts) > 1 else "unknown"
        density = parts[2] if len(parts) > 2 else "unknown"
        distribution = parts[3] if len(parts) > 3 else "unknown"

        inst_dir = os.path.join(out_base, grid_str, f"{density}_{distribution}")
        os.makedirs(inst_dir, exist_ok=True)

        config_names = list(cfg_results.keys())
        colors = [_pick_color(i) for i in range(len(config_names))]

        # 1) Bar chart: mean steps ± std (only if there's actual variance)
        means_steps = []
        stds_steps = []
        all_steps_flat: list[float] = []
        for cfg in config_names:
            steps_arr = [r["steps"] for r in cfg_results[cfg]]
            means_steps.append(float(np.mean(steps_arr)))
            stds_steps.append(float(np.std(steps_arr)))
            all_steps_flat.extend(steps_arr)

        # Skip steps charts if all values are identical (e.g. all hit max_steps)
        has_steps_variance = len(set(all_steps_flat)) > 1

        if has_steps_variance:
            _save_bar_chart_mean_std(
                os.path.join(inst_dir, "steps_mean_std.png"),
                f"Steps (mean ± std) — {grid_str} {density} {distribution}",
                config_names,
                means_steps,
                stds_steps,
                colors,
            )

        # 2) Box plot: steps distribution (only if there's variance)
        if has_steps_variance:
            data_arrays = [[r["steps"] for r in cfg_results[cfg]] for cfg in config_names]
            _save_box_plot(
                os.path.join(inst_dir, "steps_boxplot.png"),
                f"Steps Distribution — {grid_str} {density} {distribution}",
                config_names,
                data_arrays,
                colors,
            )

        # 3) Line chart: objects retrieved over time (mean ± std across seeds)
        series = []
        for i, cfg in enumerate(config_names):
            x_vals, means, stds = _downsample_step_data(
                cfg_results[cfg], metric="objects_retrieved"
            )
            if x_vals:
                series.append(
                    {
                        "label": cfg,
                        "color": _pick_color(i),
                        "x": x_vals,
                        "mean": means,
                        "std": stds,
                    }
                )
        if series:
            _save_line_chart_ci(
                os.path.join(inst_dir, "retrieval_ci.png"),
                f"Objects Retrieved (mean ± std) — {grid_str} {density} {distribution}",
                series,
                "Objects Retrieved",
            )

        # 4) Line chart: average energy over time
        series_energy = []
        for i, cfg in enumerate(config_names):
            x_vals, means, stds = _downsample_step_data(cfg_results[cfg], metric="average_energy")
            if x_vals:
                series_energy.append(
                    {
                        "label": cfg,
                        "color": _pick_color(i),
                        "x": x_vals,
                        "mean": means,
                        "std": stds,
                    }
                )
        if series_energy:
            _save_line_chart_ci(
                os.path.join(inst_dir, "energy_ci.png"),
                f"Average Energy (mean ± std) — {grid_str} {density} {distribution}",
                series_energy,
                "Avg Energy",
            )

        # 5) Line chart: efficiency (obj/100 steps) over time
        series_eff = []
        for i, cfg in enumerate(config_names):
            x_vals, means, stds = _downsample_step_data(cfg_results[cfg], metric="efficiency")
            if x_vals:
                series_eff.append(
                    {
                        "label": cfg,
                        "color": _pick_color(i),
                        "x": x_vals,
                        "mean": means,
                        "std": stds,
                    }
                )
        if series_eff:
            _save_line_chart_ci(
                os.path.join(inst_dir, "efficiency_ci.png"),
                f"Retrieval Efficiency (mean ± std) — {grid_str} {density} {distribution}",
                series_eff,
                "Obj / 100 steps",
            )

        # 6) Line chart: messages sent over time
        series_msgs = []
        for i, cfg in enumerate(config_names):
            x_vals, means, stds = _downsample_step_data(cfg_results[cfg], metric="messages_sent")
            if x_vals:
                series_msgs.append(
                    {
                        "label": cfg,
                        "color": _pick_color(i),
                        "x": x_vals,
                        "mean": means,
                        "std": stds,
                    }
                )
        if series_msgs:
            _save_line_chart_ci(
                os.path.join(inst_dir, "messages_ci.png"),
                f"Messages Sent (mean ± std) — {grid_str} {density} {distribution}",
                series_msgs,
                "Messages",
            )

        # 7) Bar chart: completion rate
        means_completion = []
        stds_completion = []
        for cfg in config_names:
            rates = [
                r["objects_retrieved"] / r["total_objects"] * 100
                for r in cfg_results[cfg]
                if r["total_objects"] > 0
            ]
            means_completion.append(float(np.mean(rates)) if rates else 0)
            stds_completion.append(float(np.std(rates)) if rates else 0)

        _save_bar_chart_mean_std(
            os.path.join(inst_dir, "completion_rate.png"),
            f"Completion Rate % (mean ± std) — {grid_str} {density} {distribution}",
            config_names,
            means_completion,
            stds_completion,
            colors,
            y_label="Completion %",
        )

        # 8) Summary table
        headers = ["Config", "Steps", "Completion%", "Efficiency", "Avg Energy", "Messages"]
        rows = []
        for cfg in config_names:
            runs = cfg_results[cfg]
            steps_arr = [r["steps"] for r in runs]
            compl_arr = [
                r["objects_retrieved"] / r["total_objects"] * 100
                for r in runs
                if r["total_objects"] > 0
            ]
            energy_arr = [r["avg_energy_final"] for r in runs]
            msgs_arr = [r["messages_sent"] for r in runs]
            last_step = np.mean(steps_arr)
            mean_retrieved = np.mean([r["objects_retrieved"] for r in runs])
            eff = (mean_retrieved / last_step * 100) if last_step > 0 else 0
            rows.append(
                [
                    cfg,
                    f"{np.mean(steps_arr):.0f} ± {np.std(steps_arr):.0f}",
                    f"{np.mean(compl_arr):.1f}% ± {np.std(compl_arr):.1f}",
                    f"{eff:.2f} obj/100s",
                    f"{np.mean(energy_arr):.1f}",
                    f"{np.mean(msgs_arr):.0f}",
                ]
            )
        _save_table(
            os.path.join(inst_dir, "summary_table.png"),
            f"Summary — {grid_str} {density} {distribution}",
            headers,
            rows,
            colors,
        )

    # ── Aggregate summary across all instances ──
    # Group by grid_size for aggregate comparison
    by_grid: dict[str, dict[str, list[float]]] = {}
    by_grid_completion: dict[str, dict[str, list[float]]] = {}
    for inst_name, cfg_results in results.items():
        parts = inst_name.split("_")
        grid_str = parts[1] if len(parts) > 1 else "unknown"
        for cfg_name, runs in cfg_results.items():
            by_grid.setdefault(grid_str, {}).setdefault(cfg_name, []).extend(
                [r["steps"] for r in runs]
            )
            by_grid_completion.setdefault(grid_str, {}).setdefault(cfg_name, []).extend(
                [
                    r["objects_retrieved"] / r["total_objects"] * 100
                    for r in runs
                    if r["total_objects"] > 0
                ]
            )

    for grid_str, cfg_data in by_grid.items():
        agg_dir = os.path.join(out_base, grid_str)
        os.makedirs(agg_dir, exist_ok=True)
        config_names = list(cfg_data.keys())
        colors = [_pick_color(i) for i in range(len(config_names))]

        # Steps aggregates — only if there's variance
        all_steps = [v for c in config_names for v in cfg_data[c]]
        has_variance = len(set(all_steps)) > 1

        if has_variance:
            means = [float(np.mean(cfg_data[c])) for c in config_names]
            stds = [float(np.std(cfg_data[c])) for c in config_names]

            _save_bar_chart_mean_std(
                os.path.join(agg_dir, "aggregate_steps.png"),
                f"Aggregate Steps (all instances) — {grid_str}",
                config_names,
                means,
                stds,
                colors,
            )

            data_arrays = [cfg_data[c] for c in config_names]
            _save_box_plot(
                os.path.join(agg_dir, "aggregate_boxplot.png"),
                f"Aggregate Steps Distribution — {grid_str}",
                config_names,
                data_arrays,
                colors,
            )

        # Completion rate aggregates — always meaningful
        compl_data = by_grid_completion.get(grid_str, {})
        if compl_data:
            compl_names = list(compl_data.keys())
            compl_means = [float(np.mean(compl_data[c])) for c in compl_names]
            compl_stds = [float(np.std(compl_data[c])) for c in compl_names]

            _save_bar_chart_mean_std(
                os.path.join(agg_dir, "aggregate_completion.png"),
                f"Aggregate Completion % (all instances) — {grid_str}",
                compl_names,
                compl_means,
                compl_stds,
                colors,
                y_label="Completion %",
            )

            compl_arrays = [compl_data[c] for c in compl_names]
            _save_box_plot(
                os.path.join(agg_dir, "aggregate_completion_boxplot.png"),
                f"Aggregate Completion % Distribution — {grid_str}",
                compl_names,
                compl_arrays,
                colors,
                y_label="Completion %",
            )

    print(f"  Plots saved to {out_base}/")


# ── JSON export ──────────────────────────────────────────────────────────────


def export_results_json(
    results: dict[str, dict[str, list[dict]]],
    path: str,
) -> None:
    """Export benchmark results to a JSON file (without bulky step_data)."""
    export: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "instances": {},
    }

    for inst_name in sorted(results.keys()):
        cfg_results = results[inst_name]
        inst_export: dict[str, Any] = {}

        for cfg_name, runs in cfg_results.items():
            steps_arr = [r["steps"] for r in runs]
            compl_arr = [
                r["objects_retrieved"] / r["total_objects"] * 100
                for r in runs
                if r["total_objects"] > 0
            ]
            energy_arr = [r["avg_energy_final"] for r in runs]
            msgs_arr = [r["messages_sent"] for r in runs]

            inst_export[cfg_name] = {
                "runs": len(runs),
                "steps": {
                    "mean": float(np.mean(steps_arr)),
                    "std": float(np.std(steps_arr)),
                    "min": int(min(steps_arr)),
                    "max": int(max(steps_arr)),
                },
                "completion_pct": {
                    "mean": float(np.mean(compl_arr)) if compl_arr else 0,
                    "std": float(np.std(compl_arr)) if compl_arr else 0,
                },
                "avg_energy_final": {
                    "mean": float(np.mean(energy_arr)),
                    "std": float(np.std(energy_arr)),
                },
                "messages_sent": {
                    "mean": float(np.mean(msgs_arr)),
                    "std": float(np.std(msgs_arr)),
                },
                "seeds": [r["seed"] for r in runs],
            }

        export["instances"][inst_name] = inst_export

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(export, f, indent=2)


# ── Summary table printer ────────────────────────────────────────────────────


def print_summary(results: dict[str, dict[str, list[dict]]]) -> None:
    """Print a text summary table to stdout."""
    print()
    print("=" * 100)
    print(
        f"  {'Instance':<45s} {'Config':<20s} {'Mean Steps':>10s} {'Std':>7s} "
        f"{'Compl%':>7s} {'AvgE':>6s}"
    )
    print("-" * 100)

    for inst_name in sorted(results.keys()):
        cfg_results = results[inst_name]
        for cfg_name, runs in cfg_results.items():
            steps_arr = [r["steps"] for r in runs]
            compl_arr = [
                r["objects_retrieved"] / r["total_objects"] * 100
                for r in runs
                if r["total_objects"] > 0
            ]
            energy_arr = [r["avg_energy_final"] for r in runs]

            mean_s = np.mean(steps_arr)
            std_s = np.std(steps_arr)
            mean_c = np.mean(compl_arr) if compl_arr else 0
            mean_e = np.mean(energy_arr)

            print(
                f"  {inst_name:<45s} {cfg_name:<20s} {mean_s:>10.1f} {std_s:>7.1f} "
                f"{mean_c:>6.1f}% {mean_e:>6.1f}"
            )
    print("=" * 100)


# ── Main ─────────────────────────────────────────────────────────────────────


def _load_config_file(path: str) -> dict[str, Any]:
    """Load a benchmark config JSON and return its contents as a dict."""
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-seed benchmark with MAPD logistics-grid instances.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show agent log lines")
    parser.add_argument(
        "--config",
        type=str,
        metavar="PATH",
        help="Path to a JSON config file (CLI args override config values)",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=None,
        metavar="N",
        help="Number of seeds to run per config (default: 30)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help="Number of parallel workers (default: cpu_count - 1)",
    )
    parser.add_argument(
        "--instances",
        nargs="+",
        metavar="FILTER",
        help="Filter instances by substring (e.g. '50x50', 'medium', 'border')",
    )
    parser.add_argument(
        "--mode",
        nargs="+",
        choices=["known", "unknown"],
        default=None,
        metavar="MODE",
        help="Map modes to test (default: both)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output directory for plots and JSON (default: docs/benchmarks/logistic)",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip plot generation (just print summary)",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip JSON results export",
    )
    parser.add_argument(
        "--unlimited-energy",
        action="store_true",
        help="Give agents unlimited energy (999999) so they never die",
    )
    args = parser.parse_args()

    # ── Merge config file + CLI args (CLI takes priority) ─────────────────
    cfg: dict[str, Any] = {}
    if args.config:
        cfg = _load_config_file(args.config)

    # Resolve final values: CLI > config file > defaults
    seeds = args.seeds if args.seeds is not None else cfg.get("seeds", 30)
    workers = args.workers if args.workers is not None else cfg.get("workers", None)
    instances_filter = args.instances if args.instances is not None else cfg.get("instances", None)
    modes = args.mode if args.mode is not None else cfg.get("mode", ["known", "unknown"])
    out_dir = args.out if args.out is not None else cfg.get("out", "docs/benchmarks/logistic")
    verbose = args.verbose or cfg.get("verbose", False)
    no_plots = args.no_plots or cfg.get("no_plots", False)
    no_json = args.no_json or cfg.get("no_json", False)
    unlimited_energy = args.unlimited_energy or cfg.get("unlimited_energy", False)

    # Find instances
    instances = list_instances(instances_filter)
    if not instances:
        print(f"ERROR: no instances match filters {instances_filter}")
        print(f"  Available in {INSTANCES_DIR}:")
        for f in sorted(INSTANCES_DIR.glob("mapd_*.json")):
            print(f"    {f.stem}")
        sys.exit(1)

    print("=" * 80)
    print("  MULTI-SEED BENCHMARK — MAPD Logistics Grid Instances")
    print("=" * 80)
    print(f"  Instances: {len(instances)}")
    for inst in instances:
        print(f"    • {inst.stem}")
    print(f"  Seeds: {seeds}")
    print(f"  Modes: {modes}")
    if unlimited_energy:
        print("  Energy: UNLIMITED (999999)")
    else:
        print(f"  Energy formula: ceil(total_energy / {NUM_AGENTS} agents)")

    # Show per-grid energy
    grid_sizes_seen = set()
    for inst in instances:
        data = json.loads(inst.read_text())
        gs = data["metadata"]["grid_size"]
        if gs not in grid_sizes_seen:
            grid_sizes_seen.add(gs)
            if unlimited_energy:
                print(f"    {gs}x{gs}: unlimited energy")
            else:
                energy = compute_energy_per_agent(gs)
                print(f"    {gs}x{gs}: {energy} energy/agent (total budget: {_TOTAL_ENERGY[gs]})")
    print()

    # Run benchmark
    results = run_multiseed_benchmark(
        instances=instances,
        modes=modes,
        num_seeds=seeds,
        num_workers=workers,
        verbose=verbose,
        unlimited_energy=unlimited_energy,
    )

    # Print summary
    print_summary(results)

    # Export JSON results
    if not no_json:
        json_path = os.path.join(out_dir, "results.json")
        os.makedirs(out_dir, exist_ok=True)
        export_results_json(results, json_path)
        print(f"\n  Results JSON saved to {json_path}")

    # Generate plots
    if not no_plots:
        print()
        generate_plots(results, out_dir)

    print()
    print("Done.")


if __name__ == "__main__":
    main()
