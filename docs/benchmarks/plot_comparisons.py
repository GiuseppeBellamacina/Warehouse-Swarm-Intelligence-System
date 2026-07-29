#!/usr/bin/env python3
"""Generate comparison plots from logistic and logistic-unlimited benchmark results."""

import json
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

# ── Configuration ──────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
LOGISTIC_DIR = BASE / "logistic"
UNLIMITED_DIR = BASE / "logistic-unlimited"
CROSS_DIR = BASE  # cross-comparison plots go here

AGENT_CONFIGS = ["0S/0C/10R", "2S/2C/6R"]
DENSITIES = ["few", "medium", "full"]
DISTRIBUTIONS = ["border", "random"]
GRID_SIZES = ["50x50", "75x75", "100x100"]

# Per-agent max energy by grid size (from evaluation.py _TOTAL_ENERGY / NUM_AGENTS)
# Logistic: ceil(total_budget / 10 agents), Unlimited: 999999
LOGISTIC_MAX_ENERGY_PER_AGENT = {"50x50": 138, "75x75": 308, "100x100": 576}
UNLIMITED_MAX_ENERGY_PER_AGENT = {"50x50": 999999, "75x75": 999999, "100x100": 999999}

# Max steps per grid size (= grid_size × 10, from evaluation.py)
MAX_STEPS = {"50x50": 500, "75x75": 750, "100x100": 1000}

# Cap detection thresholds
ENERGY_CAP_THRESHOLD = 0.99  # energy_spent_pct >= 99% → at energy cap
STEP_CAP_THRESHOLD = 0.98  # steps / max_steps >= 98% → at step cap

sns.set_theme(style="whitegrid")
plt.rcParams.update({"figure.dpi": 150, "savefig.dpi": 150, "savefig.bbox": "tight"})


