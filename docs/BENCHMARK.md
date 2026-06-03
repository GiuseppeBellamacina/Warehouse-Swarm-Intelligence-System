# Benchmark Guide

## Overview

The benchmark system runs multi-seed evaluations on MAPD (Multi-Agent Pickup and Delivery) logistics-grid instances stored in `configs/logistics/`. It tests different agent configurations across multiple random seeds and produces statistical plots and JSON results.

**Total formula:** `18 instances × N_configs × N_seeds` runs.

The default configs are:

| Config              | Agents                                   | Description                    |
| ------------------- | ---------------------------------------- | ------------------------------ |
| `0S/0C/10R known`   | 10 Retrievers                            | All retrievers, map pre-known  |
| `0S/0C/10R unknown` | 10 Retrievers                            | All retrievers, map to explore |
| `2S/2C/6R known`    | 2 Scouts + 2 Coordinators + 6 Retrievers | Mixed team, map pre-known      |
| `2S/2C/6R unknown`  | 2 Scouts + 2 Coordinators + 6 Retrievers | Mixed team, map to explore     |

So with default settings: **18 instances × 4 configs × 30 seeds = 2160 runs**.

---

## Quick Start

```bash
# Full benchmark (all instances, 30 seeds, all modes)
python evaluation.py

# Quick test: one instance, 5 seeds
python evaluation.py --instances 50x50_few_random --seeds 5

# Using a config file
python evaluation.py --config configs/benchmark_example.json

# Config file + CLI override (CLI wins)
python evaluation.py --config configs/benchmark_example.json --seeds 10
```

---

## CLI Parameters

| Parameter               | Default                    | Description                                                        |
| ----------------------- | -------------------------- | ------------------------------------------------------------------ |
| `--config PATH`         | —                          | JSON config file (see below). CLI args override values from config |
| `--seeds N`             | 30                         | Number of random seeds per configuration                           |
| `--workers N`           | cpu_count - 1              | Parallel workers for multiprocessing                               |
| `--instances FILTER...` | all                        | Filter instances by substring(s). Multiple filters are ANDed       |
| `--unlimited-energy`    | false                      | Give agents unlimited energy (999999)                              |
| `--out DIR`             | `docs/benchmarks/logistic` | Output directory for plots and JSON                                |
| `--no-plots`            | false                      | Skip plot generation (just print summary + JSON)                   |
| `--no-json`             | false                      | Skip JSON results export                                           |
| `-v` / `--verbose`      | false                      | Show per-agent log lines during simulation                         |

### Instance Filters

Instances are named: `mapd_{grid}_{density}_{distribution}_objects{N}_seed{S}`

Available filter values:

- **Grid size:** `50x50`, `75x75`, `100x100`
- **Density:** `few`, `medium`, `full`
- **Distribution:** `random`, `border`

Filters are combined with AND logic:

```bash
--instances 50x50                  # all 50x50 instances (6)
--instances medium                 # all medium-density instances (6)
--instances 75x75 border           # 75x75 AND border (3)
--instances 100x100 few random     # only 100x100_few_random (1)
```

---

## Config File

Instead of passing many CLI parameters, you can use a JSON config file:

```json
{
  "seeds": 30,
  "workers": 8,
  "instances": ["50x50", "75x75"],
  "mode": ["known", "unknown"],
  "out": "docs/benchmarks/logistic",
  "no_plots": false,
  "no_json": false,
  "verbose": false
}
```

All fields are optional. Omitted fields use their defaults.

**Priority:** CLI argument > config file > default value.

```bash
# Uses config but overrides seeds to 5
python evaluation.py --config my_config.json --seeds 5
```

---

## Outputs

### JSON Results (`results.json`)

Saved to `<out_dir>/results.json`. Contains per-instance, per-config aggregated statistics:

```json
{
  "generated_at": "2026-06-03T11:00:00",
  "instances": {
    "mapd_50x50_few_random_objects25_seed42": {
      "0S/0C/10R unknown": {
        "runs": 30,
        "steps": { "mean": 500.0, "std": 0.0, "min": 500, "max": 500 },
        "completion_pct": { "mean": 64.0, "std": 2.1 },
        "avg_energy_final": { "mean": 0.0, "std": 0.0 },
        "messages_sent": { "mean": 979.0, "std": 12.3 },
        "seeds": [0, 1, 2, ...]
      }
    }
  }
}
```

### Charts (per instance)

Generated in `<out_dir>/<grid>/<density>_<distribution>/`:

| File                  | Description                                                   |
| --------------------- | ------------------------------------------------------------- |
| `steps_mean_std.png`  | Bar chart — mean steps ± std per config                       |
| `steps_boxplot.png`   | Box plot — steps distribution per config                      |
| `retrieval_ci.png`    | Line chart — objects retrieved over time (mean ± std band)    |
| `energy_ci.png`       | Line chart — average agent energy over time (mean ± std band) |
| `efficiency_ci.png`   | Line chart — retrieval efficiency (obj/100 steps) over time   |
| `messages_ci.png`     | Line chart — messages sent over time (mean ± std band)        |
| `completion_rate.png` | Bar chart — completion rate % (mean ± std)                    |
| `summary_table.png`   | Table image — all metrics summarized                          |

### Aggregate Charts (per grid size)

Generated in `<out_dir>/<grid>/`:

| File                    | Description                                                   |
| ----------------------- | ------------------------------------------------------------- |
| `aggregate_steps.png`   | Bar chart — mean steps across all instances of that grid size |
| `aggregate_boxplot.png` | Box plot — steps distribution across all instances            |

---

## Energy Budget

Energy per agent is computed from the README formula:

```
total_energy = floor(0.8 × medium_traversable_cells)
per_agent = ceil(total_energy / N_agents)
```

| Grid    | Traversable (medium) | Total Budget | Per Agent (10 agents) |
| ------- | -------------------- | ------------ | --------------------- |
| 50×50   | 1726                 | 1380         | 138                   |
| 75×75   | 3841                 | 3072         | 308                   |
| 100×100 | 7192                 | 5753         | 576                   |

Each cell moved costs 1 energy. Scouts with speed=2 consume 2 energy per tick.
Agents that run out of energy die and become obstacles.

---

## Examples

```bash
# Fast sanity check
python evaluation.py --instances 50x50_few_random --seeds 2 --workers 1 --no-plots

# Only 50x50 maps, 10 seeds
python evaluation.py --instances 50x50 --seeds 10

# Full benchmark with JSON config
python evaluation.py --config configs/benchmark_example.json

# Generate only JSON data, no plots
python evaluation.py --no-plots --seeds 30

# Generate only plots, no JSON
python evaluation.py --no-json --seeds 30
```
