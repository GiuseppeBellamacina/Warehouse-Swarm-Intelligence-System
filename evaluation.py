"""
Standard test suite — run all reference configurations and report results.

Uses the same SimulationManager and SimulationAgentsConfig defaults used by
the backend API, so results are identical to the web UI.

Generates PNG charts (same visual style as the frontend BenchmarkPanel) and
saves them to docs/benchmarks/<map>/.

Usage:
    python evaluation.py                      # quick summary, no images
    python evaluation.py -v                   # verbose (agent log lines)
    python evaluation.py --imgs               # generate benchmark charts and snapshots
    python evaluation.py --seed 42            # set random seed for reproducibility
    python evaluation.py --maps A B           # specify which maps to run (defaults to all)
    python evaluation.py --mode known unknown # specify map mode(s) to test (defaults to all)
    python evaluation.py --help               # show all options
"""

import argparse
import copy
import json
import math
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, ".")

from backend.api.simulation_manager import SimulationManager
from backend.config.schemas import (
    AgentRoleParams,
    GridScenarioConfig,
    ScoutBehaviorParams,
    SimulationAgentsConfig,
)
from backend.core.grid_manager import CellType

# ── PNG chart renderer (Pillow — mirrors frontend BenchmarkPanel) ────────────

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


def _hex(c: str) -> tuple[int, int, int]:
    """Convert hex colour to RGB tuple."""
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _font(size: int, mono: bool = False):
    """Load a TrueType font with fallback to Pillow default."""
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


