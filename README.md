# Multi-Agent Warehouse Retrieval System

A sophisticated swarm intelligence simulation featuring autonomous agents that coordinate to explore warehouses and retrieve objects.

## Features

- **Three Specialized Agent Roles:**
  - **Scouts**: Fast explorers with wide vision
  - **Coordinators**: Strategic planners managing task assignments
  - **Retrievers**: Heavy lifters that collect and transport objects

- **Advanced Algorithms:**
  - Frontier-based exploration
  - Velocity Obstacles collision avoidance
  - A\* pathfinding with dynamic replanning
  - Radius-based agent communication
  - Utility-based decision making

- **Real-time Web Visualization:**
  - React frontend with HTML5 Canvas rendering
  - WebSocket streaming for live updates
  - Interactive controls and metrics dashboard

- **Flexible Configuration:**
  - JSON-based scenario definition
  - Configurable grid sizes, obstacles, warehouses
  - Adjustable agent parameters per role

## Installation

### Prerequisites

- Python 3.11+
- Bun (JavaScript runtime and package manager)
- UV package manager

### Backend Setup

```bash
# Install dependencies
uv sync

# Run the backend server
uv run python -m backend.api.main
```

### Frontend Setup

```bash
cd frontend
bun install
bun run dev
```

The frontend will be available at `http://localhost:3000`

## Usage

1. Start the backend server (port 8000)
2. Start the frontend development server (port 3000)
3. Upload a JSON configuration file or use one of the examples in `configs/`
4. Click "Start Simulation" to begin
5. Watch agents explore, communicate, and retrieve objects in real-time

## Configuration Format

See `configs/simple_scenario.json` for an example configuration with:

- Grid dimensions
- Warehouse positions with entrances/exits
- Obstacle definitions
- Object spawn zones
- Agent counts and parameters per role

## Architecture

```text
warehouse-swarm-system/
├── backend/
│   ├── core/           # Grid, model, decision-making
│   ├── agents/         # Agent implementations
│   ├── algorithms/     # Pathfinding, exploration
│   ├── api/            # FastAPI + WebSocket
│   ├── config/         # Configuration schemas
│   └── metrics/        # Data collection & export
├── frontend/
│   └── src/
│       ├── components/ # React components
│       ├── hooks/      # Custom hooks
│       └── types/      # TypeScript types
├── configs/            # Example scenarios
└── tests/              # Unit & integration tests
```

## Development

```bash
# Run tests
uv run pytest

# Format code
uv run black backend/
uv run ruff check backend/

# Frontend development
cd frontend
bun run lint
bun run build
```

## License

MIT

## Authors

Developed for the Multi-Agent Systems course project.
