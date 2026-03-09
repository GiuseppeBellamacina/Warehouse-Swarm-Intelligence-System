"""
Coordinator Agent - Strategic planner managing task assignments
"""

from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from backend.agents.base_agent import AgentState, BaseAgent, agent_tag, pos_to_tuple
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import (
    CoordinatorSyncMessage,
    MapDataMessage,
    ObjectLocationMessage,
    RetrieverEventMessage,
    Stamped,
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
        behavior: Optional[dict] = None,
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

        # ── Behavior params (overridable from UI / config) ─────────────
        _b = behavior or {}
        self._BOREDOM_THRESHOLD: int = _b.get("boredom_threshold", 20)
        self._POS_MAX_AGE: int = _b.get("pos_max_age", 25)
        self._RECHARGE_THRESHOLD: float = _b.get("recharge_threshold", 0.20)
        self._CENTROID_OBJECT_BIAS: float = _b.get("centroid_object_bias", 0.4)
        self._SYNC_RATE_LIMIT: int = _b.get("sync_rate_limit", 10)
        self._SEEK_RETRIEVERS: bool = _b.get("seek_retrievers", True)
        self._BOREDOM_PATROL: bool = _b.get("boredom_patrol", True)
        self._OBJECT_BIASED_CENTROID: bool = _b.get("object_biased_centroid", True)

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
        # retriever_positions inherited from BaseAgent (Dict[int, Tuple[int,int]])

        # Objects currently being collected (prevents double-assignment)
        self.objects_being_collected: Set[Tuple[int, int]] = set()
        # Step at which each objects_being_collected entry was last updated
        self.objects_being_collected_step: Dict[Tuple, int] = {}

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

        # Consecutive steps spent IDLE in _decide_exploration.
        # When this exceeds _BOREDOM_THRESHOLD the coordinator forces a waypoint
        # patrol pass rather than continuing to sit near the agent centroid.
        self._idle_steps: int = 0

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
                prev_queue = self.retriever_task_queues.get(rid, [])
                self.retriever_task_queues[rid] = list(message.task_queue)
                self.retriever_carrying[rid] = message.carrying_objects
                self.retriever_energy[rid] = message.energy_level
                self.retriever_positions[rid] = message.position
                self.retriever_positions_step[rid] = message.timestamp
                # Update assigned_tasks to match reality
                if message.task_queue:
                    # Primary task is first in queue
                    self.assigned_tasks[rid] = message.task_queue[0]
                else:
                    self.assigned_tasks.pop(rid, None)
                # Release objects that the retriever no longer has in its queue.
                now_gone = set(prev_queue) - set(message.task_queue)
                cs_now = self.model.current_step
                for completed_pos in now_gone:
                    self.objects_being_collected.discard(completed_pos)
                    self.objects_being_collected_step.pop(completed_pos, None)
                    # Refresh tombstone so relay messages (explored_cells or
                    # ObjectLocation) with a CURRENT step timestamp cannot
                    # sneak the position back into known_objects after OBC
                    # is cleared.  The tombstone must be ≥ current step.
                    if cs_now > self.known_objects_cleared.get(completed_pos, -1):
                        self.known_objects_cleared[completed_pos] = cs_now
                    # Also purge in case the same-step relay already re-inserted it.
                    self.known_objects.pop(completed_pos, None)
                    self.known_objects_step.pop(completed_pos, None)
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
                    f"{self.tag} <- {agent_tag('retriever', rid)}: "
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
                        opos = message.object_position
                        self.objects_being_collected.add(opos)
                        self.objects_being_collected_step[opos] = self.model.current_step
                        self.known_objects.pop(opos, None)
                        self.known_objects_step.pop(opos, None)
                        self.known_objects_cleared[opos] = self.model.current_step

                elif event in ("object_delivered", "idle"):
                    self.retriever_states[rid] = "idle"

                elif event == "task_completed":
                    completed_obj = self.assigned_tasks.pop(rid, None)
                    if completed_obj:
                        self.objects_being_collected.discard(completed_obj)
                        self.objects_being_collected_step.pop(completed_obj, None)

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
                            cs = self.model.current_step
                            if cs > self.known_objects_step.get(obj_pos, -1):
                                self.known_objects[obj_pos] = 1.0
                                self.known_objects_step[obj_pos] = cs
                            self._spotted_by[obj_pos] = rid
                            print(
                                f"{self.tag} SPOTTED: "
                                f"{agent_tag('retriever', rid)} saw object at {obj_pos}"
                            )

            elif isinstance(message, ObjectLocationMessage):
                # Scout discovered an object — accept if newer
                obj_pos = message.object_position
                if (
                    obj_pos not in self.objects_being_collected
                    and obj_pos not in self.assigned_tasks.values()
                    and message.timestamp > self.known_objects_step.get(obj_pos, -1)
                    and self.known_objects_cleared.get(obj_pos, -1) < message.timestamp
                ):
                    self.known_objects[obj_pos] = message.object_value
                    self.known_objects_step[obj_pos] = message.timestamp
                    print(
                        f"{self.tag} <- {agent_tag('scout', message.sender_id)}: "
                        f"object at {obj_pos}"
                    )
                    self.log_message(
                        direction="received",
                        message_type="object_location",
                        details=f"Object at {obj_pos}",
                        target_ids=[message.sender_id],
                    )

            elif isinstance(message, CoordinatorSyncMessage):
                # Another coordinator sharing its knowledge — apply "newest wins" per item
                other_id = message.sender_coordinator_id
                print(
                    f"{self.tag} <- {agent_tag('coordinator', other_id)}: "
                    f"sync ({len(message.known_objects)} objs, "
                    f"{len(message.assigned_tasks)} tasks)"
                )
                # Merge known_objects: {pos: Stamped(value, step)}
                for raw_pos, stamped in message.known_objects.items():
                    pos = tuple(raw_pos)
                    val, step = stamped.value, stamped.step
                    if pos not in self.objects_being_collected:
                        if (
                            step > self.known_objects_step.get(pos, -1)
                            and self.known_objects_cleared.get(pos, -1) < step
                        ):
                            self.known_objects[pos] = val
                            self.known_objects_step[pos] = step
                # Merge objects_being_collected: {pos: Stamped(None, step)}
                for raw_pos, stamped in message.objects_being_collected.items():
                    pos = tuple(raw_pos)
                    step = stamped.step
                    if step > self.objects_being_collected_step.get(pos, -1):
                        self.objects_being_collected.add(pos)
                        self.objects_being_collected_step[pos] = step
                        self.known_objects.pop(pos, None)
                        self.known_objects_step.pop(pos, None)
                        if step > self.known_objects_cleared.get(pos, -1):
                            self.known_objects_cleared[pos] = step
                # Merge retriever state knowledge
                for rid, state_str in message.retriever_states.items():
                    if rid not in self.retriever_states:
                        self.retriever_states[rid] = state_str
                # Merge retriever positions: {rid: Stamped((x,y), step)}
                for rid, stamped in message.retriever_positions.items():
                    step = stamped.step
                    if step > self.retriever_positions_step.get(rid, -1):
                        self.retriever_positions[rid] = tuple(stamped.value)
                        self.retriever_positions_step[rid] = step

    def step_decide(self) -> None:
        """Decide on task assignments and own actions"""
        # Reset communication flag
        self.should_communicate_this_step = False
        self.tasks_to_assign = []

        # Check if need to recharge — only start the sub-machine when energy is
        # genuinely low.  Skip if already at or above the recharge trigger range to
        # avoid the coordinator wandering into a warehouse and immediately exiting.
        if self.energy < self.max_energy * self._RECHARGE_THRESHOLD:
            if self.state != AgentState.RECHARGING:
                closest_wh = self.get_closest_warehouse()
                if closest_wh:
                    print(f"{self.tag} LOW-E ({self.energy:.1f}), heading to WH {closest_wh}")
                    self.state = AgentState.RECHARGING
                    self.target_position = closest_wh
                    self.recharge_attempt_start = self.model.current_step
                    # Always reset sub-machine so it re-initialises cleanly
                    self._coord_wh_step = None
                    self._coord_wh_station = None
                    self._coord_wh_recharge_cell = None
            else:
                if self.recharge_attempt_start is not None:
                    steps_attempting = self.model.current_step - self.recharge_attempt_start
                    if steps_attempting > 50:
                        print(
                            f"{self.tag} EMERGENCY: cannot reach WH after {steps_attempting} steps"
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
            # Fall through to exploration so the coordinator's movement state is kept
            # up-to-date even in steps where it is busy sending assignments.

        # Priority 1b: Tasks exist but no retrievers in range — go find them
        if self._SEEK_RETRIEVERS and not self.tasks_to_assign and self._seek_retrievers_if_needed():
            return

        # Priority 2: Explore / reposition — runs every step so state is always current
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
                f"{self.tag} SCAN: {len(self.available_retrievers)} "
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
            if current_step - last < self._SYNC_RATE_LIMIT:
                continue  # already synced recently
            self.last_sync_step[cid] = current_step

            sync_msg = CoordinatorSyncMessage(
                sender_id=self.unique_id,
                timestamp=current_step,
                sender_coordinator_id=self.unique_id,
                known_objects={
                    pos: Stamped(val, self.known_objects_step.get(pos, 0))
                    for pos, val in self.known_objects.items()
                },
                assigned_tasks=dict(self.assigned_tasks),
                retriever_states=dict(self.retriever_states),
                objects_being_collected={
                    pos: Stamped(None, self.objects_being_collected_step.get(pos, 0))
                    for pos in self.objects_being_collected
                },
                retriever_positions={
                    rid: Stamped(tuple(p), self.retriever_positions_step.get(rid, 0))
                    for rid, p in self.retriever_positions.items()
                    if p
                },
            )
            self.model.comm_manager.send_message(sync_msg, [cid])
            print(
                f"{self.tag} -> {agent_tag('coordinator', cid)}: "
                f"sync ({len(self.known_objects)} objs)"
            )

    def _seek_retrievers_if_needed(self) -> bool:
        """
        If there are unassigned objects but no retrievers are currently in comm
        range, move toward the nearest last-known retriever position so that task
        assignments can be delivered as soon as comm range is re-established.

        Only considers FRESH retriever positions (within ``_POS_MAX_AGE`` steps).
        Stale positions (beyond the age threshold) are ignored entirely because
        the retriever has likely moved far from there.

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

        # Find nearest FRESH last-known retriever position,
        # skipping any that are stale or already blacklisted as unreachable.
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        current_step = self.model.current_step
        best_pos: Optional[Tuple[int, int]] = None
        best_dist = float("inf")
        for rid, r_pos in self.retriever_positions.items():
            if not r_pos:
                continue
            # Skip stale positions — same threshold as _decide_exploration
            pos_age = current_step - self.retriever_positions_step.get(rid, -9999)
            if pos_age > self._POS_MAX_AGE:
                continue
            r_pos_t: Tuple[int, int] = (int(r_pos[0]), int(r_pos[1]))
            # Skip position if pathfinder recently marked it unreachable
            if r_pos_t in self.unreachable_targets:
                failed_step = self.unreachable_targets[r_pos_t]
                if current_step - failed_step < 100:
                    continue  # still blacklisted
                del self.unreachable_targets[r_pos_t]  # blacklist expired
            dist = abs(r_pos[0] - my_pos[0]) + abs(r_pos[1] - my_pos[1])
            if dist < best_dist:
                best_dist = dist
                best_pos = r_pos_t  # type: ignore[assignment]

        if best_pos is None:
            # All known retriever positions are stale or blacklisted — fall through
            return False
        if best_dist <= self.communication_radius:
            # Already close enough; retriever must have moved — stale position
            return False

        if self.target_position != best_pos:
            self.target_position = best_pos
            self.path = []
        self.state = AgentState.EXPLORING
        print(
            f"{self.tag} SEEK-RETRIEVER: "
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

        # Comfortable range: full communication radius.  The coordinator considers
        # itself close enough to the swarm centroid as long as it is within its
        # communication radius — meaning it can still directly talk to agents there.
        max_distance_from_agents = self.communication_radius

        _WH_TYPES = (
            CellType.WAREHOUSE,
            CellType.WAREHOUSE_ENTRANCE,
            CellType.WAREHOUSE_EXIT,
            CellType.OBSTACLE,
        )

        # Build centroid from communicated positions only.
        # Only consider positions that are recent enough to be meaningful.
        # Stale positions (retrievers that have moved far away and stopped reporting)
        # would pin the coordinator to the delivery area — if no fresh data is available,
        # fall back to the waypoint patrol so the coordinator keeps moving.
        cs_now = self.model.current_step
        agent_positions = [
            tuple(p)
            for rid, p in self.retriever_positions.items()
            if p and cs_now - self.retriever_positions_step.get(rid, -9999) <= self._POS_MAX_AGE
        ]

        # Boredom check: if the coordinator has been IDLE for too long AND there
        # is nothing useful to do nearby (no known unassigned objects), force a
        # waypoint patrol so it circulates and discovers new areas.
        # IMPORTANT: skip the patrol when known_objects exist — the coordinator
        # must stay near retrievers to deliver those assignments, not wander off.
        no_work_pending = not any(
            pos not in self.objects_being_collected for pos in self.known_objects
        )
        if self._BOREDOM_PATROL and self._idle_steps >= self._BOREDOM_THRESHOLD and no_work_pending:
            self._idle_steps = 0
            agent_positions = []  # pretend we have no positions → waypoint branch

        if not agent_positions:
            # No communicated positions yet — enter search mode: cycle through
            # strategic waypoints until we come within comm range of any agent.
            W, H = self.model.grid.width, self.model.grid.height
            raw_waypoints = [
                (W // 2, H // 2),
                (W // 4, H // 4),
                (3 * W // 4, H // 4),
                (3 * W // 4, 3 * H // 4),
                (W // 4, 3 * H // 4),
            ]
            # Snap each ideal waypoint to the nearest walkable floor cell so we
            # never try to path-find to an obstacle or warehouse interior.
            search_waypoints = []
            for raw in raw_waypoints:
                if (
                    self.model.grid.is_walkable(*raw)
                    and self.model.grid.get_cell_type(*raw) not in _WH_TYPES
                ):
                    search_waypoints.append(raw)
                    continue
                found = None
                for r in range(1, max(W, H)):
                    for dx in range(-r, r + 1):
                        for dy in range(-r, r + 1):
                            if abs(dx) != r and abs(dy) != r:
                                continue  # inner cells already checked at smaller r
                            cx, cy = raw[0] + dx, raw[1] + dy
                            if not (0 <= cx < W and 0 <= cy < H):
                                continue
                            if self.model.grid.get_cell_type(cx, cy) in _WH_TYPES:
                                continue
                            if not self.model.grid.is_walkable(cx, cy):
                                continue
                            found = (cx, cy)
                            break
                        if found:
                            break
                    if found:
                        break
                search_waypoints.append(found or raw)

            cs = self.model.current_step
            # Skip waypoints that are unreachable (blacklisted) or already reached.
            # Try at most one full cycle so we don't spin forever if all are blocked.
            for _ in range(len(search_waypoints)):
                wp = search_waypoints[self._search_waypoint_idx % len(search_waypoints)]
                if wp is None:
                    self._search_waypoint_idx += 1
                    continue
                failed_step = self.unreachable_targets.get(wp, -1)
                still_blacklisted = failed_step != -1 and cs - failed_step < 200
                # Consider the waypoint reached when within a few cells.
                # Use at least 4 so the coordinator doesn't stall trying to
                # reach the exact cell in a congested area.
                already_reached = abs(my_pos[0] - wp[0]) + abs(my_pos[1] - wp[1]) <= max(
                    4, max_distance_from_agents
                )
                if still_blacklisted or already_reached:
                    self._search_waypoint_idx += 1
                    continue
                break  # found a reachable waypoint
            else:
                # All waypoints currently blocked — reset blacklist and try again
                # next step rather than idling forever.
                self.unreachable_targets.clear()
                self.state = AgentState.IDLE
                return
            wp = search_waypoints[self._search_waypoint_idx % len(search_waypoints)]
            print(
                f"{self.tag} SEARCH: no communicated agent positions — " f"heading to waypoint {wp}"
            )
            self.target_position = wp
            self.state = AgentState.EXPLORING
            return

        centroid_x = sum(pos[0] for pos in agent_positions) / len(agent_positions)
        centroid_y = sum(pos[1] for pos in agent_positions) / len(agent_positions)

        # Bias the centroid toward unassigned objects so the coordinator
        # positions itself where it can quickly relay new assignments
        # rather than trailing behind retrievers that are already busy.
        pending_obj_positions = [
            pos
            for pos in self.known_objects
            if pos not in self.objects_being_collected and pos not in self.assigned_tasks.values()
        ]
        if self._OBJECT_BIASED_CENTROID and pending_obj_positions:
            # Weighted average: (1 - bias) retriever centroid, bias object centroid
            obj_cx = sum(p[0] for p in pending_obj_positions) / len(pending_obj_positions)
            obj_cy = sum(p[1] for p in pending_obj_positions) / len(pending_obj_positions)
            centroid_x = (
                centroid_x * (1.0 - self._CENTROID_OBJECT_BIAS)
                + obj_cx * self._CENTROID_OBJECT_BIAS
            )
            centroid_y = (
                centroid_y * (1.0 - self._CENTROID_OBJECT_BIAS)
                + obj_cy * self._CENTROID_OBJECT_BIAS
            )

        centroid = (int(centroid_x), int(centroid_y))

        dist_from_centroid = abs(my_pos[0] - centroid[0]) + abs(my_pos[1] - centroid[1])

        if dist_from_centroid <= max_distance_from_agents:
            # Check if current position is a chokepoint (narrow corridor) that
            # might block other agents.  If so, slide to a nearby open cell
            # that is still within range of the centroid.
            if self._is_chokepoint(my_pos) or self._is_blocking_others(my_pos):
                open_cell = self._find_open_cell_near(
                    my_pos, centroid, max_distance_from_agents, _WH_TYPES
                )
                if open_cell:
                    self.target_position = open_cell
                    self.state = AgentState.EXPLORING
                    self._idle_steps = 0
                    return

            # Already well-positioned — nothing to do this step.
            # Increment boredom counter so persistent idling eventually triggers patrol.
            self._idle_steps += 1
            self.state = AgentState.IDLE
            return

        # Moving — reset boredom counter.
        self._idle_steps = 0

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
            f"{self.tag} REPOSITION: Too far from agents "
            f"(distance: {dist_from_centroid}), moving towards {reposition_target}"
        )
        self.target_position = reposition_target
        self.state = AgentState.EXPLORING

    def _is_chokepoint(self, pos: Tuple[int, int]) -> bool:
        """
        Return True if ``pos`` is a narrow corridor cell (≤ 2 walkable neighbours).
        Coordinators should not park at chokepoints because they block passage.
        """
        walkable = 0
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nx, ny = pos[0] + dx, pos[1] + dy
            if 0 <= nx < self.model.grid.width and 0 <= ny < self.model.grid.height:
                if self.model.grid.is_walkable(nx, ny):
                    walkable += 1
        return walkable <= 2

    def _is_blocking_others(self, pos: Tuple[int, int]) -> bool:
        """
        Return True if an adjacent agent appears to be stuck (waiting to pass through
        ``pos``).  Heuristic: another agent occupies a neighbouring cell AND has a
        stuck_counter ≥ 2, meaning it has been unable to move for multiple steps.
        """
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nx, ny = pos[0] + dx, pos[1] + dy
            if not (0 <= nx < self.model.grid.width and 0 <= ny < self.model.grid.height):
                continue
            for agent in self.model.agents:
                if agent.unique_id == self.unique_id or not agent.pos:
                    continue
                if pos_to_tuple(agent.pos) == (nx, ny):
                    if getattr(agent, "stuck_counter", 0) >= 2:
                        return True
        return False

    def _find_open_cell_near(
        self,
        origin: Tuple[int, int],
        centroid: Tuple[int, int],
        max_dist: int,
        wh_types: tuple,
    ) -> Optional[Tuple[int, int]]:
        """
        Find a non-chokepoint walkable cell near ``origin`` that is still within
        ``max_dist`` of ``centroid``.  Returns None if no suitable cell is found.
        """
        best: Optional[Tuple[int, int]] = None
        best_walkable = 0
        for r in range(1, 4):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if abs(dx) != r and abs(dy) != r:
                        continue
                    cx, cy = origin[0] + dx, origin[1] + dy
                    if not (0 <= cx < self.model.grid.width and 0 <= cy < self.model.grid.height):
                        continue
                    if self.model.grid.get_cell_type(cx, cy) in wh_types:
                        continue
                    if not self.model.grid.is_walkable(cx, cy):
                        continue
                    if not self.model.grid.is_cell_empty((cx, cy)):
                        continue
                    d = abs(cx - centroid[0]) + abs(cy - centroid[1])
                    if d > max_dist + 2:
                        continue
                    # Prefer cells with more walkable neighbours (open areas)
                    w = sum(
                        1
                        for ddx, ddy in [(1, 0), (-1, 0), (0, 1), (0, -1)]
                        if 0 <= cx + ddx < self.model.grid.width
                        and 0 <= cy + ddy < self.model.grid.height
                        and self.model.grid.is_walkable(cx + ddx, cy + ddy)
                    )
                    if w > best_walkable:
                        best_walkable = w
                        best = (cx, cy)
            if best is not None:
                break
        return best

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
            # Unblock warehouse doors immediately (base class check is overridden)
            if self.pos:
                idle_pos = pos_to_tuple(self.pos)
                idle_ct = self.model.grid.get_cell_type(*idle_pos)
                if idle_ct in (
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                ):
                    self._try_move_off_cell(avoid_warehouse=True)
                    return

            # If sitting at a chokepoint or blocking others, move aside
            if self.pos:
                idle_pos = pos_to_tuple(self.pos)
                if self._is_chokepoint(idle_pos) or self._is_blocking_others(idle_pos):
                    self._try_move_off_cell(avoid_warehouse=False)

    def _execute_recharge_step(self) -> None:
        """Sub-state machine: approach warehouse → recharge → exit."""
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)

        # --- initialise sub-machine ---
        if self._coord_wh_step is None:
            # Use congestion-aware warehouse selection (same as retrievers)
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
            self._coord_wh_station = station
            entrance = station.get("entrance")
            if entrance:
                self._coord_wh_step = "approach"
                self.target_position = entrance
                print(f"{self.tag} RECHARGE: heading to entrance {entrance}")
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
                        f"{self.tag} RECHARGE: energy sufficient "
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
                    print(f"{self.tag} RECHARGE: at entrance, joining queue at {queue_cell}")
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
                    print(f"{self.tag} RECHARGE TIMEOUT: cannot reach WH, aborting")
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
                    f"{self.tag} RECHARGE: full ({self.energy:.1f}), "
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
                print(f"{self.tag} RECHARGE: exited warehouse, resuming")
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
        print(f"{self.tag} ASSIGN: sending {len(self.tasks_to_assign)} task(s)")

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
                    known_objects={
                        pos: Stamped(val, self.known_objects_step.get(pos, 0))
                        for pos, val in self.known_objects.items()
                    },
                    objects_being_collected={
                        pos: Stamped(None, self.objects_being_collected_step.get(pos, 0))
                        for pos in self.objects_being_collected
                    },
                    retriever_positions={
                        rid: Stamped(tuple(p), self.retriever_positions_step.get(rid, 0))
                        for rid, p in self.retriever_positions.items()
                        if p
                    },
                )
                self.model.comm_manager.send_message(map_msg, [retriever_id])

            print(
                f"{self.tag} -> {agent_tag('retriever', retriever_id)}: "
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
            cs = self.model.current_step
            self.objects_being_collected.add(obj_pos)
            self.objects_being_collected_step[obj_pos] = cs
            self.known_objects.pop(obj_pos, None)
            self.known_objects_step.pop(obj_pos, None)
            self.known_objects_cleared[obj_pos] = cs
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
