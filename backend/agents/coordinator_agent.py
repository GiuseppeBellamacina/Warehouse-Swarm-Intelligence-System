"""
Coordinator Agent - Strategic planner managing task assignments
"""

from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
from backend.algorithms.exploration import FrontierExplorer
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import (
    CoordinatorSyncMessage,
    ObjectLocationMessage,
    RetrieverEventMessage,
    TaskAssignmentMessage,
    TaskStatusMessage,
)

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
        vision_radius: int = 10,
        communication_radius: int = 25,
        max_energy: float = 120.0,
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
                "base": 0.12,  # Higher base (processing tasks)
                "move": 0.5,
                "communicate": 0.1,  # Lower comm cost (specialized)
            },
        )

        self.state = AgentState.IDLE
        self.pathfinder = AStarPathfinder(model.grid)

        # Task management (local to this coordinator)
        self.known_objects: Dict[Tuple[int, int], float] = {}
        self.assigned_tasks: Dict[int, Tuple[int, int]] = {}  # retriever_id -> object_pos
        self.available_retrievers: List[int] = []
        
        # Retriever state tracking (populated via TaskStatusMessage — authoritative)
        self.retriever_states: Dict[int, str] = {}        # retriever_id -> state str
        self.retriever_task_queues: Dict[int, List] = {}  # retriever_id -> declared queue
        self.retriever_carrying: Dict[int, int] = {}      # retriever_id -> carrying count
        self.retriever_energy: Dict[int, float] = {}      # retriever_id -> energy
        self.retriever_capacity: Dict[int, int] = {}      # retriever_id -> carrying_capacity
        self.retriever_positions: Dict[int, Tuple] = {}   # retriever_id -> position

        # Objects currently being collected (prevents double-assignment)
        self.objects_being_collected: Set[Tuple[int, int]] = set()

        # Hint: which retriever spotted each object (used for opportunistic assignment)
        self._spotted_by: Dict[Tuple[int, int], int] = {}
        
        # Communication flag
        self.tasks_to_assign: List[Tuple[int, Tuple[int, int], float]] = []
        
        # Coordinator sync: track last step we synced with each other coordinator
        self.last_sync_step: Dict[int, int] = {}

        # Track recharge attempts to avoid getting stuck
        self.recharge_attempt_start: Optional[int] = None

        # Warehouse recharge sub-state machine (analogous to retriever _wh_step)
        self._coord_wh_step: Optional[str] = None   # None | "approach" | "recharge" | "exit"
        self._coord_wh_station: Optional[Dict] = None

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

    def step_decide(self) -> None:
        """Decide on task assignments and own actions"""
        # Reset communication flag
        self.should_communicate_this_step = False
        self.tasks_to_assign = []
        
        # Check if need to recharge
        if self.energy < 50:
            if self.state != AgentState.RECHARGING:
                closest_wh = self.get_closest_warehouse()
                if closest_wh:
                    print(f"[COORD {self.unique_id}] LOW-E ({self.energy:.1f}), heading to WH {closest_wh}")
                    self.state = AgentState.RECHARGING
                    self.target_position = closest_wh
                    self.recharge_attempt_start = self.model.current_step
                    self.was_recharging_at_warehouse = False
            else:
                if self.recharge_attempt_start is not None:
                    steps_attempting = self.model.current_step - self.recharge_attempt_start
                    if steps_attempting > 50:
                        print(f"[COORD {self.unique_id}] EMERGENCY: cannot reach WH after {steps_attempting} steps")
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

        # Priority 2: Explore when idle
        if self.state in (AgentState.IDLE, AgentState.EXPLORING):
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

        # Build retriever positions (prefer freshly declared, fall back to direct read)
        retriever_info = {}
        for agent in self.model.agents:
            if getattr(agent, "role", None) != "retriever":
                continue
            rid = getattr(agent, "unique_id", None)
            if rid not in self.available_retrievers:
                continue
            pos = self.retriever_positions.get(rid)
            if pos is None and agent.pos:
                pos = pos_to_tuple(agent.pos)
            if pos:
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
        extra_slots: Dict[int, int] = {rid: info["free_slots"] for rid, info in retriever_info.items()}

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
        other_coords = [
            a for a in nearby if getattr(a, "role", None) == "coordinator"
        ]
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
            )
            self.model.comm_manager.send_message(sync_msg, [cid])
            print(
                f"[COORD {self.unique_id}] -> [COORD {cid}]: "
                f"sync ({len(self.known_objects)} objs)"
            )

    def _decide_exploration(self) -> None:
        """Decide exploration action when idle - stay near managed agents"""
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        
        # Calculate center of mass of managed agents (retrievers and nearby scouts)
        agent_positions = []
        for agent in self.model.agents:
            if agent.pos and hasattr(agent, "role"):
                role = getattr(agent, "role", None)
                # Include retrievers and scouts in calculation
                if role in ["retriever", "scout"]:
                    agent_pos = pos_to_tuple(agent.pos)
                    distance = abs(agent_pos[0] - my_pos[0]) + abs(agent_pos[1] - my_pos[1])
                    # Only consider agents within reasonable range (< 20 cells)
                    if distance < 20:
                        agent_positions.append(agent_pos)
        
        # Calculate centroid if we have agents nearby
        max_distance_from_agents = 12  # Maximum distance coordinator should be from agent centroid
        
        if len(agent_positions) > 0:
            centroid_x = sum(pos[0] for pos in agent_positions) / len(agent_positions)
            centroid_y = sum(pos[1] for pos in agent_positions) / len(agent_positions)
            centroid = (int(centroid_x), int(centroid_y))
            
            # Check distance from centroid
            dist_from_centroid = abs(my_pos[0] - centroid[0]) + abs(my_pos[1] - centroid[1])
            
            # If too far from agents, move back towards them
            if dist_from_centroid > max_distance_from_agents:
                print(f"[COORD {self.unique_id}] REPOSITION: Too far from agents (distance: {dist_from_centroid}), moving towards centroid {centroid}")
                self.target_position = centroid
                self.state = AgentState.EXPLORING
                return
        
        # Normal exploration but prefer staying near agents
        frontiers = FrontierExplorer.find_frontiers(self.local_map)

        if frontiers:
            nearby = self.get_nearby_agents()
            nearby_positions = [pos_to_tuple(a.pos) for a in nearby if a.pos]

            # Filter frontiers - prefer those not too far from agent centroid
            if len(agent_positions) > 0:
                centroid_x = sum(pos[0] for pos in agent_positions) / len(agent_positions)
                centroid_y = sum(pos[1] for pos in agent_positions) / len(agent_positions)
                centroid = (int(centroid_x), int(centroid_y))
                
                # Weight frontiers by distance from centroid (closer is better)
                weighted_frontiers = []
                for frontier_pos, cluster_size in frontiers:
                    dist_from_centroid = abs(frontier_pos[0] - centroid[0]) + abs(frontier_pos[1] - centroid[1])
                    # Penalize frontiers far from centroid
                    if dist_from_centroid < max_distance_from_agents * 1.5:
                        weighted_frontiers.append((frontier_pos, cluster_size))
                
                # Use weighted frontiers if any, otherwise use all
                frontiers = weighted_frontiers if weighted_frontiers else frontiers

            best_frontier = FrontierExplorer.select_best_frontier(
                frontiers, my_pos, nearby_positions
            )

            if best_frontier:
                # Only update target if significantly different or we don't have one
                if not self.target_position or abs(best_frontier[0] - self.target_position[0]) > 5 or abs(best_frontier[1] - self.target_position[1]) > 5:
                    self.target_position = best_frontier
                    self.path = []  # Clear old path
                self.state = AgentState.EXPLORING
            else:
                self.state = AgentState.IDLE
        else:
            # No frontiers, stay idle or random explore  
            if not self.target_position:
                self.state = AgentState.IDLE

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
            # Light random walk to maintain map coverage
            from backend.algorithms.exploration import RandomWalkExplorer
            my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
            new_pos = RandomWalkExplorer.get_random_walk_direction(
                my_pos,
                getattr(self, "idle_direction", None),
                self.model.grid,
                momentum=0.3,
            )
            if new_pos != my_pos:
                old_pos = my_pos
                self.move_towards(new_pos)
                my_pos_after = pos_to_tuple(self.pos) if self.pos else my_pos
                if my_pos_after != old_pos:
                    self.idle_direction = (
                        my_pos_after[0] - old_pos[0],
                        my_pos_after[1] - old_pos[1],
                    )

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
            if not entrance or my_pos == entrance or self.is_at_warehouse():
                # Reached warehouse area — proceed to recharge cell
                recharge_cell = station.get("recharge_cell") or my_pos
                self._coord_wh_step = "recharge"
                self.target_position = recharge_cell
                print(f"[COORD {self.unique_id}] RECHARGE: at entrance, moving to recharge cell {recharge_cell}")
            else:
                # Guard against getting stuck
                if self.recharge_attempt_start is not None:
                    steps = self.model.current_step - self.recharge_attempt_start
                    if steps > 60:
                        print(f"[COORD {self.unique_id}] RECHARGE TIMEOUT: cannot reach WH, aborting")
                        self._coord_wh_step = None
                        self._coord_wh_station = None
                        self.state = AgentState.IDLE
                        self.target_position = None
                        self.recharge_attempt_start = None
                        return
                self.move_towards(entrance)
            return

        # --- walk to recharge cell and recharge ---
        if self._coord_wh_step == "recharge":
            recharge_cell = station.get("recharge_cell") or my_pos
            if my_pos != recharge_cell:
                self.move_towards(recharge_cell)
                return
            # At recharge cell — recharge
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
            return

        # --- walk to exit cell ---
        if self._coord_wh_step == "exit":
            exit_cell = station.get("exit") or station.get("entrance")
            if not exit_cell or my_pos == exit_cell or not self.is_at_warehouse():
                # Reached exit or stepped outside warehouse
                print(f"[COORD {self.unique_id}] RECHARGE: exited warehouse, resuming")
                self._coord_wh_step = None
                self._coord_wh_station = None
                self.state = AgentState.IDLE
                self.target_position = None
                self.recharge_attempt_start = None
            else:
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
