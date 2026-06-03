# Warehouse Swarm Intelligence System - Quick Start Guide

This guide will help you get the multi-agent warehouse retrieval simulation up and running.

## Project Overview

A sophisticated swarm intelligence simulation featuring autonomous agents (Scouts, Coordinators, Retrievers) that coordinate to explore warehouse environments and retrieve objects efficiently.

**Key Features:**

- Three specialized agent roles with distinct behaviors
- Real-time web visualization with HTML5 Canvas
- WebSocket streaming for live updates
- JSON-based configuration system
- Advanced pathfinding (A\*) and collision avoidance
- Frontier-based exploration strategies
- Utility-based decision making

## Prerequisites

- **Python 3.11+** - Required for backend
- **Bun** - JavaScript runtime and package manager for frontend
- **UV Package Manager** - For Python dependencies

### Install Required Tools

**Install UV (Python package manager):**

**Windows (PowerShell):**

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS/Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Install Bun (JavaScript runtime):**

**Windows (PowerShell):**

```powershell
powershell -c "irm bun.sh/install.ps1 | iex"
```

**macOS/Linux:**

```bash
curl -fsSL https://bun.sh/install | bash
```

## Installation

### 1. Backend Setup

```bash
# Navigate to project root
cd c:\Users\gbellamacina\Desktop\Workspace\test

# Install Python dependencies
uv sync

# Verify installation
uv run python -c "import mesa; import fastapi; print('Dependencies OK')"
```

### 2. Frontend Setup

```bash
# Navigate to frontend directory
cd frontend

# Install dependencies with Bun
bun install

# Verify installation
bun run build
```

## Running the Application

### Method 1: Manual Start (Recommended for Development)

**Terminal 1 - Backend:**

```bash
# From project root
uv run python -m backend.api.main
```

Backend will start on: `http://localhost:8000`

- API Docs: http://localhost:8000/docs
- Health Check: http://localhost:8000/api/health

**Terminal 2 - Frontend:**

```bash
# From frontend directory
cd frontend
bun run dev
```

Frontend will start on: `http://localhost:3000`

### Method 2: Quick Start Script

**Windows (PowerShell):**

```powershell
# Backend
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; uv run python -m backend.api.main"

# Frontend (wait 5 seconds for backend to start)
Start-Sleep -Seconds 5
cd frontend
bun run dev
```

## Using the Application

### 1. Access the Web Interface

Open your browser to: `http://localhost:3000`

### 2. Load a Configuration

#### Option A: Use the dropdown

- Select a MAPD logistics instance from the "MAPD Logistics" group (default: 50×50 few random)
- Legacy maps A/B are still available in the "Legacy" group
- Click "Load"

#### Option B: Upload Custom JSON

- Click "📂 Or pick a JSON file…"
- Select a MAPD instance JSON file
- The config will load automatically

### 3. Control the Simulation

- **Start/Resume**: Begin or continue simulation
- **Pause**: Temporarily halt execution
- **Stop**: End the current simulation
- **Reset**: Restart with same configuration

### 4. Monitor Progress

The interface displays:

- **Live Grid View**: Real-time agent movements and object locations
- **Metrics Panel**: Objects retrieved, energy levels, active agents
- **Step Counter**: Current simulation timestep

### Color Legend

- 🟢 **Green Circle**: Scout (fast explorer)
- 🔵 **Blue Hexagon**: Coordinator (task manager)
- 🟠 **Orange Square**: Retriever (object collector)
- 🟡 **Yellow Circle**: Object (to be retrieved)
- 🔵 **Blue Box**: Warehouse
- 🟩 **Green**: Warehouse entrance
- ⬛ **Black**: Obstacles

## Configuration Files

MAPD logistics instances are in `configs/logistics/`. Each defines a complete grid scenario:

### Instance naming: `mapd_{size}_{density}_{distribution}_objects{N}_seed{S}`

- **size**: `50x50`, `75x75`, `100x100`
- **density**: `few`, `medium`, `full` (obstacle coverage)
- **distribution**: `random`, `border` (object placement)

