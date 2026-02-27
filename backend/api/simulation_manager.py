"""
Simulation manager for running simulations in background
"""

import asyncio
import traceback
from typing import Optional, Tuple

from backend.agents.coordinator_agent import CoordinatorAgent
from backend.agents.retriever_agent import RetrieverAgent
from backend.agents.scout_agent import ScoutAgent
from backend.config.schemas import ScenarioConfig
from backend.core.warehouse_model import WarehouseModel


class SimulationManager:
    """
    Manages simulation lifecycle and execution
    """

    def __init__(self):
        self.model: Optional[WarehouseModel] = None
        self.is_running = False
        self.is_paused = False
        self.config: Optional[ScenarioConfig] = None
        self.simulation_task: Optional[asyncio.Task] = None
        self.update_rate = 30  # Updates per second

    def initialize_simulation(self, config: ScenarioConfig) -> None:
        """
        Initialize a new simulation with given configuration

        Args:
            config: Scenario configuration
        """
        self.config = config

        # Create model
        self.model = WarehouseModel(config)

        # Spawn scout agents
        scout_config = config.agents.scouts
        for i in range(scout_config.count):
            agent = ScoutAgent(
                unique_id=i,
                model=self.model,
                vision_radius=scout_config.parameters.vision_radius,
                communication_radius=scout_config.parameters.communication_radius,
                max_energy=scout_config.parameters.max_energy,
                speed=scout_config.parameters.speed,
            )

            spawn_pos = (scout_config.spawn_location.x, scout_config.spawn_location.y)
            free_pos = self._find_free_cell_near(spawn_pos)
            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
            else:
                print(f"Warning: Could not find free cell for scout {i}")

        # Spawn coordinator agents
        coord_config = config.agents.coordinators
        base_id = scout_config.count
        for i in range(coord_config.count):
            agent = CoordinatorAgent(
                unique_id=base_id + i,
                model=self.model,
                vision_radius=coord_config.parameters.vision_radius,
                communication_radius=coord_config.parameters.communication_radius,
                max_energy=coord_config.parameters.max_energy,
                speed=coord_config.parameters.speed,
            )

            spawn_pos = (coord_config.spawn_location.x, coord_config.spawn_location.y)
            free_pos = self._find_free_cell_near(spawn_pos)
            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
            else:
                print(f"Warning: Could not find free cell for coordinator {i}")

        # Spawn retriever agents
        retr_config = config.agents.retrievers
        base_id = scout_config.count + coord_config.count
        for i in range(retr_config.count):
            agent = RetrieverAgent(
                unique_id=base_id + i,
                model=self.model,
                vision_radius=retr_config.parameters.vision_radius,
                communication_radius=retr_config.parameters.communication_radius,
                max_energy=retr_config.parameters.max_energy,
                speed=retr_config.parameters.speed,
                carrying_capacity=retr_config.parameters.carrying_capacity,
            )

            spawn_pos = (retr_config.spawn_location.x, retr_config.spawn_location.y)
            free_pos = self._find_free_cell_near(spawn_pos)
            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
            else:
                print(f"Warning: Could not find free cell for retriever {i}")

        print(f"Simulation initialized with {len(self.model.agents)} agents")
        print(f"  - {len(self.model.scouts)} scouts")
        print(f"  - {len(self.model.coordinators)} coordinators")
        print(f"  - {len(self.model.retrievers)} retrievers")
        print(f"  - {self.model.total_objects} objects to retrieve")

    def _find_free_cell_near(self, target_pos: Tuple[int, int], max_radius: int = 5) -> Optional[Tuple[int, int]]:
        """
        Find a free cell near the target position
        
        Args:
            target_pos: Desired position
            max_radius: Maximum search radius
            
        Returns:
            Free cell position or None if not found
        """
        if not self.model:
            return None
            
        x, y = target_pos
        
        # Check if target itself is free
        if self.model.grid.is_cell_empty(target_pos):
            return target_pos
        
        # Search in expanding radius
        for radius in range(1, max_radius + 1):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    # Only check cells at current radius distance
                    if abs(dx) != radius and abs(dy) != radius:
                        continue
                    
                    check_pos = (x + dx, y + dy)
                    
                    # Skip if out of bounds
                    if self.model.grid.out_of_bounds(check_pos):
                        continue
                    
                    # Check if cell is empty and walkable
                    if self.model.grid.is_cell_empty(check_pos) and self.model.grid.is_walkable(*check_pos):
                        return check_pos
        
        return None

    async def start_simulation(self, ws_manager) -> None:
        """
        Start running the simulation

        Args:
            ws_manager: WebSocket manager for broadcasting updates
        """
        if not self.model:
            raise ValueError("Simulation not initialized")

        if self.is_running:
            raise ValueError("Simulation already running")

        self.is_running = True
        self.is_paused = False

        print("Starting simulation...")

        try:
            # Run simulation loop
            await self._simulation_loop(ws_manager)
        except Exception as e:
            print(f"Simulation error: {e}")
            traceback.print_exc()
        finally:
            self.is_running = False
            print("Simulation ended")

    async def _simulation_loop(self, ws_manager) -> None:
        """
        Main simulation loop

        Args:
            ws_manager: WebSocket manager for broadcasting
        """
        if not self.model:
            return

        update_interval = 1.0 / self.update_rate

        while self.is_running and self.model.running:
            if not self.is_paused:
                # Step simulation
                self.model.step()

                # Get state and broadcast
                state = self.get_simulation_state()
                await ws_manager.broadcast_state(state)

                # Check if complete
                if not self.model.running:
                    await ws_manager.broadcast_event(
                        "simulation_complete",
                        {
                            "steps": self.model.current_step,
                            "objects_retrieved": self.model.objects_retrieved,
                            "total_objects": self.model.total_objects,
                        },
                    )
                    break

            # Wait for next update
            await asyncio.sleep(update_interval)

    def pause_simulation(self) -> None:
        """Pause the simulation"""
        self.is_paused = True
        print("Simulation paused")

    def resume_simulation(self) -> None:
        """Resume the simulation"""
        self.is_paused = False
        print("Simulation resumed")

    def stop_simulation(self) -> None:
        """Stop the simulation"""
        self.is_running = False
        if self.model:
            self.model.running = False
        print("Simulation stopped")

    def reset_simulation(self) -> None:
        """Reset the simulation"""
        self.stop_simulation()
        if self.config:
            self.initialize_simulation(self.config)
        print("Simulation reset")

    def get_simulation_state(self) -> dict:
        """
        Get current simulation state

        Returns:
            State dictionary for WebSocket broadcast
        """
        if not self.model:
            return {}

        state = self.model.get_state_dict()

        if not self.config:
            return state

        # Add grid info for initial setup
        state["grid"] = {
            "width": self.model.grid.width,
            "height": self.model.grid.height,
            "warehouse": {
                "x": self.model.warehouse_position[0],
                "y": self.model.warehouse_position[1],
                "width": self.config.warehouse.width,
                "height": self.config.warehouse.height,
                "entrances": [{"x": e.x, "y": e.y} for e in self.config.warehouse.entrances],
                "exits": [
                    {"x": e.x, "y": e.y}
                    for e in (self.config.warehouse.exits or self.config.warehouse.entrances)
                ],
            },
            "obstacles": [
                {"type": obs.type, "data": obs.model_dump()} for obs in self.config.obstacles
            ],
        }

        state["status"] = {"running": self.is_running, "paused": self.is_paused}

        return state

    def get_statistics(self) -> dict:
        """Get simulation statistics"""
        if not self.model:
            return {}

        return {
            "current_step": self.model.current_step,
            "objects_retrieved": self.model.objects_retrieved,
            "total_objects": self.model.total_objects,
            "retrieval_progress": (
                self.model.objects_retrieved / self.model.total_objects
                if self.model.total_objects > 0
                else 0
            ),
            "active_agents": len([a for a in self.model.agents if getattr(a, "energy", 0) > 0]),
            "total_agents": len(self.model.agents),
        }


# Global simulation manager
sim_manager = SimulationManager()
