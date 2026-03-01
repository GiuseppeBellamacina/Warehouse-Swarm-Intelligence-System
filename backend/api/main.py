"""
FastAPI main application with REST endpoints and WebSocket support
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.api.simulation_manager import sim_manager
from backend.api.telegram_notifier import (
    notify_simulation_start,
    notify_simulation_stopped,
)
from backend.api.websocket_manager import ws_manager
from backend.config.config_loader import ConfigLoader
from backend.config.settings import settings

# FastAPI app
app = FastAPI(
    title="Warehouse Swarm Intelligence System",
    description="Multi-agent warehouse object retrieval simulation",
    version="0.1.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get the project root directory for configs
project_root = Path(__file__).parent.parent.parent
configs_path = project_root / "configs"


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
    return {"status": "healthy", "simulation_running": sim_manager.is_running}


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
async def load_simulation_endpoint(request: StartSimulationRequest):
    """
    Load a simulation configuration: initialize agents/grid and broadcast step 0.
    The simulation loop does NOT start — call /api/simulation/start to run it.

    Args:
        request: Configuration dictionary

    Returns:
        Loaded status message
    """
    try:
        print("[DEBUG] Received load simulation request")
        config = ConfigLoader.load_from_dict(request.config)
        print("[DEBUG] Configuration parsed — initializing and broadcasting step 0...")
        await sim_manager.load_simulation(config, ws_manager)
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

    Returns:
        Success message
    """
    if not sim_manager.model:
        raise HTTPException(
            status_code=400,
            detail="No simulation loaded. Call /api/simulation/load first.",
        )
    if sim_manager.is_running:
        raise HTTPException(status_code=400, detail="Simulation is already running")

    print("[DEBUG] Starting simulation loop...")
    sim_manager.simulation_task = asyncio.create_task(sim_manager.start_simulation(ws_manager))
    print("[DEBUG] Simulation loop task created")

    # Fire-and-forget Telegram notification
    config_name: Optional[str] = None
    agent_count: Optional[int] = None
    if sim_manager.model:
        config_name = getattr(sim_manager.config, "name", None)
        agent_count = len(sim_manager.model.agents) if sim_manager.model.agents else None
    # Extract client IP (respects X-Forwarded-For set by Render/Vercel proxies)
    forwarded_for = request.headers.get("x-forwarded-for")
    user_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
        request.client.host if request.client else None
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
async def upload_configuration(file: UploadFile = File(...)):
    """
    Upload and start simulation from JSON configuration file

    Args:
        file: JSON configuration file

    Returns:
        Success message
    """
    try:
        # Read file content
        content = await file.read()
        config_dict = json.loads(content)

        # Parse configuration
        config = ConfigLoader.load_from_dict(config_dict)

        # Load (init + broadcast step 0) without starting the loop
        await sim_manager.load_simulation(config, ws_manager)

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
async def pause_simulation():
    """Pause the running simulation"""
    if not sim_manager.is_running:
        raise HTTPException(status_code=400, detail="No simulation running")

    sim_manager.pause_simulation()

    await ws_manager.broadcast_event(
        "simulation_paused", {"step": sim_manager.model.current_step if sim_manager.model else 0}
    )

    return {"status": "paused"}


@app.post("/api/simulation/resume")
async def resume_simulation():
    """Resume the paused simulation"""
    if not sim_manager.is_running:
        raise HTTPException(status_code=400, detail="No simulation running")

    sim_manager.resume_simulation()

    await ws_manager.broadcast_event(
        "simulation_resumed", {"step": sim_manager.model.current_step if sim_manager.model else 0}
    )

    return {"status": "resumed"}


@app.post("/api/simulation/stop")
async def stop_simulation():
    """Stop the running simulation"""
    if not sim_manager.is_running:
        raise HTTPException(status_code=400, detail="No simulation running")

    sim_manager.stop_simulation()

    await ws_manager.broadcast_event("simulation_stopped", {})

    # Fire-and-forget Telegram notification
    _cfg = getattr(sim_manager.config, "name", None)
    _model = sim_manager.model
    asyncio.create_task(
        notify_simulation_stopped(
            config_name=_cfg,
            steps=_model.current_step if _model else None,
            objects_retrieved=_model.objects_retrieved if _model else None,
            total_objects=_model.total_objects if _model else None,
        )
    )

    return {"status": "stopped"}


@app.post("/api/simulation/reset")
async def reset_simulation():
    """Reset the simulation to initial state and broadcast step 0"""
    if not sim_manager.config:
        raise HTTPException(status_code=400, detail="No configuration loaded")

    await sim_manager.reset_simulation()

    # Broadcast the fresh step-0 state before sending the reset event
    state = sim_manager.get_simulation_state()
    await ws_manager.broadcast_state(state)
    await ws_manager.broadcast_event("simulation_reset", {})

    return {"status": "reset"}


@app.post("/api/simulation/speed")
async def set_simulation_speed(speed: float):
    """
    Set simulation speed

    Args:
        speed: Speed multiplier (0.1 to 10.0, where 1.0 is normal speed)
    """
    # Validate speed
    if speed < 0.1 or speed > 10.0:
        raise HTTPException(status_code=400, detail="Speed must be between 0.1 and 10.0")

    sim_manager.set_speed(speed)

    return {"status": "success", "speed": speed, "update_rate": sim_manager.update_rate}


@app.get("/api/simulation/status")
async def get_simulation_status():
    """Get current simulation status"""
    if not sim_manager.model:
        return {"initialized": False, "running": False}

    stats = sim_manager.get_statistics()

    return {
        "initialized": True,
        "running": sim_manager.is_running,
        "paused": sim_manager.is_paused,
        **stats,
    }


@app.get("/api/simulation/state")
async def get_simulation_state():
    """Get current simulation state (snapshot)"""
    if not sim_manager.model:
        raise HTTPException(status_code=404, detail="No simulation initialized")

    return sim_manager.get_simulation_state()


@app.get("/api/simulation/metrics")
async def get_simulation_metrics():
    """Get detailed simulation metrics"""
    if not sim_manager.model:
        raise HTTPException(status_code=404, detail="No simulation initialized")

    # Get data from data collector
    model_data = sim_manager.model.datacollector.get_model_vars_dataframe()

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
