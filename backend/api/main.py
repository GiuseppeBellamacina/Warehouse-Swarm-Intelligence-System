"""
FastAPI main application with REST endpoints and WebSocket support
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.api.session_registry import session_registry
from backend.api.simulation_manager import SimulationManager
from backend.api.telegram_notifier import notify_simulation_start, notify_simulation_stopped
from backend.api.websocket_manager import ws_manager
from backend.config.config_loader import ConfigLoader
from backend.config.settings import settings

# Get the project root directory for configs
project_root = Path(__file__).parent.parent.parent
configs_path = project_root / "configs"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start background cleanup of idle sessions on startup."""
    asyncio.create_task(session_registry.cleanup_loop())
    yield


# FastAPI app
app = FastAPI(
    title="Warehouse Swarm Intelligence System",
    description="Multi-agent warehouse object retrieval simulation",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── session dependency ─────────────────────────────────────────────────────


def _session_id(request: Request) -> str:
    return request.headers.get("x-session-id", "default")


def _get_manager(request: Request) -> SimulationManager:
    return session_registry.get_or_create(_session_id(request))


# Pydantic models for requests
class StartSimulationRequest(BaseModel):
    """Request body for starting simulation"""

    config: dict


# API Routes


@app.get("/")
async def root():
    """Root endpoint"""
    return {"name": "Warehouse Swarm Intelligence API", "version": "0.1.0", "status": "running"}


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.get("/api/configs")
async def list_configs():
    """
    List all available configuration files

    Returns:
        List of available config names
    """
    try:
        if not configs_path.exists():
            return {"configs": []}

        # Get all .json files in configs directory
        config_files = [f.stem for f in configs_path.glob("*.json")]
        return {"configs": sorted(config_files)}
    except Exception as e:
        print(f"[ERROR] Error listing configs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error listing configs: {str(e)}")


@app.get("/configs/{config_name}")
async def get_config(config_name: str):
    """
    Get a configuration file

    Args:
        config_name: Name of the config file (e.g., 'simple_scenario.json')

    Returns:
        Configuration JSON
    """
    try:
        config_file = configs_path / config_name

        # Log the path for debugging
        print(f"[DEBUG] Looking for config file at: {config_file}")
        print(f"[DEBUG] Config file exists: {config_file.exists()}")
        print(f"[DEBUG] Configs path: {configs_path}")
        print(f"[DEBUG] Configs path exists: {configs_path.exists()}")

        if not config_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Configuration file '{config_name}' not found at {config_file}",
            )

        if not config_file.is_file():
            raise HTTPException(status_code=400, detail=f"'{config_name}' is not a file")

        # Read and return JSON
        with open(config_file, "r", encoding="utf-8") as f:
            config_data = json.load(f)

        print(f"[DEBUG] Successfully loaded config: {config_name}")
        return config_data
    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON decode error in '{config_name}': {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON in '{config_name}': {str(e)}")
    except Exception as e:
        print(f"[ERROR] Unexpected error reading config '{config_name}': {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error reading config: {str(e)}")


@app.post("/api/simulation/load")
async def load_simulation_endpoint(request: Request, body: StartSimulationRequest):
    """
    Load a simulation configuration: initialize agents/grid and broadcast step 0.
    The simulation loop does NOT start — call /api/simulation/start to run it.
    """
    try:
        mgr = _get_manager(request)
        sid = _session_id(request)
        print("[DEBUG] Received load simulation request")
        config = ConfigLoader.load_from_dict(body.config)
        print("[DEBUG] Configuration parsed — initializing and broadcasting step 0...")
        await mgr.load_simulation(config, ws_manager, sid)
        print("[DEBUG] Step 0 broadcast complete")
        return {
            "status": "loaded",
            "message": "Simulation loaded. Press Start to begin.",
            "agents": {
                "scouts": config.agents.scouts.count,
                "coordinators": config.agents.coordinators.count,
                "retrievers": config.agents.retrievers.count,
            },
            "grid": {
                "width": config.simulation.grid_width,
                "height": config.simulation.grid_height,
            },
            "objects": config.objects.count,
        }
    except ValueError as e:
        print(f"[ERROR] Validation error: {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[ERROR] Unexpected error loading simulation: {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to load simulation: {str(e)}")


@app.post("/api/simulation/start")
async def start_simulation(request: Request):
    """
    Start the simulation loop. The simulation must be loaded first via /api/simulation/load.
    """
    mgr = _get_manager(request)
    if not mgr.model:
        raise HTTPException(
            status_code=400,
            detail="No simulation loaded. Call /api/simulation/load first.",
        )
    if mgr.is_running:
        raise HTTPException(status_code=400, detail="Simulation is already running")

    print("[DEBUG] Starting simulation loop...")
    mgr.simulation_task = asyncio.create_task(mgr.start_simulation(ws_manager))
    print("[DEBUG] Simulation loop task created")

    # Fire-and-forget Telegram notification
    config_name: Optional[str] = getattr(mgr.config, "name", None)
    agent_count: Optional[int] = len(mgr.model.agents) if mgr.model and mgr.model.agents else None
    forwarded_for = request.headers.get("x-forwarded-for")
    user_ip = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request.client else None)
    )
    user_agent = request.headers.get("user-agent")
    asyncio.create_task(
        notify_simulation_start(
            config_name=config_name,
            agent_count=agent_count,
            user_ip=user_ip,
            user_agent=user_agent,
        )
    )

    return {"status": "started", "message": "Simulation running"}


