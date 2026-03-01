"""
Warehouse simulation model
"""

import random
from typing import Dict, List, Optional, Tuple

import numpy as np

from backend.agents.base_agent import BaseAgent
from backend.config.schemas import ScenarioConfig
from backend.core.communication import CommunicationManager, CoordinationSystem
from backend.core.framework import DataCollector, Model
from backend.core.grid_manager import CellType, GridManager
from backend.metrics import MetricsCollector


class WarehouseModel(Model):
    """
    Main simulation model for multi-agent warehouse system

    Manages:
    - Grid and environment
    - Agent scheduling
    - Communication between agents
    - Metrics collection
    - Simulation state
    """

    # Stages for staged activation (prevents race conditions)
    STAGE_SENSE = 0
    STAGE_COMMUNICATE = 1
    STAGE_DECIDE = 2
    STAGE_ACT = 3

    def __init__(self, config: ScenarioConfig):
        # Initialize Model with seed
        rng_seed = config.simulation.seed if config.simulation.seed is not None else None
        super().__init__(seed=rng_seed)

        self.config = config

        # Set random seed for reproducibility (NumPy)
        if config.simulation.seed is not None:
            np.random.seed(config.simulation.seed)

        # Initialize grid
        self.grid = GridManager(
            config.simulation.grid_width, config.simulation.grid_height, torus=False
        )

        # Communication and coordination
        self.comm_manager = CommunicationManager()
        self.coordination = CoordinationSystem()

        # Simulation state
        self.running = True
        self.current_step = 0
        self.max_steps = config.simulation.max_steps

        # Warehouse info
        self.warehouse_position = (config.warehouse.position.x, config.warehouse.position.y)
        self.warehouse_entrances = []
        self.warehouse_exits = []
        # Station info: computed after _setup_warehouse()
        self.warehouse_stations = []

        # Objects tracking
        self.total_objects = config.objects.count
        self.objects_retrieved = 0

        # Initialize environment
        self._setup_warehouse()
        self._compute_warehouse_stations()
        self._setup_obstacles()
        self._spawn_objects()

        # Agents will be added by external code
        # (see agents/*.py for agent implementations)
        self.scouts = []
        self.coordinators = []
        self.retrievers = []

        # Metrics collection
        self._setup_data_collector()
        self.metrics_collector = MetricsCollector(
            simulation_id=f"sim_{config.simulation.seed or 'random'}",
            grid_size=(config.simulation.grid_width, config.simulation.grid_height),
            config={
                "scouts": 0,  # Will be updated when agents added
                "coordinators": 0,
                "retrievers": 0,
                "total_objects": config.objects.count,
            },
        )

    def _setup_warehouse(self) -> None:
        """Setup warehouse cells on the grid"""
        cfg = self.config.warehouse

        # Mark warehouse area - either from explicit cells or from position+size
        if cfg.warehouse_cells:
            # Use explicit cell positions for multiple warehouse areas
            for cell in cfg.warehouse_cells:
                self.grid.set_cell_type(cell.x, cell.y, CellType.WAREHOUSE)
        else:
            # Use traditional single warehouse block
            for x in range(cfg.position.x, cfg.position.x + cfg.width):
                for y in range(cfg.position.y, cfg.position.y + cfg.height):
                    self.grid.set_cell_type(x, y, CellType.WAREHOUSE)

        # Mark entrances
        for entrance in cfg.entrances:
            self.grid.set_cell_type(entrance.x, entrance.y, CellType.WAREHOUSE_ENTRANCE)
            self.warehouse_entrances.append((entrance.x, entrance.y))

        # Mark exits (or use entrances if no exits specified)
        exits = cfg.exits if cfg.exits else cfg.entrances
        for exit_point in exits:
            self.grid.set_cell_type(exit_point.x, exit_point.y, CellType.WAREHOUSE_EXIT)
            self.warehouse_exits.append((exit_point.x, exit_point.y))

    def _compute_warehouse_stations(self) -> None:
        """
        Group warehouse cells into stations, one per entrance.
        For each entrance, find the nearby WAREHOUSE cells and derive:
          - deposit_cell  : nearest WAREHOUSE cell to entrance (drop objects here)
          - recharge_cell : farthest WAREHOUSE cell from entrance (recharge here,
                            avoids blocking the entrance)
          - exit          : matching exit cell for this entrance
        """
        if not self.warehouse_entrances:
            return

        # Collect all WAREHOUSE-type interior cells
        interior_cells = [
            (x, y)
            for x in range(self.grid.width)
            for y in range(self.grid.height)
            if self.grid.get_cell_type(x, y) == CellType.WAREHOUSE
        ]

        for entrance in self.warehouse_entrances:
            ex, ey = entrance

            # Assign cells within Manhattan-3 to this entrance
            nearby_cells = [c for c in interior_cells if abs(c[0] - ex) + abs(c[1] - ey) <= 3]

            if not nearby_cells:
                nearby_cells = interior_cells  # fallback (single station)

            # Deposit = closest interior cell to entrance
            deposit_cell = min(
                nearby_cells,
                key=lambda c: abs(c[0] - ex) + abs(c[1] - ey),
            )

            # Recharge = farthest interior cell from entrance
            recharge_cell = max(
                nearby_cells,
                key=lambda c: abs(c[0] - ex) + abs(c[1] - ey),
            )

            # Match the exit closest to this entrance
            exit_cell = entrance  # default: use entrance itself
            if self.warehouse_exits:
                exit_cell = min(
                    self.warehouse_exits,
                    key=lambda e: abs(e[0] - ex) + abs(e[1] - ey),
                )

            # Queue cells: sorted by distance to entrance DESCENDING so that slot 0
            # is the deepest interior cell (farthest from entrance / door).
            # Agents fill from the back of the warehouse toward the entrance; none
            # ever parks right at the door even when the warehouse is crowded.
            queue_cells = sorted(
                nearby_cells,
                key=lambda c: abs(c[0] - ex) + abs(c[1] - ey),
                reverse=True,  # farthest from entrance = slot 0 (deepest inside)
            )

            self.warehouse_stations.append(
                {
                    "entrance": entrance,
                    "exit": exit_cell,
                    "deposit_cell": deposit_cell,
                    "recharge_cell": recharge_cell,
                    "queue_cells": queue_cells,
                    "cells": nearby_cells,
                }
            )

    def get_nearest_warehouse_to(self, pos: Tuple[int, int]) -> dict:
        """
        Return the nearest warehouse station dict to *pos*.

        The dict has keys: entrance, exit, deposit_cell, recharge_cell, queue_cells, cells.
        Falls back to a minimal dict built from warehouse_position if no stations.
        """
        if self.warehouse_stations:
            return min(
                self.warehouse_stations,
                key=lambda s: (abs(s["entrance"][0] - pos[0]) + abs(s["entrance"][1] - pos[1])),
            )
        # Fallback for configs without explicit entrances
        wp = self.warehouse_position
        return {
            "entrance": wp,
            "exit": wp,
            "deposit_cell": wp,
            "recharge_cell": wp,
            "queue_cells": [wp],
            "cells": [wp],
        }

    def get_queue_slot(self, station: dict) -> Tuple[int, int]:
        """
        Return the interior cell an agent should target when joining the recharge queue.

        Cells are ordered farthest-from-entrance first (slot 0 = deepest inside).
        A new agent is assigned the slot equal to the number of agents currently
        occupying interior cells in this station, so it backs up from the rear.
        The cell closest to the entrance is never a door cell; agents therefore
        recharge well inside the warehouse regardless of congestion.
        """
        queue_cells: list = station.get("queue_cells") or []
        if not queue_cells:
            return station.get("recharge_cell", station.get("entrance", (0, 0)))

        station_cells_set: set = set(map(tuple, station.get("cells", [])))
        if not station_cells_set:
            return queue_cells[-1]  # back of queue (entrance-side)

        # Count agents already occupying interior (WAREHOUSE-type) cells of this station
        occupied = 0
        for agent in self.agents:
            if agent.pos is None:
                continue
            ap = (int(agent.pos[0]), int(agent.pos[1]))
            if ap in station_cells_set:
                ct = self.grid.get_cell_type(*ap)
                if ct == CellType.WAREHOUSE:
                    occupied += 1

        # New agent goes to slot `occupied` (back), capped at the last available cell
        idx = min(occupied, len(queue_cells) - 1)
        return queue_cells[idx]

    def get_best_warehouse_for(
        self,
        pos: Tuple[int, int],
        known_entrances: List[Tuple[int, int]],
        excluded_entrance: Optional[Tuple[int, int]] = None,
        congestion_penalty: int = 8,
    ) -> dict:
        """
        Select the best warehouse station for an agent at *pos*.

        Scoring (lower is better):
          score = Manhattan-distance(pos, entrance) + congestion_penalty * num_agents_heading_there

        Args:
            pos: agent's current position
            known_entrances: WAREHOUSE_ENTRANCE cells visible in the agent's local map.
                             If empty, all stations are considered.
            excluded_entrance: entrance to exclude (e.g. one the agent is already at).
            congestion_penalty: extra distance units per agent already heading to a station.
        """
        # Build candidate station list
        if known_entrances:
            # Map each known entrance to its station
            candidates = []
            seen = set()
            for ent in known_entrances:
                station = self.get_nearest_warehouse_to(ent)
                key = station["entrance"]
                if key not in seen and key != excluded_entrance:
                    candidates.append(station)
                    seen.add(key)
        else:
            candidates = [s for s in self.warehouse_stations if s["entrance"] != excluded_entrance]

        if not candidates:
            # Fall back to absolute nearest (ignore exclusion)
            return self.get_nearest_warehouse_to(pos)

        # Count how many retriever agents are currently heading to each entrance
        heading_count: Dict[Tuple[int, int], int] = {}
        for agent in self.agents:
            if getattr(agent, "role", None) != "retriever":
                continue
            wh_station = getattr(agent, "_wh_station", None)
            if wh_station:
                ent = wh_station.get("entrance")
                if ent:
                    heading_count[ent] = heading_count.get(ent, 0) + 1

        def score(s: dict) -> float:
            ent = s["entrance"]
            dist = abs(ent[0] - pos[0]) + abs(ent[1] - pos[1])
            congestion = heading_count.get(ent, 0)
            return dist + congestion_penalty * congestion

        return min(candidates, key=score)

    def _setup_obstacles(self) -> None:
        """Setup obstacles on the grid"""
        for obstacle in self.config.obstacles:
            if obstacle.type == "wall":
                # Draw line from start to end
                x0, y0 = obstacle.start.x, obstacle.start.y
                x1, y1 = obstacle.end.x, obstacle.end.y

                # Bresenham's line algorithm
                dx = abs(x1 - x0)
                dy = abs(y1 - y0)
                sx = 1 if x0 < x1 else -1
                sy = 1 if y0 < y1 else -1
                err = dx - dy

                x, y = x0, y0
                while True:
                    self.grid.place_obstacle(x, y)

                    if x == x1 and y == y1:
                        break

                    e2 = 2 * err
                    if e2 > -dy:
                        err -= dy
                        x += sx
                    if e2 < dx:
                        err += dx
                        y += sy

            elif obstacle.type == "box":
                # Fill rectangle
                for x in range(obstacle.top_left.x, obstacle.top_left.x + obstacle.width):
                    for y in range(obstacle.top_left.y, obstacle.top_left.y + obstacle.height):
                        self.grid.place_obstacle(x, y)

    def _spawn_objects(self) -> None:
        """Spawn objects in designated zones"""
        objects_placed = 0
        max_attempts = self.total_objects * 10
        attempts = 0

        # Calculate total probability weight
        total_prob = sum(zone.probability for zone in self.config.objects.spawn_zones)

        while objects_placed < self.total_objects and attempts < max_attempts:
            attempts += 1

            # Select zone based on probability
            rand = random.random() * total_prob
            cumulative = 0.0
            selected_zone = self.config.objects.spawn_zones[0]

            for zone in self.config.objects.spawn_zones:
                cumulative += zone.probability
                if rand <= cumulative:
                    selected_zone = zone
                    break

            # Random position in zone
            x = random.randint(selected_zone.x_range[0], selected_zone.x_range[1] - 1)
            y = random.randint(selected_zone.y_range[0], selected_zone.y_range[1] - 1)

            # Check if position is valid (not obstacle, not warehouse, not another object)
            if self.grid.is_walkable(x, y):
                cell_type = self.grid.get_cell_type(x, y)
                if cell_type not in [
                    CellType.WAREHOUSE,
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                    CellType.OBJECT,
                ]:
                    self.grid.place_object(x, y)
                    objects_placed += 1

        if objects_placed < self.total_objects:
            print(f"Warning: Only placed {objects_placed}/{self.total_objects} objects")

    def _setup_data_collector(self) -> None:
        """Setup Mesa data collector for metrics"""
        self.datacollector = DataCollector(
            model_reporters={
                "Objects Retrieved": lambda m: m.objects_retrieved,
                "Total Objects": lambda m: m.total_objects,
                "Retrieval Progress": lambda m: (
                    m.objects_retrieved / m.total_objects if m.total_objects > 0 else 0
                ),
                "Average Energy": lambda m: (
                    np.mean([getattr(a, "energy", 0) for a in m.agents]) if m.agents else 0
                ),
                "Active Agents": lambda m: len(
                    [a for a in m.agents if getattr(a, "energy", 0) > 0]
                ),
                "Step": lambda m: m.current_step,
            },
            agent_reporters={
                "Energy": lambda a: getattr(a, "energy", 0),
                "State": lambda a: (
                    a.state.value
                    if isinstance(a, BaseAgent) and hasattr(a.state, "value")
                    else "unknown"
                ),
                "X": lambda a: a.pos[0] if a.pos else 0,
                "Y": lambda a: a.pos[1] if a.pos else 0,
            },
        )

    def add_agent(self, agent) -> None:
        """
        Add an agent to the simulation

        Agents spawn at their designated location and immediately know
        the warehouse positions they can see from spawn.

        Args:
            agent: Agent instance to add
        """
        # Call parent's add_agent directly to avoid recursion
        super().add_agent(agent)

        # Add warehouse knowledge for all warehouse cells the agent can see
        if hasattr(agent, "known_warehouses") and agent.pos:
            # Get all warehouse cells
            warehouse_cells = []
            for x in range(self.grid.width):
                for y in range(self.grid.height):
                    cell_type = self.grid.get_cell_type(x, y)
                    if cell_type in [
                        CellType.WAREHOUSE,
                        CellType.WAREHOUSE_ENTRANCE,
                        CellType.WAREHOUSE_EXIT,
                    ]:
                        warehouse_cells.append((x, y))

            # Add visible warehouses to agent's memory
            if warehouse_cells and isinstance(agent, BaseAgent):
                from backend.agents.base_agent import pos_to_tuple

                agent_pos = pos_to_tuple(agent.pos)
                for wh_pos in warehouse_cells:
                    distance = abs(wh_pos[0] - agent_pos[0]) + abs(wh_pos[1] - agent_pos[1])
                    if distance <= agent.vision_radius:
                        if wh_pos not in agent.known_warehouses:
                            agent.known_warehouses.append(wh_pos)

                # If no warehouses visible, add the main warehouse position
                if not agent.known_warehouses:
                    agent.known_warehouses.append(self.warehouse_position)

        # Track by role for easy access
        if isinstance(agent, BaseAgent):
            if agent.role == "scout":
                self.scouts.append(agent)
            elif agent.role == "coordinator":
                self.coordinators.append(agent)
            elif agent.role == "retriever":
                self.retrievers.append(agent)

    def get_agent_positions(self) -> Dict[int, Tuple[int, int]]:
        """Get dictionary of all agent positions"""
        positions = {}
        for agent in self.agents:
            if agent.pos:
                agent_id = getattr(agent, "unique_id", None)
                if agent_id is not None:
                    positions[agent_id] = agent.pos
        return positions

    def step(self) -> None:
        """
        Advance simulation by one step

        Stages:
        1. SENSE: Agents perceive environment
        2. COMMUNICATE: Agents exchange information
        3. DECIDE: Agents make decisions
        4. ACT: Agents execute actions
        """
        # Update spatial index for proximity queries
        agent_positions = [agent.pos for agent in self.agents if agent.pos]
        self.grid.update_agent_spatial_index(agent_positions)

        # Step all agents
        for agent in list(self.agents):
            agent.step()

        # Collect data
        self.datacollector.collect(self)

        # Collect detailed metrics
        if hasattr(self, "metrics_collector"):
            self.metrics_collector.collect_step_metrics(self)

        # Update step counter
        self.current_step += 1

        # Check termination conditions
        if self.current_step >= self.max_steps:
            self.running = False
            if hasattr(self, "metrics_collector"):
                self.metrics_collector.finalize()

        if self.objects_retrieved >= self.total_objects:
            self.running = False
            print(f"All objects retrieved in {self.current_step} steps!")

    def get_state_dict(self) -> Dict:
        """
        Get current simulation state as dictionary (for WebSocket)

        Returns:
            Dictionary with agents, objects, and metrics
        """
        agents_data = []
        for agent in self.agents:
            if agent.pos:
                # Get state and convert Enum to string
                if isinstance(agent, BaseAgent) and hasattr(agent.state, "value"):
                    state = agent.state.value
                else:
                    state = "unknown"

                agent_data = {
                    "id": agent.unique_id,
                    "role": getattr(agent, "role", "unknown"),
                    "x": agent.pos[0],
                    "y": agent.pos[1],
                    "energy": getattr(agent, "energy", 0),
                    "state": state,
                    "carrying": getattr(agent, "carrying_objects", 0),
                    "vision_radius": getattr(agent, "vision_radius", 5),
                    "communication_radius": getattr(agent, "communication_radius", 10),
                    "recent_messages": getattr(agent, "recent_messages", []),
                    "path": (
                        [{"x": p[0], "y": p[1]} for p in getattr(agent, "path", [])]
                        if getattr(agent, "path", None)
                        else []
                    ),
                }
                agents_data.append(agent_data)

        objects_data = [{"x": x, "y": y, "retrieved": False} for x, y in self.grid.objects]

        # Add retrieved objects
        objects_data.extend(
            [{"x": x, "y": y, "retrieved": True} for x, y in self.grid.retrieved_objects]
        )

        avg_energy = np.mean([getattr(a, "energy", 0) for a in self.agents]) if self.agents else 0

        return {
            "step": self.current_step,
            "agents": agents_data,
            "objects": objects_data,
            "metrics": {
                "objects_retrieved": self.objects_retrieved,
                "total_objects": self.total_objects,
                "retrieval_progress": (
                    self.objects_retrieved / self.total_objects if self.total_objects > 0 else 0
                ),
                "average_energy": float(avg_energy),
                "active_agents": len([a for a in self.agents if getattr(a, "energy", 0) > 0]),
            },
        }
