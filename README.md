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
| Coordinator | Purple | Assign retrieval tasks, manage agent recharging          |
| Retriever   | Blue   | Navigate to objects, carry them to the deposit zone      |

### Algorithms

- A\* pathfinding with dynamic replanning and forbidden-zone support
- Frontier-based exploration with two-level anti-clustering (hard distance filter + soft utility penalty)
- Priority-based collision avoidance with wait/replan back-off
- `ClearWayMessage` chain protocol — agents negotiate to unblock entrances
- FIFO task queue with opportunistic multi-carry optimisation
- Radius-based inter-agent communication (scout → coordinator → retriever)
- **Hive-mind retrieval**: Retrievers self-assign from the full shared `known_objects` map without waiting for coordinator commands
- **SEEK-RETRIEVER**: Coordinator actively moves toward retrievers when tasks are pending but no retriever is in comm range
- **Centroid repositioning**: Coordinator stays near the fleet centroid; holds position when already within range

### Real-time interface

- HTML5 Canvas grid rendering streamed over Socket.IO
- Resizable panel layout (agents · map · metrics · controls)
- Map editor for drawing custom scenarios
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
├── 📁 frontend
│   ├── 📁 public
│   │   └── 🖼️ favicon.svg
│   ├── 📁 src
│   │   ├── 📁 components
│   │   │   ├── 📄 AgentList.tsx
│   │   │   ├── 📄 ControlPanel.tsx
│   │   │   ├── 📄 GridCanvas.tsx
│   │   │   ├── 📄 MapEditor.tsx
│   │   │   └── 📄 MetricsDisplay.tsx
│   │   ├── 📁 hooks
│   │   │   └── 📄 useSimulation.ts
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
