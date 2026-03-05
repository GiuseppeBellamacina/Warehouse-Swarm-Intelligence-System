"""
Coordinator Agent - Strategic planner managing task assignments
"""

from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import (
    CoordinatorSyncMessage,
    MapDataMessage,
    ObjectLocationMessage,
    RetrieverEventMessage,
    TaskAssignmentMessage,
    TaskStatusMessage,
)
from backend.core.grid_manager import CellType

if TYPE_CHECKING:
    from backend.core.warehouse_model import WarehouseModel


class CoordinatorAgent(BaseAgent):
    """
    Coordinator agent for strategic planning and task assignment

    Characteristics:
    - Wide communication radius
    - Large vision radius
    - Assigns retrieval tasks to retrievers
    - Maintains global knowledge of discovered objects
    - Can explore when idle
    """

    def __init__(
        self,
        unique_id: int,
        model: "WarehouseModel",
        vision_radius: int = 2,
        communication_radius: int = 3,
        max_energy: float = 500.0,
        speed: float = 1.0,
    ):
        super().__init__(
            unique_id=unique_id,
            model=model,
            role="coordinator",
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

        self.state = AgentState.IDLE
        self.pathfinder = AStarPathfinder(model.grid)

        # Task management (local to this coordinator)
        self.known_objects: Dict[Tuple[int, int], float] = {}
        self.assigned_tasks: Dict[int, Tuple[int, int]] = {}  # retriever_id -> object_pos
        self.available_retrievers: List[int] = []

        # Retriever state tracking (populated via TaskStatusMessage — authoritative)
        self.retriever_states: Dict[int, str] = {}  # retriever_id -> state str
        self.retriever_task_queues: Dict[int, List] = {}  # retriever_id -> declared queue
        self.retriever_carrying: Dict[int, int] = {}  # retriever_id -> carrying count
        self.retriever_energy: Dict[int, float] = {}  # retriever_id -> energy
        self.retriever_capacity: Dict[int, int] = {}  # retriever_id -> carrying_capacity
        self.retriever_positions: Dict[int, Tuple] = {}  # retriever_id -> position

        # Objects currently being collected (prevents double-assignment)
        self.objects_being_collected: Set[Tuple[int, int]] = set()

        # Hint: which retriever spotted each object (used for opportunistic assignment)
        self._spotted_by: Dict[Tuple[int, int], int] = {}

        # Communication flag
        self.tasks_to_assign: List[Tuple[int, Tuple[int, int], float]] = []

        # Coordinator sync: track last step we synced with each other coordinator
        self.last_sync_step: Dict[int, int] = {}

        # Search mode: cycle through waypoints when no agent positions are known
        self._search_waypoint_idx: int = 0

        # Track recharge attempts to avoid getting stuck
        self.recharge_attempt_start: Optional[int] = None

        # Warehouse recharge sub-state machine (analogous to retriever _wh_step)
        self._coord_wh_step: Optional[str] = None  # None | "approach" | "recharge" | "exit"
        self._coord_wh_station: Optional[Dict] = None
        # Dedicated slot for the recharge queue cell so target_position can't corrupt it
        self._coord_wh_recharge_cell: Optional[Tuple[int, int]] = None

    def process_received_messages(self) -> None:
        """Process incoming messages (base handles MapData / ObjectLocation)."""
        super().process_received_messages()

        # Iterate the SAME list (already drained once by base class)
        for message in self._step_messages:

            if isinstance(message, TaskStatusMessage):
                # Retriever declares its current task queue — authoritative source
                rid = message.retriever_id
                self.retriever_task_queues[rid] = list(message.task_queue)
                self.retriever_carrying[rid] = message.carrying_objects
                self.retriever_energy[rid] = message.energy_level
                self.retriever_positions[rid] = message.position
                # Update assigned_tasks to match reality
                if message.task_queue:
                    # Primary task is first in queue
                    self.assigned_tasks[rid] = message.task_queue[0]
                else:
                    self.assigned_tasks.pop(rid, None)
                # Infer state
                if message.carrying_objects > 0:
                    self.retriever_states[rid] = "delivering"
                elif message.task_queue:
                    self.retriever_states[rid] = "busy"
                else:
                    self.retriever_states[rid] = "idle"

            elif isinstance(message, RetrieverEventMessage):
                rid = message.retriever_id
                event = message.event_type
                print(
                    f"[COORD {self.unique_id}] <- [RETRIEVER {rid}]: "
                    f"event='{event}' at {message.position}"
                )
                self.log_message(
                    direction="received",
                    message_type="retriever_event",
                    details=f"{event} at {message.position}",
                    target_ids=[rid],
                )

                if event == "object_picked":
                    if message.object_position:
                        self.objects_being_collected.add(message.object_position)
                        self.known_objects.pop(message.object_position, None)

                elif event in ("object_delivered", "idle"):
                    self.retriever_states[rid] = "idle"

                elif event == "task_completed":
                    completed_obj = self.assigned_tasks.pop(rid, None)
                    if completed_obj:
                        self.objects_being_collected.discard(completed_obj)

                elif event == "busy":
                    self.retriever_states[rid] = "busy"

                elif event == "object_spotted":
                    # Retriever saw an object — treat as scout report
                    obj_pos = message.object_position
                    if obj_pos:
                        if (
                            obj_pos not in self.objects_being_collected
                            and obj_pos not in self.assigned_tasks.values()
                        ):
                            self.known_objects[obj_pos] = 1.0
                            # Remember who spotted it so _plan_task_assignments
                            # can prefer assigning it back to the same retriever
                            # (better cache locality, avoids cross-assignments)
                            self._spotted_by[obj_pos] = rid
                            print(
                                f"[COORD {self.unique_id}] SPOTTED: "
                                f"retriever {rid} saw object at {obj_pos}"
                            )

            elif isinstance(message, ObjectLocationMessage):
                # Scout discovered an object
                obj_pos = message.object_position
                if (
                    obj_pos not in self.objects_being_collected
                    and obj_pos not in self.assigned_tasks.values()
                ):
                    self.known_objects[obj_pos] = message.object_value
                    print(
                        f"[COORD {self.unique_id}] <- [SCOUT {message.sender_id}]: "
                        f"object at {obj_pos}"
                    )
                    self.log_message(
                        direction="received",
                        message_type="object_location",
                        details=f"Object at {obj_pos}",
                        target_ids=[message.sender_id],
                    )

            elif isinstance(message, CoordinatorSyncMessage):
                # Another coordinator sharing its knowledge
                other_id = message.sender_coordinator_id
                print(
                    f"[COORD {self.unique_id}] <- [COORD {other_id}]: "
                    f"sync ({len(message.known_objects)} objs, "
                    f"{len(message.assigned_tasks)} tasks)"
                )
                # Merge known objects (don't overwrite existing entries)
                for pos, val in message.known_objects.items():
                    if pos not in self.known_objects and pos not in self.objects_being_collected:
                        self.known_objects[pos] = val
                # Merge objects being collected
                for pos in message.objects_being_collected:
                    self.objects_being_collected.add(tuple(pos))
                    self.known_objects.pop(tuple(pos), None)
                # Merge retriever state knowledge
                for rid, state_str in message.retriever_states.items():
                    if rid not in self.retriever_states:
                        self.retriever_states[rid] = state_str
                # Merge retriever positions (indirect learning via other coordinators)
                for rid, pos in message.retriever_positions.items():
                    if rid not in self.retriever_positions:
                        self.retriever_positions[rid] = tuple(pos)

    def step_decide(self) -> None:
        """Decide on task assignments and own actions"""
        # Reset communication flag
        self.should_communicate_this_step = False
        self.tasks_to_assign = []

        # Check if need to recharge — only start the sub-machine when energy is
        # genuinely low.  Skip if already at or above the recharge trigger range to
        # avoid the coordinator wandering into a warehouse and immediately exiting.
        if self.energy < self.max_energy * 0.20:
            if self.state != AgentState.RECHARGING:
                closest_wh = self.get_closest_warehouse()
                if closest_wh:
                    print(
                        f"[COORD {self.unique_id}] LOW-E ({self.energy:.1f}), heading to WH {closest_wh}"
                    )
                    self.state = AgentState.RECHARGING
                    self.target_position = closest_wh
                    self.recharge_attempt_start = self.model.current_step
                    self.was_recharging_at_warehouse = False
                    # Always reset sub-machine so it re-initialises cleanly
                    self._coord_wh_step = None
                    self._coord_wh_station = None
                    self._coord_wh_recharge_cell = None
            else:
                if self.recharge_attempt_start is not None:
                    steps_attempting = self.model.current_step - self.recharge_attempt_start
                    if steps_attempting > 50:
                        print(
                            f"[COORD {self.unique_id}] EMERGENCY: cannot reach WH after {steps_attempting} steps"
                        )
                        # Full reset — sub-machine MUST be cleared or step_act will keep running it
                        self._coord_wh_step = None
                        self._coord_wh_station = None
                        self._coord_wh_recharge_cell = None
                        self.state = AgentState.IDLE
                        self.target_position = None
                        self.recharge_attempt_start = None
                        return
            return

        if self.recharge_attempt_start is not None:
            self.recharge_attempt_start = None
            if self.state == AgentState.RECHARGING:
                self.state = AgentState.IDLE

        # Sync with any nearby coordinators
        self._sync_with_nearby_coordinators()

        # Identify available retrievers and plan assignments
        self._identify_available_retrievers()
        self._plan_task_assignments()

        # Priority 1: Communicate (send tasks OR sync)
        if self.tasks_to_assign:
            self.should_communicate_this_step = True
            return

        # Priority 1b: Tasks exist but no retrievers in range — go find them
        if self._seek_retrievers_if_needed():
            return

        # Priority 2: Explore when idle — but never while a warehouse sub-sequence is active
        if self.state in (AgentState.IDLE, AgentState.EXPLORING) and self._coord_wh_step is None:
            self._decide_exploration()

    def _identify_available_retrievers(self) -> None:
        """
        Find nearby retrievers that have spare task-queue capacity.
        Availability is based on the DECLARED task_queue from TaskStatusMessage
        (latest authoritative data) rather than guessing from state alone.
        """
        nearby = self.get_nearby_agents(self.communication_radius)
        self.available_retrievers = []

        for agent in nearby:
            if getattr(agent, "role", None) != "retriever":
                continue
            rid = getattr(agent, "unique_id", None)
            if rid is None:
                continue

            # Cache the carrying capacity when we can read it directly
            cap = getattr(agent, "carrying_capacity", 2)
            self.retriever_capacity[rid] = cap

            # Use declared task queue length (authoritative, avoids race conditions)
            declared_queue = self.retriever_task_queues.get(rid, [])
            carrying = self.retriever_carrying.get(rid, getattr(agent, "carrying_objects", 0))
            energy = self.retriever_energy.get(rid, getattr(agent, "energy", 0))

            # Available slots = capacity - (declared queue + objects currently carried)
            used_slots = len(declared_queue) + carrying
            free_slots = cap - used_slots

            if free_slots > 0 and energy > 40:
                self.available_retrievers.append(rid)

        if self.available_retrievers:
            print(
                f"[COORD {self.unique_id}] SCAN: {len(self.available_retrievers)} "
                f"retrievers with free slots: {self.available_retrievers}"
            )

    def _plan_task_assignments(self) -> None:
        """
        Greedy task planning.
        Each retriever may receive up to (capacity - len(declared_queue)) new tasks.
        Priority = object_value / (distance + 1).
        """
        if not self.known_objects or not self.available_retrievers:
            return

        # Build retriever info from communicated positions only (no direct model access)
        retriever_info = {}
        for rid in self.available_retrievers:
            pos = self.retriever_positions.get(rid)
            if pos is None:
                continue  # no communicated position yet — skip until we hear from them
            cap = self.retriever_capacity.get(rid, 2)
            declared_q = self.retriever_task_queues.get(rid, [])
            carrying = self.retriever_carrying.get(rid, 0)
            free_slots = cap - len(declared_q) - carrying
            retriever_info[rid] = {"pos": pos, "free_slots": max(0, free_slots)}

        # Build candidate (retriever, object) pairs with priority
        candidates = []
        for rid, info in retriever_info.items():
            if info["free_slots"] <= 0:
                continue
            for obj_pos, obj_value in list(self.known_objects.items()):
                if obj_pos in self.objects_being_collected:
                    continue
                # Skip objects already claimed by any agent (incl. self-assigned retrievers)
                claimer = self.model.comm_manager.get_claimer(obj_pos)
                if claimer is not None and claimer != rid:
                    continue
                # Skip objects already in this retriever's declared queue
                if obj_pos in self.retriever_task_queues.get(rid, []):
                    continue
                dist = abs(obj_pos[0] - info["pos"][0]) + abs(obj_pos[1] - info["pos"][1])
                priority = obj_value / (dist + 1)
                # Bonus for the retriever who spotted the object (opportunistic assignment)
                if self._spotted_by.get(obj_pos) == rid:
                    priority += 0.5
                candidates.append((priority, rid, obj_pos))

        candidates.sort(key=lambda x: x[0], reverse=True)

        assigned_objects: Set[Tuple] = set()
        extra_slots: Dict[int, int] = {
            rid: info["free_slots"] for rid, info in retriever_info.items()
        }

        for priority, rid, obj_pos in candidates:
            if extra_slots.get(rid, 0) <= 0:
                continue
            if obj_pos in assigned_objects:
                continue
            self.tasks_to_assign.append((rid, obj_pos, priority))
            assigned_objects.add(obj_pos)
            extra_slots[rid] -= 1

    def _sync_with_nearby_coordinators(self) -> None:
        """
        When another coordinator is in range, share full knowledge state.
        Rate-limited to once every 10 steps per coordinator pair.
        """
        nearby = self.get_nearby_agents(self.communication_radius)
        other_coords = [a for a in nearby if getattr(a, "role", None) == "coordinator"]
        if not other_coords:
            return

        current_step = self.model.current_step
        for coord in other_coords:
            cid = getattr(coord, "unique_id", None)
            if cid is None:
                continue
            last = self.last_sync_step.get(cid, -999)
            if current_step - last < 10:
                continue  # already synced recently
            self.last_sync_step[cid] = current_step

            sync_msg = CoordinatorSyncMessage(
                sender_id=self.unique_id,
                timestamp=current_step,
                sender_coordinator_id=self.unique_id,
                known_objects=dict(self.known_objects),
                assigned_tasks=dict(self.assigned_tasks),
                retriever_states=dict(self.retriever_states),
                objects_being_collected=list(self.objects_being_collected),
                retriever_positions={k: tuple(v) for k, v in self.retriever_positions.items() if v},
            )
            self.model.comm_manager.send_message(sync_msg, [cid])
            print(
                f"[COORD {self.unique_id}] -> [COORD {cid}]: "
                f"sync ({len(self.known_objects)} objs)"
            )

    def _seek_retrievers_if_needed(self) -> bool:
        """
        If there are unassigned objects but no retrievers are currently in comm
        range, move toward the nearest last-known retriever position so that task
        assignments can be delivered as soon as comm range is re-established.

        Skips positions already in unreachable_targets (blacklisted by pathfinder)
        and falls back to normal exploration when all known positions are stale.

        Returns True if the coordinator is actively seeking retrievers (so the
        caller can skip the normal exploration decision).
        """
        if self.available_retrievers:
            return False  # already have retrievers in range
        if self._coord_wh_step is not None:
            return False  # busy with warehouse sequence

        # Check for objects that still need assigning
        pending = [
            pos
            for pos in self.known_objects
            if pos not in self.objects_being_collected and pos not in self.assigned_tasks.values()
        ]
        if not pending:
            return False

        # Find nearest last-known retriever from declared positions,
        # skipping any that are already blacklisted as unreachable.
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        current_step = self.model.current_step
        best_pos: Optional[Tuple[int, int]] = None
        best_dist = float("inf")
        for rid, r_pos in self.retriever_positions.items():
            if not r_pos:
                continue
            r_pos_t = tuple(r_pos)  # type: ignore[arg-type]
            # Skip position if pathfinder recently marked it unreachable
            if r_pos_t in self.unreachable_targets:
                failed_step = self.unreachable_targets[r_pos_t]
                if current_step - failed_step < 30:
                    continue  # still blacklisted
                del self.unreachable_targets[r_pos_t]  # blacklist expired
            dist = abs(r_pos[0] - my_pos[0]) + abs(r_pos[1] - my_pos[1])
            if dist < best_dist:
                best_dist = dist
                best_pos = r_pos_t  # type: ignore[assignment]

        if best_pos is None:
            # All known retriever positions are blacklisted — fall through to exploration
            return False
        if best_dist <= self.communication_radius:
            # Already close enough; retriever must have moved — stale position
            return False

        if self.target_position != best_pos:
            self.target_position = best_pos
            self.path = []
        self.state = AgentState.EXPLORING
        print(
            f"[COORD {self.unique_id}] SEEK-RETRIEVER: "
            f"{len(pending)} unassigned object(s), no retrievers in range — "
            f"heading to last known retriever pos {best_pos} (dist={int(best_dist)})"
        )
        return True

    def _decide_exploration(self) -> None:
        """
        Position the coordinator near the centroid of its managed agents.
        The coordinator never does frontier exploration — that is the scout's job.
        When already close enough to the centroid, simply hold position (IDLE).
        When too far, move toward the nearest walkable cell around the centroid.
        """
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)

        # Comfortable range: half of comm radius.  Wide enough that the coordinator
        # doesn't chase micro-shifts in the centroid, but still keeps it reachable.
        max_distance_from_agents = max(8, self.communication_radius // 2)

        _WH_TYPES = (
            CellType.WAREHOUSE,
            CellType.WAREHOUSE_ENTRANCE,
            CellType.WAREHOUSE_EXIT,
            CellType.OBSTACLE,
        )

        # Build centroid from communicated (last-known) positions only.
        # No direct access to model.agents positions — positions must arrive via messages.
        agent_positions = [tuple(p) for p in self.retriever_positions.values() if p]

        if not agent_positions:
            # No communicated positions yet — enter search mode: cycle through
            # strategic waypoints until we come within comm range of any agent.
            W, H = self.model.grid.width, self.model.grid.height
            search_waypoints = [
                (W // 2, H // 2),
                (W // 4, H // 4),
                (3 * W // 4, H // 4),
                (3 * W // 4, 3 * H // 4),
                (W // 4, 3 * H // 4),
            ]
            wp = search_waypoints[self._search_waypoint_idx % len(search_waypoints)]
            # Advance to next waypoint when we're close enough to the current one
            if abs(my_pos[0] - wp[0]) + abs(my_pos[1] - wp[1]) <= max_distance_from_agents:
                self._search_waypoint_idx += 1
                wp = search_waypoints[self._search_waypoint_idx % len(search_waypoints)]
            print(
                f"[COORD {self.unique_id}] SEARCH: no communicated agent positions — "
                f"heading to waypoint {wp}"
            )
            self.target_position = wp
            self.state = AgentState.EXPLORING
            return

        centroid_x = sum(pos[0] for pos in agent_positions) / len(agent_positions)
        centroid_y = sum(pos[1] for pos in agent_positions) / len(agent_positions)
        centroid = (int(centroid_x), int(centroid_y))

        dist_from_centroid = abs(my_pos[0] - centroid[0]) + abs(my_pos[1] - centroid[1])

        if dist_from_centroid <= max_distance_from_agents:
            # Already well-positioned — nothing to do this step
            self.state = AgentState.IDLE
            return

        # Need to reposition — snap centroid to nearest usable cell if necessary
        reposition_target = centroid
        if (
            not self.model.grid.is_walkable(*centroid)
            or self.model.grid.get_cell_type(*centroid) in _WH_TYPES
            or centroid in self.unreachable_targets
        ):
            reposition_target = None
            for radius in range(1, 6):
                for dx in range(-radius, radius + 1):
                    for dy in range(-radius, radius + 1):
                        if abs(dx) != radius and abs(dy) != radius:
                            continue  # only the outer ring
                        cx, cy = centroid[0] + dx, centroid[1] + dy
                        if not (
                            0 <= cx < self.model.grid.width and 0 <= cy < self.model.grid.height
                        ):
                            continue
                        if self.model.grid.get_cell_type(cx, cy) in _WH_TYPES:
                            continue
                        if not self.model.grid.is_walkable(cx, cy):
                            continue
                        candidate = (cx, cy)
                        if candidate in self.unreachable_targets:
                            continue
                        reposition_target = candidate
                        break
                    if reposition_target:
                        break

        if reposition_target is None or reposition_target in self.unreachable_targets:
            # Can't find a suitable nearby cell — hold position
            self.state = AgentState.IDLE
            return

        print(
            f"[COORD {self.unique_id}] REPOSITION: Too far from agents "
            f"(distance: {dist_from_centroid}), moving towards {reposition_target}"
        )
        self.target_position = reposition_target
        self.state = AgentState.EXPLORING

    def step_act(self) -> None:
        """Execute decided action: COMMUNICATE or MOVE (not both)"""
        if self.energy <= 0:
            return

        # OPTION 1: Assign tasks (communicate this step, skip movement)
        if self.should_communicate_this_step and self.tasks_to_assign:
            self._send_task_assignments()
            return

        # OPTION 2: Execute recharge sub-state machine
        if self.state == AgentState.RECHARGING or self._coord_wh_step is not None:
            self._execute_recharge_step()
            return

        # OPTION 3: Move based on exploration state
        if self.state == AgentState.EXPLORING:
            if self.target_position:
                self.move_towards(self.target_position)
                my_pos = pos_to_tuple(self.pos) if self.pos else None
                if my_pos and my_pos == self.target_position:
                    self.target_position = None
                    self.state = AgentState.IDLE

        elif self.state == AgentState.IDLE:
            # Hold position — the coordinator's job is coordination, not exploration.
            # Movement is handled deliberately via _decide_exploration (centroid
            # repositioning) and _seek_retrievers_if_needed.
            pass

    def _execute_recharge_step(self) -> None:
        """Sub-state machine: approach warehouse → recharge → exit."""
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)

        # --- initialise sub-machine ---
        if self._coord_wh_step is None:
            station = self.model.get_nearest_warehouse_to(my_pos)
            self._coord_wh_station = station
            entrance = station.get("entrance")
            if entrance:
                self._coord_wh_step = "approach"
                self.target_position = entrance
                print(f"[COORD {self.unique_id}] RECHARGE: heading to entrance {entrance}")
            else:
                # No station found — abort recharge
                self.state = AgentState.IDLE
                self.recharge_attempt_start = None
                return

        station = self._coord_wh_station or {}

        # --- approach entrance ---
        if self._coord_wh_step == "approach":
            entrance = station.get("entrance")
            cell_type = self.model.grid.get_cell_type(*my_pos)
            at_or_inside = (
                not entrance
                or my_pos == entrance
                or cell_type
                in (
                    CellType.WAREHOUSE,
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                )
            )
            if at_or_inside:
                if self.energy >= self.max_energy * 0.80:
                    # Enough energy — skip recharge entirely, exit immediately
                    print(
                        f"[COORD {self.unique_id}] RECHARGE: energy sufficient "
                        f"({self.energy:.1f}/{self.max_energy}), skipping recharge"
                    )
                    exit_cell = station.get("exit") or station.get("entrance")
                    self._coord_wh_step = "exit"
                    self.target_position = exit_cell
                    if exit_cell and my_pos != exit_cell:
                        self.move_towards(exit_cell)
                else:
                    # Need recharge — join FIFO queue near exit
                    queue_cell = self.model.get_queue_slot(station)
                    self._coord_wh_step = "recharge"
                    # Store in dedicated attribute so target_position changes can't corrupt it
                    self._coord_wh_recharge_cell = queue_cell
                    self.target_position = queue_cell
                    print(
                        f"[COORD {self.unique_id}] RECHARGE: at entrance, joining queue at {queue_cell}"
                    )
                    if my_pos != queue_cell:
                        self.move_towards(queue_cell)
            else:
                # Guard against getting stuck.
                # Ensure recharge_attempt_start is always set (may be None if we arrived
                # here after an EMERGENCY reset wiped it while _coord_wh_step was left set).
                if self.recharge_attempt_start is None:
                    self.recharge_attempt_start = self.model.current_step
                steps = self.model.current_step - self.recharge_attempt_start
                if steps > 60:
                    print(f"[COORD {self.unique_id}] RECHARGE TIMEOUT: cannot reach WH, aborting")
                    self._coord_wh_step = None
                    self._coord_wh_station = None
                    self._coord_wh_recharge_cell = None
                    self.state = AgentState.IDLE
                    self.target_position = None
                    self.recharge_attempt_start = None
                    return
                if entrance:
                    # If the entrance cell is occupied, ask the blocker to move
                    blocker = self._get_agent_at_pos(entrance)
                    if blocker is not None:
                        self._send_clear_way_request(entrance, blocker)
                    self.move_towards(entrance)
            return

        # --- walk to recharge cell and recharge ---
        if self._coord_wh_step == "recharge":
            # Use the dedicated attribute so that target_position changes (e.g. REPOSITION)
            # can never send the coordinator toward the wrong cell while recharging.
            recharge_cell = self._coord_wh_recharge_cell or station.get("recharge_cell") or my_pos
            cell_type = self.model.grid.get_cell_type(*my_pos)
            # Only recharge on true interior cells — never on entrance or exit
            if my_pos != recharge_cell:
                self.move_towards(recharge_cell)
                return
            if cell_type in (CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT):
                # Shouldn't happen, but move further inside just in case
                self.move_towards(recharge_cell)
                return
            # At interior recharge cell — recharge
            rate = self.model.config.warehouse.recharge_rate
            self.recharge_energy(rate)
            if self.energy >= self.max_energy * 0.95:
                exit_cell = station.get("exit") or station.get("entrance")
                self._coord_wh_step = "exit"
                self.target_position = exit_cell
                print(
                    f"[COORD {self.unique_id}] RECHARGE: full ({self.energy:.1f}), "
                    f"heading to exit {exit_cell}"
                )
                # Move toward exit immediately
                if exit_cell and my_pos != exit_cell:
                    self.move_towards(exit_cell)
            return

        # --- walk to exit cell ---
        if self._coord_wh_step == "exit":
            exit_cell = station.get("exit") or station.get("entrance")
            cell_type = self.model.grid.get_cell_type(*my_pos)
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
                print(f"[COORD {self.unique_id}] RECHARGE: exited warehouse, resuming")
                self._coord_wh_step = None
                self._coord_wh_station = None
                self._coord_wh_recharge_cell = None
                self.state = AgentState.IDLE
                self.recharge_attempt_start = None
                # Find a walkable cell just outside the warehouse to move to immediately
                if self.pos:
                    for dx, dy in [
                        (1, 0),
                        (-1, 0),
                        (0, 1),
                        (0, -1),
                        (1, 1),
                        (-1, 1),
                        (1, -1),
                        (-1, -1),
                    ]:
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
                                self.target_position = np_
                                self.model.grid.move_agent(self, np_)
                                break
                    else:
                        self.target_position = None
            else:
                if exit_cell:
                    self.move_towards(exit_cell)
            return

    def _send_task_assignments(self) -> None:
        """Send task assignments to retrievers and update local tracking."""
        print(f"[COORD {self.unique_id}] ASSIGN: sending {len(self.tasks_to_assign)} task(s)")

        for retriever_id, obj_pos, priority in self.tasks_to_assign:
            message = TaskAssignmentMessage(
                sender_id=self.unique_id or 0,
                timestamp=self.model.current_step,
                target_id=retriever_id,
                task_type="retrieve",
                target_position=obj_pos,
                priority=priority,
            )
            self.model.comm_manager.send_message(message, [retriever_id])

            # Share known warehouse cells with the retriever so it can navigate
            # to the correct warehouse even if it has never seen one directly.
            if self.known_warehouses:
                wh_cells = [
                    (wx, wy, int(self.model.grid.get_cell_type(wx, wy)))
                    for wx, wy in self.known_warehouses
                ]
                map_msg = MapDataMessage(
                    sender_id=self.unique_id or 0,
                    timestamp=self.model.current_step,
                    explored_cells=wh_cells,
                    known_objects=dict(self.known_objects),
                    objects_being_collected=list(self.objects_being_collected),
                )
                self.model.comm_manager.send_message(map_msg, [retriever_id])

            print(
                f"[COORD {self.unique_id}] -> [RETRIEVER {retriever_id}]: "
                f"retrieve {obj_pos} (priority={priority:.2f})"
            )
            self.log_message(
                direction="sent",
                message_type="task_assignment",
                details=f"Retrieve at {obj_pos} (p={priority:.2f})",
                target_ids=[retriever_id],
            )

            # Update local tracking (optimistic — will be overwritten by next TaskStatusMessage)
            self.assigned_tasks[retriever_id] = obj_pos
            self.objects_being_collected.add(obj_pos)
            self.known_objects.pop(obj_pos, None)
            self.retriever_states[retriever_id] = "busy"

            # Update our local cache of the retriever's task queue
            q = self.retriever_task_queues.setdefault(retriever_id, [])
            if obj_pos not in q:
                q.append(obj_pos)

            # Remove the spotted-by hint once assigned
            self._spotted_by.pop(obj_pos, None)

            self.consume_energy(self.energy_consumption["communicate"])

        self.tasks_to_assign = []
        self.last_communication_step = self.model.current_step
