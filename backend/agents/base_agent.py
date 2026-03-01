"""
Base agent class with common functionality
"""

from enum import Enum
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np

from backend.core.communication import MapDataMessage, MapSharingSystem, ObjectLocationMessage
from backend.core.decision_maker import DecisionMaker
from backend.core.framework import Agent
from backend.core.grid_manager import CellType

if TYPE_CHECKING:
    from backend.core.warehouse_model import WarehouseModel


def pos_to_tuple(pos) -> Tuple[int, int]:
    """Convert Mesa Position to tuple"""
    if hasattr(pos, "__iter__") and not isinstance(pos, str):
        return tuple(int(x) for x in pos)  # type: ignore
    return (int(pos), int(pos))  # Fallback


class AgentState(Enum):
    """Possible agent states"""

    EXPLORING = "exploring"
    MOVING_TO_TARGET = "moving_to_target"
    RETRIEVING = "retrieving"
    DELIVERING = "delivering"
    RECHARGING = "recharging"
    IDLE = "idle"


class BaseAgent(Agent):
    """
    Base agent with common functionality for all agent types

    Features:
    - Energy management
    - Vision and perception
    - Local map memory
    - Communication
    - Basic movement
    """

    def __init__(
        self,
        unique_id: int,
        model: "WarehouseModel",
        role: str,
        vision_radius: int = 5,
        communication_radius: int = 15,
        max_energy: float = 100.0,
        speed: float = 1.0,
        energy_consumption: Optional[dict] = None,
    ):
        super().__init__(unique_id, model)
        self.model: "WarehouseModel" = model

        self.role = role
        self.state = AgentState.IDLE

        # Physical properties
        self.vision_radius = vision_radius
        self.communication_radius = communication_radius
        self.speed = speed

        # Energy system
        self.max_energy = max_energy
        self.energy = max_energy
        self.energy_consumption = energy_consumption or {
            "base": 0.1,
            "move": 0.5,
            "communicate": 0.2,
        }

        # Local map memory (initialized to UNKNOWN)
        grid_width = model.grid.width
        grid_height = model.grid.height
        self.local_map = np.zeros((grid_height, grid_width), dtype=np.int8)

        # Known object locations (position -> value)
        self.known_objects = {}

        # Known warehouse locations
        self.known_warehouses: List[Tuple[int, int]] = []

        # Movement
        self.path: List[Tuple[int, int]] = []
        self.target_position: Optional[Tuple[int, int]] = None
        self.stuck_counter: int = 0  # Count consecutive failed moves
        self.last_position: Optional[Tuple[int, int]] = None
        self.wait_counter: int = 0  # Counter for waiting when path blocked by other agents
        self.position_history: List[Tuple[int, int]] = []  # Track recent positions to detect loops
        self.max_history_length: int = 15  # Keep last 15 positions
        self.unreachable_targets: dict[Tuple[int, int], int] = {}  # Track unreachable targets {position: step}

        # Decision making
        self.decision_maker = DecisionMaker()

        # Communication
        self.last_communication_step = -1
        self.should_communicate_this_step = False  # Flag: communicate OR move this step
        self.communication_message = None  # Message to send if communicating
        self.recent_messages: List[dict] = []  # Track recent messages for UI display
        self.max_messages = 10  # Keep last 10 messages
        # Messages collected this step — drained exactly once in step_communicate
        self._step_messages: list = []

    @property
    def energy_percentage(self) -> float:
        """Get energy as percentage"""
        return (self.energy / self.max_energy) * 100.0

    def consume_energy(self, amount: float) -> None:
        """Consume energy, ensuring it doesn't go below 0"""
        self.energy = max(0.0, self.energy - amount)

    def recharge_energy(self, amount: float) -> None:
        """Recharge energy, capped at max_energy"""
        self.energy = min(self.max_energy, self.energy + amount)
    
    def log_message(self, direction: str, message_type: str, details: str, target_ids: Optional[List[int]] = None) -> None:
        """
        Log a message for UI display
        
        Args:
            direction: "sent" or "received"
            message_type: Type of message (e.g., "object_location", "task_assignment")
            details: Summary of message content
            target_ids: List of recipient/sender IDs
        """
        message_entry = {
            "step": self.model.current_step,
            "direction": direction,
            "type": message_type,
            "details": details,
            "targets": target_ids or []
        }
        self.recent_messages.append(message_entry)
        
        # Keep only last N messages
        if len(self.recent_messages) > self.max_messages:
            self.recent_messages = self.recent_messages[-self.max_messages:]

    def is_at_warehouse(self) -> bool:
        """Check if agent is at warehouse entrance"""
        if not self.pos:
            return False

        pos_tuple = pos_to_tuple(self.pos)
        cell_type = self.model.grid.get_cell_type(*pos_tuple)
        return cell_type in [
            CellType.WAREHOUSE,
            CellType.WAREHOUSE_ENTRANCE,
            CellType.WAREHOUSE_EXIT,
        ]

    def perceive_environment(self) -> List[Tuple[int, int, CellType]]:
        """
        Perceive visible cells within vision radius

        Returns:
            List of (x, y, cell_type) tuples
        """
        if not self.pos:
            return []

        pos_tuple = pos_to_tuple(self.pos)
        visible = self.model.grid.get_visible_cells(pos_tuple[0], pos_tuple[1], self.vision_radius)

        return visible

    def update_local_map(self, visible_cells: List[Tuple[int, int, CellType]]) -> None:
        """
        Update local map with perceived information

        Args:
            visible_cells: List of visible (x, y, cell_type) tuples
        """
        for x, y, cell_type in visible_cells:
            if 0 <= y < self.local_map.shape[0] and 0 <= x < self.local_map.shape[1]:
                self.local_map[y, x] = cell_type

                # Track discovered objects
                if cell_type == CellType.OBJECT:
                    self.known_objects[(x, y)] = 1.0

                # Track discovered warehouses (entrance cells are navigation targets)
                if cell_type in (
                    CellType.WAREHOUSE,
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                ) and (x, y) not in self.known_warehouses:
                    self.known_warehouses.append((x, y))

    def get_closest_warehouse(self) -> Optional[Tuple[int, int]]:
        """
        Get the closest known warehouse *entrance* position.

        Returns:
            Closest entrance position or None
        """
        if not self.pos:
            return self.model.warehouse_position

        pos_tuple = pos_to_tuple(self.pos)

        # Prefer entrance cells if we know some
        entrances = [
            wh for wh in self.known_warehouses
            if self.model.grid.get_cell_type(*wh) in (
                CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT
            )
        ]
        candidates = entrances if entrances else self.known_warehouses

        if not candidates:
            return self.model.warehouse_position

        return min(
            candidates,
            key=lambda wh: abs(wh[0] - pos_tuple[0]) + abs(wh[1] - pos_tuple[1]),
        )

    def get_nearby_agents(self, radius: Optional[float] = None) -> List[Agent]:
        """
        Get agents within specified radius (defaults to communication radius)

        Returns:
            List of nearby agents
        """
        if not self.pos:
            return []

        if radius is None:
            radius = self.communication_radius

        pos_tuple = pos_to_tuple(self.pos)
        agent_indices = self.model.grid.get_agents_in_radius(pos_tuple[0], pos_tuple[1], radius)

        nearby = []
        all_agents = list(self.model.agents)

        for idx in agent_indices:
            if idx < len(all_agents):
                agent = all_agents[idx]
                agent_id = getattr(agent, "unique_id", None)
                my_id = getattr(self, "unique_id", None)
                if agent_id != my_id:
                    nearby.append(agent)

        return nearby

    def communicate_with_nearby_agents(self) -> int:
        """
        Share map data with nearby agents

        Returns:
            Number of agents communicated with
        """
        if not self.pos:
            return 0

        # Get nearby agents
        nearby = self.get_nearby_agents(self.communication_radius)

        if not nearby:
            return 0

        # Extract explored cells from local map
        explored_cells = MapSharingSystem.extract_explored_cells(self.local_map)

        # Create message
        message = MapDataMessage(
            sender_id=self.unique_id or 0,
            timestamp=self.model.current_step,
            explored_cells=explored_cells,
        )

        # Send to all nearby agents
        recipient_ids = [agent.unique_id for agent in nearby]
        self.model.comm_manager.send_message(message, recipient_ids)

        # Consume energy
        self.consume_energy(self.energy_consumption["communicate"])
        self.last_communication_step = self.model.current_step

        return len(nearby)

    def process_received_messages(self) -> None:
        """
        Process messages received this step.
        Uses self._step_messages which was drained exactly once in step_communicate.
        Subclasses call super() then iterate self._step_messages for their own types.
        """
        for message in self._step_messages:
            if isinstance(message, MapDataMessage):
                # Merge received map data
                self.local_map = MapSharingSystem.apply_shared_map_data(
                    self.local_map, message.explored_cells
                )
                # Extract object locations from shared data
                for x, y, cell_type in message.explored_cells:
                    if cell_type == CellType.OBJECT:
                        self.known_objects[(x, y)] = 1.0

            elif isinstance(message, ObjectLocationMessage):
                # Add discovered object to known objects
                self.known_objects[message.object_position] = message.object_value

    def move_towards(self, target: Tuple[int, int]) -> bool:
        """
        Move one step towards target position using intelligent pathfinding

        Args:
            target: Target (x, y) position

        Returns:
            True if moved successfully
        """
        if not self.pos:
            return False

        pos_tuple = pos_to_tuple(self.pos)
        if pos_tuple == target:
            return True  # Already at target

        # If waiting due to temporary blockage, decrement wait counter
        if self.wait_counter > 0:
            self.wait_counter -= 1
            return False  # Don't move, just wait

        # Track if we moved
        moved = False

        # Get positions of other agents to avoid collisions
        other_agent_positions = set()
        for agent in self.model.agents:
            if agent.unique_id != self.unique_id and agent.pos:
                other_agent_positions.add(pos_to_tuple(agent.pos))

        # Try to use A* pathfinding if agent has it
        if hasattr(self, 'pathfinder'):
            # Recompute path if we don't have one or if stuck
            if not self.path or self.stuck_counter > 5:
                self.path = self.pathfinder.find_path(pos_tuple, target, other_agent_positions)  # type: ignore[attr-defined]
                
                # If no path found, target is unreachable
                if self.path is None:
                    print(f"[{self.role.upper()} {self.unique_id}] PATH: No path found to {target}, target unreachable")
                    # Blacklist this target for 100 steps
                    self.unreachable_targets[target] = self.model.current_step
                    self.target_position = None
                    self.stuck_counter = 0
                    return False
                
                # Remove first element (current position) if path exists
                if self.path and len(self.path) > 1 and self.path[0] == pos_tuple:
                    self.path.pop(0)

            # Follow path if exists
            if self.path and len(self.path) > 0:
                next_pos = self.path[0]
                
                # Check if next position is walkable and not occupied
                if self.model.grid.is_walkable(*next_pos):
                    # Check collision with other agents
                    if self._check_collision(next_pos):
                        # Move to next position
                        self.model.grid.move_agent(self, next_pos)
                        self.consume_energy(self.energy_consumption["move"])
                        self.path.pop(0)  # Remove reached waypoint
                        moved = True
                    else:
                        # Position temporarily occupied by another agent
                        # Priority-based collision resolution: lower ID has priority
                        blocking_agent = None
                        for agent in self.model.agents:
                            if agent.pos and pos_to_tuple(agent.pos) == next_pos:
                                blocking_agent = agent
                                break
                        
                        self.stuck_counter += 1
                        
                        # If blocking agent has higher ID (lower priority), wait less
                        # If blocking agent has lower ID (higher priority), wait more
                        has_priority = blocking_agent is None or self.unique_id < blocking_agent.unique_id
                        
                        # Check if we should wait or replan
                        if self.stuck_counter <= 3 and has_priority:
                            # Short wait with priority - just wait a bit
                            self.wait_counter = 2
                        elif self.stuck_counter <= 7:
                            # Medium wait - other agent should move or we'll find alternative
                            self.wait_counter = 4 if not has_priority else 2
                        elif self.stuck_counter <= 15:
                            # Long wait - try alternative path
                            self.path = []
                        else:
                            # Too long stuck - give up on this target
                            print(f"[{self.role.upper()} {self.unique_id}] STUCK: Too long stuck at {pos_tuple}, abandoning target {target}")
                            self.target_position = None
                            self.path = []
                            self.stuck_counter = 0
                else:
                    # Path is permanently blocked (obstacle), replan
                    self.path = []
                    self.stuck_counter += 1
        else:
            # Fallback: Simple greedy movement if no pathfinder
            current_x, current_y = pos_tuple
            target_x, target_y = target

            # Calculate direction
            dx = 0 if target_x == current_x else (1 if target_x > current_x else -1)
            dy = 0 if target_y == current_y else (1 if target_y > current_y else -1)

            # Try diagonal move first
            if dx != 0 and dy != 0:
                new_pos = (current_x + dx, current_y + dy)
                if self.model.grid.is_walkable(*new_pos) and self._check_collision(new_pos):
                    self.model.grid.move_agent(self, new_pos)
                    self.consume_energy(self.energy_consumption["move"])
                    moved = True

            # Try horizontal move
            if not moved and dx != 0:
                new_pos = (current_x + dx, current_y)
                if self.model.grid.is_walkable(*new_pos) and self._check_collision(new_pos):
                    self.model.grid.move_agent(self, new_pos)
                    self.consume_energy(self.energy_consumption["move"])
                    moved = True

            # Try vertical move
            if not moved and dy != 0:
                new_pos = (current_x, current_y + dy)
                if self.model.grid.is_walkable(*new_pos) and self._check_collision(new_pos):
                    self.model.grid.move_agent(self, new_pos)
                    self.consume_energy(self.energy_consumption["move"])
                    moved = True

        # Update stuck counter
        if moved:
            self.stuck_counter = 0
            self.wait_counter = 0
            self.last_position = pos_to_tuple(self.pos) if self.pos else pos_tuple
            
            # Track position history to detect loops
            current_pos = pos_to_tuple(self.pos) if self.pos else pos_tuple
            self.position_history.append(current_pos)
            if len(self.position_history) > self.max_history_length:
                self.position_history.pop(0)
            
            # Detect loop: if we're oscillating between 2-3 positions
            if len(self.position_history) >= 10:
                # Count unique positions in recent history
                recent_positions = set(self.position_history[-10:])
                if len(recent_positions) <= 3:
                    # Oscillating between few positions - clear target
                    print(f"[{self.role.upper()} {self.unique_id}] LOOP: Detected position loop (oscillating between {len(recent_positions)} positions), abandoning target {self.target_position}")
                    self.target_position = None
                    self.path = []
                    self.stuck_counter = 0
                    self.wait_counter = 0
                    self.position_history = []
        else:
            self.stuck_counter += 1
            # If stuck for too long, clear target and path to find alternative
            if self.stuck_counter > 20:
                print(f"[{self.role.upper()} {self.unique_id}] STUCK: Stuck for {self.stuck_counter} steps at {pos_to_tuple(self.pos)}, abandoning target {self.target_position}")
                self.target_position = None
                self.path = []
                self.stuck_counter = 0
                self.wait_counter = 0
                self.position_history = []

        return moved

    def _check_collision(self, new_pos: Tuple[int, int]) -> bool:
        """
        Check if moving to new position would collide with another agent

        Args:
            new_pos: Target position

        Returns:
            True if no collision (safe to move)
        """
        # With single-agent-per-cell grid, simply check if cell is empty
        return self.model.grid.is_cell_empty(new_pos)

    def step_sense(self) -> None:
        """Stage 0: Perceive environment (everyone always does this)"""
        # Base energy consumption (reduced to avoid draining when stuck)
        self.consume_energy(self.energy_consumption["base"] * 0.1)

        # Perceive visible cells
        visible = self.perceive_environment()
        self.update_local_map(visible)

    def step_communicate(self) -> None:
        """Stage 1: Drain mailbox once, share map, process messages."""
        # Drain mailbox exactly ONCE — subclasses must NOT call get_messages() again
        self._step_messages = self.model.comm_manager.get_messages(self.unique_id)
        # Proactively share map data with all agents in communication radius
        self.communicate_with_nearby_agents()
        # Process all received messages (subclasses extend this)
        self.process_received_messages()

    def step_decide(self) -> None:
        """Stage 2: Make decisions (implemented by subclasses)"""
        # Subclasses should set self.should_communicate_this_step flag
        # if they want to communicate instead of move
        pass

    def step_act(self) -> None:
        """Stage 3: Execute actions - either MOVE or COMMUNICATE (not both)"""
        # Check if agent is blocking warehouse entrance/exit and should move
        if self.pos:
            pos_tuple = pos_to_tuple(self.pos)
            cell_type = self.model.grid.get_cell_type(*pos_tuple)
            
            # If idle/exploring on entrance/exit, move away to unblock
            if cell_type in [CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT]:
                if self.state in [AgentState.IDLE, AgentState.EXPLORING]:
                    # Try to move to adjacent non-entrance/exit cell
                    for dx, dy in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
                        new_pos = (pos_tuple[0] + dx, pos_tuple[1] + dy)
                        if (0 <= new_pos[0] < self.model.grid.width and 
                            0 <= new_pos[1] < self.model.grid.height):
                            new_cell_type = self.model.grid.get_cell_type(*new_pos)
                            if (new_cell_type not in [CellType.OBSTACLE,
                                                       CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE,
                                                       CellType.WAREHOUSE_EXIT] and
                                self.model.grid.is_cell_empty(new_pos)):
                                self.model.grid.move_agent(self, new_pos)
                                print(f"[{self.role.upper()} {self.unique_id}] UNBLOCK: Moving out of entrance/exit to {new_pos}")
                                return
        
        # Subclasses override this to implement specific behavior
        pass

    def step(self) -> None:
        """
        Execute one step of the agent's behavior
        
        Cycle: SENSE -> COMMUNICATE (receive) -> DECIDE -> ACT (move OR send)
        """
        if self.energy <= 0:
            return  # Agent is out of energy

        # Execute all stages sequentially
        self.step_sense()
        self.step_communicate()
        self.step_decide()
        self.step_act()
