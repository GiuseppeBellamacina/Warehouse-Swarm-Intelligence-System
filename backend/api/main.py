"""
FastAPI main application with REST endpoints and WebSocket support
"""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.api.simulation_manager import sim_manager
from backend.api.websocket_manager import ws_manager
from backend.config.config_loader import ConfigLoader

# FastAPI app
app = FastAPI(
    title="Warehouse Swarm Intelligence System",
    description="Multi-agent warehouse object retrieval simulation",
    version="0.1.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
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


@app.post("/api/simulation/start")
async def start_simulation(request: StartSimulationRequest):
    """
    Start a new simulation with given configuration

    Args:
        request: Configuration dictionary

    Returns:
        Success message
    """
    try:
        print("[DEBUG] Received start simulation request")
        print(f"[DEBUG] Config keys: {request.config.keys()}")

        # Parse configuration
        print("[DEBUG] Parsing configuration with ConfigLoader...")
        config = ConfigLoader.load_from_dict(request.config)
        print("[DEBUG] Configuration parsed successfully")

        # Initialize simulation
        print("[DEBUG] Initializing simulation...")
        sim_manager.initialize_simulation(config)
        print("[DEBUG] Simulation initialized successfully")

        # Start simulation in background
        print("[DEBUG] Starting simulation task...")
        sim_manager.simulation_task = asyncio.create_task(sim_manager.start_simulation(ws_manager))
        print("[DEBUG] Simulation task started")

        return {
            "status": "started",
            "message": "Simulation started successfully",
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
        print(f"[ERROR] Unexpected error starting simulation: {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to start simulation: {str(e)}")


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

        # Initialize and start simulation
        sim_manager.initialize_simulation(config)
        sim_manager.simulation_task = asyncio.create_task(sim_manager.start_simulation(ws_manager))

        return {
            "status": "started",
            "message": f"Configuration loaded from {file.filename}",
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

    return {"status": "stopped"}


@app.post("/api/simulation/reset")
async def reset_simulation():
    """Reset the simulation to initial state"""
    if not sim_manager.config:
        raise HTTPException(status_code=400, detail="No configuration loaded")

    sim_manager.reset_simulation()

    await ws_manager.broadcast_event("simulation_reset", {})

    return {"status": "reset"}


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
    print("API Documentation: http://localhost:8000/docs")
    print("WebSocket: ws://localhost:8000/socket.io")

    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
