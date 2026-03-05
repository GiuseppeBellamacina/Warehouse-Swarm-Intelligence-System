"""
Base agent class with common functionality
"""

from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import numpy as np

from backend.core.communication import (
    ClearWayMessage,
    MapDataMessage,
    MapSharingSystem,
    ObjectLocationMessage,
    Stamped,
)
from backend.core.decision_maker import DecisionMaker
from backend.core.framework import Agent
from backend.core.grid_manager import CellType

if TYPE_CHECKING:
    from backend.algorithms.pathfinding import AStarPathfinder
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
        vision_radius: int = 3,
        communication_radius: int = 2,
        max_energy: float = 500.0,
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
            "base": 0.0,
            "move": 1.0,
            "communicate": 0.0,
        }

        # Local map memory (initialized to UNKNOWN)
        grid_width = model.grid.width
        grid_height = model.grid.height
        self.local_map = np.zeros((grid_height, grid_width), dtype=np.int8)

        # Known object locations (position -> value)
        self.known_objects: Dict[Tuple, float] = {}
        # Step at which each known_objects entry was last confirmed
        self.known_objects_step: Dict[Tuple, int] = {}
        # Tombstone: step at which we (or a peer) last confirmed a position is NOT an object
        self.known_objects_cleared: Dict[Tuple, int] = {}

        # Last-known positions of retrievers, learned via message relay
        # {retriever_id: (x, y)} — re-broadcast so every agent is a relay
        self.retriever_positions: Dict[int, Tuple[int, int]] = {}
        # Step at which each retriever_positions entry was last updated
        self.retriever_positions_step: Dict[int, int] = {}

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
        self.unreachable_targets: dict[Tuple[int, int], int] = (
            {}
        )  # Track unreachable targets {position: step}

        # Optional A* pathfinder — set by subclasses that support it (e.g. RetrieverAgent)
        self.pathfinder: Optional["AStarPathfinder"] = None

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
        # Throttle ClearWayMessage sending: track the step we last sent one
        self._last_clearway_sent: int = -99

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

    def log_message(
        self,
        direction: str,
        message_type: str,
        details: str,
        target_ids: Optional[List[int]] = None,
    ) -> None:
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
            "targets": target_ids or [],
        }
        self.recent_messages.append(message_entry)

        # Keep only last N messages
        if len(self.recent_messages) > self.max_messages:
            self.recent_messages = self.recent_messages[-self.max_messages :]

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
                    pos = (x, y)
                    self.known_objects[pos] = 1.0
                    self.known_objects_step[pos] = self.model.current_step
                    self.known_objects_cleared.pop(pos, None)  # re-seen: remove tombstone
                else:
                    # Direct vision confirms this cell is NOT an object right now
                    pos = (x, y)
                    if pos in self.known_objects:
                        self.known_objects.pop(pos, None)
                        self.known_objects_step.pop(pos, None)
                        self.known_objects_cleared[pos] = self.model.current_step
                    elif self.known_objects_cleared.get(pos, -1) < self.model.current_step:
                        self.known_objects_cleared[pos] = self.model.current_step

                # Track discovered warehouses (entrance cells are navigation targets)
                if (
                    cell_type
                    in (
                        CellType.WAREHOUSE,
                        CellType.WAREHOUSE_ENTRANCE,
                        CellType.WAREHOUSE_EXIT,
                    )
                    and (x, y) not in self.known_warehouses
                ):
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
            wh
            for wh in self.known_warehouses
            if self.model.grid.get_cell_type(*wh)
            in (CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT)
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

        # Create message — carry full knowledge with Stamped timestamps so every
        # recipient can apply "newest wins" and use this agent as a relay node.
        cs = self.model.current_step
        message = MapDataMessage(
            sender_id=self.unique_id or 0,
            timestamp=cs,
            explored_cells=explored_cells,
            known_objects={
                pos: Stamped(val, self.known_objects_step.get(pos, 0))
                for pos, val in self.known_objects.items()
            },
            objects_being_collected={
                pos: Stamped(None, getattr(self, "objects_being_collected_step", {}).get(pos, 0))
                for pos in getattr(self, "objects_being_collected", [])
            },
            retriever_positions={
                rid: Stamped(tuple(p), self.retriever_positions_step.get(rid, 0))
                for rid, p in self.retriever_positions.items()
                if p
            },
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

        All knowledge merges apply a "newest wins" rule using per-item timestamps.
        """
        for message in self._step_messages:
            if isinstance(message, MapDataMessage):
                # Merge raw topology first
                self.local_map = MapSharingSystem.apply_shared_map_data(
                    self.local_map, message.explored_cells
                )
                _WH_CELL_TYPES = (
                    CellType.WAREHOUSE,
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                )

                # --- explored_cells: object positions with message-level timestamp ---
                msg_ts = message.timestamp
                for x, y, cell_type in message.explored_cells:
                    pos = (x, y)
                    if cell_type == CellType.OBJECT:
                        # Accept only if newer than current entry and tombstone
                        if msg_ts > self.known_objects_step.get(
                            pos, -1
                        ) and msg_ts > self.known_objects_cleared.get(pos, -1):
                            self.known_objects[pos] = 1.0
                            self.known_objects_step[pos] = msg_ts
                    else:
                        # Sender confirms no object here — clear if their info is newer
                        if pos in self.known_objects:
                            if msg_ts >= self.known_objects_step.get(pos, 0):
                                del self.known_objects[pos]
                                self.known_objects_step.pop(pos, None)
                                self.known_objects_cleared[pos] = msg_ts
                        elif cell_type in _WH_CELL_TYPES:
                            if pos not in self.known_warehouses:
                                self.known_warehouses.append(pos)

                # --- objects_being_collected: Stamped(value=None, step), newest wins ---
                my_obc = getattr(self, "objects_being_collected", None)
                my_obc_step = getattr(self, "objects_being_collected_step", None)
                for raw_pos, stamped in message.objects_being_collected.items():
                    pos = tuple(raw_pos)
                    step = stamped.step if isinstance(stamped, Stamped) else int(stamped)
                    if my_obc is not None and my_obc_step is not None:
                        if step > my_obc_step.get(pos, -1):
                            my_obc.add(pos)
                            my_obc_step[pos] = step
                    # Remove from known_objects when OBC entry is at least as recent
                    if step >= self.known_objects_step.get(pos, -1):
                        self.known_objects.pop(pos, None)
                        self.known_objects_step.pop(pos, None)
                        if step > self.known_objects_cleared.get(pos, -1):
                            self.known_objects_cleared[pos] = step

                # --- known_objects relay: Stamped(value=float, step), newest wins ---
                for raw_pos, stamped in message.known_objects.items():
                    pos = tuple(raw_pos)
                    val = stamped.value if isinstance(stamped, Stamped) else stamped
                    step = stamped.step if isinstance(stamped, Stamped) else 0
                    if self.known_objects_cleared.get(pos, -1) >= step:
                        continue
                    if step > self.known_objects_step.get(pos, -1):
                        if my_obc is None or pos not in my_obc:
                            self.known_objects[pos] = val
                            self.known_objects_step[pos] = step

                # --- retriever_positions relay: Stamped(value=(x,y), step), newest wins ---
                for rid, stamped in message.retriever_positions.items():
                    pos_val = stamped.value if isinstance(stamped, Stamped) else stamped[0]
                    step = stamped.step if isinstance(stamped, Stamped) else stamped[1]
                    if step > self.retriever_positions_step.get(rid, -1):
                        self.retriever_positions[rid] = tuple(pos_val)
                        self.retriever_positions_step[rid] = step

            elif isinstance(message, ObjectLocationMessage):
                # Accept only if newer than current entry and not tombstoned
                pos = message.object_position
                if (
                    message.timestamp > self.known_objects_step.get(pos, -1)
                    and self.known_objects_cleared.get(pos, -1) < message.timestamp
                ):
                    self.known_objects[pos] = message.object_value
                    self.known_objects_step[pos] = message.timestamp

            elif isinstance(message, ClearWayMessage):
                self._handle_clear_way_message(message)

    # ------------------------------------------------------------------
    # ClearWay helpers
    # ------------------------------------------------------------------

    def _get_agent_at_pos(self, pos: Tuple[int, int]) -> Optional[int]:
        """Return the unique_id of the agent occupying ``pos``, or None."""
        for agent in self.model.agents:
            if agent.unique_id != self.unique_id and agent.pos:
                agent_pos = pos_to_tuple(agent.pos)
                if agent_pos == pos:
                    return agent.unique_id
        return None

    def _send_clear_way_request(
        self,
        cell: Tuple[int, int],
        recipient_id: int,
        chain_depth: int = 0,
    ) -> None:
        """Send a ClearWayMessage to a specific agent, throttled to once per 5 steps."""
        current_step = self.model.current_step
        if current_step - self._last_clearway_sent < 5:
            return
        self._last_clearway_sent = current_step
        msg = ClearWayMessage(
            sender_id=self.unique_id or 0,
            timestamp=current_step,
            cell=cell,
            chain_depth=chain_depth,
        )
        self.model.comm_manager.send_message(msg, [recipient_id])
        print(
            f"[{self.role.upper()} {self.unique_id}] CLEARWAY: "
            f"asking agent {recipient_id} to vacate {cell} (depth={chain_depth})"
        )

    def _try_move_off_cell(self, avoid_warehouse: bool = False) -> bool:
        """
        Try to step off the current cell to any adjacent free non-obstacle cell.

        When ``avoid_warehouse`` is True (e.g. when standing on an entrance/exit),
        warehouse cells are also excluded so we don't immediately re-block a door.

        Returns True if the agent managed to move.
        """
        if not self.pos:
            return False
        my_pos = pos_to_tuple(self.pos)
        _wh = (CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT)
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            np_ = (my_pos[0] + dx, my_pos[1] + dy)
            if not (0 <= np_[0] < self.model.grid.width and 0 <= np_[1] < self.model.grid.height):
                continue
            nc = self.model.grid.get_cell_type(*np_)
            if nc == CellType.OBSTACLE:
                continue
            if avoid_warehouse and nc in _wh:
                continue
            if self.model.grid.is_cell_empty(np_):
                self.model.grid.move_agent(self, np_)
                self.consume_energy(self.energy_consumption["move"])
                print(
                    f"[{self.role.upper()} {self.unique_id}] CLEARWAY: "
                    f"moved off {my_pos} → {np_}"
                )
                return True
        return False

    def _try_move_off_entrance_exit(self) -> bool:
        """Backward-compat wrapper: vacate a warehouse door, staying off WH cells."""
        return self._try_move_off_cell(avoid_warehouse=True)

    def _handle_clear_way_message(self, message: ClearWayMessage) -> None:
        """
        Respond to a ClearWayMessage:
        - If we are on the requested cell, attempt to move off it.
        - If we cannot move and chain_depth < MAX, forward the request to
          whoever is blocking *our* preferred escape path.
        """
        if not self.pos:
            return
        my_pos = pos_to_tuple(self.pos)
        if my_pos != message.cell:
            return  # message delivered to wrong agent or we already moved

        cell_type = self.model.grid.get_cell_type(*my_pos)
        # On a warehouse door, avoid stepping onto another door; for plain cells
        # any free adjacent non-obstacle cell is acceptable.
        avoid_wh = cell_type in (CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT)

        print(
            f"[{self.role.upper()} {self.unique_id}] CLEARWAY: "
            f"received request to vacate {my_pos} (depth={message.chain_depth})"
        )

        if self._try_move_off_cell(avoid_warehouse=avoid_wh):
            return  # successfully moved — done

        # Could not move; try to forward the chain if budget allows
        if message.chain_depth >= ClearWayMessage.MAX_CHAIN_DEPTH:
            return

        # Find who is blocking adjacent escape cells and chain to them
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            np_ = (my_pos[0] + dx, my_pos[1] + dy)
            if not (0 <= np_[0] < self.model.grid.width and 0 <= np_[1] < self.model.grid.height):
                continue
            nc = self.model.grid.get_cell_type(*np_)
            if nc in (CellType.OBSTACLE,):
                continue
            blocker_id = self._get_agent_at_pos(np_)
            if blocker_id is not None:
                self._send_clear_way_request(
                    cell=np_,
                    recipient_id=blocker_id,
                    chain_depth=message.chain_depth + 1,
                )
                break  # forward to first blocker only

    def move_towards(self, target: Tuple[int, int]) -> bool:
        """
        Move one step towards target position using intelligent pathfinding.

        Warehouse ENTRANCE and EXIT cells are forbidden as *intermediate* path
        nodes whenever the target itself is not a warehouse cell.  This prevents
        A* from using doors as shortcuts on exterior routes, and also stops it
        from routing *out* through an exit just to re-enter via the entrance.

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

        # Determine whether warehouse doors may be used as transit nodes.
        #
        # Rules (prevent EXIT→ENTRANCE shortcuts and ENTRANCE→EXIT shortcuts):
        #
        #  1. Agent is already INSIDE a warehouse cell (any WH type):
        #     → no restrictions — agent must be able to exit via any door.
        #
        #  2. Agent is OUTSIDE and heading to an ENTRANCE (or internal WH cell):
        #     → forbid EXIT cells as transit (block the wrong-door shortcut).
        #
        #  3. Agent is OUTSIDE and heading to an EXIT (shouldn't happen in normal
        #     operation, but guard anyway):
        #     → forbid ENTRANCE cells as transit.
        #
        #  4. Pure external navigation (target is not a warehouse cell):
        #     → forbid ALL door types as transit.
        target_type = self.model.grid.get_cell_type(*target)
        my_type = self.model.grid.get_cell_type(*pos_tuple)
        _DOOR_TYPES = {CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT}
        _WH_TYPES = {CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT}
        if my_type in _WH_TYPES:
            # Case 1: inside warehouse — no restrictions so agent can always exit
            forbidden_types = None
        elif target_type in (CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE):
            # Case 2: approaching via entrance — only block exit-cell shortcuts
            forbidden_types = {CellType.WAREHOUSE_EXIT}
        elif target_type == CellType.WAREHOUSE_EXIT:
            # Case 3: targeting exit from outside — only block entrance shortcuts
            forbidden_types = {CellType.WAREHOUSE_ENTRANCE}
        else:
            # Case 4: external navigation — never shortcut through any door
            forbidden_types = _DOOR_TYPES

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
        if self.pathfinder is not None:
            # Recompute path if:
            # - we don't have one yet
            # - stuck too long
            # - cached path leads somewhere other than the current target
            #   (target changed since path was computed, e.g. new task assigned)
            cached_destination = self.path[-1] if self.path else None
            if cached_destination != target:
                self.path = []  # stale — destination changed
            if not self.path or self.stuck_counter > 5:
                new_path = self.pathfinder.find_path(
                    pos_tuple,
                    target,
                    other_agent_positions,
                    forbidden_types=forbidden_types,
                )

                # If no path found, target is unreachable
                if new_path is None:
                    print(
                        f"[{self.role.upper()} {self.unique_id}] PATH: No path found to {target}, target unreachable"
                    )
                    # Blacklist this target for 100 steps
                    self.unreachable_targets[target] = self.model.current_step
                    self.target_position = None
                    self.stuck_counter = 0
                    return False

                self.path = new_path

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

                        # Ask the blocker to step aside (works for any cell type)
                        if blocking_agent is not None and self.stuck_counter >= 3:
                            self._send_clear_way_request(next_pos, blocking_agent.unique_id)

                        # If blocking agent has higher ID (lower priority), wait less
                        # If blocking agent has lower ID (higher priority), wait more
                        has_priority = (
                            blocking_agent is None or self.unique_id < blocking_agent.unique_id
                        )

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
                            print(
                                f"[{self.role.upper()} {self.unique_id}] STUCK: Too long stuck at {pos_tuple}, abandoning target {target}"
                            )
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
                    print(
                        f"[{self.role.upper()} {self.unique_id}] LOOP: Detected position loop (oscillating between {len(recent_positions)} positions), abandoning target {self.target_position}"
                    )
                    # Blacklist the stuck target so step_decide doesn't immediately re-assign it
                    if self.target_position is not None:
                        self.unreachable_targets[self.target_position] = self.model.current_step
                    self.target_position = None
                    self.path = []
                    self.stuck_counter = 0
                    self.wait_counter = 0
                    self.position_history = []
        else:
            self.stuck_counter += 1
            # If stuck for too long, clear target and path to find alternative
            if self.stuck_counter > 20:
                print(
                    f"[{self.role.upper()} {self.unique_id}] STUCK: Stuck for {self.stuck_counter} steps at {pos_to_tuple(self.pos)}, abandoning target {self.target_position}"
                )
                # Blacklist the stuck target so step_decide doesn't immediately re-assign it
                if self.target_position is not None:
                    self.unreachable_targets[self.target_position] = self.model.current_step
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
                        if (
                            0 <= new_pos[0] < self.model.grid.width
                            and 0 <= new_pos[1] < self.model.grid.height
                        ):
                            new_cell_type = self.model.grid.get_cell_type(*new_pos)
                            if new_cell_type not in [
                                CellType.OBSTACLE,
                                CellType.WAREHOUSE,
                                CellType.WAREHOUSE_ENTRANCE,
                                CellType.WAREHOUSE_EXIT,
                            ] and self.model.grid.is_cell_empty(new_pos):
                                self.model.grid.move_agent(self, new_pos)
                                self.consume_energy(self.energy_consumption["move"])
                                print(
                                    f"[{self.role.upper()} {self.unique_id}] UNBLOCK: Moving out of entrance/exit to {new_pos}"
                                )
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

        # Respect global tick limit (ticks are incremented per individual move)
        if not self.model.running or self.model.current_step >= self.model.max_steps:
            return

        # Execute all stages sequentially
        self.step_sense()
        self.step_communicate()
        self.step_decide()
        self.step_act()