# ── Helpers ────────────────────────────────────────────────────────────────────
def load_results(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def parse_instance_name(name: str) -> dict:
    """Extract grid_size, density, distribution from instance name like
    'mapd_100x100_few_border_objects25_seed42'."""
    m = re.match(r"mapd_(\d+x\d+)_(few|medium|full)_(border|random)", name)
    if not m:
        raise ValueError(f"Unexpected instance name: {name}")
    return {"grid_size": m.group(1), "density": m.group(2), "distribution": m.group(3)}


def build_dataframe(data: dict, max_energy_map: dict[str, int]) -> dict:
    """Convert results.json data into a nested dict keyed by (grid_size, density, distribution, agent_config).

    Args:
        data: Loaded results.json dict.
        max_energy_map: Per-grid-size max energy per agent (e.g. {"50x50": 138, ...}).
    """
    df = {}
    for instance_name, configs in data["instances"].items():
        meta = parse_instance_name(instance_name)
        gs, den, dist = meta["grid_size"], meta["density"], meta["distribution"]
        max_e = max_energy_map[gs]
        for agent_cfg, metrics in configs.items():
            key = (gs, den, dist, agent_cfg)
            energy_spent = max_e - metrics["avg_energy_final"]["mean"]
            energy_spent_pct = (energy_spent / max_e * 100) if max_e > 0 else 0
            completion = metrics["completion_pct"]["mean"]
            # efficiency = completion % per 100 energy consumed (higher = better use of energy)
            eff_mean = (completion / energy_spent * 100) if energy_spent > 0 else 0
            eff_std = 0.0
            if eff_mean > 0:
                rel_c = metrics["completion_pct"]["std"] / completion if completion > 0 else 0
                rel_e = metrics["avg_energy_final"]["std"] / energy_spent if energy_spent > 0 else 0
                eff_std = eff_mean * np.sqrt(rel_c**2 + rel_e**2)
            df[key] = {
                "completion_mean": completion,
                "completion_std": metrics["completion_pct"]["std"],
                "steps_mean": metrics["steps"]["mean"],
                "steps_std": metrics["steps"]["std"],
                "messages_mean": metrics["messages_sent"]["mean"],
                "messages_std": metrics["messages_sent"]["std"],
                "energy_spent_mean": energy_spent,
                "energy_spent_std": metrics["avg_energy_final"]["std"],
                "energy_spent_pct": energy_spent_pct,
                "eff_mean": eff_mean,
                "eff_std": eff_std,
                "at_energy_cap": energy_spent_pct >= ENERGY_CAP_THRESHOLD * 100,
                "at_step_cap": metrics["steps"]["mean"] >= STEP_CAP_THRESHOLD * MAX_STEPS[gs],
            }
    return df


# ── Internal comparison plots (within one mode) ────────────────────────────────
def plot_internal_comparison(
    df: dict, output_dir: Path, label: str, max_energy_map: dict[str, int]
):
    """For each grid size, create a figure with 4 subplots (completion%, steps, messages, energy)
    comparing 0S/0C/10R vs 2S/2C/6R across density/distribution combos."""
    output_dir.mkdir(parents=True, exist_ok=True)

    for gs in GRID_SIZES:
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle(f"{label} — Internal Comparison: {gs} Grid", fontsize=14, fontweight="bold")

        metric_keys = [
            ("completion_mean", "completion_std", "Completion %", "Completion (%)"),
            ("steps_mean", "steps_std", "Steps to Completion", "Steps"),
            ("messages_mean", "messages_std", "Messages Sent", "Messages"),
            ("energy_spent_mean", "energy_spent_std", "Energy Consumed (per agent)", "Energy"),
        ]
        axes = axes.flatten()
        max_energy_for_gs = max_energy_map.get(gs, 999999)

        # Build x-axis labels: "few\nborder", "few\nrandom", "medium\nborder", ...
        x_labels = [f"{d}\n{dist}" for d in DENSITIES for dist in DISTRIBUTIONS]
        x = np.arange(len(x_labels))
        width = 0.35
        colors = ["#5b9bd5", "#ed7d31"]  # blue for 0S/0C/10R, orange for 2S/2C/6R

        for ax_idx, (mean_key, std_key, title, ylabel) in enumerate(metric_keys):
            ax = axes[ax_idx]
            for i, agent_cfg in enumerate(AGENT_CONFIGS):
                means = []
                stds = []
                for den in DENSITIES:
                    for dist in DISTRIBUTIONS:
                        key = (gs, den, dist, agent_cfg)
                        if key in df:
                            means.append(df[key][mean_key])
                            stds.append(df[key][std_key])
                        else:
                            means.append(0)
                            stds.append(0)

                ax.bar(
                    x + i * width - width / 2,
                    means,
                    width,
                    yerr=stds,
                    label=agent_cfg,
                    color=colors[i],
                    capsize=4,
                    edgecolor="white",
                    linewidth=0.6,
                )

            ax.set_title(title, fontsize=12)
            ax.set_ylabel(ylabel)
            ax.set_xticks(x)
            ax.set_xticklabels(x_labels, fontsize=9)
            ax.legend(fontsize=9)
            ax.grid(axis="y", alpha=0.3)

            # Energy subplot: fix y-axis baseline and draw cap line
            if mean_key == "energy_spent_mean":
                ax.set_ylim(bottom=0)
                # Only draw cap line if it's within a reasonable range (skip for unlimited: 999,999)
                all_vals = [df[key][mean_key] for key in df if key[0] == gs]
                data_max = max(all_vals) if all_vals else 0
                if max_energy_for_gs <= data_max * 3:
                    ax.axhline(
                        y=max_energy_for_gs,
                        color="red",
                        linestyle="--",
                        linewidth=1.5,
                        alpha=0.7,
                        label=f"energy cap ({max_energy_for_gs}/agent)",
                    )
                    # Merge the cap line into the legend
                    handles, labels = ax.get_legend_handles_labels()
                    ax.legend(handles, labels, fontsize=9)

        plt.tight_layout()
        fname = output_dir / f"internal_comparison_{gs.replace('x','x')}.png"
        fig.savefig(fname)
        plt.close(fig)
        print(f"  Saved: {fname}")


# ── Dedicated agent-config split plots (prominent error bars) ──────────────────
def plot_cross_by_agent_config(df_log: dict, df_unl: dict, output_dir: Path):
    """Create per-metric figures split by agent config with prominent error bars
    and STD annotations. Each figure has 2 rows (agent configs) x 3 cols (grid sizes)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics = [
        ("completion", "completion_mean", "completion_std", "Completion %", "%"),
        ("steps", "steps_mean", "steps_std", "Steps to Completion", "steps"),
        ("messages", "messages_mean", "messages_std", "Messages Sent", "msg"),
        ("efficiency", "eff_mean", "eff_std", "Energy Efficiency", "%/100e"),
    ]

    mode_colors = {"Logistic": "#5b9bd5", "Unlimited": "#ed7d31"}
    x_labels = [f"{d}\n{dist}" for d in DENSITIES for dist in DISTRIBUTIONS]

    for metric_name, mean_key, std_key, metric_title, unit in metrics:
        fig, axes = plt.subplots(2, 3, figsize=(22, 12))
        fig.suptitle(
            f"Agent Config Comparison — {metric_title}\n"
            f"Logistic vs Unlimited with Standard Deviation"
            + (" — [CAP] = at step cap" if metric_name == "steps" else ""),
            fontsize=16,
            fontweight="bold",
        )

        for row_i, agent_cfg in enumerate(AGENT_CONFIGS):
            for col_i, gs in enumerate(GRID_SIZES):
                ax = axes[row_i, col_i]
                ax.set_title(f"{agent_cfg} — {gs} Grid", fontsize=12, fontweight="bold")

                x = np.arange(len(x_labels))
                width = 0.35

                # Logistic bars
                means_log, stds_log = [], []
                for den in DENSITIES:
                    for dist in DISTRIBUTIONS:
                        k = (gs, den, dist, agent_cfg)
                        means_log.append(df_log[k][mean_key] if k in df_log else 0)
                        stds_log.append(df_log[k][std_key] if k in df_log else 0)

                bars_log = ax.bar(
                    x - width / 2,
                    means_log,
                    width,
                    yerr=stds_log,
                    label="Logistic",
                    color=mode_colors["Logistic"],
                    capsize=6,
                    edgecolor="white",
                    linewidth=0.8,
                    error_kw={"linewidth": 2, "elinewidth": 2},
                )

                # Unlimited bars
                means_unl, stds_unl = [], []
                for den in DENSITIES:
                    for dist in DISTRIBUTIONS:
                        k = (gs, den, dist, agent_cfg)
                        means_unl.append(df_unl[k][mean_key] if k in df_unl else 0)
                        stds_unl.append(df_unl[k][std_key] if k in df_unl else 0)

                bars_unl = ax.bar(
                    x + width / 2,
                    means_unl,
                    width,
                    yerr=stds_unl,
                    label="Unlimited",
                    color=mode_colors["Unlimited"],
                    capsize=6,
                    edgecolor="white",
                    linewidth=0.8,
                    error_kw={"linewidth": 2, "elinewidth": 2},
                )

                # Annotate STD values above error bar caps
                for bars, means, stds in [
                    (bars_log, means_log, stds_log),
                    (bars_unl, means_unl, stds_unl),
                ]:
                    for bar, mean, std in zip(bars, means, stds):
                        if mean > 0 and std > 0:
                            ax.annotate(
                                f"±{std:.1f}",
                                xy=(bar.get_x() + bar.get_width() / 2, mean + std),
                                xytext=(0, 4),
                                textcoords="offset points",
                                ha="center",
                                va="bottom",
                                fontsize=6.5,
                                fontweight="bold",
                                color="#333333",
                            )

                # For steps metric: mark step-cap bars with a ★
                if metric_name == "steps":
                    for src_df, bars, mode_color in [
                        (df_log, bars_log, mode_colors["Logistic"]),
                        (df_unl, bars_unl, mode_colors["Unlimited"]),
                    ]:
                        for bar_idx, (den, dist) in enumerate(
                            [(d, di) for d in DENSITIES for di in DISTRIBUTIONS]
                        ):
                            k = (gs, den, dist, agent_cfg)
                            if k in src_df and src_df[k].get("at_step_cap"):
                                bar = bars[bar_idx]
                                ax.annotate(
                                    "[CAP]",
                                    xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                                    xytext=(0, 12),
                                    textcoords="offset points",
                                    ha="center",
                                    va="bottom",
                                    fontsize=9,
                                    color="#cc0000",
                                    fontweight="bold",
                                    bbox=dict(
                                        boxstyle="round,pad=0.2", facecolor="white", alpha=0.85
                                    ),
                                )

                ax.set_xticks(x)
                ax.set_xticklabels(x_labels, fontsize=9)
                ax.set_ylabel(unit)
                ax.legend(fontsize=9, loc="upper right")
                ax.grid(axis="y", alpha=0.3)

                # Set y=0 as baseline
                ax.set_ylim(bottom=0)

        plt.tight_layout()
        fname = output_dir / f"cross_by_config_{metric_name}.png"
        fig.savefig(fname)
        plt.close(fig)
        print(f"  Saved: {fname}")

    # ── Energy: linear scale (both modes now in comparable 10²–10³ range) ──
    en_metric = (
        "energy",
        "energy_spent_mean",
        "energy_spent_std",
        "Energy Consumed (per agent)",
        "energy",
    )
    metric_name, mean_key, std_key, metric_title, unit = en_metric

    fig, axes = plt.subplots(2, 3, figsize=(22, 12))
    fig.suptitle(
        f"Agent Config Comparison — {metric_title}\n"
        f"Logistic (cap: 138–576) vs Unlimited (cap: 999,999) — hatched bars = at energy cap",
        fontsize=16,
        fontweight="bold",
    )

    for row_i, agent_cfg in enumerate(AGENT_CONFIGS):
        for col_i, gs in enumerate(GRID_SIZES):
            ax = axes[row_i, col_i]
            ax.set_title(f"{agent_cfg} — {gs} Grid", fontsize=12, fontweight="bold")

            x = np.arange(len(x_labels))
            width = 0.35

            means_log, stds_log = [], []
            for den in DENSITIES:
                for dist in DISTRIBUTIONS:
                    k = (gs, den, dist, agent_cfg)
                    means_log.append(df_log[k][mean_key] if k in df_log else 0)
                    stds_log.append(df_log[k][std_key] if k in df_log else 0)

            # Get cap flags for hatching
            cap_log, cap_unl = [], []
            for den in DENSITIES:
                for dist in DISTRIBUTIONS:
                    k = (gs, den, dist, agent_cfg)
                    cap_log.append(df_log[k]["at_energy_cap"] if k in df_log else False)
                    cap_unl.append(df_unl[k]["at_energy_cap"] if k in df_unl else False)

            bars_log = ax.bar(
                x - width / 2,
                means_log,
                width,
                yerr=stds_log,
                label="Logistic",
                color=mode_colors["Logistic"],
                capsize=6,
                edgecolor="white",
                linewidth=0.8,
                error_kw={"linewidth": 2, "elinewidth": 2},
            )
            # Hatch cap-reaching logistic bars
            for bar, capped in zip(bars_log, cap_log):
                if capped:
                    bar.set_hatch("//")
                    bar.set_edgecolor("#333333")

            means_unl, stds_unl = [], []
            for den in DENSITIES:
                for dist in DISTRIBUTIONS:
                    k = (gs, den, dist, agent_cfg)
                    means_unl.append(df_unl[k][mean_key] if k in df_unl else 0)
                    stds_unl.append(df_unl[k][std_key] if k in df_unl else 0)

            bars_unl = ax.bar(
                x + width / 2,
                means_unl,
                width,
                yerr=stds_unl,
                label="Unlimited",
                color=mode_colors["Unlimited"],
                capsize=6,
                edgecolor="white",
                linewidth=0.8,
                error_kw={"linewidth": 2, "elinewidth": 2},
            )
            # Hatch cap-reaching unlimited bars
            for bar, capped in zip(bars_unl, cap_unl):
                if capped:
                    bar.set_hatch("//")
                    bar.set_edgecolor("#333333")

            # Annotate STD above error bar caps
            for bars, means, stds in [
                (bars_log, means_log, stds_log),
                (bars_unl, means_unl, stds_unl),
            ]:
                for bar, mean, std in zip(bars, means, stds):
                    if mean > 0 and std > 0:
                        ax.annotate(
                            f"±{std:.1f}",
                            xy=(bar.get_x() + bar.get_width() / 2, mean + std),
                            xytext=(0, 4),
                            textcoords="offset points",
                            ha="center",
                            va="bottom",
                            fontsize=6.5,
                            fontweight="bold",
                            color="#333333",
                        )

            ax.set_xticks(x)
            ax.set_xticklabels(x_labels, fontsize=9)
            ax.set_ylabel(unit)
            ax.legend(fontsize=9, loc="upper right")
            ax.grid(axis="y", alpha=0.3)
            ax.set_ylim(bottom=0)

            # Energy cap line for logistic
            cap_e = LOGISTIC_MAX_ENERGY_PER_AGENT[gs]
            if cap_e > 0:
                ax.axhline(y=cap_e, color="red", linestyle=":", linewidth=1.2, alpha=0.6)

    plt.tight_layout()
    fname = output_dir / f"cross_by_config_{metric_name}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"  Saved: {fname}")


# ── Per-density summary plots (averaged across border/random) ───────────────────
def plot_cross_by_density(df_log: dict, df_unl: dict, output_dir: Path):
    """Create per-metric plots highlighting density effect by averaging border+random.
    Each figure has 2 rows (agent configs) x 3 cols (grid sizes).
    X-axis: density (few, medium, full)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics = [
        ("completion", "completion_mean", "completion_std", "Completion %", "%"),
        ("steps", "steps_mean", "steps_std", "Steps to Completion", "steps"),
        ("messages", "messages_mean", "messages_std", "Messages Sent", "msg"),
        ("efficiency", "eff_mean", "eff_std", "Energy Efficiency", "%/100e"),
        (
            "energy",
            "energy_spent_mean",
            "energy_spent_std",
            "Energy Consumed (per agent)",
            "energy",
        ),
    ]

    mode_colors = {"Logistic": "#5b9bd5", "Unlimited": "#ed7d31"}

    def avg_across_distributions(df_src, gs, den, agent_cfg, mean_key, std_key):
        """Average border+random for a given density. Returns (mean_avg, std_avg)."""
        means, stds = [], []
        for dist in DISTRIBUTIONS:
            k = (gs, den, dist, agent_cfg)
            if k in df_src:
                means.append(df_src[k][mean_key])
                stds.append(df_src[k][std_key])
        if not means:
            return 0, 0
        # Pooled mean and std: avg = mean(means), std_avg = sqrt(sum(std_i²)) / n
        avg_mean = np.mean(means)
        avg_std = np.sqrt(sum(s * s for s in stds)) / len(stds)
        return avg_mean, avg_std

    for metric_name, mean_key, std_key, metric_title, unit in metrics:
        fig, axes = plt.subplots(2, 3, figsize=(19, 11))
        fig.suptitle(
            f"Density Effect: {metric_title}\n"
            f"Logistic vs Unlimited — Averaged across Border/Random",
            fontsize=15,
            fontweight="bold",
        )

        for row_i, agent_cfg in enumerate(AGENT_CONFIGS):
            for col_i, gs in enumerate(GRID_SIZES):
                ax = axes[row_i, col_i]
                ax.set_title(f"{agent_cfg} — {gs} Grid", fontsize=12, fontweight="bold")

                x_labels = DENSITIES  # ["few", "medium", "full"]
                x = np.arange(len(x_labels))
                width = 0.35
                fallback = 0

                means_log, stds_log = [], []
                for den in DENSITIES:
                    m, s = avg_across_distributions(df_log, gs, den, agent_cfg, mean_key, std_key)
                    means_log.append(m if m > 0 else fallback)
                    stds_log.append(s)

                means_unl, stds_unl = [], []
                for den in DENSITIES:
                    m, s = avg_across_distributions(df_unl, gs, den, agent_cfg, mean_key, std_key)
                    means_unl.append(m if m > 0 else fallback)
                    stds_unl.append(s)

                ax.bar(
                    x - width / 2,
                    means_log,
                    width,
                    yerr=stds_log,
                    label="Logistic",
                    color=mode_colors["Logistic"],
                    capsize=6,
                    edgecolor="white",
                    linewidth=0.8,
                    error_kw={"linewidth": 2, "elinewidth": 2},
                )
                ax.bar(
                    x + width / 2,
                    means_unl,
                    width,
                    yerr=stds_unl,
                    label="Unlimited",
                    color=mode_colors["Unlimited"],
                    capsize=6,
                    edgecolor="white",
                    linewidth=0.8,
                    error_kw={"linewidth": 2, "elinewidth": 2},
                )

                ax.set_xticks(x)
                ax.set_xticklabels(x_labels, fontsize=11)
                ax.set_ylabel(unit)
                ax.legend(fontsize=9, loc="upper right")
                ax.grid(axis="y", alpha=0.3)

        plt.tight_layout()
        fname = output_dir / f"cross_by_density_{metric_name}.png"
        fig.savefig(fname)
        plt.close(fig)
        print(f"  Saved: {fname}")


# ── Cross-comparison plots (logistic vs unlimited) ─────────────────────────────
def plot_cross_comparison(df_log: dict, df_unl: dict, output_dir: Path):
    """Compare logistic vs unlimited across all configs."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1) Completion % heatmap-style grouped bars ──
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle(
        "Cross-Comparison: Logistic vs Unlimited — Completion %", fontsize=15, fontweight="bold"
    )

    for ax_i, agent_cfg in enumerate(AGENT_CONFIGS):
        ax = axes[ax_i]
        ax.set_title(f"Agent Config: {agent_cfg}", fontsize=13)

        # Build x-axis labels
        x_labels = []
        for gs in GRID_SIZES:
            for den in DENSITIES:
                for dist in DISTRIBUTIONS:
                    x_labels.append(f"{gs}\n{den} {dist}")

        x = np.arange(len(x_labels))
        width = 0.35

        means_log = []
        means_unl = []
        for gs in GRID_SIZES:
            for den in DENSITIES:
                for dist in DISTRIBUTIONS:
                    k = (gs, den, dist, agent_cfg)
                    means_log.append(df_log[k]["completion_mean"] if k in df_log else 0)
                    means_unl.append(df_unl[k]["completion_mean"] if k in df_unl else 0)

        ax.bar(
            x - width / 2, means_log, width, label="Logistic", color="#5b9bd5", edgecolor="white"
        )
        ax.bar(
            x + width / 2, means_unl, width, label="Unlimited", color="#ed7d31", edgecolor="white"
        )
        ax.set_xticks(x)
        ax.set_xticklabels(x_labels, fontsize=7, rotation=45, ha="right")
        ax.set_ylabel("Completion %")
        ax.legend()
        ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    fname = output_dir / "cross_completion_pct.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"  Saved: {fname}")

    # ── 2) Completion % delta (unlimited - logistic) ──
    fig, ax = plt.subplots(figsize=(16, 6))
    fig.suptitle(
        "Cross-Comparison: Δ Completion % (Unlimited − Logistic)", fontsize=14, fontweight="bold"
    )

    x_labels = []
    for gs in GRID_SIZES:
        for den in DENSITIES:
            for dist in DISTRIBUTIONS:
                x_labels.append(f"{gs}\n{den}\n{dist}")

    x = np.arange(len(x_labels))
    width = 0.35

    for i, agent_cfg in enumerate(AGENT_CONFIGS):
        deltas = []
        for gs in GRID_SIZES:
            for den in DENSITIES:
                for dist in DISTRIBUTIONS:
                    k = (gs, den, dist, agent_cfg)
                    c_log = df_log[k]["completion_mean"] if k in df_log else 0
                    c_unl = df_unl[k]["completion_mean"] if k in df_unl else 0
                    deltas.append(c_unl - c_log)
        ax.bar(
            x + i * width - width / 2,
            deltas,
            width,
            label=agent_cfg,
            color=["#5b9bd5", "#ed7d31"][i],
            edgecolor="white",
        )

    ax.axhline(y=0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=8, rotation=45, ha="right")
    ax.set_ylabel("Δ Completion %")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fname = output_dir / "cross_completion_delta.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"  Saved: {fname}")

    # ── 3) Steps, Messages & Efficiency: grouped bar per grid size ──
    for metric_name, metric_key, ylabel in [
        ("steps", "steps_mean", "Steps"),
        ("messages", "messages_mean", "Messages Sent"),
        ("efficiency", "eff_mean", "Energy Efficiency (%/100e)"),
    ]:
        fig, axes = plt.subplots(1, 3, figsize=(20, 6))
        fig.suptitle(
            f"Cross-Comparison: Logistic vs Unlimited — {ylabel}",
            fontsize=15,
            fontweight="bold",
        )

        for gs_i, gs in enumerate(GRID_SIZES):
            ax = axes[gs_i]
            ax.set_title(f"{gs} Grid", fontsize=13)

            x_labels = [f"{d}\n{dist}" for d in DENSITIES for dist in DISTRIBUTIONS]
            x = np.arange(len(x_labels))
            width = 0.2

            # 4 bars: logistic-0S/0C/10R, logistic-2S/2C/6R, unlimited-0S/0C/10R, unlimited-2S/2C/6R
            sources = [
                (df_log, "Logistic", 0, "#5b9bd5"),
                (df_log, "Logistic", 1, "#7fb8e0"),
                (df_unl, "Unlimited", 0, "#ed7d31"),
                (df_unl, "Unlimited", 1, "#f2a65a"),
            ]

            for j, (src_df, src_label, cfg_idx, color) in enumerate(sources):
                agent_cfg = AGENT_CONFIGS[cfg_idx]
                vals = []
                for den in DENSITIES:
                    for dist in DISTRIBUTIONS:
                        k = (gs, den, dist, agent_cfg)
                        vals.append(src_df[k][metric_key] if k in src_df else 0)
                offset = (j - 1.5) * width
                label = f"{src_label} {agent_cfg}"
                ax.bar(
                    x + offset,
                    vals,
                    width,
                    label=label,
                    color=color,
                    edgecolor="white",
                    linewidth=0.5,
                )

            ax.set_xticks(x)
            ax.set_xticklabels(x_labels, fontsize=9)
            ax.set_ylabel(ylabel)
            ax.legend(fontsize=7, ncol=2)
            ax.grid(axis="y", alpha=0.3)

        plt.tight_layout()
        fname = output_dir / f"cross_{metric_name}.png"
        fig.savefig(fname)
        plt.close(fig)
        print(f"  Saved: {fname}")

    # ── 4) Energy consumed: linear scale ──
    fig, axes = plt.subplots(1, 3, figsize=(20, 6))
    fig.suptitle(
        "Cross-Comparison: Logistic vs Unlimited — Energy Consumed\n"
        "Logistic (138–576) · Unlimited (~200–1200)",
        fontsize=15,
        fontweight="bold",
    )

    for gs_i, gs in enumerate(GRID_SIZES):
        ax = axes[gs_i]
        ax.set_title(f"{gs} Grid", fontsize=13)

        x_labels = [f"{d}\n{dist}" for d in DENSITIES for dist in DISTRIBUTIONS]
        x = np.arange(len(x_labels))
        width = 0.2

        sources = [
            (df_log, "Logistic", 0, "#5b9bd5"),
            (df_log, "Logistic", 1, "#7fb8e0"),
            (df_unl, "Unlimited", 0, "#ed7d31"),
            (df_unl, "Unlimited", 1, "#f2a65a"),
        ]

        for j, (src_df, src_label, cfg_idx, color) in enumerate(sources):
            agent_cfg = AGENT_CONFIGS[cfg_idx]
            vals = []
            for den in DENSITIES:
                for dist in DISTRIBUTIONS:
                    k = (gs, den, dist, agent_cfg)
                    vals.append(src_df[k]["energy_spent_mean"] if k in src_df else 0)
            offset = (j - 1.5) * width
            label = f"{src_label} {agent_cfg}"
            ax.bar(
                x + offset, vals, width, label=label, color=color, edgecolor="white", linewidth=0.5
            )

        ax.set_xticks(x)
        ax.set_xticklabels(x_labels, fontsize=9)
        ax.set_ylabel("Energy Consumed (per agent)")
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", alpha=0.3)

        # Energy cap line for logistic (drawn before legend so it appears)
        cap_e = LOGISTIC_MAX_ENERGY_PER_AGENT[gs]
        if cap_e > 0:
            ax.axhline(
                y=cap_e,
                color="red",
                linestyle=":",
                linewidth=1.2,
                alpha=0.6,
                label=f"logistic cap ({cap_e})",
            )
        ax.legend(fontsize=7, ncol=2)

    plt.tight_layout()
    fname = output_dir / "cross_energy.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"  Saved: {fname}")

    # ── 5) Energy consumed heatmap (linear, per-mode normalization) ──
    fig, axes = plt.subplots(1, 2, figsize=(18, 9))
    fig.suptitle(
        "Cross-Comparison: Energy Consumed (Logistic vs Unlimited)\n"
        "Linear scale — each panel has its own color range",
        fontsize=14,
        fontweight="bold",
    )

    for ax_i, (df_src, mode_label) in enumerate([(df_log, "Logistic"), (df_unl, "Unlimited")]):
        ax = axes[ax_i]
        rows = [f"{gs} {den}" for gs in GRID_SIZES for den in DENSITIES]
        cols = [f"{dist}\n{cfg}" for dist in DISTRIBUTIONS for cfg in AGENT_CONFIGS]
        data = np.zeros((len(rows), len(cols)))
        for ri, (gs, den) in enumerate([(g, d) for g in GRID_SIZES for d in DENSITIES]):
            for ci, (dist, cfg) in enumerate(
                [(d, c) for d in DISTRIBUTIONS for c in AGENT_CONFIGS]
            ):
                k = (gs, den, dist, cfg)
                data[ri, ci] = df_src[k]["energy_spent_mean"] if k in df_src else np.nan

        # Per-mode vmin/vmax from this panel's data (skip NaN)
        panel_data = data[~np.isnan(data)]
        pvmin = np.min(panel_data) if len(panel_data) > 0 else 0
        pvmax = np.max(panel_data) if len(panel_data) > 0 else 1

        sns.heatmap(
            data,
            annot=True,
            fmt=".0f",
            cmap="OrRd",
            vmin=pvmin,
            vmax=pvmax,
            ax=ax,
            xticklabels=cols,
            yticklabels=rows,
            linewidths=0.5,
            cbar_kws={"label": "Energy Consumed (per agent)"},
        )
        ax.set_title(f"{mode_label} Mode  [{pvmin:.0f}–{pvmax:.0f}]", fontsize=13)

    plt.tight_layout()
    fname = output_dir / "cross_heatmap_energy.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"  Saved: {fname}")

    fig, axes = plt.subplots(2, 1, figsize=(18, 10))
    fig.suptitle(
        "Cross-Comparison Summary: Completion % (Logistic vs Unlimited)",
        fontsize=15,
        fontweight="bold",
    )

    for ax_i, mode_data in enumerate([(df_log, "Logistic"), (df_unl, "Unlimited")]):
        df_src, mode_label = mode_data
        ax = axes[ax_i]

        # Rows: grid_size × density, Columns: 2S/2C/6R and 0S/0C/10R for border, then random
        rows = [f"{gs} {den}" for gs in GRID_SIZES for den in DENSITIES]
        cols = [f"{dist}\n{cfg}" for dist in DISTRIBUTIONS for cfg in AGENT_CONFIGS]

        data = np.zeros((len(rows), len(cols)))
        for ri, (gs, den) in enumerate([(g, d) for g in GRID_SIZES for d in DENSITIES]):
            for ci, (dist, cfg) in enumerate(
                [(d, c) for d in DISTRIBUTIONS for c in AGENT_CONFIGS]
            ):
                k = (gs, den, dist, cfg)
                data[ri, ci] = df_src[k]["completion_mean"] if k in df_src else np.nan

        sns.heatmap(
            data,
            annot=True,
            fmt=".0f",
            cmap="RdYlGn",
            vmin=0,
            vmax=100,
            ax=ax,
            xticklabels=cols,
            yticklabels=rows,
            linewidths=0.5,
            cbar_kws={"label": "Completion %"},
        )
        ax.set_title(f"{mode_label} Mode", fontsize=13)

    plt.tight_layout()
    fname = output_dir / "cross_heatmap_completion.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"  Saved: {fname}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("Loading data...")
    data_log = load_results(LOGISTIC_DIR / "results.json")
    data_unl = load_results(UNLIMITED_DIR / "results.json")

    df_log = build_dataframe(data_log, LOGISTIC_MAX_ENERGY_PER_AGENT)
    df_unl = build_dataframe(data_unl, UNLIMITED_MAX_ENERGY_PER_AGENT)

    # ── Internal comparisons ──
    print("\n--- Internal: Logistic ---")
    plot_internal_comparison(df_log, LOGISTIC_DIR, "Logistic", LOGISTIC_MAX_ENERGY_PER_AGENT)

    print("\n--- Internal: Unlimited ---")
    plot_internal_comparison(df_unl, UNLIMITED_DIR, "Unlimited", UNLIMITED_MAX_ENERGY_PER_AGENT)

    # ── Cross-comparison ──
    print("\n--- Cross-Comparison: By Density (averaged border/random) ---")
    plot_cross_by_density(df_log, df_unl, CROSS_DIR)

    print("\n--- Cross-Comparison: By Agent Config (prominent error bars) ---")
    plot_cross_by_agent_config(df_log, df_unl, CROSS_DIR)

    print("\n--- Cross-Comparison: Logistic vs Unlimited ---")
    plot_cross_comparison(df_log, df_unl, CROSS_DIR)

    print("\n[DONE] All plots generated successfully!")


if __name__ == "__main__":
    main()