Example: `mapd_50x50_few_random_objects25_seed42.json`

Legacy configs (`configs/A.json`, `configs/B.json`) are kept for reference from the earlier project phase.

## MAPD Instance Format

```json
{
  "metadata": {
    "instance_id": "mapd_50x50_few_random_objects25_seed42",
    "grid_size": 50,
    "num_warehouses": 4,
    "num_objects": 25,
    "seed": 42,
    "obstacle_density": "few",
    "object_distribution": "random"
  },
  "grid": [[0, 0, 1, ...], ...],
  "warehouses": [{"cells": [...], "entrances": [...], "exits": [...]}],
  "objects": [[row, col], ...]
}
```

Grid cell values: `0`=free, `1`=wall, `2`=warehouse, `3`=entrance, `4`=exit.

## Troubleshooting

### Backend won't start

- Check Python version: `python --version` (need 3.11+)
- Reinstall dependencies: `uv sync --reinstall`
- Check port 8000 is free: `netstat -ano | findstr :8000`

### Frontend won't start

- Check Bun version: `bun --version` (need 1.0+)
- Clear node_modules: `rm -rf node_modules && bun install`
- Check port 3000 is free

### WebSocket connection fails

- Ensure backend started successfully
- Check browser console for errors
- Verify CORS settings in backend/api/main.py

### Simulation runs slowly

- Reduce grid size in configuration
- Decrease number of agents
- Lower update_rate in SimulationManager (line 23)

## Architecture Overview

```text
Backend (Python/FastAPI)
├── core/           # Grid, model, decision-making
├── agents/         # Scout, Coordinator, Retriever
├── algorithms/     # Pathfinding, exploration
├── api/            # REST + WebSocket endpoints
└── config/         # JSON schema validation

Frontend (React/TypeScript)
├── components/     # GridCanvas, ControlPanel, Metrics
├── hooks/          # useSimulation (WebSocket)
└── types/          # TypeScript definitions
```

## Development

### Running Tests

```bash
uv run pytest tests/
```

### Code Formatting

```bash
uv run black backend/
uv run ruff check backend/
```

### API Documentation

Visit `http://localhost:8000/docs` for interactive API documentation.

## Performance Tips

1. **Smaller grids** (< 100x100) render faster
2. **Fewer agents** (< 20 total) reduce computation
3. **Lower vision/communication radius** speeds up proximity queries
4. **Increase timestep_duration_ms** for slower, more observable simulation

## Known Limitations

- Agents can overlap in individual mode (group mode prevents this)
- Path smoothing may cause navigation through diagonals near obstacles
- WebSocket may disconnect on slow networks (auto-reconnect implemented)
- Large grids (> 200x200) may cause performance issues

## Project Structure

```text
root/
├── backend/              # Python backend
│   ├── agents/          # Agent implementations
│   ├── algorithms/      # Pathfinding & exploration
│   ├── api/             # FastAPI & WebSocket
│   ├── config/          # Configuration schemas
│   ├── core/            # Grid, model, decision
│   └── metrics/         # Data collection
├── frontend/            # React frontend
│   ├── src/
│   │   ├── components/ # UI components
│   │   ├── hooks/      # Custom hooks
│   │   └── types/      # TypeScript types
│   └── public/
├── configs/             # Example scenarios
├── tests/               # Unit tests
├── pyproject.toml       # Python dependencies
└── README.md            # This file
```

## Support & Contribution

This project was developed for Academic coursework on Multi-Agent Systems and Swarm Intelligence.

**Technologies Used:**

- **Backend**: Python, FastAPI, Mesa, NumPy, SciPy
- **Frontend**: React, TypeScript, Socket.IO, TailwindCSS
- **Tools**: UV (package manager), Vite (bundler)

## License

MIT License - See LICENSE file for details

## Acknowledgments

- Mesa framework for agent-based modeling
- FastAPI for modern Python web framework
- React ecosystem for frontend development
