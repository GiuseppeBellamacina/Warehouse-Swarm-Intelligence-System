# Warehouse Swarm Intelligence System

**[Live demo](https://warehouse-swarm-intelligence-system.vercel.app)**

**[Relazione PDF](docs/relazione.pdf)**

A real-time swarm intelligence simulation where autonomous agents cooperate to explore warehouses and retrieve objects. Built with a Python/FastAPI backend and a React/TypeScript frontend.

---

## Features

### Three specialised agent roles

| Role        | Colour | Responsibility                                           |
| ----------- | ------ | -------------------------------------------------------- |
| Scout       | Green  | Explore the map, report object locations to coordinators |
| Coordinator | Blue   | Assign retrieval tasks, manage agent recharging          |
| Retriever   | Yellow | Navigate to objects, carry them to the deposit zone      |

### Algorithms

#### Pathfinding & navigation

- A\* pathfinding with dynamic replanning, forbidden-zone support and diagonal corner-cutting prevention
- Warehouse door directionality — entrance and exit cells are enforced via forbidden types so agents always enter/exit through the correct door
- A\* path-distance warehouse selection — agents choose the optimal warehouse using real path length instead of Manhattan distance, combined with congestion and energy-feasibility scoring
- Swap & yield protocols — head-on corridor swaps, `ClearWayMessage` chain negotiation, yield cooldown (3-step penalty after yielding)

#### Exploration (Scout)

- Frontier-based exploration with sub-linear distance weighting ($d^{0.4}$), target lock, recent-targets blacklist and far-frontier preference
- Two-level anti-clustering (hard distance filter + soft utility penalty)
- Stale coverage patrol — cyclic revisitation of old zones driven by a per-cell age matrix
- Passive relay + coordinator search — scouts relay map data and scan at 4× comm radius when no coordinator is nearby
- Vision-explored tracking with fog-of-war for `map_known` mode

#### Coordination (Coordinator)

- Object-biased centroid positioning — the centroid is weighted toward known objects when tasks are pending
- Boredom patrol — after 50 idle steps the coordinator leaves the centroid to explore
- Seek-retriever — actively moves toward the nearest retriever (with stale-position filtering) when tasks are pending
- Chokepoint detection and blocking heuristic for spatial awareness
- Sync rate limiting (max 1 sync every 10 steps per peer)

#### Retrieval (Retriever)

- Hive-mind self-assignment with 4-layer safety (grid truth → claim age → peer queue → atomic claim)
- Stale claim takeover — claims older than 50 steps can be acquired by another retriever
- P3b opportunistic pickup — retriever claims extra objects within vision radius during transit
- Task queue reordering by Manhattan distance at every step
- Stale task cancellation on map share — tasks for missing objects are dropped immediately
- Peer yield via `TaskStatusMessage` and peer-to-peer object/cargo broadcast via `RetrieverEventMessage`
- Cargo drop on energy depletion — objects are released on adjacent cells and broadcast to peers

#### Communication

- Radius-based inter-agent communication (scout → coordinator → retriever)
- Timestamped message wrappers with "newest wins" merge
- Atomic `try_claim_object` protocol with age-based takeover and energy preemption
- Tombstone mechanism for stale objects — prevents re-propagation of already-collected objects
- `messages_sent` counter per recipient for communication cost metrics

### Real-time interface

- HTML5 Canvas grid rendering streamed over Socket.IO
- Fog-of-war / scan fog — unexplored cells are dimmed, toggleable per-agent or global
- Resizable panel layout (agents · map · metrics · controls)
- Click on any agent in the grid to select it and view its details
- Timeline slider with step history — scrub back through past simulation states
- Benchmark panel with multi-run execution and CSV export
- Map editor for drawing custom scenarios
- Dirty settings reminder — visual warning when unsaved parameter changes exist
- Log-scale speed slider with 1× label
- ⚡ "Wake up backend" button — polls `/api/health` until Render cold-starts (~30 s)

### Optional Telegram notifications

Receive a message when a simulation starts, completes or is stopped (see [Environment variables](#environment-variables)).

---

## Architecture

```text
├── 📁 backend
│   ├── 📁 agents
│   │   ├── 🐍 __init__.py
│   │   ├── 🐍 base_agent.py
│   │   ├── 🐍 coordinator_agent.py
│   │   ├── 🐍 retriever_agent.py
│   │   └── 🐍 scout_agent.py
│   ├── 📁 algorithms
│   │   ├── 🐍 __init__.py
│   │   ├── 🐍 collision_avoidance.py
│   │   ├── 🐍 exploration.py
│   │   └── 🐍 pathfinding.py
│   ├── 📁 api
│   │   ├── 🐍 __init__.py
│   │   ├── 🐍 main.py
│   │   ├── 🐍 session_registry.py
│   │   ├── 🐍 simulation_manager.py
│   │   ├── 🐍 telegram_notifier.py
│   │   └── 🐍 websocket_manager.py
│   ├── 📁 config
│   │   ├── 🐍 __init__.py
│   │   ├── 🐍 config_loader.py
│   │   ├── 🐍 schemas.py
│   │   └── 🐍 settings.py
│   ├── 📁 core
│   │   ├── 🐍 __init__.py
│   │   ├── 🐍 communication.py
│   │   ├── 🐍 decision_maker.py
│   │   ├── 🐍 framework.py
│   │   ├── 🐍 grid_manager.py
│   │   └── 🐍 warehouse_model.py
│   ├── 📁 metrics
│   │   ├── 🐍 __init__.py
│   │   └── 🐍 collector.py
│   └── 🐍 __init__.py
├── 📁 configs
│   ├── ⚙️ A.json
│   └── ⚙️ B.json
├── 📁 docs
│   ├── 📁 benchmarks
│   │   ├── 📁 A
│   │   │   ├── 🖼️ benchmark-efficiency-2026-03-11.png
│   │   │   ├── 🖼️ benchmark-energy-2026-03-11.png
│   │   │   ├── 🖼️ benchmark-messages-2026-03-11.png
│   │   │   ├── 🖼️ benchmark-retrieval-2026-03-11.png
│   │   │   └── 🖼️ benchmark-table-2026-03-11.png
│   │   ├── 📁 B
│   │   │   ├── 🖼️ benchmark-efficiency-2026-03-11.png
│   │   │   ├── 🖼️ benchmark-energy-2026-03-11.png
│   │   │   ├── 🖼️ benchmark-messages-2026-03-11.png
│   │   │   ├── 🖼️ benchmark-retrieval-2026-03-11.png
│   │   │   └── 🖼️ benchmark-table-2026-03-11.png
│   │   └── ⚙️ benchmark-runs-2026-03-11.json
│   ├── 📁 latex
│   │   ├── 📁 parts
│   │   │   ├── 📄 baseagent.tex
│   │   │   ├── 📄 benchmarks.tex
│   │   │   ├── 📄 communication.tex
│   │   │   ├── 📄 coordinator.tex
│   │   │   ├── 📄 frontespizio.tex
│   │   │   ├── 📄 retriever.tex
│   │   │   └── 📄 scout.tex
│   │   ├── 📕 relazione.pdf
│   │   └── 📄 relazione.tex
│   ├── 📕 20260226-progetto.pdf
│   └── 📝 PARAMETERS.md
├── 📁 frontend
│   ├── 📁 public
│   │   └── 🖼️ favicon.svg
│   ├── 📁 src
│   │   ├── 📁 components
│   │   │   ├── 📄 AgentList.tsx
│   │   │   ├── 📄 BenchmarkPanel.tsx
│   │   │   ├── 📄 ControlPanel.tsx
│   │   │   ├── 📄 GridCanvas.tsx
│   │   │   ├── 📄 MapEditor.tsx
│   │   │   ├── 📄 MetricsDisplay.tsx
│   │   │   └── 📄 TimelineSlider.tsx
│   │   ├── 📁 hooks
│   │   │   ├── 📄 useBenchmark.ts
│   │   │   ├── 📄 useSimulation.ts
│   │   │   └── 📄 useStepHistory.ts
│   │   ├── 📁 presets
│   │   │   └── 📄 index.ts
│   │   ├── 📁 types
│   │   │   └── 📄 simulation.ts
│   │   ├── 📄 App.tsx
│   │   ├── 🎨 index.css
│   │   ├── 📄 main.tsx
│   │   └── 📄 vite-env.d.ts
│   ├── ⚙️ .eslintrc.cjs
│   ├── 📄 bun.lock
│   ├── 🌐 index.html
│   ├── ⚙️ package.json
│   ├── 📄 postcss.config.js
│   ├── 📄 tailwind.config.js
│   ├── ⚙️ tsconfig.json
│   ├── ⚙️ tsconfig.node.json
│   └── 📄 vite.config.ts
├── ⚙️ .gitignore
├── 📄 LICENSE
├── 📝 QUICK_START.md
├── 📝 README.md
├── 📄 format.ps1
├── ⚙️ pyproject.toml
├── ⚙️ render.yaml
├── 📄 start.ps1
├── 📄 start.sh
├── 📄 uv.lock
└── ⚙️ vercel.json
```

---

## Local development

### Prerequisites

- Python 3.11+ · [uv](https://github.com/astral-sh/uv)
- [Bun](https://bun.sh)

### 1 — Clone & configure environment

```bash
git clone https://github.com/GiuseppeBellamacina/Warehouse-Swarm-Intelligence-System.git
cd Warehouse-Swarm-Intelligence-System
```

Copy and edit the backend environment file:

```bash
cp .env.example .env
# edit .env — set ALLOWED_ORIGINS, and optionally TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
```

Copy and edit the frontend environment file:

```bash
cp frontend/.env.example frontend/.env
# VITE_BACKEND_URL=http://localhost:8000  (default, no change needed for local dev)
```

### 2 — Start backend

```bash
uv sync
uv run python -m backend.api.main
# → http://localhost:8000  |  API docs: http://localhost:8000/docs
```

### 3 — Start frontend

```bash
cd frontend
bun install
bun run dev
# → http://localhost:3000
```

---

## Environment variables

### Backend (`.env` / Render dashboard)

| Variable             | Default                     | Description                                                                  |
| -------------------- | --------------------------- | ---------------------------------------------------------------------------- |
| `HOST`               | `0.0.0.0`                   | Bind address                                                                 |
| `PORT`               | `8000`                      | Bind port                                                                    |
| `ALLOWED_ORIGINS`    | `http://localhost:3000,...` | Comma-separated CORS origins                                                 |
| `TELEGRAM_BOT_TOKEN` | _(empty)_                   | Bot token from [@BotFather](https://t.me/BotFather) — leave empty to disable |
| `TELEGRAM_CHAT_ID`   | _(empty)_                   | Your chat ID from [@userinfobot](https://t.me/userinfobot)                   |

### Frontend (`.env` / Vercel dashboard)

| Variable           | Default                 | Description      |
| ------------------ | ----------------------- | ---------------- |
| `VITE_BACKEND_URL` | `http://localhost:8000` | Backend base URL |

---

## Deploy

| Service  | Platform                     | Config file   |
| -------- | ---------------------------- | ------------- |
| Frontend | [Vercel](https://vercel.com) | `vercel.json` |
| Backend  | [Render](https://render.com) | `render.yaml` |

Commits that touch **only** `README.md`, `QUICK_START.md` or files inside `docs/` will **not** trigger a rebuild on either platform.

---

## Code formatting

```bash
# Backend
uv run black backend/
uv run ruff check --fix backend/

# Frontend
cd frontend && bun run lint
```

Or run the helper script:

```powershell
.\format.ps1
```

---

## License

MIT — developed for the Multi-Agent Systems course project.
