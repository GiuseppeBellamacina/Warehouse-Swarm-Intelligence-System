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

#### Option A: Use Example Scenarios

- Select "Simple" or "Complex" from the dropdown
- Click "Load" button

#### Option B: Upload Custom JSON

- Click "Choose File"
- Select a JSON configuration file
- Click "Upload & Start"

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

Example configurations are in `configs/`:

### simple_scenario.json

- Grid: 50x50
- Agents: 3 scouts, 1 coordinator, 2 retrievers
- Objects: 10
- Difficulty: Easy

### complex_scenario.json

- Grid: 100x100
- Agents: 5 scouts, 2 coordinators, 4 retrievers
- Objects: 30
- Difficulty: Hard with maze-like obstacles

## Creating Custom Configurations

See `configs/simple_scenario.json` as a template. Key sections:

```json
{
  "simulation": {
    "grid_width": 50,
    "grid_height": 50,
    "max_steps": 5000
  },
  "warehouse": {
    "position": {"x": 2, "y": 2},
    "width": 8,
    "height": 8,
    "entrances": [...]
  },
  "obstacles": [...],
  "objects": {
    "count": 10,
    "spawn_zones": [...]
  },
  "agents": {
    "scouts": {...},
    "coordinators": {...},
    "retrievers": {...}
  }
}
```

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
