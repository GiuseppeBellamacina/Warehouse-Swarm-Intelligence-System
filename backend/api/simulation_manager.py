"""
Simulation manager for running simulations in background
"""

import asyncio
import random
import time
import traceback
from typing import Optional, Set, Tuple

from backend.agents.base_agent import _type_index_map, register_type_index
from backend.agents.coordinator_agent import CoordinatorAgent
from backend.agents.retriever_agent import RetrieverAgent
from backend.agents.scout_agent import ScoutAgent
from backend.config.schemas import (
    GridScenarioConfig,
    ScenarioConfig,
    SimulationAgentsConfig,
)
from backend.core.grid_manager import CellType
from backend.core.warehouse_model import WarehouseModel

# imported lazily to avoid circular issues at module load
_notify_complete_fn = None


def _get_notify_complete():
    global _notify_complete_fn
    if _notify_complete_fn is None:
        from backend.api.telegram_notifier import notify_simulation_complete

        _notify_complete_fn = notify_simulation_complete
    return _notify_complete_fn


class SimulationManager:
    """
    Manages simulation lifecycle and execution
    """

    def __init__(self):
        self.model: Optional[WarehouseModel] = None
        self.is_running = False
        self.is_paused = False
        self.config: Optional[ScenarioConfig] = None
        self._grid_config: Optional[GridScenarioConfig] = None
        self._agents_config: Optional[SimulationAgentsConfig] = None
        self.simulation_task: Optional[asyncio.Task] = None
        self.update_rate = 18  # Updates per second (1x speed)
        self.occupied_spawn_positions: Set[Tuple[int, int]] = set()
        self._sim_start_time: Optional[float] = None  # monotonic clock at sim start
        self.session_id: str = "default"  # owning session

    def _find_random_spawn_position(
        self,
        center: Optional[Tuple[int, int]] = None,
        max_radius: int = 10,
        max_attempts: int = 100,
    ) -> Optional[Tuple[int, int]]:
        """
        Find a random valid spawn position near a center point

        Args:
            center: Center position for spawn area (default: grid center)
            max_radius: Maximum distance from center
            max_attempts: Maximum number of attempts to find a valid position

        Returns:
            Valid spawn position or None if not found
        """
        if not self.model:
            return None

        # Default to grid center
        if center is None:
            center = (self.model.grid.width // 2, self.model.grid.height // 2)

        for _ in range(max_attempts):
            # Random offset within radius
            offset_x = random.randint(-max_radius, max_radius)
            offset_y = random.randint(-max_radius, max_radius)

            x = center[0] + offset_x
            y = center[1] + offset_y

            # Check bounds
            if x < 0 or x >= self.model.grid.width or y < 0 or y >= self.model.grid.height:
                continue

            pos = (x, y)

            # Check if position is valid, not occupied, and not inside a warehouse
            cell_type = self.model.grid.get_cell_type(x, y)
            _WAREHOUSE_TYPES = (
                CellType.WAREHOUSE,
                CellType.WAREHOUSE_ENTRANCE,
                CellType.WAREHOUSE_EXIT,
            )
            if (
                pos not in self.occupied_spawn_positions
                and self.model.grid.is_walkable(x, y)
                and self.model.grid.is_cell_empty(pos)
                and cell_type not in _WAREHOUSE_TYPES
            ):

                # Mark as occupied
                self.occupied_spawn_positions.add(pos)
                return pos

        # Fallback: try to find any free cell near center
        print("Warning: Could not find random spawn near center, searching systematically...")
        for radius in range(max_radius, max_radius + 5):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    x = center[0] + dx
                    y = center[1] + dy

                    if x < 0 or x >= self.model.grid.width or y < 0 or y >= self.model.grid.height:
                        continue

                    pos = (x, y)
                    cell_type = self.model.grid.get_cell_type(x, y)
                    if (
                        pos not in self.occupied_spawn_positions
                        and self.model.grid.is_walkable(x, y)
                        and self.model.grid.is_cell_empty(pos)
                        and cell_type
                        not in (
                            CellType.WAREHOUSE,
                            CellType.WAREHOUSE_ENTRANCE,
                            CellType.WAREHOUSE_EXIT,
                        )
                    ):
                        self.occupied_spawn_positions.add(pos)
                        return pos

        return None

    def _find_tl_spawn_position(self) -> Optional[Tuple[int, int]]:
        """
        Find the next free spawn position by expanding square shells
        from (0, 0).  Each shell *d* adds the new column x=d (top to
        bottom) then the new row y=d (left to right, excluding the
        corner already visited), keeping agents packed in a tight
        rectangle as close to the origin as possible.
        """
        if not self.model:
            return None
        _WAREHOUSE_TYPES = (
            CellType.WAREHOUSE,
            CellType.WAREHOUSE_ENTRANCE,
            CellType.WAREHOUSE_EXIT,
        )
        w, h = self.model.grid.width, self.model.grid.height
        for d in range(max(w, h)):
            # New column at x = d  (y: 0 → d)
            if d < w:
                for y in range(min(d + 1, h)):
                    pos = (d, y)
                    if (
                        pos not in self.occupied_spawn_positions
                        and self.model.grid.is_walkable(d, y)
                        and self.model.grid.is_cell_empty(pos)
                        and self.model.grid.get_cell_type(d, y) not in _WAREHOUSE_TYPES
                    ):
                        self.occupied_spawn_positions.add(pos)
                        return pos
            # New row at y = d  (x: 0 → d-1, corner (d,d) already covered)
            if d < h:
                for x in range(min(d, w)):
                    pos = (x, d)
                    if (
                        pos not in self.occupied_spawn_positions
                        and self.model.grid.is_walkable(x, d)
                        and self.model.grid.is_cell_empty(pos)
                        and self.model.grid.get_cell_type(x, d) not in _WAREHOUSE_TYPES
                    ):
                        self.occupied_spawn_positions.add(pos)
                        return pos
        return None

    def initialize_simulation(self, config: ScenarioConfig) -> None:
        """
        Initialize a new simulation with given configuration

        Args:
            config: Scenario configuration
        """
        self.config = config

        # Create model
        self.model = WarehouseModel(config)

        # Reset per-type index registry
        _type_index_map.clear()

        # Reset occupied spawn positions
        self.occupied_spawn_positions = set()

        scout_config = config.agents.scouts
        coord_config = config.agents.coordinators
        retr_config = config.agents.retrievers

        # Spawn order: coordinators → retrievers → scouts (top-left first)

        # Spawn coordinator agents
        for i in range(coord_config.count):
            agent = CoordinatorAgent(
                unique_id=i,
                model=self.model,
                vision_radius=coord_config.parameters.vision_radius,
                communication_radius=coord_config.parameters.communication_radius,
                max_energy=coord_config.parameters.max_energy,
                speed=coord_config.parameters.speed,
            )
            register_type_index(i, i + 1)

            if coord_config.spawn_location is None:
                free_pos = self._find_tl_spawn_position()
            else:
                spawn_pos = (coord_config.spawn_location.x, coord_config.spawn_location.y)
                free_pos = self._find_free_cell_near(spawn_pos, max_radius=15, spread=True)

            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
                agent.step_sense()
            else:
                print(f"Warning: Could not find free cell for coordinator {i}")

        # Spawn retriever agents
        base_id = coord_config.count
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
            register_type_index(base_id + i, i + 1)

            if retr_config.spawn_location is None:
                free_pos = self._find_tl_spawn_position()
            else:
                spawn_pos = (retr_config.spawn_location.x, retr_config.spawn_location.y)
                free_pos = self._find_free_cell_near(spawn_pos, max_radius=15, spread=True)

            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
                agent.step_sense()
            else:
                print(f"Warning: Could not find free cell for retriever {i}")

        # Spawn scout agents
        base_id = coord_config.count + retr_config.count
        for i in range(scout_config.count):
            agent = ScoutAgent(
                unique_id=base_id + i,
                model=self.model,
                vision_radius=scout_config.parameters.vision_radius,
                communication_radius=scout_config.parameters.communication_radius,
                max_energy=scout_config.parameters.max_energy,
                speed=scout_config.parameters.speed,
            )
            register_type_index(base_id + i, i + 1)

            if scout_config.spawn_location is None:
                free_pos = self._find_tl_spawn_position()
            else:
                spawn_pos = (scout_config.spawn_location.x, scout_config.spawn_location.y)
                free_pos = self._find_free_cell_near(spawn_pos, max_radius=15, spread=True)

            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
                agent.step_sense()
            else:
                print(f"Warning: Could not find free cell for scout {i}")

        print(f"Simulation initialized with {len(self.model.agents)} agents")
        print(f"  - {len(self.model.scouts)} scouts")
        print(f"  - {len(self.model.coordinators)} coordinators")
        print(f"  - {len(self.model.retrievers)} retrievers")
        print(f"  - {self.model.total_objects} objects to retrieve")

        # Snapshot RNG state so that step() can isolate itself from external
        # async consumers (WebSocket, Telegram notifier, etc.)
        self.model.snapshot_rng()

    async def load_simulation(
        self, config: ScenarioConfig, ws_manager, session_id: str = "default"
    ) -> None:
        """
        Initialize simulation and broadcast step 0 without starting the loop.
        The client will see the initial grid state and can then call start_simulation.

        Args:
            config: Scenario configuration
            ws_manager: WebSocket manager for broadcasting the initial state
            session_id: Owning session identifier
        """
        self.session_id = session_id
        self.initialize_simulation(config)
        self.is_running = False
        self.is_paused = False
        state = self.get_simulation_state()
        await ws_manager.broadcast_state_to_session(session_id, state)

    def initialize_from_grid(
        self,
        grid_config: GridScenarioConfig,
        agents_config: Optional[SimulationAgentsConfig] = None,
    ) -> None:
        """
        Initialize a simulation from a compact GridScenarioConfig.

        Args:
            grid_config: Grid-based scenario (A/B format).
            agents_config: Agent composition. Falls back to SimulationAgentsConfig
                           defaults (1 Scout, 1 Coordinator, 3 Retrievers) if None.
        """
        if agents_config is None:
            agents_config = SimulationAgentsConfig()

        self._grid_config = grid_config
        self._agents_config = agents_config
        self.config = None  # not used in grid mode

        # Build model from grid
        self.model = WarehouseModel.from_grid(grid_config)

        # Reset per-type index registry
        _type_index_map.clear()

        # Reset occupied spawn positions
        self.occupied_spawn_positions = set()

        meta = grid_config.metadata
        sc = agents_config.scouts
        co = agents_config.coordinators
        re = agents_config.retrievers

        # Behavior param dicts for each role
        scout_beh = agents_config.scout_behavior.model_dump()
        coord_beh = agents_config.coordinator_behavior.model_dump()
        retr_beh = agents_config.retriever_behavior.model_dump()

        # Spawn order: coordinators → retrievers → scouts (top-left first)

        # — spawn coordinators —
        for i in range(co.count):
            agent = CoordinatorAgent(
                unique_id=i,
                model=self.model,
                vision_radius=co.vision_radius,
                communication_radius=co.communication_radius,
                max_energy=co.max_energy,
                speed=co.speed,
                behavior=coord_beh,
            )
            register_type_index(i, i + 1)
            free_pos = self._find_tl_spawn_position()
            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
                agent.step_sense()
            else:
                print(f"Warning: Could not find free cell for coordinator {i}")

        # — spawn retrievers —
        base_id = co.count
        for i in range(re.count):
            agent = RetrieverAgent(
                unique_id=base_id + i,
                model=self.model,
                vision_radius=re.vision_radius,
                communication_radius=re.communication_radius,
                max_energy=re.max_energy,
                speed=re.speed,
                carrying_capacity=re.carrying_capacity,
                behavior=retr_beh,
            )
            register_type_index(base_id + i, i + 1)
            free_pos = self._find_tl_spawn_position()
            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
                agent.step_sense()
            else:
                print(f"Warning: Could not find free cell for retriever {i}")

        # — spawn scouts —
        base_id = co.count + re.count
        for i in range(sc.count):
            agent = ScoutAgent(
                unique_id=base_id + i,
                model=self.model,
                vision_radius=sc.vision_radius,
                communication_radius=sc.communication_radius,
                max_energy=sc.max_energy,
                speed=sc.speed,
                behavior=scout_beh,
            )
            register_type_index(base_id + i, i + 1)
            free_pos = self._find_tl_spawn_position()
            if free_pos:
                self.model.grid.place_agent(agent, free_pos)
                self.model.add_agent(agent)
                agent.step_sense()
            else:
                print(f"Warning: Could not find free cell for scout {i}")

        print(f"Grid simulation initialized with {len(self.model.agents)} agents")
        print(f"  Scenario: {meta.grid_size}x{meta.grid_size}, {meta.num_warehouses} warehouses")
        print(f"  - {len(self.model.scouts)} scouts")
        print(f"  - {len(self.model.coordinators)} coordinators")
        print(f"  - {len(self.model.retrievers)} retrievers")
        print(f"  - {self.model.total_objects} objects to retrieve (max_steps={meta.max_steps})")

        # Pre-knowledge: reveal full map (terrain + warehouses) to every agent
        if agents_config.map_known:
            self._apply_map_knowledge()

        # Snapshot RNG state so that step() can isolate itself from external
        # async consumers (WebSocket, Telegram notifier, etc.)
        self.model.snapshot_rng()

    def _apply_map_knowledge(self) -> None:
        """Pre-fill every agent's known_warehouses with all warehouse
        locations.  local_map is NOT pre-filled so exploration heuristics
        (frontier detection via local_map==0) remain identical to unknown
        mode — agents are still incentivised to explore and find objects.
        The only advantage is warehouse knowledge for efficient delivery
        pathfinding (A* already uses the real grid)."""
        if not self.model:
            return
        grid = self.model.grid
        w, h = grid.width, grid.height

        # Collect all warehouse cells
        _WH = {CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT}
        wh_cells = [
            (x, y) for x in range(w) for y in range(h) if CellType(grid.cell_types[x, y]) in _WH
        ]

        from backend.agents.base_agent import BaseAgent

        for agent in self.model.agents:
            if not isinstance(agent, BaseAgent):
                continue
            # Populate known_warehouses (avoid duplicates)
            existing = set(agent.known_warehouses)
            for wc in wh_cells:
                if wc not in existing:
                    agent.known_warehouses.append(wc)

        # Store flag on the model so get_state_dict can forward it to the frontend
        self.model.map_known = True
        print(f"  Map pre-knowledge applied to {len(self.model.agents)} agents")

    async def load_from_grid(
        self,
        grid_config: GridScenarioConfig,
        agents_config: Optional[SimulationAgentsConfig],
        ws_manager,
        session_id: str = "default",
    ) -> None:
        """
        Initialize from grid format and broadcast step 0 state.

        Args:
            grid_config: Grid-based scenario.
            agents_config: Agent composition (uses defaults if None).
            ws_manager: WebSocket manager for broadcasting.
            session_id: Owning session identifier.
        """
        self.session_id = session_id
        self.initialize_from_grid(grid_config, agents_config)
        self.is_running = False
        self.is_paused = False
        state = self.get_simulation_state()
        await ws_manager.broadcast_state_to_session(session_id, state)

    def _find_free_cell_near(
        self, target_pos: Tuple[int, int], max_radius: int = 10, spread: bool = True
    ) -> Optional[Tuple[int, int]]:
        """
        Find a free cell near the target position with better distribution

        Args:
            target_pos: Desired position
            max_radius: Maximum search radius
            spread: If True, prefer positions further from target for better distribution

        Returns:
            Free cell position or None if not found
        """
        if not self.model:
            return None

        x, y = target_pos

        # Check if target itself is free
        if self.model.grid.is_cell_empty(target_pos) and self.model.grid.is_walkable(*target_pos):
            return target_pos

        # Collect all valid positions
        candidates = []

        # Search in expanding radius
        for radius in range(1, max_radius + 1):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    check_pos = (x + dx, y + dy)

                    # Skip if out of bounds
                    if self.model.grid.out_of_bounds(check_pos):
                        continue

                    # Check if cell is empty, walkable, and not already claimed
                    if (
                        check_pos not in self.occupied_spawn_positions
                        and self.model.grid.is_cell_empty(check_pos)
                        and self.model.grid.is_walkable(*check_pos)
                    ):
                        # Calculate actual distance
                        dist = abs(dx) + abs(dy)  # Manhattan distance
                        candidates.append((check_pos, dist))

        if not candidates:
            return None

        # If spread is True, prefer positions further away to distribute agents
        if spread:
            # Sort by distance descending (furthest first)
            candidates.sort(key=lambda x: x[1], reverse=True)
            chosen = candidates[0][0] if candidates else None
        else:
            # Return closest available position
            candidates.sort(key=lambda x: x[1])
            chosen = candidates[0][0] if candidates else None

        if chosen:
            self.occupied_spawn_positions.add(chosen)
        return chosen

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
        self._sim_start_time = time.monotonic()

        print("\n" + "=" * 60)
        print("SIMULATION STARTING")
        print(
            f"  Agents: {len(self.model.scouts)} scouts, {len(self.model.coordinators)} coordinators, {len(self.model.retrievers)} retrievers"
        )
        print(f"  Target: Retrieve {self.model.total_objects} objects")
        print(f"  Grid: {self.model.grid.width}x{self.model.grid.height}")
        print("=" * 60 + "\n")

        try:
            # Run simulation loop
            await self._simulation_loop(ws_manager)
        except Exception as e:
            print(f"\n!!! SIMULATION ERROR: {e} !!!\n")
            traceback.print_exc()
        finally:
            self.is_running = False
            print("\n" + "=" * 60)
            print("SIMULATION ENDED")
            if self.model:
                print(
                    f"  Final Stats: {self.model.objects_retrieved}/{self.model.total_objects} objects retrieved in {self.model.current_step} steps"
                )
            print("=" * 60 + "\n")

    async def _simulation_loop(self, ws_manager) -> None:
        """
        Main simulation loop

        Args:
            ws_manager: WebSocket manager for broadcasting
        """
        if not self.model:
            return

        while self.is_running and self.model.running:
            if not self.is_paused:
                # Step simulation
                self.model.step()

                # Periodic status report every 100 steps
                if self.model.current_step % 100 == 0:
                    print(
                        f"\n=== STEP {self.model.current_step} === Progress: {self.model.objects_retrieved}/{self.model.total_objects} objects retrieved ==="
                    )

                # Get state and broadcast (scoped to this session)
                state = self.get_simulation_state()
                await ws_manager.broadcast_state_to_session(self.session_id, state)

                # Check if complete
                if not self.model.running:
                    await ws_manager.broadcast_event_to_session(
                        self.session_id,
                        "simulation_complete",
                        {
                            "steps": self.model.current_step,
                            "objects_retrieved": self.model.objects_retrieved,
                            "total_objects": self.model.total_objects,
                        },
                    )
                    elapsed = (
                        time.monotonic() - self._sim_start_time
                        if self._sim_start_time is not None
                        else None
                    )
                    asyncio.create_task(
                        _get_notify_complete()(
                            config_name=getattr(self.config, "name", None),
                            steps=self.model.current_step,
                            objects_retrieved=self.model.objects_retrieved,
                            total_objects=self.model.total_objects,
                            elapsed_seconds=elapsed,
                        )
                    )
                    break

            # Wait for next update (re-read rate each iteration so speed changes apply immediately)
            await asyncio.sleep(1.0 / self.update_rate)

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

    async def reset_simulation(self) -> None:
        """Reset the simulation to a clean initial state.

        Cancels and awaits any active simulation task so it cannot
        interfere with the freshly-initialised model.
        """
        # Cancel the running asyncio task (if any) and wait for it to exit
        # cleanly before touching the model.  This prevents the old loop
        # from waking up after the new model is created and stepping it.
        if self.simulation_task is not None and not self.simulation_task.done():
            # Signal the loop to stop (belt-and-suspenders alongside cancel())
            self.is_running = False
            if self.model:
                self.model.running = False
            self.simulation_task.cancel()
            try:
                await self.simulation_task
            except asyncio.CancelledError:
                pass
            self.simulation_task = None
        else:
            self.stop_simulation()

        if self._grid_config is not None:
            # Grid-format mode
            self.initialize_from_grid(self._grid_config, self._agents_config)
        elif self.config:
            self.initialize_simulation(self.config)
        print("Simulation reset")

    def set_speed(self, speed: float) -> None:
        """
        Set simulation speed

        Args:
            speed: Speed multiplier (0.1 to 10.0, where 1.0 is normal speed)
        """
        # Clamp speed between 0.1 and 10.0
        speed = max(0.1, min(10.0, speed))
        # Base rate is 18 updates per second at 1.0 speed
        self.update_rate = 18 * speed
        print(f"Simulation speed set to {speed}x (update_rate: {self.update_rate:.1f} Hz)")

    def get_simulation_state(self) -> dict:
        """
        Get current simulation state

        Returns:
            State dictionary for WebSocket broadcast
        """
        if not self.model:
            return {}

        state = self.model.get_state_dict()

        # Collect warehouse and obstacle cells directly from the live grid
        warehouse_cells = []
        obstacle_cells = []
        entrance_cells = []
        exit_cells = []
        for x in range(self.model.grid.width):
            for y in range(self.model.grid.height):
                cell_type = self.model.grid.get_cell_type(x, y)
                if cell_type == CellType.WAREHOUSE:
                    warehouse_cells.append({"x": x, "y": y})
                elif cell_type == CellType.OBSTACLE:
                    obstacle_cells.append({"x": x, "y": y})
                elif cell_type == CellType.WAREHOUSE_ENTRANCE:
                    entrance_cells.append({"x": x, "y": y})
                elif cell_type == CellType.WAREHOUSE_EXIT:
                    exit_cells.append({"x": x, "y": y})

        state["grid"] = {
            "width": self.model.grid.width,
            "height": self.model.grid.height,
            "warehouse": {
                "x": self.model.warehouse_position[0],
                "y": self.model.warehouse_position[1],
                "width": 1,
                "height": 1,
                "cells": warehouse_cells,
                "entrances": entrance_cells,
                "exits": exit_cells if exit_cells else entrance_cells,
            },
            "obstacles": [
                {
                    "type": "box",
                    "data": {"top_left": {"x": c["x"], "y": c["y"]}, "width": 1, "height": 1},
                }
                for c in obstacle_cells
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
