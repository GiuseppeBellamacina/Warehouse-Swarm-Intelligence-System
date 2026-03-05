"""
Scout Agent - Fast explorer with wide vision
"""

import random
from typing import TYPE_CHECKING, List, Optional, Tuple

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
from backend.algorithms.exploration import FrontierExplorer, RandomWalkExplorer
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import ObjectLocationMessage
from backend.core.decision_maker import ActionType, UtilityFunctions
from backend.core.grid_manager import CellType

if TYPE_CHECKING:
    from backend.core.warehouse_model import WarehouseModel


class ScoutAgent(BaseAgent):
    """
    Scout agent specialized in rapid exploration

    Characteristics:
    - High speed (1.5x)
    - Wide vision radius
    - Focuses on frontier exploration
    - Shares discovered objects with coordinators
    """

    def __init__(
        self,
        unique_id: int,
        model: "WarehouseModel",
        vision_radius: int = 3,
        communication_radius: int = 2,
        max_energy: float = 500.0,
        speed: float = 1.5,
    ):
        super().__init__(
            unique_id=unique_id,
            model=model,
            role="scout",
            vision_radius=vision_radius,
            communication_radius=communication_radius,
            max_energy=max_energy,
            speed=speed,
            energy_consumption={
                "base": 0.0,
                "move": 1.0,
                "communicate": 0.0,
            },
        )

        self.state = AgentState.EXPLORING
        self.pathfinder = AStarPathfinder(model.grid)
        self.previous_direction: Optional[Tuple[int, int]] = None

        # Track discovered objects to communicate
        self.newly_discovered_objects: List[Tuple[Tuple[int, int], float]] = []
        self._discovery_age: int = 0  # steps without a coordinator to receive discoveries

        # Track repeated failures on same target
        self.last_failed_target: Optional[Tuple[int, int]] = None
        self.consecutive_failures_on_target = 0

        # Last known coordinator position — used to seek the coordinator when
        # discoveries are pending but no coordinator is currently in comms range.
        self.last_seen_coordinator_pos: Optional[Tuple[int, int]] = None

        # Warehouse recharge sub-state machine (avoids getting stuck on entrance/exit)
        # Values: None | "approach" | "recharge" | "exit"
        self._scout_wh_step: Optional[str] = None
        self._scout_wh_station: Optional[dict] = None

        # Setup decision making
        self._setup_decision_maker()

    def _setup_decision_maker(self) -> None:
        """Setup utility functions for decision making"""
        self.decision_maker.register_utility_function(
            ActionType.EXPLORE, UtilityFunctions.explore_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.RECHARGE, UtilityFunctions.recharge_utility
        )

    def step_sense(self) -> None:
        """Perceive environment and track newly discovered objects"""
        # Store old known objects
        old_objects = set(self.known_objects.keys())

        # Call parent sense
        super().step_sense()

        # Check for newly discovered objects
        new_objects = set(self.known_objects.keys())
        discovered = new_objects - old_objects

        if discovered:
            print(
                f"[SCOUT {self.unique_id}] SENSE: Discovered {len(discovered)} new objects at positions: {list(discovered)}"
            )
            # Add to list of objects to communicate
            for obj_pos in discovered:
                obj_value = self.known_objects.get(obj_pos, 1.0)
                self.newly_discovered_objects.append((obj_pos, obj_value))

        # Update last known coordinator position whenever one is within vision
        # (use vision_radius so we track coordinator movements sooner than comm range)
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        for agent in self.model.agents:
            if getattr(agent, "role", None) == "coordinator" and agent.pos:
                c_pos = pos_to_tuple(agent.pos)
                dist = abs(c_pos[0] - my_pos[0]) + abs(c_pos[1] - my_pos[1])
                if dist <= self.vision_radius:
                    self.last_seen_coordinator_pos = c_pos
                    break

    def step_decide(self) -> None:
        """Decide next action based on utility"""
        # Reset communication flag
        self.should_communicate_this_step = False

        # If the recharge sub-machine is already running, let step_act handle it
        if self._scout_wh_step is not None:
            return

        # ---- Priority 1: Communicate discoveries to any nearby coordinator ----
        if self.newly_discovered_objects:
            # _discovery_age counts steps WITHOUT a known coordinator destination.
            # While we are actively heading somewhere don't count — reset instead.
            if self.last_seen_coordinator_pos:
                self._discovery_age = 0  # we have a destination; no timeout
            else:
                self._discovery_age += 1
            nearby = self.get_nearby_agents(self.communication_radius)
            coordinators = [a for a in nearby if getattr(a, "role", None) == "coordinator"]
            if coordinators:
                self.should_communicate_this_step = True
                return
            # No coordinator in range — head toward the last known coordinator position
            # so the scout can hand off its discoveries without just waiting idly.
            if self.last_seen_coordinator_pos:
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                dist_to_saved = abs(self.last_seen_coordinator_pos[0] - my_pos[0]) + abs(
                    self.last_seen_coordinator_pos[1] - my_pos[1]
                )
                # Arrived at last known position but coordinator not here → it moved;
                # do a wide scan first to find the coordinator's new position before
                # giving up and falling through to exploration.
                if dist_to_saved <= max(1, self.communication_radius):
                    self.last_seen_coordinator_pos = None
                    # Wide scan: check all coordinators within 3× comm radius
                    new_coord_pos = self._wide_scan_for_coordinator()
                    if new_coord_pos:
                        self.last_seen_coordinator_pos = new_coord_pos
                        print(
                            f"[SCOUT {self.unique_id}] SEEK-COORD: old pos stale, "
                            f"found coordinator at {new_coord_pos} via wide scan"
                        )
                    else:
                        print(
                            f"[SCOUT {self.unique_id}] SEEK-COORD: reached stale pos, "
                            f"coordinator not found nearby — resuming exploration"
                        )
                    # Fall through (if new_coord_pos set, next step will use it)
                else:
                    if self.target_position != self.last_seen_coordinator_pos:
                        self.target_position = self.last_seen_coordinator_pos
                        self.path = []
                    self.state = AgentState.MOVING_TO_TARGET
                    print(
                        f"[SCOUT {self.unique_id}] SEEK-COORD: heading to last known "
                        f"coordinator pos {self.last_seen_coordinator_pos} "
                        f"({len(self.newly_discovered_objects)} discoveries pending)"
                    )
                    return
            # No known coordinator position — keep discoveries, but discard after 80 steps
            # of being truly unable to locate any coordinator.
            if self._discovery_age > 80:
                print(
                    f"[SCOUT {self.unique_id}] TIMEOUT: discarding "
                    f"{len(self.newly_discovered_objects)} stale discoveries"
                )
                self.newly_discovered_objects = []
                self._discovery_age = 0
        else:
            self._discovery_age = 0

        # ---- Priority 2: Recharge if critically low (< 25 %) ----
        if self.energy < self.max_energy * 0.25:
            if self._scout_wh_step is None:
                # Start the warehouse recharge sub-state machine
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                visible_entrances = [
                    wh
                    for wh in self.known_warehouses
                    if self.model.grid.get_cell_type(*wh) == CellType.WAREHOUSE_ENTRANCE
                ]
                station = self.model.get_best_warehouse_for(
                    pos=my_pos,
                    known_entrances=visible_entrances,
                    agent_energy=self.energy,
                )
                self._scout_wh_station = station
                self._scout_wh_step = "approach"
                self.state = AgentState.RECHARGING
                self.target_position = station.get("entrance")
                print(
                    f"[SCOUT {self.unique_id}] LOW-E ({self.energy:.1f}), "
                    f"heading to WH entrance {self.target_position}"
                )
            # Sub-machine runs in step_act — just return here
            return

        # ---- Priority 3: Frontier-based exploration ----
        self.state = AgentState.EXPLORING
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        current_step = self.model.current_step

        frontiers = FrontierExplorer.find_frontiers(self.local_map)

        # Filter blacklisted / stale frontiers
        valid_frontiers = []
        for frontier_pos, cluster_size in frontiers:
            if frontier_pos in self.unreachable_targets:
                failed_step = self.unreachable_targets[frontier_pos]
                if current_step - failed_step < 30:
                    continue
                del self.unreachable_targets[frontier_pos]
            valid_frontiers.append((frontier_pos, cluster_size))

        # Anti-clustering: prefer frontiers far from other scouts
        nearby = self.get_nearby_agents(self.communication_radius)
        scout_positions = [
            pos_to_tuple(a.pos)
            for a in nearby
            if getattr(a, "role", None) == "scout" and a.pos and pos_to_tuple(a.pos) != my_pos
        ]
        anti_clustered = [
            f
            for f in valid_frontiers
            if all(abs(f[0][0] - sp[0]) + abs(f[0][1] - sp[1]) >= 8 for sp in scout_positions)
        ]
        frontiers_to_use = anti_clustered if anti_clustered else valid_frontiers

        if frontiers_to_use:
            nearby_positions = [pos_to_tuple(a.pos) for a in nearby if a.pos]
            best = FrontierExplorer.select_best_frontier(frontiers_to_use, my_pos, nearby_positions)
            if best:
                if (
                    not self.target_position
                    or abs(best[0] - self.target_position[0]) > 5
                    or abs(best[1] - self.target_position[1]) > 5
                ):
                    self.target_position = best
                    self.path = []
                    self.consecutive_failures_on_target = 0
                self.state = AgentState.MOVING_TO_TARGET
                return

        # ---- Fallback: navigate towards a random unknown boundary cell ----
        self._pick_unexplored_target(my_pos)

    def _wide_scan_for_coordinator(self) -> Optional[Tuple[int, int]]:
        """
        Scan all agents within 3× communication_radius to find the nearest coordinator.
        Used after the saved coordinator position turns out to be stale, so the scout
        can update its heading without falling back to blind exploration.
        Returns the nearest coordinator position found, or None.
        """
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        # Use a generous radius (quarter of grid diagonal) so the scout can detect
        # coordinators that drifted away from the last-seen position.
        search_radius = max(12, self.communication_radius * 4)
        best_pos: Optional[Tuple[int, int]] = None
        best_dist = float("inf")
        for agent in self.model.agents:
            if getattr(agent, "role", None) == "coordinator" and agent.pos:
                c_pos = pos_to_tuple(agent.pos)
                dist = abs(c_pos[0] - my_pos[0]) + abs(c_pos[1] - my_pos[1])
                if dist <= search_radius and dist < best_dist:
                    best_dist = dist
                    best_pos = c_pos
        return best_pos

    def _pick_unexplored_target(self, my_pos: Tuple[int, int]) -> None:
        """Navigate towards a random UNKNOWN boundary cell via A* when no frontier found."""
        height, width = self.local_map.shape
        unknown_boundary: List[Tuple[int, int]] = []

        for y in range(height):
            for x in range(width):
                if self.local_map[y, x] != 0:
                    continue  # already explored
                # Must be adjacent to an explored cell
                for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height and self.local_map[ny, nx] != 0:
                        unknown_boundary.append((x, y))
                        break

        random.shuffle(unknown_boundary)

        for candidate in unknown_boundary[:20]:
            if candidate in self.unreachable_targets:
                continue
            if not self.model.grid.is_walkable(*candidate):
                continue
            self.target_position = candidate
            self.path = []
            self.state = AgentState.MOVING_TO_TARGET
            return

        # Absolute fallback: random walk handled in step_act
        self.target_position = None
        self.state = AgentState.EXPLORING

    def step_act(self) -> None:
        """Execute decided action: COMMUNICATE or MOVE (not both)"""
        if self.energy <= 0:
            return

        # OPTION 1: Communicate newly discovered objects to coordinators
        if self.should_communicate_this_step and self.newly_discovered_objects:
            self._broadcast_discovered_objects()
            self.newly_discovered_objects = []
            self._discovery_age = 0
            return  # Don't move this step

        # OPTION 2: Warehouse recharge sub-state machine
        if self._scout_wh_step is not None:
            self._execute_scout_recharge_step()
            return

        # OPTION 3: Move based on state (scouts move 2× per step when speed > 1)
        moves_per_step = 2 if self.speed > 1.0 else 1

        for _ in range(moves_per_step):
            if self.energy <= 0:
                break

            if self.state == AgentState.MOVING_TO_TARGET and self.target_position:
                moved = self.move_towards(self.target_position)

                my_pos = pos_to_tuple(self.pos) if self.pos else None
                if my_pos and my_pos == self.target_position:
                    self.target_position = None
                    self.state = AgentState.EXPLORING
                    self.consecutive_failures_on_target = 0
                    break

                if not moved:
                    self.consecutive_failures_on_target += 1
                    if self.consecutive_failures_on_target >= 8:
                        print(
                            f"[SCOUT {self.unique_id}] STUCK: giving up on "
                            f"{self.target_position}"
                        )
                        if self.target_position:
                            self.unreachable_targets[self.target_position] = self.model.current_step
                        self.target_position = None
                        self.path = []
                        self.state = AgentState.EXPLORING
                        self.consecutive_failures_on_target = 0
                else:
                    self.consecutive_failures_on_target = 0

            elif self.state == AgentState.EXPLORING:
                # Random walk with momentum as absolute fallback
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                new_pos = RandomWalkExplorer.get_random_walk_direction(
                    my_pos, self.previous_direction, self.model.grid
                )
                if new_pos != my_pos:
                    old_pos = my_pos
                    self.move_towards(new_pos)
                    my_pos_after = pos_to_tuple(self.pos) if self.pos else my_pos
                    if my_pos_after != old_pos:
                        self.previous_direction = (
                            my_pos_after[0] - old_pos[0],
                            my_pos_after[1] - old_pos[1],
                        )

            elif self.state == AgentState.RECHARGING:
                if self.target_position:
                    self.move_towards(self.target_position)
                break  # Only one move per step when recharging

    def _execute_scout_recharge_step(self) -> None:
        """
        Three-phase warehouse recharge: approach entrance → recharge at interior cell → exit.
        Ensures the scout never stays parked on entrance/exit cells.
        """
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        station = self._scout_wh_station or {}

        if self._scout_wh_step == "approach":
            entrance = station.get("entrance")
            if not entrance:
                self._scout_wh_step = None
                self._scout_wh_station = None
                self.state = AgentState.EXPLORING
                return
            cell_type = self.model.grid.get_cell_type(*my_pos)
            at_or_inside = (
                my_pos == entrance
                or cell_type == CellType.WAREHOUSE
                or cell_type == CellType.WAREHOUSE_ENTRANCE
                or cell_type == CellType.WAREHOUSE_EXIT
            )
            if at_or_inside:
                if self.energy >= self.max_energy * 0.80:
                    # Enough energy — skip recharge, exit immediately
                    print(
                        f"[SCOUT {self.unique_id}] WH: energy sufficient "
                        f"({self.energy:.1f}/{self.max_energy}), skipping recharge"
                    )
                    exit_cell = station.get("exit") or entrance
                    self._scout_wh_step = "exit"
                    self.target_position = exit_cell
                    if my_pos != exit_cell:
                        self.move_towards(exit_cell)
                else:
                    # Need recharge — join FIFO queue near exit
                    queue_cell = self.model.get_queue_slot(station)
                    self._scout_wh_step = "recharge"
                    self.target_position = queue_cell
                    print(
                        f"[SCOUT {self.unique_id}] WH: at entrance, joining queue at {queue_cell}"
                    )
                    if my_pos != queue_cell:
                        self.move_towards(queue_cell)
            else:
                # If the entrance cell is occupied, ask the blocker to move
                blocker = self._get_agent_at_pos(entrance)
                if blocker is not None:
                    self._send_clear_way_request(entrance, blocker)
                self.move_towards(entrance)

        elif self._scout_wh_step == "recharge":
            # target_position holds the assigned FIFO queue slot (set during approach)
            recharge_cell = self.target_position or station.get("recharge_cell")
            # Only recharge when exactly at the assigned FIFO queue slot
            cell_type = self.model.grid.get_cell_type(*my_pos)
            at_recharge = (
                recharge_cell is not None
                and my_pos == recharge_cell
                and cell_type
                not in (
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                )
            )
            if at_recharge:
                rate = self.model.config.warehouse.recharge_rate
                self.recharge_energy(rate)
                if self.energy >= self.max_energy * 0.90:
                    exit_cell = station.get("exit") or station.get("entrance")
                    self._scout_wh_step = "exit"
                    self.target_position = exit_cell
                    print(f"[SCOUT {self.unique_id}] WH: recharged, heading to exit {exit_cell}")
                    # Move toward exit immediately
                    if exit_cell and my_pos != exit_cell:
                        self.move_towards(exit_cell)
            else:
                if recharge_cell:
                    self.move_towards(recharge_cell)
                # recharge_cell should always be set by approach; if not, just wait

        elif self._scout_wh_step == "exit":
            exit_cell = station.get("exit") or station.get("entrance")
            cell_type = self.model.grid.get_cell_type(*my_pos)
            # Finish when on the exit cell OR when already outside warehouse
            left_wh = (
                not exit_cell
                or my_pos == exit_cell
                or cell_type
                not in (
                    CellType.WAREHOUSE,
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                )
            )
            if left_wh:
                print(f"[SCOUT {self.unique_id}] WH: exited, resuming exploration")
                self._scout_wh_step = None
                self._scout_wh_station = None
                self.state = AgentState.EXPLORING
                self.target_position = None
                self.path = []
                # Move off the exit cell immediately so it doesn't block
                if my_pos == exit_cell and self.pos:
                    for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                        np_ = (my_pos[0] + dx, my_pos[1] + dy)
                        if (
                            0 <= np_[0] < self.model.grid.width
                            and 0 <= np_[1] < self.model.grid.height
                        ):
                            nc = self.model.grid.get_cell_type(*np_)
                            if nc not in (
                                CellType.WAREHOUSE,
                                CellType.WAREHOUSE_ENTRANCE,
                                CellType.WAREHOUSE_EXIT,
                                CellType.OBSTACLE,
                            ) and self.model.grid.is_cell_empty(np_):
                                self.model.grid.move_agent(self, np_)
                                break
            else:
                if exit_cell:
                    self.move_towards(exit_cell)

    def _broadcast_discovered_objects(self) -> None:
        """Send object location messages to nearby coordinators"""
        # Get nearby coordinators
        nearby = self.get_nearby_agents(self.communication_radius)
        coordinators = [a for a in nearby if getattr(a, "role", None) == "coordinator"]

        if not coordinators:
            return

        coordinator_ids = [
            getattr(c, "unique_id", 0) for c in coordinators if hasattr(c, "unique_id")
        ]
        print(
            f"[SCOUT {self.unique_id}] COMM: Broadcasting "
            f"{len(self.newly_discovered_objects)} objects to "
            f"coordinators {coordinator_ids}"
        )

        # Send messages about each newly discovered object
        for obj_pos, obj_value in self.newly_discovered_objects:
            message = ObjectLocationMessage(
                sender_id=self.unique_id or 0,
                timestamp=self.model.current_step,
                object_position=obj_pos,
                object_value=obj_value,
            )
            if coordinator_ids:
                self.model.comm_manager.send_message(message, coordinator_ids)
                print(
                    f"[SCOUT {self.unique_id}] -> COORD {coordinator_ids}: "
                    f"object at {obj_pos} (v={obj_value:.1f})"
                )

        # Log message for UI
        if self.newly_discovered_objects:
            self.log_message(
                direction="sent",
                message_type="object_location",
                details=f"Broadcast {len(self.newly_discovered_objects)} objects",
                target_ids=coordinator_ids,
            )

        # Consume energy for broadcast
        self.consume_energy(self.energy_consumption["communicate"] * len(coordinators))
        self.last_communication_step = self.model.current_step