def _font_bold(size: int):
    """Load a bold TrueType font — mirrors Canvas `bold Xpx sans-serif`."""
    names = ["arialbd.ttf", "Arial Bold.ttf", "Arial_Bold.ttf", "DejaVuSans-Bold.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    # Fallback: regular font (still renders, just not bold)
    return _font(size)


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    """Measure text width."""
    bb = draw.textbbox((0, 0), text, font=font)
    return int(bb[2] - bb[0])


def _paste_rotated(
    img: Image.Image, text: str, font, fill: tuple, cx: int, cy: int, angle: int = 90
) -> None:
    """Paste text rotated CCW by *angle* degrees, centred at (cx, cy)."""
    tmp = Image.new("RGBA", (800, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    bb = d.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text((-bb[0], -bb[1]), text, fill=(*fill, 255), font=font)
    cropped = tmp.crop((0, 0, tw, th))
    rotated = cropped.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    x, y = cx - rotated.width // 2, cy - rotated.height // 2
    img.paste(rotated, (x, y), rotated)


# ── Pillow chart savers ──────────────────────────────────────────────────────


def _save_line_chart(
    path: str,
    title: str,
    series: list[dict],
    y_label: str,
    x_label: str = "Step",
    width: int = 620,
    height: int = 320,
    scale: int = 2,
) -> None:
    """Draw a line chart with Pillow and save as PNG."""
    S = scale
    W, H = width * S, height * S
    img = Image.new("RGB", (W, H), _hex(THEME["bg"]))
    draw = ImageDraw.Draw(img)
    ft, fs, fl = _font(13 * S), _font(10 * S), _font(9 * S)

    pt, pr, pb, pl = 40 * S, 20 * S, 52 * S, 58 * S
    cw, ch = W - pl - pr, H - pt - pb

    # Data bounds
    xn = yn = float("inf")
    xx = yx = float("-inf")
    for sr in series:
        for d in sr["data"]:
            xn, xx = min(xn, d["x"]), max(xx, d["x"])
            yn, yx = min(yn, d["y"]), max(yx, d["y"])
    if xn > xx:
        xn, xx, yn, yx = 0, 1, 0, 1
    yr = (yx - yn) or 1
    yn, yx = max(0, yn - yr * 0.05), yx + yr * 0.05
    xr = (xx - xn) or 1

    def sx(x: float) -> int:
        return pl + int((x - xn) / xr * cw)

    def sy(y: float) -> int:
        return pt + ch - int((y - yn) / ((yx - yn) or 1) * ch)

    # Title
    tw_t = _tw(draw, title, ft)
    draw.text(((W - tw_t) // 2, 6 * S), title, fill=_hex(THEME["title"]), font=ft)

    # Y label (vertical)
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
        lbl = str(int(v)) if v == int(v) else f"{v:.1f}"
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

    # Data lines — collect pixel points for legend placement
    all_pts: list[tuple[int, int]] = []
    for sr in series:
        if len(sr["data"]) < 2:
            continue
        pts = [(sx(d["x"]), sy(d["y"])) for d in sr["data"]]
        all_pts.extend(pts)
        draw.line(pts, fill=_hex(sr["color"]), width=S + 1)

    # Legend — auto-place in the corner with fewest data points
    legend_h = len(series) * 14 * S + 6 * S
    legend_w = 120 * S  # approximate legend width
    margin = 8 * S
    corners = {
        "tl": (pl + margin, pt + margin),
        "tr": (pl + cw - legend_w - margin, pt + margin),
        "bl": (pl + margin, pt + ch - legend_h - margin),
        "br": (pl + cw - legend_w - margin, pt + ch - legend_h - margin),
    }
    best_corner = "tl"
    best_count = float("inf")
    for key, (cx0, cy0) in corners.items():
        count = sum(
            1
            for px_, py_ in all_pts
            if cx0 <= px_ <= cx0 + legend_w and cy0 <= py_ <= cy0 + legend_h
        )
        if count < best_count:
            best_count = count
            best_corner = key
    leg_x, leg_y = corners[best_corner]

    lc = _hex(THEME["legend"])
    for i, sr in enumerate(series):
        lx = leg_x
        ly = leg_y + i * 14 * S
        draw.line([(lx, ly), (lx + 16 * S, ly)], fill=_hex(sr["color"]), width=S + 1)
        draw.text((lx + 20 * S, ly - 5 * S), sr["label"], fill=lc, font=fl)

    img.save(path, "PNG")


def _save_bar_chart(
    path: str,
    title: str,
    labels: list[str],
    values: list[int],
    colors: list[str],
    y_label: str = "Steps",
    width: int = 620,
    height: int = 360,
    scale: int = 2,
) -> None:
    """Draw a bar chart with Pillow and save as PNG."""
    S = scale
    W, H = width * S, height * S
    img = Image.new("RGB", (W, H), _hex(THEME["bg"]))
    draw = ImageDraw.Draw(img)
    ft, fs, fl, fb = _font(13 * S), _font(10 * S), _font(8 * S), _font(10 * S)

    pt, pr, pb, pl = 40 * S, 20 * S, 100 * S, 58 * S
    cw, ch = W - pl - pr, H - pt - pb

    ym = max(values) * 1.12 if values else 1

    def sy(v: float) -> int:
        return pt + ch - int(v / ym * ch)

    gap = 8 * S
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
        v = round(ym * i / 4)
        yy = sy(v)
        draw.line([(pl, yy), (pl + cw, yy)], fill=gc, width=1)
        lbl = str(v)
        lw = _tw(draw, lbl, fl)
        draw.text((pl - lw - 4 * S, yy - 4 * S), lbl, fill=tc, font=fl)

    # Axes
    ac = _hex(THEME["axis"])
    draw.line([(pl, pt), (pl, pt + ch)], fill=ac, width=S)
    draw.line([(pl, pt + ch), (pl + cw, pt + ch)], fill=ac, width=S)

    # Bars + labels
    for i, (lbl, val, col) in enumerate(zip(labels, values, colors)):
        bx = pl + gap + i * (bw + gap)
        by = sy(val)
        draw.rectangle([(bx, by), (bx + bw, pt + ch)], fill=_hex(col))
        # Value on top
        vtxt = str(val)
        vw = _tw(draw, vtxt, fb)
        draw.text((bx + (bw - vw) // 2, by - 14 * S), vtxt, fill=_hex(THEME["legend"]), font=fb)
        # Label below (rotated 35° CCW)
        _paste_rotated(
            img, lbl, fl, _hex(THEME["tickLabel"]), bx + bw // 2, pt + ch + pb // 2, angle=35
        )
        draw = ImageDraw.Draw(img)

    img.save(path, "PNG")


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
            if ci == 0:
                col = _hex(colors[ri]) if ri < len(colors) else _hex("#6b7280")
                r_dot = 4 * S
                draw.ellipse(
                    [
                        (x + pad_x, y + row_h // 2 - r_dot),
                        (x + pad_x + r_dot * 2, y + row_h // 2 + r_dot),
                    ],
                    fill=col,
                )
                draw.text(
                    (x + pad_x + r_dot * 3, y + row_h // 4), cell, fill=_hex("#d1d5db"), font=fm
                )
            else:
                fill = _hex("#6b7280") if ci >= 6 else _hex("#d1d5db")
                draw.text((x + pad_x, y + row_h // 4), cell, fill=fill, font=fm)
            x += col_w[ci]

    img.save(path, "PNG")


# ── Grid snapshot renderer ────────────────────────────────────────────────────

# Agent colors matching the frontend GridCanvas
_ROLE_COLORS = {
    "scout": "#22c55e",
    "coordinator": "#3b82f6",
    "retriever": "#f97316",
}
_ROLE_SHORT = {"scout": "SCO", "coordinator": "COO", "retriever": "RET"}

# Cell → colour map
_CELL_COLORS = {
    CellType.FREE: "#0c0e14",
    CellType.OBSTACLE: "#4a4a4a",
    CellType.WAREHOUSE: "#1e3a5f",  # rgba(59,130,246,0.3) on bg
    CellType.WAREHOUSE_ENTRANCE: "#10b981",
    CellType.WAREHOUSE_EXIT: "#ef4444",
    CellType.OBJECT_ZONE: "#0c0e14",
    CellType.OBJECT: "#0c0e14",
    CellType.UNKNOWN: "#0c0e14",
}


def _save_grid_snapshot(
    path: str,
    model,
    title: str,
    trail_history: dict[int, list[tuple[int, int]]],
    cell_px: int = 20,
    scale: int = 2,
) -> None:
    """
    Render the final simulation state as a PNG grid snapshot.

    Faithfully replicates the frontend GridCanvas + exportSnapshot rendering:
    same layer order, same colours, same fog-of-war, same semi-transparent
    warehouse cells, same trail dots, same agent shapes / energy bars.
    """
    S = scale
    cp = cell_px * S  # pixels per cell
    gw, gh = model.grid.width, model.grid.height
    grid_w, grid_h = gw * cp, gh * cp
    panel_w = 260 * S
    W, H = grid_w + panel_w, max(grid_h, 400 * S)
    fsm = _font(9 * S)

    # ── 1. Classify cells ──
    cell_types = model.grid.cell_types  # ndarray [width, height], indexed [x, y]
    wh_cells: list[tuple[int, int]] = []
    ent_cells: list[tuple[int, int]] = []
    exit_cells: list[tuple[int, int]] = []
    obs_cells: list[tuple[int, int]] = []
    for x in range(gw):
        for y in range(gh):
            ct = CellType(cell_types[x, y])
            if ct == CellType.WAREHOUSE:
                wh_cells.append((x, y))
            elif ct == CellType.WAREHOUSE_ENTRANCE:
                ent_cells.append((x, y))
            elif ct == CellType.WAREHOUSE_EXIT:
                exit_cells.append((x, y))
            elif ct == CellType.OBSTACLE:
                obs_cells.append((x, y))

    # ── 2. Build global explored masks ──
    # local_map / vision_explored are shaped [height, width], indexed [y, x]
    is_map_known = getattr(model, "map_known", False)
    g_explored: np.ndarray | None = None
    g_obj_explored: np.ndarray | None = None
    for agent in model.agents:
        lm = getattr(agent, "local_map", None)
        if lm is not None:
            m = (lm != 0).astype(np.uint8)
            g_explored = m if g_explored is None else (g_explored | m)
        ve = getattr(agent, "vision_explored", None)
        if ve is not None:
            g_obj_explored = ve.copy() if g_obj_explored is None else (g_obj_explored | ve)

    # ── 3. Base image — panel bg #0f1117, grid bg #0c0e14 ──
    img = Image.new("RGBA", (W, H), (15, 17, 23, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (grid_w - 1, grid_h - 1)], fill=(12, 14, 20, 255))

    # ── 4. Grid lines (semi-transparent white, matching Canvas) ──
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    lc = (255, 255, 255, 10)  # ~rgba(255,255,255,0.04)
    for x in range(gw + 1):
        od.line([(x * cp, 0), (x * cp, grid_h)], fill=lc, width=1)
    for y in range(gh + 1):
        od.line([(0, y * cp), (grid_w, y * cp)], fill=lc, width=1)
    img = Image.alpha_composite(img, ov)

    # ── 5. Warehouse cells — semi-transparent blue fill + stroke ──
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    wh_fill = (59, 130, 246, 77)  # rgba(59,130,246,0.3)
    wh_stroke = (59, 130, 246, 128)  # rgba(59,130,246,0.5)
    for x, y in wh_cells:
        px, py = x * cp, y * cp
        od.rectangle([(px, py), (px + cp - 1, py + cp - 1)], fill=wh_fill)
        od.rectangle([(px, py), (px + cp - 1, py + cp - 1)], outline=wh_stroke, width=1)
    img = Image.alpha_composite(img, ov)

    # Entrances (solid #10b981)
    draw = ImageDraw.Draw(img)
    for x, y in ent_cells:
        draw.rectangle(
            [(x * cp, y * cp), (x * cp + cp - 1, y * cp + cp - 1)],
            fill=(16, 185, 129, 255),
        )
    # Exits (solid #ef4444)
    for x, y in exit_cells:
        draw.rectangle(
            [(x * cp, y * cp), (x * cp + cp - 1, y * cp + cp - 1)],
            fill=(239, 68, 68, 255),
        )
    # Obstacles (solid #4a4a4a)
    for x, y in obs_cells:
        draw.rectangle(
            [(x * cp, y * cp), (x * cp + cp - 1, y * cp + cp - 1)],
            fill=(74, 74, 74, 255),
        )

    # ── 6. Fog-of-war ──
    if is_map_known and g_obj_explored is not None:
        # map_known mode: amber tint + dot on unscanned cells
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for gy in range(gh):
            for gx in range(gw):
                if g_obj_explored[gy, gx] == 0:
                    px, py = gx * cp, gy * cp
                    od.rectangle(
                        [(px, py), (px + cp - 1, py + cp - 1)],
                        fill=(180, 140, 60, 46),  # rgba(180,140,60,0.18)
                    )
                    cx, cy = px + cp // 2, py + cp // 2
                    r = max(1, int(cp * 0.12))
                    od.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(250, 200, 50, 38))
        img = Image.alpha_composite(img, ov)

    elif not is_map_known and g_explored is not None:
        # Normal mode: dark fog on unexplored, bright tint on explored
        fog_ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fog_ov)
        fog_a = 166  # ~0.65 * 255 (global view)
        for gy in range(gh):
            for gx in range(gw):
                px, py = gx * cp, gy * cp
                if g_explored[gy, gx] == 0:
                    fd.rectangle(
                        [(px, py), (px + cp - 1, py + cp - 1)],
                        fill=(0, 0, 0, fog_a),
                    )
                else:
                    fd.rectangle(
                        [(px, py), (px + cp - 1, py + cp - 1)],
                        fill=(200, 220, 255, 18),  # rgba(200,220,255,0.07)
                    )
        img = Image.alpha_composite(img, fog_ov)

        # Diagonal hash pattern on unexplored cells
        hash_step = max(4 * S, cp // 3)
        hash_tile = Image.new("RGBA", (cp, cp), (0, 0, 0, 0))
        hd = ImageDraw.Draw(hash_tile)
        hc = (255, 255, 255, 15)  # rgba(255,255,255,0.06)
        for d in range(-cp, 2 * cp, hash_step):
            hd.line([(d, 0), (d - cp, cp)], fill=hc, width=max(1, S // 2))
        hash_ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for gy in range(gh):
            for gx in range(gw):
                if g_explored[gy, gx] == 0:
                    hash_ov.paste(hash_tile, (gx * cp, gy * cp))
        img = Image.alpha_composite(img, hash_ov)

    # ── 7. Objects (not retrieved — yellow circles, matching Canvas) ──
    draw = ImageDraw.Draw(img)
    for ox, oy in model.grid.objects:
        cx = int((ox + 0.5) * cp)
        cy = int((oy + 0.5) * cp)
        r = int(cp * 0.3)
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=_hex("#facc15"))

    # ── 8. Agent trails ──
    _offset_patterns = [
        (0.0, 0.0),
        (-0.2, -0.2),
        (0.2, -0.2),
        (-0.2, 0.2),
        (0.2, 0.2),
        (0.0, -0.25),
        (0.0, 0.25),
        (-0.25, 0.0),
        (0.25, 0.0),
    ]

    cell_visitors: dict[tuple[int, int], list[int]] = {}
    agent_roles: dict[int, str] = {}
    for agent in model.agents:
        agent_roles[agent.unique_id] = getattr(agent, "role", "unknown")
    for aid, positions in trail_history.items():
        for pos in positions:
            arr = cell_visitors.setdefault(pos, [])
            if aid not in arr:
                arr.append(aid)

    trail_ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(trail_ov)
    dot_r = max(1, int(cp * 0.12))
    trail_alpha = 89  # ~0.35 * 255

    for aid, positions in trail_history.items():
        role = agent_roles.get(aid, "unknown")
        base_col = _hex(_ROLE_COLORS.get(role, "#ffffff"))
        fill_col = base_col + (trail_alpha,)
        for pos in positions:
            visitors = cell_visitors.get(pos, [aid])
            idx = visitors.index(aid) if aid in visitors else 0
            if len(visitors) > 1:
                ox, oy = _offset_patterns[idx % len(_offset_patterns)]
            else:
                ox, oy = 0.0, 0.0
            cx = int((pos[0] + 0.5 + ox) * cp)
            cy = int((pos[1] + 0.5 + oy) * cp)
            td.ellipse(
                [(cx - dot_r, cy - dot_r), (cx + dot_r, cy + dot_r)],
                fill=fill_col,
            )

    img = Image.alpha_composite(img, trail_ov)
    draw = ImageDraw.Draw(img)

    # ── 9. Agents — shapes, energy bars, carrying count ──
    for agent in model.agents:
        if not agent.pos:
            continue
        ax, ay = agent.pos
        cx = int((ax + 0.5) * cp)
        cy = int((ay + 0.5) * cp)
        r = int(cp * 0.4)
        role = getattr(agent, "role", "unknown")
        colour = _hex(_ROLE_COLORS.get(role, "#ffffff"))

        if role == "scout":
            draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=colour)
        elif role == "coordinator":
            pts = [
                (
                    cx + int(r * math.cos(math.pi / 3 * i)),
                    cy + int(r * math.sin(math.pi / 3 * i)),
                )
                for i in range(6)
            ]
            draw.polygon(pts, fill=colour)
        else:
            draw.rectangle([(cx - r, cy - r), (cx + r, cy + r)], fill=colour)

        # Energy bar
        bar_w = int(cp * 0.8)
        bar_h = max(2 * S, 3)
        bx = ax * cp + int(cp * 0.1)
        by_ = int((ay + 0.9) * cp)
        draw.rectangle([(bx, by_), (bx + bar_w, by_ + bar_h)], fill=_hex("#334155"))
        max_e = getattr(agent, "max_energy", 100)
        ep = min(getattr(agent, "energy", 0) / max_e, 1.0) if max_e else 0
        e_col = "#22c55e" if ep > 0.5 else ("#facc15" if ep > 0.25 else "#ef4444")
        draw.rectangle([(bx, by_), (bx + int(bar_w * ep), by_ + bar_h)], fill=_hex(e_col))

        # Agent number label (type_index) — centred on the agent body
        # Matches the GridCanvas: bold sans-serif, white, dark shadow.
        type_index = getattr(model, "_frozen_type_index", {}).get(
            agent.unique_id, getattr(agent, "type_index", agent.unique_id + 1)
        )
        lbl = str(type_index)
        label_size = max(int(r * 0.9), 7 * S)
        f_lbl = _font_bold(label_size)
        bb = draw.textbbox((0, 0), lbl, font=f_lbl)
        lbl_w, lbl_h = bb[2] - bb[0], bb[3] - bb[1]
        # Precise vertical centre: account for top bearing
        lx = cx - lbl_w // 2 - bb[0]
        ly_ = cy - lbl_h // 2 - bb[1]
        # Shadow pass (1px offset, ~70% opacity)
        draw.text((lx + S, ly_ + S), lbl, fill=(0, 0, 0, 178), font=f_lbl)
        # White text
        draw.text((lx, ly_), lbl, fill=(255, 255, 255, 255), font=f_lbl)

        # Carrying count (yellow, above the agent)
        carrying = getattr(agent, "carrying_objects", 0)
        if carrying > 0:
            ct_label = str(carrying)
            ct_w = _tw(draw, ct_label, fsm)
            draw.text(
                (cx - ct_w // 2, cy - int(r * 1.5) - 2 * S),
                ct_label,
                fill=_hex("#facc15"),
                font=fsm,
            )

    # ── 10. Info panel (matches frontend exportSnapshot) ──
    padding = 16 * S
    px0 = grid_w + padding
    line_h = 18 * S
    ly = padding

    f_title = _font(16 * S)
    f_section = _font(11 * S)
    f_label = _font(12 * S, mono=True)
    f_value = _font(12 * S, mono=True)
    f_small = _font(10 * S)

    label_off = 130 * S

    def _draw_label(label: str, value: str, color: str = "#e5e7eb") -> None:
        nonlocal ly
        draw.text((px0, ly), label, fill=_hex("#9ca3af"), font=f_label)
        draw.text((px0 + label_off, ly), value, fill=_hex(color), font=f_value)
        ly += line_h

    def _draw_section(section_title: str) -> None:
        nonlocal ly
        ly += 6 * S
        draw.text((px0, ly), section_title.upper(), fill=_hex("#6b7280"), font=f_section)
        bb = draw.textbbox((px0, ly), section_title.upper(), font=f_section)
        ly = bb[3] + 4 * S  # line starts below the actual text bottom
        draw.line([(px0, ly), (W - padding, ly)], fill=_hex("#374151"), width=S)
        ly += line_h - 4 * S

    # Title
    draw.text((px0, ly + 4 * S), "Warehouse Swarm Intelligence", fill=_hex("#f3f4f6"), font=f_title)
    ly += 28 * S

    # Simulation section
    _draw_section("Simulation")
    _draw_label("Step", str(model.current_step), "#60a5fa")
    _draw_label(
        "Retrieved",
        f"{model.objects_retrieved} / {model.total_objects}",
        "#34d399",
    )
    progress = model.objects_retrieved / model.total_objects if model.total_objects > 0 else 0
    _draw_label(
        "Progress",
        f"{progress * 100:.1f}%",
        "#34d399" if progress > 0.5 else "#fbbf24",
    )
    avg_energy = (
        float(np.mean([getattr(a, "energy", 0) for a in model.agents])) if model.agents else 0.0
    )
    _draw_label("Avg Energy", f"{avg_energy:.1f}")
    active = len([a for a in model.agents if getattr(a, "energy", 0) > 0])
    _draw_label("Active Agents", str(active))
    _draw_label("Messages", str(model.comm_manager.messages_sent))

    # Agents section — per-agent listing (matches frontend exportSnapshot)
    _draw_section("Agents")
    role_colors = {"scout": "#22c55e", "coordinator": "#3b82f6", "retriever": "#f97316"}
    for role in ("scout", "coordinator", "retriever"):
        group = [a for a in model.agents if getattr(a, "role", "") == role]
        if not group:
            continue
        short = _ROLE_SHORT[role]
        for a in group:
            tag = f"{short} {getattr(a, 'type_index', a.unique_id + 1)}"
            draw.text((px0, ly), tag, fill=_hex(role_colors[role]), font=f_label)
            dl = getattr(a, "total_delivered", 0)
            if dl > 0:
                draw.text(
                    (px0 + 80 * S, ly),
                    f"delivered {dl}",
                    fill=_hex("#34d399"),
                    font=f_value,
                )
            ly += line_h

    # Config name
    ly += 6 * S
    draw.text((px0, ly), title, fill=_hex("#6b7280"), font=f_small)
    ly += line_h

    # Watermark
    draw.text(
        (px0, H - padding),
        f"Step {model.current_step}",
        fill=_hex("#4b5563"),
        font=f_small,
    )

    img = img.convert("RGB")
    img.save(path, "PNG")


# ── Snapshot collection ──────────────────────────────────────────────────────

# Type alias for trail data
TrailHistory = dict[int, list[tuple[int, int]]]


def _run(name: str, grid_cfg: GridScenarioConfig, agents_cfg: SimulationAgentsConfig):
    """Run a single simulation, collect per-step snapshots.  Returns (steps, snapshots, model, trails)."""
    mgr = SimulationManager()
    mgr.initialize_from_grid(grid_cfg, agents_cfg)
    assert mgr.model is not None, "initialize_from_grid failed to create model"
    model = mgr.model

    snapshots: list[dict] = []
    trails: TrailHistory = {}
    t0 = time.perf_counter()
    while model.running:
        model.step()
        # Record agent positions for trail rendering
        for agent in model.agents:
            if agent.pos:
                trails.setdefault(agent.unique_id, []).append((agent.pos[0], agent.pos[1]))
        snapshots.append(
            {
                "step": model.current_step,
                "objects_retrieved": model.objects_retrieved,
                "total_objects": model.total_objects,
                "average_energy": (
                    float(np.mean([getattr(a, "energy", 0) for a in model.agents]))
                    if model.agents
                    else 0.0
                ),
                "active_agents": len([a for a in model.agents if getattr(a, "energy", 0) > 0]),
                "messages_sent": model.comm_manager.messages_sent,
            }
        )
    elapsed = time.perf_counter() - t0

    # Freeze each agent's type_index NOW, while _type_index_map still belongs
    # to this run.  By the time _generate_charts renders snapshots, subsequent
    # simulations will have cleared the module-level map and the values would
    # be wrong without this snapshot.
    frozen: dict[int, int] = {
        a.unique_id: getattr(a, "type_index", a.unique_id + 1) for a in model.agents
    }
    setattr(model, "_frozen_type_index", frozen)

    steps = model.current_step
    done = model.objects_retrieved >= model.total_objects
    tag = "" if done else "  ** INCOMPLETE **"
    print(
        f"[{name:30s}]  {model.objects_retrieved}/{model.total_objects}"
        f"  in {steps:4d} steps  ({elapsed:.2f}s){tag}"
    )
    return steps, snapshots, model, trails


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
            retrievers=AgentRoleParams(
                count=5, vision_radius=3, communication_radius=3, carrying_capacity=2
            ),
        ),
    ),
    (
        "0S/0C/5R map_known",
        _default_agents(
            scouts=AgentRoleParams(count=0),
            coordinators=AgentRoleParams(count=0),
            retrievers=AgentRoleParams(
                count=5, vision_radius=3, communication_radius=3, carrying_capacity=2
            ),
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


def _generate_charts(
    map_name: str,
    results: list[tuple[str, int, list[dict], object, TrailHistory]],
    out_dir: str,
):
    """Generate PNG charts + table + final snapshots for one map and save to out_dir."""
    os.makedirs(out_dir, exist_ok=True)

    # 1) Line charts (one per metric, all configs overlaid)
    for cdef in CHART_DEFS:
        series = []
        for i, (name, _steps, snapshots, _mdl, _trails) in enumerate(results):
            ds = _downsample(snapshots)
            series.append(
                {
                    "label": name,
                    "color": _pick_color(i),
                    "data": [{"x": sn["step"], "y": cdef["extract"](sn)} for sn in ds],
                }
            )
        _save_line_chart(
            os.path.join(out_dir, f"{cdef['key']}.png"),
            f"{cdef['title']}  —  {map_name}",
            series,
            cdef["y_label"],
        )

    # 2) Bar chart — total steps comparison
    labels = [name for name, _, _, _, _ in results]
    values = [steps for _, steps, _, _, _ in results]
    colors = [_pick_color(i) for i in range(len(results))]
    _save_bar_chart(
        os.path.join(out_dir, "steps_comparison.png"),
        f"Total Steps Comparison  —  {map_name}",
        labels,
        values,
        colors,
    )

    # 3) Summary table
    headers = ["Config", "Steps", "Retrieved", "Completion", "Efficiency", "Avg Energy", "Messages"]
    rows = []
    for name, steps, snapshots, _model, _trails in results:
        last = snapshots[-1] if snapshots else {}
        retrieved = last.get("objects_retrieved", 0)
        total = last.get("total_objects", 0)
        pct = f"{(retrieved / total * 100):.1f}%" if total else "—"
        eff = f"{(retrieved / steps * 100):.2f}" if steps > 0 else "—"
        avg_e = f"{last.get('average_energy', 0):.0f}"
        msgs = str(last.get("messages_sent", 0))
        rows.append([name, str(steps), f"{retrieved}/{total}", pct, eff, avg_e, msgs])
    _save_table(
        os.path.join(out_dir, "summary_table.png"),
        f"Summary  —  {map_name}",
        headers,
        rows,
        colors,
    )

    # 4) Final grid snapshots (one per config)
    for i, (name, _steps, _snapshots, mdl, tr) in enumerate(results):
        safe = name.replace("/", "-").replace(" ", "_").strip("_")
        _save_grid_snapshot(
            os.path.join(out_dir, f"snapshot_{safe}.png"),
            mdl,
            f"{name}  \u2014  {map_name}",
            tr,
        )


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Run benchmark test suite with optional image export.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show agent log lines")
    parser.add_argument(
        "--imgs",
        action="store_true",
        help="Generate benchmark charts and final grid snapshots",
    )
    parser.add_argument(
        "--seed",
        type=int,
        metavar="N",
        help="Override the random seed for all maps",
    )
    parser.add_argument(
        "--maps",
        nargs="+",
        metavar="MAP",
        help="Maps to run, e.g. A B (defaults to all)",
    )
    parser.add_argument(
        "--mode",
        nargs="+",
        choices=["known", "unknown"],
        metavar="MODE",
        help="Map modes to test: known and/or unknown (defaults to all)",
    )
    parser.add_argument(
        "--seed-mine",
        type=str,
        metavar="N1-N2",
        help="Search best seed in range N1-N2 (e.g. 0-99)",
    )
    args = parser.parse_args()
    verbose = args.verbose
    generate_images = args.imgs
    import builtins

    _real_print = builtins.print

    # ── Apply --maps filter ───────────────────────────────────────────────
    grid_files = GRID_FILES
    if args.maps:
        requested = {m.upper() for m in args.maps}
        grid_files = [
            f for f in GRID_FILES if os.path.splitext(os.path.basename(f))[0].upper() in requested
        ]
        if not grid_files:
            _real_print(f"ERROR: no matching maps found for {args.maps}. Available: A, B")
            sys.exit(1)

    # ── Apply --mode filter ───────────────────────────────────────────────
    configs = CONFIGS
    if args.mode:
        modes = set(args.mode)

        def _matches_mode(cfg_name: str) -> bool:
            is_known = "map_known" in cfg_name
            return ("known" in modes and is_known) or ("unknown" in modes and not is_known)

        configs = [(n, a) for n, a in CONFIGS if _matches_mode(n)]
        if not configs:
            _real_print(f"ERROR: no configs match mode(s) {args.mode}")
            sys.exit(1)

    # ── Seed mining mode ─────────────────────────────────────────────────
    if args.seed_mine:
        from tqdm import tqdm

        parts = args.seed_mine.split("-")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            _real_print("ERROR: --seed-mine expects N1-N2 (e.g. 0-99)")
            sys.exit(1)
        s_start, s_end = int(parts[0]), int(parts[1])

        # Collect per-seed results: {seed: {map: {config: steps}}}
        seed_totals: dict[int, int] = {}
        seed_details: dict[int, dict[str, dict[str, int]]] = {}

        total_runs = (s_end - s_start + 1) * len(grid_files) * len(configs)
        pbar = tqdm(total=total_runs, desc="Seed mining", unit="run")

        for seed in range(s_start, s_end + 1):
            grand_total = 0
            seed_details[seed] = {}
            for grid_file in grid_files:
                with open(grid_file) as f:
                    grid_cfg = GridScenarioConfig(**json.load(f))
                grid_cfg_s = copy.deepcopy(grid_cfg)
                grid_cfg_s.metadata.seed = seed
                map_name = os.path.splitext(os.path.basename(grid_file))[0]
                seed_details[seed][map_name] = {}

                builtins.print = lambda *a, **kw: None
                for name, agents_cfg in configs:
                    steps, _, _, _ = _run(name, grid_cfg_s, agents_cfg)
                    seed_details[seed][map_name][name] = steps
                    grand_total += steps
                    pbar.update(1)
                builtins.print = _real_print

            seed_totals[seed] = grand_total

        pbar.close()

        # ── Rank seeds ───────────────────────────────────────────────────
        def _known_better(seed: int) -> bool:
            """True when every map_known config beats its unknown counterpart."""
            det = seed_details[seed]
            for m in det:
                for name_k, steps_k in det[m].items():
                    if "map_known" not in name_k:
                        continue
                    name_u = name_k.replace("map_known", "unknown")
                    steps_u = det[m].get(name_u)
                    if steps_u is not None and steps_k >= steps_u:
                        return False
            return True

        ranked = sorted(
            seed_totals.keys(),
            key=lambda s: (not _known_better(s), seed_totals[s]),
        )

        _real_print("\n" + "=" * 80)
        _real_print("  SEED MINING RESULTS")
        _real_print("=" * 80)
        _real_print(f"  {'Seed':>6s}  {'Total':>7s}  {'known<unknown':>14s}")
        _real_print("-" * 80)
        for s in ranked[:20]:
            flag = "  ✓" if _known_better(s) else "  ✗"
            _real_print(f"  {s:6d}  {seed_totals[s]:7d} {flag}")
        _real_print("-" * 80)
        best = ranked[0]
        _real_print(f"  ★ Best seed: {best}  (total steps: {seed_totals[best]})")

        # Detail for best seed
        _real_print()
        for m in sorted(seed_details[best]):
            _real_print(f"  Map {m}:")
            for cfg_name, st in seed_details[best][m].items():
                _real_print(f"    {cfg_name:30s}  {st:4d} steps")
        _real_print("=" * 80)
        return

    # ── Normal evaluation mode ───────────────────────────────────────────
    for grid_file in grid_files:
        with open(grid_file) as f:
            grid_cfg = GridScenarioConfig(**json.load(f))

        if args.seed is not None:
            grid_cfg.metadata.seed = args.seed

        map_name = os.path.splitext(os.path.basename(grid_file))[0]  # "A" or "B"
        meta = grid_cfg.metadata

        _real_print("=" * 80)
        _real_print(
            f"  TEST SUITE — {grid_file} "
            f"({meta.grid_size}×{meta.grid_size}, {meta.num_objects} objects, "
            f"max {meta.max_steps} steps, seed={meta.seed})"
        )
        _real_print("=" * 80)

        results: list[tuple[str, int, list[dict], object, TrailHistory]] = []
        for name, agents_cfg in configs:
            if not verbose:
                builtins.print = lambda *a, **kw: None
            steps, snapshots, model, trails = _run(name, grid_cfg, agents_cfg)
            if not verbose:
                builtins.print = _real_print
                _real_print(f"  [{name:30s}]  => {steps:4d} steps")
            results.append((name, steps, snapshots, model, trails))

        _real_print("-" * 80)
        _real_print(f"  Total steps: {sum(s for _, s, _, _, _ in results)}")

        # Generate charts & snapshots
        if generate_images:
            out_dir = os.path.join("docs", "benchmarks", map_name)
            _generate_charts(map_name, results, out_dir)
            _real_print(f"  Charts & snapshots saved to {out_dir}/")
        else:
            _real_print("  Image generation skipped (use --imgs to enable)")
        _real_print("=" * 80)
        _real_print()


if __name__ == "__main__":
    main()