@app.post("/api/simulation/upload")
async def upload_configuration(request: Request, file: UploadFile = File(...)):
    """
    Upload and start simulation from JSON configuration file
    """
    try:
        mgr = _get_manager(request)
        sid = _session_id(request)
        content = await file.read()
        config_dict = json.loads(content)
        config = ConfigLoader.load_from_dict(config_dict)
        await mgr.load_simulation(config, ws_manager, sid)
        return {
            "status": "loaded",
            "message": f"Configuration loaded from {file.filename}. Press Start to begin.",
            "agents": {
                "scouts": config.agents.scouts.count,
                "coordinators": config.agents.coordinators.count,
                "retrievers": config.agents.retrievers.count,
            },
        }
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process file: {str(e)}")


@app.post("/api/simulation/pause")
async def pause_simulation(request: Request):
    """Pause the running simulation"""
    mgr = _get_manager(request)
    if not mgr.is_running:
        raise HTTPException(status_code=400, detail="No simulation running")

    mgr.pause_simulation()

    await ws_manager.broadcast_event_to_session(
        _session_id(request),
        "simulation_paused",
        {"step": mgr.model.current_step if mgr.model else 0},
    )

    return {"status": "paused"}


@app.post("/api/simulation/resume")
async def resume_simulation(request: Request):
    """Resume the paused simulation"""
    mgr = _get_manager(request)
    if not mgr.is_running:
        raise HTTPException(status_code=400, detail="No simulation running")

    mgr.resume_simulation()

    await ws_manager.broadcast_event_to_session(
        _session_id(request),
        "simulation_resumed",
        {"step": mgr.model.current_step if mgr.model else 0},
    )

    return {"status": "resumed"}


@app.post("/api/simulation/stop")
async def stop_simulation(request: Request):
    """Stop the running simulation"""
    mgr = _get_manager(request)
    if not mgr.is_running:
        raise HTTPException(status_code=400, detail="No simulation running")

    mgr.stop_simulation()

    await ws_manager.broadcast_event_to_session(_session_id(request), "simulation_stopped", {})

    asyncio.create_task(
        notify_simulation_stopped(
            config_name=getattr(mgr.config, "name", None),
            steps=mgr.model.current_step if mgr.model else None,
            objects_retrieved=mgr.model.objects_retrieved if mgr.model else None,
            total_objects=mgr.model.total_objects if mgr.model else None,
        )
    )

    return {"status": "stopped"}


@app.post("/api/simulation/reset")
async def reset_simulation(request: Request):
    """Reset the simulation to initial state and broadcast step 0"""
    mgr = _get_manager(request)
    sid = _session_id(request)
    if not mgr.config:
        raise HTTPException(status_code=400, detail="No configuration loaded")

    await mgr.reset_simulation()

    state = mgr.get_simulation_state()
    await ws_manager.broadcast_state_to_session(sid, state)
    await ws_manager.broadcast_event_to_session(sid, "simulation_reset", {})

    return {"status": "reset"}


@app.post("/api/simulation/speed")
async def set_simulation_speed(speed: float, request: Request):
    """
    Set simulation speed (0.1 to 10.0, where 1.0 is normal speed)
    """
    if speed < 0.1 or speed > 10.0:
        raise HTTPException(status_code=400, detail="Speed must be between 0.1 and 10.0")

    mgr = _get_manager(request)
    mgr.set_speed(speed)

    return {"status": "success", "speed": speed, "update_rate": mgr.update_rate}


@app.get("/api/simulation/status")
async def get_simulation_status(request: Request):
    """Get current simulation status"""
    mgr = _get_manager(request)
    if not mgr.model:
        return {"initialized": False, "running": False}

    stats = mgr.get_statistics()
    return {
        "initialized": True,
        "running": mgr.is_running,
        "paused": mgr.is_paused,
        **stats,
    }


@app.get("/api/simulation/state")
async def get_simulation_state(request: Request):
    """Get current simulation state (snapshot)"""
    mgr = _get_manager(request)
    if not mgr.model:
        raise HTTPException(status_code=404, detail="No simulation initialized")

    return mgr.get_simulation_state()


@app.get("/api/simulation/metrics")
async def get_simulation_metrics(request: Request):
    """Get detailed simulation metrics"""
    mgr = _get_manager(request)
    if not mgr.model:
        raise HTTPException(status_code=404, detail="No simulation initialized")

    model_data = mgr.model.datacollector.get_model_vars_dataframe()
    return {
        "steps": model_data.index.tolist(),
        "objects_retrieved": model_data["Objects Retrieved"].tolist(),
        "average_energy": model_data["Average Energy"].tolist(),
        "active_agents": model_data["Active Agents"].tolist(),
        "retrieval_progress": model_data["Retrieval Progress"].tolist(),
    }


# Mount Socket.IO
socket_app = ws_manager.get_asgi_app()
app.mount("/socket.io", socket_app)


if __name__ == "__main__":
    import uvicorn

    print("Starting Warehouse Swarm Intelligence System")
    print(f"API Documentation: http://{settings.host}:{settings.port}/docs")
    print(f"WebSocket: ws://{settings.host}:{settings.port}/socket.io")
    print(f"Allowed origins: {settings.allowed_origins_list}")

    uvicorn.run(
        "backend.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level="info",
    )
