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


_AGENT_COLORS: Dict[str, str] = {
    "scout": "\033[1;32m",  # bold green
    "coordinator": "\033[1;34m",  # bold blue
    "retriever": "\033[1;33m",  # bold yellow
}
_RESET = "\033[0m"


_ROLE_SHORT = {"scout": "SCO", "coordinator": "COO", "retriever": "RET"}

# Mapping from unique_id → per-type 1-based index, populated at spawn time
_type_index_map: dict[int, int] = {}


def register_type_index(uid: int, idx: int) -> None:
    """Register the per-type 1-based index for an agent."""
    _type_index_map[uid] = idx


def agent_tag(role: str, uid: int) -> str:
    """Return a colored terminal label, e.g. \033[1;32m[SCO 1]\033[0m."""
    color = _AGENT_COLORS.get(role, "")
    short = _ROLE_SHORT.get(role, role.upper())
    idx = _type_index_map.get(uid, uid)
    reset = _RESET if color else ""
    return f"{color}[{short} {idx}]{reset}"


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
        # Cells actually seen via vision (for object-scan fog when map is pre-known)
        self.vision_explored = np.zeros((grid_height, grid_width), dtype=np.uint8)

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

        # Last-known positions of coordinators, learned via message relay
        # {coordinator_id: (x, y)} — re-broadcast so every agent is a relay
        self.coordinator_positions: Dict[int, Tuple[int, int]] = {}
        self.coordinator_positions_step: Dict[int, int] = {}

        # Last-known exploration targets of ALL agents, learned via message relay.
        # {agent_id: (x, y)} — used for global area division / deconfliction.
        self.peer_explore_targets: Dict[int, Tuple[int, int]] = {}
        self.peer_explore_targets_step: Dict[int, int] = {}
        _EXPLORE_TARGET_TTL: int = 40  # entries older than this are pruned
        self._explore_target_ttl = _EXPLORE_TARGET_TTL

        # Known warehouse locations
        self.known_warehouses: List[Tuple[int, int]] = []

        # Movement
        self.path: List[Tuple[int, int]] = []
        self.target_position: Optional[Tuple[int, int]] = None
        self.stuck_counter: int = 0  # Count consecutive failed moves
        self.last_position: Optional[Tuple[int, int]] = None
        self.wait_counter: int = 0  # Counter for waiting when path blocked by other agents
        self.position_history: List[Tuple[int, int]] = []  # Track recent positions to detect loops
        self.max_history_length: int = 25  # Keep last 25 positions
        self.unreachable_targets: dict[Tuple[int, int], int] = (
            {}
        )  # Track unreachable targets {position: step}
        # Distance-progress tracking: detect agents that move laterally
        # without ever getting closer to their target.
        self._progress_best_dist: int = 999999  # best Manhattan dist seen
        self._progress_steps: int = 0  # steps since last improvement
        _NO_PROGRESS_LIMIT: int = 12  # abandon after this many steps w/o progress
        self._no_progress_limit = _NO_PROGRESS_LIMIT
        # Cells yielded via ClearWay — avoid routing back for a few steps
        self._yield_cooldown: dict[Tuple[int, int], int] = {}

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
    def type_index(self) -> int:
        """1-based index within this agent's role type."""
        return _type_index_map.get(self.unique_id, self.unique_id + 1)

    @property
    def tag(self) -> str:
        """Colored terminal label with step, e.g. [SCO 1 @42]."""
        color = _AGENT_COLORS.get(self.role, "")
        short = _ROLE_SHORT.get(self.role, self.role.upper())
        reset = _RESET if color else ""
        step = self.model.current_step
        return f"{color}[{short} {self.type_index} @{step}]{reset}"

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
        _path_set = set(self.path) if self.path else set()
        _path_invalidated = False

        for x, y, cell_type in visible_cells:
            if 0 <= y < self.local_map.shape[0] and 0 <= x < self.local_map.shape[1]:
                old_type = int(self.local_map[y, x])
                self.local_map[y, x] = cell_type
                self.vision_explored[y, x] = 1

                # Path invalidation: if a cell that was UNKNOWN (or FREE)
                # turns out to be an OBSTACLE and our cached A* path goes
                # through it, clear the path immediately so the next
                # move_towards call triggers an instant replan.
                if (
                    not _path_invalidated
                    and cell_type == CellType.OBSTACLE
                    and old_type != CellType.OBSTACLE
                    and (x, y) in _path_set
                ):
                    self.path = []
                    _path_invalidated = True

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

                # Track discovered warehouses — when any warehouse-related
                # cell is spotted, flood-fill to reveal the entire connected
                # warehouse cluster (all blue/green/red cells).
                if (
                    cell_type
                    in (
                        CellType.WAREHOUSE,
                        CellType.WAREHOUSE_ENTRANCE,
                        CellType.WAREHOUSE_EXIT,
                    )
                    and (x, y) not in self.known_warehouses
                ):
                    wh_cells = self.model.grid.flood_fill_warehouse(x, y)
                    for wx, wy, wt in wh_cells:
                        if (wx, wy) not in self.known_warehouses:
                            self.known_warehouses.append((wx, wy))
                        if 0 <= wy < self.local_map.shape[0] and 0 <= wx < self.local_map.shape[1]:
                            self.local_map[wy, wx] = wt

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
            key=lambda wh: self.model._path_distance(pos_tuple, wh),
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

        # Extract explored cells from local map (raw topology).
        # Object tracking is handled exclusively via the known_objects dict
        # which carries per-object timestamps for proper tombstone semantics.
        explored_cells = MapSharingSystem.extract_explored_cells(self.local_map)
        cs = self.model.current_step

        # Create message — carry full knowledge with Stamped timestamps so every
        # recipient can apply "newest wins" and use this agent as a relay node.

        # Build explore_targets dict: own target + relayed peers (TTL-filtered).
        _et: Dict = {}
        _own_target = getattr(self, "_explore_target", None)
        if _own_target is not None:
            _et[self.unique_id or 0] = Stamped(tuple(_own_target), cs)
        for aid, pos in self.peer_explore_targets.items():
            step = self.peer_explore_targets_step.get(aid, 0)
            if cs - step <= self._explore_target_ttl:
                _et[aid] = Stamped(tuple(pos), step)

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
            coordinator_positions={
                cid: Stamped(tuple(p), self.coordinator_positions_step.get(cid, 0))
                for cid, p in self.coordinator_positions.items()
                if p
            },
            explore_targets=_et,
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

                # Resolve OBC references once — used by known_objects relay
                # to prevent re-adding in-flight positions.
                my_obc = getattr(self, "objects_being_collected", None)
                my_obc_step = getattr(self, "objects_being_collected_step", None)

                # --- explored_cells: topology only (warehouses) ---
                # Object tracking (add / remove / tombstone) is handled
                # exclusively by the known_objects dict which carries
                # per-object timestamps.  Using explored_cells for objects
                # is unreliable: the sender's local_map may be stale, and
                # the message-level timestamp would bypass tombstones.
                for x, y, cell_type in message.explored_cells:
                    if cell_type in _WH_CELL_TYPES:
                        pos = (x, y)
                        if pos not in self.known_warehouses:
                            self.known_warehouses.append(pos)

                # --- objects_being_collected: Stamped(value=None, step), newest wins ---
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

                # --- coordinator_positions relay: Stamped(value=(x,y), step), newest wins ---
                for cid, stamped in message.coordinator_positions.items():
                    pos_val = stamped.value if isinstance(stamped, Stamped) else stamped[0]
                    step = stamped.step if isinstance(stamped, Stamped) else stamped[1]
                    if step > self.coordinator_positions_step.get(cid, -1):
                        self.coordinator_positions[cid] = tuple(pos_val)
                        self.coordinator_positions_step[cid] = step

                # --- explore_targets relay: Stamped(value=(x,y), step), newest wins ---
                for aid, stamped in message.explore_targets.items():
                    if aid == (self.unique_id or 0):
                        continue  # skip own entry
                    pos_val = stamped.value if isinstance(stamped, Stamped) else stamped[0]
                    step = stamped.step if isinstance(stamped, Stamped) else stamped[1]
                    if step > self.peer_explore_targets_step.get(aid, -1):
                        self.peer_explore_targets[aid] = tuple(pos_val)
                        self.peer_explore_targets_step[aid] = step

            elif isinstance(message, ObjectLocationMessage):
                # Accept only if newer, not tombstoned, and not currently being collected
                pos = message.object_position
                _obc = getattr(self, "objects_being_collected", None)
                if (
                    (_obc is None or pos not in _obc)
                    and message.timestamp > self.known_objects_step.get(pos, -1)
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
        """Send a ClearWayMessage to a specific agent, throttled to once per 3 steps."""
        current_step = self.model.current_step
        if current_step - self._last_clearway_sent < 3:
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
            f"{self.tag} CLEARWAY: "
            f"asking agent {recipient_id} to vacate {cell} (depth={chain_depth})"
        )

    def _try_move_off_cell(
        self, avoid_warehouse: bool = False, away_from: Optional[Tuple[int, int]] = None
    ) -> bool:
        """
        Try to step off the current cell to any adjacent free non-obstacle cell.

        When ``avoid_warehouse`` is True (e.g. when standing on an entrance/exit),
        warehouse cells are also excluded so we don't immediately re-block a door.

        When ``away_from`` is provided, candidate cells are sorted so that cells
        farther from ``away_from`` are tried first.  This avoids oscillation by
        preferring the direction that keeps the agent out of the requester's path.

        Returns True if the agent managed to move.
        """
        if not self.pos:
            return False
        my_pos = pos_to_tuple(self.pos)
        _wh = (CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT)
        candidates: List[Tuple[int, Tuple[int, int]]] = []  # (sort_key, cell)
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]:
            np_ = (my_pos[0] + dx, my_pos[1] + dy)
            if not (0 <= np_[0] < self.model.grid.width and 0 <= np_[1] < self.model.grid.height):
                continue
            nc = self.model.grid.get_cell_type(*np_)
            if nc == CellType.OBSTACLE:
                continue
            # Prevent diagonal corner-cutting
            if dx != 0 and dy != 0:
                if not self.model.grid.is_walkable(
                    my_pos[0] + dx, my_pos[1]
                ) or not self.model.grid.is_walkable(my_pos[0], my_pos[1] + dy):
                    continue
            if avoid_warehouse and nc in _wh:
                continue
            if not self.model.grid.is_cell_empty(np_):
                continue
            if away_from is not None:
                # Higher distance from away_from → tried first (negate for sort)
                dist = abs(np_[0] - away_from[0]) + abs(np_[1] - away_from[1])
                candidates.append((-dist, np_))
            else:
                candidates.append((0, np_))
        candidates.sort()  # lowest key first → farthest from away_from first
        for _, np_ in candidates:
            self.model.grid.move_agent(self, np_)
            self.consume_energy(self.energy_consumption["move"])
            print(f"{self.tag} CLEARWAY: moved off {my_pos} → {np_}")
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

        Warehouse rules:
        - Agents NOT in a warehouse sequence never step onto any warehouse
          cell (entrance, exit, or interior).  The warehouse is not
          maneuvering space for agents that don't need it.
        - Agents inside a warehouse sequence can move within interior cells
          but still avoid door cells.
        - Escape direction is biased away from the sender so the receiver
          doesn't land right back in the requester's path.
        """
        if not self.pos:
            return
        my_pos = pos_to_tuple(self.pos)
        if my_pos != message.cell:
            return  # message delivered to wrong agent or we already moved

        cell_type = self.model.grid.get_cell_type(*my_pos)

        # Determine whether warehouse cells should be avoided.
        # An agent NOT currently in a warehouse navigation sequence must
        # NEVER step onto any warehouse cell — the warehouse is not a
        # shortcut or maneuvering space for external agents.
        in_wh_sequence = (
            getattr(self, "_wh_step", None) is not None
            or getattr(self, "_scout_wh_step", None) is not None
            or getattr(self, "_coord_wh_step", None) is not None
        )
        if in_wh_sequence:
            # Inside warehouse: avoid doors to not block entrance/exit,
            # but interior cells are OK.
            _DOOR = (CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT)
            avoid_wh = cell_type in _DOOR or any(
                self.model.grid.get_cell_type(my_pos[0] + dx, my_pos[1] + dy) in _DOOR
                for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]
                if 0 <= my_pos[0] + dx < self.model.grid.width
                and 0 <= my_pos[1] + dy < self.model.grid.height
            )
        else:
            # Outside warehouse: avoid ALL warehouse cells unconditionally.
            avoid_wh = True

        # Determine escape direction: move away from the sender so the
        # receiver doesn't land back in the requester's path.
        sender = next((a for a in self.model.agents if a.unique_id == message.sender_id), None)
        sender_pos = pos_to_tuple(sender.pos) if sender and sender.pos else None

        print(
            f"{self.tag} CLEARWAY: "
            f"received request to vacate {my_pos} (depth={message.chain_depth})"
        )

        if self._try_move_off_cell(avoid_warehouse=avoid_wh, away_from=sender_pos):
            self.path = []  # force A* replan so we don't route back
            # Cooldown: avoid routing back through yielded cell for 3 steps
            self._yield_cooldown[my_pos] = self.model.current_step + 3
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

        # Reset distance-progress tracking when the target changes
        if not hasattr(self, "_progress_last_target") or self._progress_last_target != target:
            self._progress_last_target = target
            self._progress_best_dist = abs(pos_tuple[0] - target[0]) + abs(pos_tuple[1] - target[1])
            self._progress_steps = 0

        # Determine whether warehouse doors may be used as transit nodes.
        #
        # Rules enforce strict door directionality:
        # - Entrance is ONLY for entering, exit is ONLY for exiting.
        # - Even an agent inside the warehouse must not path out through the
        #   entrance when heading to the exit (or vice versa).
        #
        #  1. Agent is INSIDE a warehouse cell and heading to EXIT:
        #     → forbid ENTRANCE as transit (prevents going backwards out the
        #       entrance and around the outside to re-enter at exit).
        #
        #  1b. Agent is INSIDE a warehouse cell heading to interior/entrance:
        #     → forbid EXIT as transit.
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
            # Case 1: inside warehouse — enforce strict door directionality
            if target_type == CellType.WAREHOUSE_EXIT:
                # Heading to exit → entrance is NOT a valid transit node
                forbidden_types = {CellType.WAREHOUSE_ENTRANCE}
            elif target_type in (CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE):
                # Heading to entrance/interior → exit is NOT a valid transit node
                forbidden_types = {CellType.WAREHOUSE_EXIT}
            else:
                # Heading outside from inside → allow exit as the way out
                forbidden_types = {CellType.WAREHOUSE_ENTRANCE}
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

        # Add yield-cooldown cells so A* penalises recently-yielded positions
        cs = self.model.current_step
        expired = [p for p, exp in self._yield_cooldown.items() if cs >= exp]
        for p in expired:
            del self._yield_cooldown[p]
        other_agent_positions |= set(self._yield_cooldown.keys())

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
            if not self.path or self.stuck_counter > 3:
                # Unified fog-of-war pathfinding:
                # A* uses the agent's local_map in BOTH modes.  UNKNOWN
                # cells (value 0) are treated as walkable so paths can
                # pass through unexplored territory.  In map_known mode
                # the local_map has obstacles pre-filled, so A* avoids
                # walls from the start (like knowing your city's streets).
                # In map_unknown the agent discovers walls at runtime.
                new_path = self.pathfinder.find_path(
                    pos_tuple,
                    target,
                    other_agent_positions,
                    forbidden_types=forbidden_types,
                    agent_local_map=self.local_map,
                )

                # If no path found, target is unreachable
                if new_path is None:
                    print(f"{self.tag} PATH: No path found to {target}, target unreachable")
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
                        blocking_agent = None
                        for agent in self.model.agents:
                            if agent.pos and pos_to_tuple(agent.pos) == next_pos:
                                blocking_agent = agent
                                break

                        # NOTE: stuck_counter is incremented at the bottom of
                        # move_towards() when moved==False, so we must NOT
                        # increment it here again (that would double-count).

                        # ── Head-on corridor swap ────────────────────────
                        # If the blocker is heading toward us, swap positions
                        # immediately to resolve the deadlock without
                        # oscillation.
                        if blocking_agent is not None and isinstance(blocking_agent, BaseAgent):
                            if self._try_corridor_swap(pos_tuple, blocking_agent):
                                moved = True

                        # ── Cooperative negotiation ──────────────────────
                        # Gather all agents in the jam cluster and negotiate
                        # non-conflicting moves based on priority (more cargo
                        # first, warehouse-bound second, lower ID last).
                        if not moved and self.stuck_counter >= 1:
                            if self._cooperative_unstick(pos_tuple, target):
                                moved = True

                        # ── Fallback: ClearWay + individual sidestep ─────
                        if not moved:
                            if blocking_agent is not None and self.stuck_counter >= 1:
                                self._send_clear_way_request(next_pos, blocking_agent.unique_id)

                            if self.stuck_counter >= 2:
                                side = self._try_sidestep(
                                    pos_tuple,
                                    next_pos,
                                    target,
                                    other_agent_positions,
                                )
                                if side is not None:
                                    self.model.grid.move_agent(self, side)
                                    self.consume_energy(self.energy_consumption["move"])
                                    self.path = []  # replan from new position
                                    # Sidestep is lateral, NOT forward progress.
                                    # Don't set moved=True so stuck_counter keeps
                                    # incrementing, preventing infinite sidestep loops.
                                    # But do record position for loop detection.
                                    current_pos = pos_to_tuple(self.pos) if self.pos else pos_tuple
                                    self.position_history.append(current_pos)
                                    if len(self.position_history) > self.max_history_length:
                                        self.position_history.pop(0)

                        if not moved:
                            if self.stuck_counter <= 2:
                                # Brief wait — give the blocker 1 step to move
                                self.wait_counter = 1
                            elif self.stuck_counter <= 6:
                                # Force replan around current obstacles
                                self.path = []
                                self.wait_counter = 1
                            else:
                                # Too long stuck - give up on this target
                                print(
                                    f"{self.tag} STUCK: Too long stuck at {pos_tuple}, "
                                    f"abandoning target {target}"
                                )
                                self.target_position = None
                                self.path = []
                                self.stuck_counter = 0
                else:
                    # Path is permanently blocked (obstacle), replan
                    self.path = []
                    # stuck_counter incremented at the bottom when moved==False
        else:
            # Fallback: Simple greedy movement if no pathfinder
            current_x, current_y = pos_tuple
            target_x, target_y = target

            # Calculate direction
            dx = 0 if target_x == current_x else (1 if target_x > current_x else -1)
            dy = 0 if target_y == current_y else (1 if target_y > current_y else -1)

            # Try diagonal move first (only if not corner-cutting)
            if dx != 0 and dy != 0:
                new_pos = (current_x + dx, current_y + dy)
                if (
                    self.model.grid.is_walkable(*new_pos)
                    and self.model.grid.is_walkable(current_x + dx, current_y)
                    and self.model.grid.is_walkable(current_x, current_y + dy)
                    and self._check_collision(new_pos)
                ):
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

            # ── Distance-progress check ──
            # Even when moving (including sidesteps recorded above), verify
            # the agent is actually getting closer to its target.  If not,
            # abandon after _no_progress_limit steps.
            if self.target_position is not None:
                cur_dist = abs(current_pos[0] - self.target_position[0]) + abs(
                    current_pos[1] - self.target_position[1]
                )
                if cur_dist < self._progress_best_dist:
                    self._progress_best_dist = cur_dist
                    self._progress_steps = 0
                else:
                    self._progress_steps += 1
                if self._progress_steps >= self._no_progress_limit:
                    print(
                        f"{self.tag} NO-PROGRESS: no distance improvement in "
                        f"{self._progress_steps} steps at {current_pos}, "
                        f"abandoning target {self.target_position}"
                    )
                    if self.target_position is not None:
                        self.unreachable_targets[self.target_position] = self.model.current_step
                    self.target_position = None
                    self.path = []
                    self.stuck_counter = 0
                    self._progress_best_dist = 999999
                    self._progress_steps = 0
                    return moved

            # Detect loop: if oscillating between few positions
            if len(self.position_history) >= 10:
                recent_positions = set(self.position_history[-10:])
                if len(recent_positions) <= 5:
                    # Oscillating between few positions - clear target
                    print(
                        f"{self.tag} LOOP: Detected position loop (oscillating between {len(recent_positions)} positions), abandoning target {self.target_position}"
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
            # Track distance progress even when not moving
            if self.target_position is not None and self.pos:
                cp = pos_to_tuple(self.pos)
                cur_dist = abs(cp[0] - self.target_position[0]) + abs(
                    cp[1] - self.target_position[1]
                )
                if cur_dist < self._progress_best_dist:
                    self._progress_best_dist = cur_dist
                    self._progress_steps = 0
                else:
                    self._progress_steps += 1
                if self._progress_steps >= self._no_progress_limit:
                    print(
                        f"{self.tag} NO-PROGRESS: no distance improvement in "
                        f"{self._progress_steps} steps at {cp}, "
                        f"abandoning target {self.target_position}"
                    )
                    self.unreachable_targets[self.target_position] = self.model.current_step
                    self.target_position = None
                    self.path = []
                    self.stuck_counter = 0
                    self._progress_best_dist = 999999
                    self._progress_steps = 0
                    return moved
            # If stuck for too long, clear target and path to find alternative
            if self.stuck_counter > 20:
                print(
                    f"{self.tag} STUCK: Stuck for {self.stuck_counter} steps at {pos_to_tuple(self.pos)}, abandoning target {self.target_position}"
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

    def _try_sidestep(
        self,
        current: Tuple[int, int],
        blocked: Tuple[int, int],
        target: Tuple[int, int],
        occupied: set,
    ) -> Optional[Tuple[int, int]]:
        """Try to step to an adjacent cell to route around a blocker.

        Picks the neighbour that is walkable, unoccupied, not the blocked cell,
        and closest (Manhattan) to the target.  When the agent is outside the
        warehouse, ALL warehouse cells (entrance, exit, and interior) are avoided
        so the warehouse is never used as maneuvering space.
        """
        _WH_TYPES = {CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT}
        my_type = self.model.grid.get_cell_type(*current)
        # Agents outside the warehouse avoid ALL warehouse cells, not just doors.
        avoid_wh = my_type not in _WH_TYPES

        candidates: List[Tuple[Tuple[int, int], int]] = []
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]:
            np_ = (current[0] + dx, current[1] + dy)
            if np_ == blocked:
                continue
            if not (0 <= np_[0] < self.model.grid.width and 0 <= np_[1] < self.model.grid.height):
                continue
            if not self.model.grid.is_walkable(*np_):
                continue
            # Prevent diagonal corner-cutting
            if dx != 0 and dy != 0:
                if not self.model.grid.is_walkable(
                    current[0] + dx, current[1]
                ) or not self.model.grid.is_walkable(current[0], current[1] + dy):
                    continue
            if np_ in occupied:
                continue
            ct = self.model.grid.get_cell_type(*np_)
            if avoid_wh and ct in _WH_TYPES:
                continue
            dist = abs(target[0] - np_[0]) + abs(target[1] - np_[1])
            candidates.append((np_, dist))

        if not candidates:
            return None
        candidates.sort(key=lambda c: c[1])
        return candidates[0][0]

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

    # ------------------------------------------------------------------
    # Cooperative jam resolution
    # ------------------------------------------------------------------

    def _is_valid_cell_for_negotiation(self, agent: "BaseAgent", cell: Tuple[int, int]) -> bool:
        """Check if *cell* is a valid negotiation target for *agent*.

        Respects warehouse rules:
        - Agent NOT in a warehouse sequence: never step onto any WH cell.
        - Agent IN a warehouse sequence: interior cells OK; door cells only
          if they are the agent's current target (so an exit-phase agent can
          step onto the exit, but not onto the entrance).
        """
        x, y = cell
        if not (0 <= x < self.model.grid.width and 0 <= y < self.model.grid.height):
            return False
        if not self.model.grid.is_walkable(x, y):
            return False

        ct = self.model.grid.get_cell_type(x, y)
        in_wh = (
            getattr(agent, "_wh_step", None) is not None
            or getattr(agent, "_scout_wh_step", None) is not None
            or getattr(agent, "_coord_wh_step", None) is not None
        )

        _WH = (CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT)
        _DOOR = (CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT)

        if not in_wh:
            # Outside warehouse: never step onto any warehouse cell
            if ct in _WH:
                return False
        else:
            # Inside warehouse: doors only if they are the target
            agent_target = getattr(agent, "target_position", None)
            if ct in _DOOR and cell != agent_target:
                return False
        return True

    # ── Corridor swap ─────────────────────────────────────────────────────

    def _try_corridor_swap(
        self,
        my_pos: Tuple[int, int],
        blocking_agent: "BaseAgent",
    ) -> bool:
        """Swap positions with *blocking_agent* when both agents would
        benefit from trading places.

        A swap is performed when ANY of these conditions hold:
        1. **Head-on**: blocker's desired direction points back toward us.
        2. **Mutual benefit**: swapping moves *both* agents closer to their
           respective targets (or at least one closer and the other no worse).

        Additional guards:
        - Both resulting positions satisfy warehouse negotiation rules.
        - The move does not cut through obstacle corners (diagonal case).

        Returns True if the swap was executed.
        """
        if blocking_agent is None or blocking_agent.pos is None:
            return False

        b_pos = pos_to_tuple(blocking_agent.pos)

        # ── Determine blocker's desired cell ─────────────────────────────
        b_desired = None
        if hasattr(blocking_agent, "path") and blocking_agent.path:
            b_desired = blocking_agent.path[0]
        elif hasattr(blocking_agent, "target_position") and blocking_agent.target_position:
            t = blocking_agent.target_position
            dx = 0 if t[0] == b_pos[0] else (1 if t[0] > b_pos[0] else -1)
            dy = 0 if t[1] == b_pos[1] else (1 if t[1] > b_pos[1] else -1)
            b_desired = (b_pos[0] + dx, b_pos[1] + dy)

        head_on = b_desired == my_pos

        # ── Mutual-benefit check (fallback when not head-on) ─────────────
        if not head_on:
            my_target = getattr(self, "target_position", None)
            b_target = getattr(blocking_agent, "target_position", None)
            if my_target is None or b_target is None:
                return False
            # Current distances
            my_dist_now = abs(my_pos[0] - my_target[0]) + abs(my_pos[1] - my_target[1])
            b_dist_now = abs(b_pos[0] - b_target[0]) + abs(b_pos[1] - b_target[1])
            # After-swap distances
            my_dist_after = abs(b_pos[0] - my_target[0]) + abs(b_pos[1] - my_target[1])
            b_dist_after = abs(my_pos[0] - b_target[0]) + abs(my_pos[1] - b_target[1])
            # Accept if total distance decreases (at least one improves, none worsens)
            if (my_dist_after + b_dist_after) >= (my_dist_now + b_dist_now):
                return False

        # ── Diagonal corner-cutting guard ────────────────────────────────
        dx = b_pos[0] - my_pos[0]
        dy = b_pos[1] - my_pos[1]
        if abs(dx) == 1 and abs(dy) == 1:
            if not self.model.grid.is_walkable(
                my_pos[0] + dx, my_pos[1]
            ) or not self.model.grid.is_walkable(my_pos[0], my_pos[1] + dy):
                return False

        # ── Warehouse-rule validation ────────────────────────────────────
        if not self._is_valid_cell_for_negotiation(self, b_pos):
            return False
        if not self._is_valid_cell_for_negotiation(blocking_agent, my_pos):
            return False

        # ── Execute atomic swap ──────────────────────────────────────────
        self.model.grid.swap_agents(self, blocking_agent)

        self.path = []
        self.stuck_counter = 0
        self.wait_counter = 0
        blocking_agent.path = []
        blocking_agent.stuck_counter = 0
        blocking_agent.wait_counter = 0

        self.consume_energy(self.energy_consumption["move"])
        blocking_agent.consume_energy(blocking_agent.energy_consumption["move"])

        print(f"{self.tag} SWAP: {my_pos} <-> {b_pos} with {blocking_agent.tag}")
        return True

    def _cooperative_unstick(self, my_pos: Tuple[int, int], target: Tuple[int, int]) -> bool:
        """Cooperative jam resolution for 2+ agents stuck near each other.

        Gathers all agents within Manhattan distance ≤ 2, sorts them by
        priority, then lets each agent — highest priority first — pick the
        best *currently-free* adjacent cell that moves it closer to its own
        target without conflicting with cells already reserved by
        higher-priority agents.  All moves execute immediately.

        Priority (highest → lowest):
          1. More ``carrying_objects``  (heavier loads delivered first)
          2. In warehouse sequence      (deliver/recharge/exit in progress)
          3. Lower ``unique_id``        (deterministic tie-breaker)

        Returns True if *this* agent moved as a result of the negotiation.
        """
        # ── 1. Gather the jam cluster ────────────────────────────────────
        cluster: List["BaseAgent"] = []
        for agent in self.model.agents:
            if not isinstance(agent, BaseAgent):
                continue
            if agent.pos is None or agent.energy <= 0:
                continue
            ap = pos_to_tuple(agent.pos)
            dist = abs(ap[0] - my_pos[0]) + abs(ap[1] - my_pos[1])
            if dist <= 2:
                cluster.append(agent)

        if len(cluster) < 2:
            return False

        # ── 2. Sort by priority ──────────────────────────────────────────
        cluster.sort(
            key=lambda a: (
                -getattr(a, "carrying_objects", 0),
                -(1 if getattr(a, "_wh_step", None) is not None else 0),
                a.unique_id,
            )
        )

        # ── 3. Detect and resolve swappable pairs ─────────────────────────
        # Swap when either (a) exact head-on (a wants b's cell AND vice
        # versa), or (b) mutual benefit (both get closer to their respective
        # targets after swapping).
        swapped: set = set()  # agent unique_ids already swapped this round
        moved_self = False
        desires: dict = {}
        for agent in cluster:
            ap = pos_to_tuple(agent.pos)
            if hasattr(agent, "path") and agent.path:
                desires[agent.unique_id] = agent.path[0]
            elif hasattr(agent, "target_position") and agent.target_position:
                t = agent.target_position
                ddx = 0 if t[0] == ap[0] else (1 if t[0] > ap[0] else -1)
                ddy = 0 if t[1] == ap[1] else (1 if t[1] > ap[1] else -1)
                desires[agent.unique_id] = (ap[0] + ddx, ap[1] + ddy)

        for i, a in enumerate(cluster):
            if a.unique_id in swapped:
                continue
            a_pos = pos_to_tuple(a.pos)
            a_want = desires.get(a.unique_id)
            a_target = getattr(a, "target_position", None)
            for j in range(i + 1, len(cluster)):
                b = cluster[j]
                if b.unique_id in swapped:
                    continue
                b_pos = pos_to_tuple(b.pos)
                # Only consider adjacent agents (incl. diagonal ≤ Manhattan 2)
                mdist = abs(a_pos[0] - b_pos[0]) + abs(a_pos[1] - b_pos[1])
                if mdist > 2 or mdist == 0:
                    continue
                b_want = desires.get(b.unique_id)
                b_target = getattr(b, "target_position", None)

                # Condition (a): exact head-on
                head_on = (
                    a_want is not None
                    and b_want is not None
                    and a_want == b_pos
                    and b_want == a_pos
                )

                # Condition (b): mutual benefit — swap reduces total distance
                mutual_benefit = False
                if not head_on and a_target is not None and b_target is not None:
                    a_d_now = abs(a_pos[0] - a_target[0]) + abs(a_pos[1] - a_target[1])
                    b_d_now = abs(b_pos[0] - b_target[0]) + abs(b_pos[1] - b_target[1])
                    a_d_aft = abs(b_pos[0] - a_target[0]) + abs(b_pos[1] - a_target[1])
                    b_d_aft = abs(a_pos[0] - b_target[0]) + abs(a_pos[1] - b_target[1])
                    if (a_d_aft + b_d_aft) < (a_d_now + b_d_now):
                        mutual_benefit = True

                if not head_on and not mutual_benefit:
                    continue

                # Validate swap
                ddx = b_pos[0] - a_pos[0]
                ddy = b_pos[1] - a_pos[1]
                corner_ok = True
                if abs(ddx) == 1 and abs(ddy) == 1:
                    if not self.model.grid.is_walkable(
                        a_pos[0] + ddx, a_pos[1]
                    ) or not self.model.grid.is_walkable(a_pos[0], a_pos[1] + ddy):
                        corner_ok = False
                if (
                    corner_ok
                    and self._is_valid_cell_for_negotiation(a, b_pos)
                    and self._is_valid_cell_for_negotiation(b, a_pos)
                ):
                    self.model.grid.swap_agents(a, b)
                    a.path = []
                    a.stuck_counter = 0
                    a.wait_counter = 0
                    b.path = []
                    b.stuck_counter = 0
                    b.wait_counter = 0
                    a.consume_energy(a.energy_consumption["move"])
                    b.consume_energy(b.energy_consumption["move"])
                    swapped.add(a.unique_id)
                    swapped.add(b.unique_id)
                    print(f"{a.tag} NEGOTIATE-SWAP: {a_pos} <-> {b_pos} with {b.tag}")
                    if a.unique_id == self.unique_id or b.unique_id == self.unique_id:
                        moved_self = True
                    break

        # If this agent was already swapped, we're done
        if self.unique_id in swapped:
            return moved_self

        # Remove swapped agents from cluster for free-cell planning
        cluster = [a for a in cluster if a.unique_id not in swapped]
        if len(cluster) < 2:
            return moved_self

        # ── 4. Plan non-conflicting moves ────────────────────────────────
        reserved: set = set()  # cells claimed for next step
        plan: List[Tuple["BaseAgent", Tuple[int, int], Tuple[int, int]]] = []

        for agent in cluster:
            ap = pos_to_tuple(agent.pos)
            agent_target = getattr(agent, "target_position", None)

            # Desired next cell from cached path
            desired = None
            if hasattr(agent, "path") and agent.path:
                desired = agent.path[0]

            chosen = None

            # Try desired cell if it's currently free and not reserved
            if (
                desired is not None
                and desired not in reserved
                and self.model.grid.is_cell_empty(desired)
                and self._is_valid_cell_for_negotiation(agent, desired)
            ):
                chosen = desired

            # Otherwise pick the best free adjacent cell toward own target
            if chosen is None:
                best_score = float("inf")
                best_cell = None
                for dx, dy in [
                    (1, 0),
                    (-1, 0),
                    (0, 1),
                    (0, -1),
                    (1, 1),
                    (1, -1),
                    (-1, 1),
                    (-1, -1),
                ]:
                    np_ = (ap[0] + dx, ap[1] + dy)
                    if np_ in reserved:
                        continue
                    if not self.model.grid.is_cell_empty(np_):
                        continue
                    # Prevent diagonal corner-cutting
                    if dx != 0 and dy != 0:
                        if not self.model.grid.is_walkable(
                            ap[0] + dx, ap[1]
                        ) or not self.model.grid.is_walkable(ap[0], ap[1] + dy):
                            continue
                    if not self._is_valid_cell_for_negotiation(agent, np_):
                        continue
                    if agent_target:
                        score = abs(np_[0] - agent_target[0]) + abs(np_[1] - agent_target[1])
                    else:
                        score = 0
                    if score < best_score:
                        best_score = score
                        best_cell = np_
                chosen = best_cell

            if chosen is not None and chosen != ap:
                reserved.add(chosen)
                plan.append((agent, ap, chosen))
            else:
                # Agent stays put — reserve current position
                reserved.add(ap)

        if not plan:
            return False

        # ── 5. Execute (all targets are currently-free → no deps) ────────
        moved_self = False
        for agent, from_pos, to_pos in plan:
            if not self.model.grid.is_cell_empty(to_pos):
                continue  # safety: cell already taken by earlier move
            self.model.grid.move_agent(agent, to_pos)
            agent.consume_energy(agent.energy_consumption["move"])
            agent.path = []  # force recompute from new position
            agent.stuck_counter = 0
            agent.wait_counter = 0
            if agent.unique_id == self.unique_id:
                moved_self = True
            print(f"{agent.tag} NEGOTIATE: {from_pos} → {to_pos}")

        return moved_self

    def step_sense(self) -> None:
        """Stage 0: Perceive environment (everyone always does this)"""
        # Base energy consumption (reduced to avoid draining when stuck)
        self.consume_energy(self.energy_consumption["base"] * 0.1)

        # Perceive visible cells
        visible = self.perceive_environment()
        self.update_local_map(visible)

        # Track coordinator positions from direct vision so every agent
        # can relay them via MapDataMessage.
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        cs = self.model.current_step
        # If this agent IS a coordinator, register own position first
        if getattr(self, "role", None) == "coordinator":
            self.coordinator_positions[self.unique_id] = my_pos
            self.coordinator_positions_step[self.unique_id] = cs
        for agent in self.model.agents:
            if getattr(agent, "role", None) == "coordinator" and agent.pos:
                c_pos = pos_to_tuple(agent.pos)
                dist = abs(c_pos[0] - my_pos[0]) + abs(c_pos[1] - my_pos[1])
                if dist <= self.vision_radius:
                    cid = agent.unique_id
                    self.coordinator_positions[cid] = c_pos
                    self.coordinator_positions_step[cid] = cs

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
                                    f"{self.tag} UNBLOCK: Moving out of entrance/exit to {new_pos}"
                                )
                                return

        # Subclasses override this to implement specific behavior
        pass

    def step(self) -> None:
        """
        Execute one step of the agent's behavior

        Cycle: SENSE -> COMMUNICATE (receive) -> DECIDE -> ACT (move OR send)

        When speed > 1 the agent performs multiple move sub-steps per tick.
        Sense/Communicate/Decide run once; Act runs int(speed) times so
        faster agents cover more ground per simulation step.
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

        # Agents with speed > 1 get additional act (move) sub-steps.
        # Sense/Communicate/Decide are NOT repeated — only movement benefits.
        moves = max(1, int(self.speed))
        for _ in range(moves):
            if self.energy <= 0:
                break
            self.step_act()
