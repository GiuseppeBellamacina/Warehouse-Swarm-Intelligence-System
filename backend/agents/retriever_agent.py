"""
Retriever Agent - Heavy lifter for object collection and delivery
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from backend.agents.base_agent import AgentState, BaseAgent, agent_tag, pos_to_tuple
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import (
    RetrieverEventMessage,
    TaskAssignmentMessage,
    TaskStatusMessage,
)
from backend.core.grid_manager import CellType

if TYPE_CHECKING:
    from backend.core.warehouse_model import WarehouseModel


class RetrieverAgent(BaseAgent):
    """
    Retriever agent: collects objects and delivers to warehouse.

    Decision priority (highest to lowest):
    P0 — Communicate status to nearby coordinators (non-blocking).
    P1 — Deliver if fully loaded or task queue empty while carrying something.
    P2 — Recharge if energy critically low (< 20 %).
    P3 — Execute next task in queue (coordinator-assigned or self-assigned).
    P4 — Self-assign from accumulated map knowledge when idle (hive-mind mode):
         scans the entire ``known_objects`` dict — populated both from direct
         vision AND from MapDataMessage exchanges — and claims the nearest
         unclaimed object using the shared CommunicationManager (atomic,
         race-safe).  Falls back to local exploration only if no known objects
         remain unclaimed.

    Hive-mind properties:
    - Shares map data with ALL nearby agents (scouts, coordinators, other
      retrievers) every communication step.
    - Reacts to received MapDataMessages: stale task_queue entries (objects
      that peers report as gone) are dropped and claims released immediately.
    - After delivery, immediately checks known_objects for a new target before
      falling back to random exploration.
    - Coordinates with peer retrievers via TaskStatusMessage to avoid
      duplicate pickup attempts; always uses try_claim_object() as the
      atomic final arbiter.

    Navigation rules:
    - Enters warehouse ONLY through entrance cell, exits ONLY through exit cell.
    - Deposits at deposit_cell; recharges at recharge_cell when energy < 80 %.
    - Reports newly spotted objects to nearby coordinators.
    """

    def __init__(
        self,
        unique_id: int,
        model: "WarehouseModel",
        vision_radius: int = 2,
        communication_radius: int = 2,
        max_energy: float = 500.0,
        speed: float = 1.0,
        carrying_capacity: int = 2,
        behavior: Optional[dict] = None,
    ):
        super().__init__(
            unique_id=unique_id,
            model=model,
            role="retriever",
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
        # All keys are guaranteed present by Pydantic (RetrieverBehaviorParams).
        if behavior is None:
            from backend.config.schemas import RetrieverBehaviorParams

            behavior = RetrieverBehaviorParams().model_dump()
        _b = behavior
        self._RECHARGE_THRESHOLD: float = _b["recharge_threshold"]
        self._STALE_CLAIM_AGE: int = _b["stale_claim_age"]
        self._EXPLORE_RETARGET: int = _b["explore_retarget_interval"]
        self._OPPORTUNISTIC_PICKUP: bool = _b["opportunistic_pickup"]
        self._TASK_QUEUE_REORDER: bool = _b["task_queue_reorder"]
        self._SELF_ASSIGN: bool = _b["self_assign_from_shared_map"]
        self._PEER_BROADCAST: bool = _b["peer_broadcast"]
        self._SMART_EXPLORE: bool = _b["smart_explore"]
        self._WH_CONGESTION_REROUTE: bool = _b["warehouse_congestion_reroute"]
        self._WH_CONGESTION_THRESHOLD: int = _b["warehouse_congestion_threshold"]
        self._JAM_PRIORITY: bool = _b["jam_priority"]
        self._AUTONOMOUS_PICKUP: bool = _b["autonomous_pickup"]

        self.carrying_capacity = carrying_capacity
        self.carrying_objects = 0
        self.total_delivered = 0
        self.state = AgentState.IDLE
        self.pathfinder = AStarPathfinder(model.grid)

        # Ordered task queue assigned by coordinator (list of object positions)
        self.task_queue: List[Tuple[int, int]] = []

        # Warehouse navigation sub-phases
        # Possible values: None | "approach" | "deposit_cell" | "recharge_cell" | "exit"
        self._wh_step: Optional[str] = None
        self._wh_station: Optional[Dict[str, Any]] = None
        self._wh_approach_steps: int = 0  # steps spent in "approach" phase
        self._wh_exit_stuck: int = 0  # steps truly stuck (position unchanged)
        self._wh_exit_last_pos: Optional[Tuple[int, int]] = None

        # Pending events to report to coordinator
        self.pending_events: List[str] = []

        # Newly spotted objects to report (coordinator decides whether to assign them)
        self.newly_spotted_objects: List[Tuple[int, int]] = []

        # Exploration target when idle
        self._explore_target: Optional[Tuple[int, int]] = None
        self._explore_steps: int = 0  # steps since last new explore target

        # Fruitless exploration tracking: how many steps since last productive
        # activity (pickup, delivery, or task assignment).  When this exceeds
        # _SEEK_INFO_INTERVAL the retriever heads toward the nearest known
        # coordinator/peer to exchange map data.
        self._fruitless_explore_steps: int = 0
        self._SEEK_INFO_INTERVAL: int = 40
        # Guard against seek-info infinite loops: count consecutive seeks
        # without productive outcome.  After _MAX_SEEK_ATTEMPTS, force
        # the retriever back to frontier exploration for one full interval.
        self._seek_info_attempts: int = 0
        self._MAX_SEEK_ATTEMPTS: int = 3

    # ------------------------------------------------------------------
    # Sense
    # ------------------------------------------------------------------

    def step_sense(self) -> None:
        """Perceive, track new objects for reporting to coordinator."""
        old_objects = set(self.known_objects.keys())
        super().step_sense()
        new_objects = set(self.known_objects.keys())
        # Queue newly visible objects for coordinator notification
        for pos in new_objects - old_objects:
            if pos not in self.task_queue:
                self.newly_spotted_objects.append(pos)

    # ------------------------------------------------------------------
    # Communicate  (base class: map share + mailbox drain)
    # ------------------------------------------------------------------

    def process_received_messages(self) -> None:
        """Handle TaskAssignmentMessage from coordinator and peer TaskStatusMessages."""
        super().process_received_messages()

        # --- Stale-target cancellation -------------------------------------------
        # super() has already merged incoming MapDataMessages and pruned known_objects
        # for cells that peers report as no longer carrying an object.  Mirror that
        # same pruning onto our task_queue: any queued position that is no longer
        # present on the actual grid is invalid — release the claim immediately so
        # another agent (or a re-assigned coordinator task) can take it.
        stale_tasks = [t for t in list(self.task_queue) if t not in self.known_objects]
        for stale in stale_tasks:
            self.task_queue.remove(stale)
            self.model.comm_manager.release_claim(stale, self.unique_id)
            if stale in self.known_objects:
                del self.known_objects[stale]
            # If we were physically heading to this object, abort the movement
            if self.target_position == stale:
                self.target_position = None
                self.path = []
            print(
                f"{self.tag} MAP-PRUNE: cancelled stale task {stale} "
                f"(object no longer on grid — learned via map share)"
            )
        # -------------------------------------------------------------------------

        for message in self._step_messages:
            if isinstance(message, TaskAssignmentMessage):
                if message.target_id == self.unique_id:
                    target = message.target_position
                    if target is None:
                        continue
                    # Add to task queue only if not already there and not picked up
                    if target not in self.task_queue and target in self.known_objects:
                        self.task_queue.append(target)
                        self._fruitless_explore_steps = 0
                        print(
                            f"{self.tag} <- {agent_tag('coordinator', message.sender_id)}: "
                            f"queued task {target} "
                            f"(queue depth={len(self.task_queue)})"
                        )
                        self.log_message(
                            direction="received",
                            message_type="task_assignment",
                            details=f"Retrieve at {target}",
                            target_ids=[message.sender_id],
                        )
                    elif target not in self.known_objects:
                        # Coordinator told us about an object we don't know
                        self.known_objects[target] = message.priority
                        if target not in self.task_queue:
                            self.task_queue.append(target)
                        print(
                            f"{self.tag} <- {agent_tag('coordinator', message.sender_id)}: "
                            f"queued unknown task {target}"
                        )

            elif isinstance(message, TaskStatusMessage):
                # Peer retriever broadcast its queue after a self/peer-assign.
                # Prune any of their tasks from our own task_queue if we haven't
                # claimed them yet, and release the claim so the peer keeps it.
                peer_id = message.sender_id
                if peer_id == self.unique_id:
                    continue  # own echo
                # Check we're receiving from a retriever (not a coordinator)
                sender_agent = next((a for a in self.model.agents if a.unique_id == peer_id), None)
                if sender_agent is None or getattr(sender_agent, "role", None) != "retriever":
                    continue
                for peer_task in message.task_queue:
                    if peer_task in self.task_queue:
                        # Peer already claimed this — drop it from our queue
                        self.task_queue.remove(peer_task)
                        self.model.comm_manager.release_claim(peer_task, self.unique_id)
                        print(
                            f"{self.tag} PEER-YIELD: "
                            f"dropped {peer_task} (peer {peer_id} has it)"
                        )

            elif isinstance(message, RetrieverEventMessage):
                # React to "object_spotted" and "cargo_dropped" broadcasts from
                # PEER retrievers.  Add the object to known_objects so P4 (or P3b
                # opportunistic) can self-assign to it in the next decide phase.
                if message.event_type not in ("object_spotted", "cargo_dropped"):
                    continue
                peer_id = message.sender_id
                if peer_id == self.unique_id:
                    continue  # own echo
                sender_agent = next((a for a in self.model.agents if a.unique_id == peer_id), None)
                if sender_agent is None or getattr(sender_agent, "role", None) != "retriever":
                    continue  # only handle peer-retriever broadcasts here
                obj_pos = message.object_position
                if obj_pos is None:
                    continue
                # Only insert if not already known and not tombstoned as gone
                if (
                    obj_pos not in self.known_objects
                    and message.timestamp > self.known_objects_cleared.get(obj_pos, -1)
                ):
                    obc = getattr(self, "objects_being_collected", None)
                    if obc is None or obj_pos not in obc:
                        self.known_objects[obj_pos] = 1.0
                        self.known_objects_step[obj_pos] = message.timestamp
                        print(
                            f"{self.tag} PEER-SPOT: learned object {obj_pos} "
                            f"from full peer retriever {peer_id} — adding to known_objects"
                        )

    # ------------------------------------------------------------------
    # Decide
    # ------------------------------------------------------------------

    def step_decide(self) -> None:
        """Priority-based decision: communicate > deliver > recharge > retrieve > explore."""
        self.should_communicate_this_step = False

        # ---- P0: Flag status communication to any nearby coordinator ----
        # Does NOT return — the retriever still evaluates and executes other
        # priorities this step. Communication happens at the start of step_act.
        nearby = self.get_nearby_agents(self.communication_radius)
        coordinators = [a for a in nearby if getattr(a, "role", None) == "coordinator"]
        if coordinators:
            self.should_communicate_this_step = True

        # ---- P1: Deliver if carrying objects and warehouse sequence not started ----
        # Only head to the warehouse when:
        #   (a) fully loaded, OR
        #   (b) carrying something but the task queue is exhausted (nothing left to pick up)
        if self.carrying_objects > 0 and self._wh_step is None:
            if self.carrying_objects >= self.carrying_capacity:
                # Release ALL remaining claims immediately so peer retrievers
                # can pick them up without waiting for the 50-step stale timeout.
                for remaining_task in list(self.task_queue):
                    self.model.comm_manager.release_claim(remaining_task, self.unique_id)
                    print(
                        f"{self.tag} FULL-RELEASE: releasing claim {remaining_task} "
                        f"(at capacity, offering to peers)"
                    )
                self.task_queue.clear()
                self._start_warehouse_sequence("deliver")
                return
            elif not self.task_queue:
                # Before committing to the warehouse, try to self-assign one
                # more object — the retriever may have learned about new
                # objects from peers while travelling.  This avoids a wasted
                # trip when there are still nearby unclaimed packages.
                if (
                    self.carrying_objects < self.carrying_capacity
                    and (self._SELF_ASSIGN or self._AUTONOMOUS_PICKUP)
                    and self._try_self_assign_visible()
                ):
                    pass  # claimed something — fall through to P3
                else:
                    self._start_warehouse_sequence("deliver")
                    return
            # else: still have capacity AND queued tasks — fall through to P3

        # ---- P2: Recharge if energy critically low ----
        if self.energy < self.max_energy * self._RECHARGE_THRESHOLD and self._wh_step is None:
            self._start_warehouse_sequence("recharge")
            return

        # ---- P3: Execute next task in queue ----
        # Guard: never re-decide tasks while inside a warehouse sub-sequence
        if (
            self.task_queue
            and self.carrying_objects < self.carrying_capacity
            and self._wh_step is None
        ):
            # Re-sort queue by Manhattan distance so the nearest task is always first.
            # This avoids FIFO suboptimality: a closer task added later should be
            # picked up before a farther task added earlier.
            if self._TASK_QUEUE_REORDER:
                pos_tuple = pos_to_tuple(self.pos) if self.pos else (0, 0)
                self.task_queue.sort(
                    key=lambda t: abs(t[0] - pos_tuple[0]) + abs(t[1] - pos_tuple[1])
                )

            # ── Purge unreachable targets before selecting ──────────
            # Loop/stuck detection blacklists positions into unreachable_targets.
            # Re-queue them only after a cooldown so the agent doesn't oscillate.
            _UNREACHABLE_COOLDOWN = 20
            self.task_queue = [
                t
                for t in self.task_queue
                if t not in self.unreachable_targets
                or self.model.current_step - self.unreachable_targets[t] > _UNREACHABLE_COOLDOWN
            ]
            if self.task_queue:
                next_target = self.task_queue[0]
                # Skip if object is no longer in our knowledge base
                if next_target not in self.known_objects:
                    self.task_queue.pop(0)
                    # Don't return — fall through to P4 so the retriever
                    # immediately self-assigns or explores instead of sitting
                    # idle for a full step with no target.
                else:
                    # Claim the object
                    pos_tuple = pos_to_tuple(self.pos) if self.pos else (0, 0)
                    distance = abs(next_target[0] - pos_tuple[0]) + abs(
                        next_target[1] - pos_tuple[1]
                    )
                    can_claim = self.model.comm_manager.try_claim_object(
                        next_target, self.unique_id, self.model.current_step, distance, self.energy
                    )
                    if can_claim:
                        self.state = AgentState.RETRIEVING
                        if self.target_position != next_target:
                            self.target_position = next_target
                            self.path = []  # invalidate cached path — new target
                        print(f"{self.tag} CLAIM: heading to {next_target} " f"(dist={distance})")
                    else:
                        # Object claimed by someone else, abort
                        print(
                            f"{self.tag} SKIP: task {next_target} "
                            f"already claimed, removing from queue"
                        )
                        self.task_queue.pop(0)
                        self.model.comm_manager.release_claim(next_target, self.unique_id)
                    # ---- P3b: opportunistic nearby objects while travelling ----
                    # Even if we already have a primary task, try to claim unclaimed objects
                    # that are close by and fit in the remaining carrying slots.
                    if self.state == AgentState.RETRIEVING and self._wh_step is None:
                        if self._OPPORTUNISTIC_PICKUP or self._AUTONOMOUS_PICKUP:
                            self._try_opportunistic_pickup()
                    return
            # else: task_queue emptied by unreachable purge — fall through to P4

        # ---- P4: No tasks — self-assign from full known_objects map before exploring ----
        # Uses the entire accumulated knowledge base (vision + shared map from all
        # nearby agents), not just currently visible cells.  This is the "hive-mind"
        # behaviour: if any peer has spotted objects and shared the info, the retriever
        # will proactively head there without waiting for a coordinator assignment.
        #
        # When autonomous_pickup is enabled the retriever ALWAYS self-assigns
        # from known_objects — it behaves as if no coordinator exists, picking
        # up any object it knows about without waiting for an assignment.
        if self._wh_step is None:
            if (self._SELF_ASSIGN or self._AUTONOMOUS_PICKUP) and self._try_self_assign_visible():
                self._fruitless_explore_steps = 0
                return  # claimed something, P3 will handle it next step
            # No objects to claim — enter exploration
            import numpy as _np_p4
            _unk_count = int(_np_p4.count_nonzero(self.local_map == 0))
            _total = self.local_map.size
            _explored_pct = 100.0 * (1 - _unk_count / _total) if _total else 100.0
            print(
                f"{self.tag} P4-EXPLORE: no tasks, no self-assign → exploring "
                f"(map {_explored_pct:.0f}% explored, "
                f"{len(self.known_objects)} known objects, "
                f"carrying {self.carrying_objects}/{self.carrying_capacity})"
            )
            self._update_explore_target()

    def _try_opportunistic_pickup(self) -> None:
        """
        Scan known objects near the current position and try to self-claim any that
        are unclaimed, unqueued, and within communication_radius.  All objects in
        known_objects (direct vision OR map-share from peers) are evaluated with the
        same full communication_radius — no reduced or multi-tier distances.

        The claim goes through the shared CommunicationManager so it is race-safe.
        After claiming, the object is appended to task_queue and a TaskStatusMessage
        is broadcast so all peers see the updated queue immediately.
        """
        spare = self.carrying_capacity - self.carrying_objects - len(self.task_queue)
        if spare <= 0:
            return  # no room for extra items

        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)

        candidates: List[Tuple[int, Tuple[int, int]]] = []
        for obj_pos in list(self.known_objects.keys()):
            if obj_pos in self.task_queue:
                continue  # already queued
            dist = abs(obj_pos[0] - my_pos[0]) + abs(obj_pos[1] - my_pos[1])
            # Use vision_radius instead of communication_radius so the retriever
            # opportunistically claims objects it can see, not just touch.
            if dist <= self.vision_radius:
                candidates.append((dist, obj_pos))

        candidates.sort()  # closest first
        claimed = 0
        for dist, obj_pos in candidates:
            if claimed >= spare:
                break
            can_claim = self.model.comm_manager.try_claim_object(
                obj_pos, self.unique_id, self.model.current_step, dist, self.energy
            )
            if can_claim:
                self.task_queue.append(obj_pos)
                print(
                    f"{self.tag} OPP: claimed nearby {obj_pos} "
                    f"dist={dist} (queue depth={len(self.task_queue)})"
                )
                claimed += 1
            # If claim fails, object is already taken — nothing to do

        if claimed:
            # Force a status broadcast next act so coordinator sees the new queue ASAP
            self.should_communicate_this_step = True

    def _try_self_assign_visible(self) -> bool:
        """
        Autonomous self-assignment: claim the best known unclaimed object.

        Searches the entire ``known_objects`` map (populated both from direct vision
        AND from MapDataMessage exchanges with scouts/coordinators/peers), so the
        retriever can proactively pursue objects it has heard about from colleagues
        even when they are far away.  This is the "hive-mind" behaviour: the shared
        knowledge base is leveraged to avoid idle wandering.

        Three-layer safety against double-assignment:
          1. Global claim check  — skip objects already locked in CommunicationManager.
          2. Peer queue scan     — read task_queue of every nearby retriever directly;
             skip if any peer has the object queued (handles coordinator-assigned but
             not yet claimed items that haven't propagated through comm yet).
          3. Atomic try_claim    — CommunicationManager.try_claim_object() is the
             final arbiter for same-step ties (first caller wins).

        Candidates are sorted by Manhattan distance so the nearest unclaimed object
        is claimed first.  Multiple objects are claimed up to the spare carrying
        capacity.

        After a successful claim the retriever broadcasts a TaskStatusMessage to both
        nearby coordinators AND nearby retrievers, so all peers see the updated queue
        immediately and won't attempt to claim the same object.

        Returns:
            True if at least one object was claimed (caller skips exploration).
        """
        spare = self.carrying_capacity - self.carrying_objects - len(self.task_queue)
        if spare <= 0:
            return False

        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)

        # --- layer 3: collect all objects already queued by nearby retrievers ---
        nearby_agents = self.get_nearby_agents(self.communication_radius)
        peer_queued: Set[Tuple[int, int]] = set()
        for agent in nearby_agents:
            if getattr(agent, "role", None) == "retriever" and agent.unique_id != self.unique_id:
                for t in getattr(agent, "task_queue", []):
                    peer_queued.add(t)

        candidates: List[Tuple[int, Tuple[int, int]]] = []
        for obj_pos in list(self.known_objects.keys()):
            if obj_pos in self.task_queue:
                continue
            # layer 1: global claim check — skip only if the claim is FRESH.
            # A stale claim (>= 45 steps old with no refresh) indicates the
            # original claimer died, got stuck, or was reassigned.  In that case
            # fall through to try_claim_object which will atomically take it over.
            # If the claimer is ourselves, always fall through (refresh our claim).
            claimer = self.model.comm_manager.get_claimer(obj_pos)
            if claimer is not None and claimer != self.unique_id:
                claim_data = self.model.comm_manager.claimed_objects.get(obj_pos)
                if claim_data is not None:
                    _, claim_time, _ = claim_data
                    if self.model.current_step - claim_time < self._STALE_CLAIM_AGE:
                        continue  # fresh claim by someone else — skip
                # else: stale claim — fall through to let try_claim_object take over
            # layer 3: peer queue check
            if obj_pos in peer_queued:
                continue
            dist = abs(obj_pos[0] - my_pos[0]) + abs(obj_pos[1] - my_pos[1])
            # Scan ALL known objects — not limited to vision_radius.
            # The retriever uses its full accumulated knowledge so it never idles
            # when objects it has heard about from peers are still waiting.
            candidates.append((dist, obj_pos))

        if not candidates:
            return False

        candidates.sort()  # closest first
        claimed = 0
        for dist, obj_pos in candidates:
            if claimed >= spare:
                break
            # layer 4: atomic first-come-first-served claim
            can_claim = self.model.comm_manager.try_claim_object(
                obj_pos, self.unique_id, self.model.current_step, dist, self.energy
            )
            if can_claim:
                self.task_queue.append(obj_pos)
                print(
                    f"{self.tag} SELF-ASSIGN: claimed {obj_pos} "
                    f"dist={dist} from known_objects "
                    f"({'nearby' if dist <= self.vision_radius else 'remote via shared map'})"
                )
                claimed += 1

        if claimed:
            # Notify both coordinators and nearby retrievers so all peers see
            # the updated queue immediately (prevents redundant claims next step)
            self._broadcast_status_to_nearby(nearby_agents)
            return True
        return False

    def _broadcast_status_to_nearby(self, nearby_agents: Optional[List] = None) -> None:
        """
        Send TaskStatusMessage to all nearby agents (coordinators + retrievers).
        Used after a self/peer-assign so every neighbour immediately sees the
        updated task_queue and won't attempt to claim the same object.
        """
        if nearby_agents is None:
            nearby_agents = self.get_nearby_agents(self.communication_radius)

        target_ids = [
            a.unique_id
            for a in nearby_agents
            if getattr(a, "role", None) in ("coordinator", "retriever")
            and a.unique_id != self.unique_id
        ]
        if not target_ids:
            return

        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        # Keep our own entry in retriever_positions up to date so it gets
        # relayed through every agent that receives our MapDataMessage
        self.retriever_positions[self.unique_id] = my_pos
        self.retriever_positions_step[self.unique_id] = self.model.current_step
        status_msg = TaskStatusMessage(
            sender_id=self.unique_id,
            timestamp=self.model.current_step,
            retriever_id=self.unique_id,
            task_queue=list(self.task_queue),
            carrying_objects=self.carrying_objects,
            energy_level=self.energy,
            position=my_pos,
        )
        self.model.comm_manager.send_message(status_msg, target_ids)
        self.consume_energy(self.energy_consumption["communicate"])

    def _count_agents_heading_to(self, entrance: Tuple[int, int]) -> int:
        """Count retriever agents whose warehouse target entrance matches *entrance*."""
        count = 0
        for agent in self.model.agents:
            if getattr(agent, "role", None) != "retriever":
                continue
            wh = getattr(agent, "_wh_station", None)
            if wh and wh.get("entrance") == entrance:
                count += 1
        return count

    def _start_warehouse_sequence(self, purpose: str) -> None:
        """
        Begin entering the best warehouse for this agent.
        purpose = "deliver" | "recharge"

        Selection considers:
        - Proximity (nearest entrance wins by default)
        - Congestion (penalises stations already being targeted by other retrievers)
        - Visibility (prefer entrances already seen in local map; fall back to
          model-wide list if none known yet)
        """
        pos_tuple = pos_to_tuple(self.pos) if self.pos else (0, 0)

        # Collect all WAREHOUSE_ENTRANCE cells we have seen so far
        visible_entrances = [
            wh
            for wh in self.known_warehouses
            if self.model.grid.get_cell_type(*wh) == CellType.WAREHOUSE_ENTRANCE
        ]

        station = self.model.get_best_warehouse_for(
            pos=pos_tuple,
            known_entrances=visible_entrances,
            agent_energy=self.energy,
        )

        self._wh_station = station
        self._wh_step = "approach"
        self._wh_approach_steps = 0
        self._wh_exit_stuck = 0
        self._wh_known_wh_count = len(self.known_warehouses)
        self.target_position = station["entrance"]

        if purpose == "deliver":
            self.state = AgentState.DELIVERING
        else:
            self.state = AgentState.RECHARGING

        print(
            f"{self.tag} WH-SEQ: starting {purpose} → " f"entrance={self._wh_station['entrance']}"
        )

    def _update_explore_target(self) -> None:
        """Pick a new exploration target when idle (no tasks).

        Priority:
          0. Seek info — if exploring fruitlessly for too long, head toward
             the nearest coordinator or peer to exchange map data.
          1. Head toward the centroid of peers' last-known object sightings
             (via retriever_positions of peers that had objects) — best guess
             for where new objects might appear.
          2. Head toward the nearest UNKNOWN boundary in local_map — help
             expand explored territory instead of wandering randomly.
          3. Random walkable cell within 8 cells (original fallback).

        Anti-clustering: when a coordinator is nearby, targets are biased
        away from the coordinator so idle retrievers spread out instead of
        shadowing the coordinator.
        """
        pos_tuple = pos_to_tuple(self.pos) if self.pos else (0, 0)

        # Check if we've reached the current explore target OR if it was
        # abandoned (target_position cleared by stuck/loop detection in
        # move_towards).  Without this, _explore_target stays stale and
        # blocks re-evaluation for up to _EXPLORE_RETARGET steps, during
        # which the agent is completely frozen.
        if self._explore_target:
            if pos_tuple == self._explore_target or self.target_position is None:
                self._explore_target = None

        # Force immediate retarget when stuck next to another agent that is
        # heading in a conflicting direction — waiting the full
        # _EXPLORE_RETARGET interval only prolongs the jam.
        # Use threshold of 3 to avoid premature recalculation on a single
        # blocked step, which would reset the path every step and stall.
        if self._explore_target and self.stuck_counter >= 3:
            self._explore_target = None

        # Pick a new target every N steps or when we don't have one
        self._explore_steps += 1
        self._fruitless_explore_steps += 1
        if self._explore_target is None or self._explore_steps > self._EXPLORE_RETARGET:
            self._explore_steps = 0

            # --- Strategy 0: seek info from coordinator/peer ---
            # If the retriever has been exploring without productive
            # activity for too long, head toward the nearest known
            # coordinator (or any other agent) to exchange map data.
            # Once within communication range the normal MapDataMessage
            # exchange delivers discoveries that self-assign can use.
            _seek_interval = self._SEEK_INFO_INTERVAL

            # Gather nearby agent positions AND target positions for anti-clustering.
            # Include ALL agent types so two idle retrievers near each other are
            # pushed toward DIFFERENT frontiers.
            # Using *target* positions (where they're heading) is critical: two
            # agents can be at different cells but pick the same frontier if we
            # only penalise current positions.
            nearby_agents = self.get_nearby_agents(self.communication_radius)
            # If we found ANY nearby agents, map data was just exchanged —
            # reset fruitless counter so we don't keep seeking.
            # This check MUST run before the SEEK-INFO block below;
            # otherwise the SEEK-INFO `return` prevents the reset and
            # the agent loops in SEEK-INFO forever after arriving.
            if nearby_agents and self._fruitless_explore_steps > _seek_interval:
                self._fruitless_explore_steps = 0
                self._seek_info_attempts = 0  # productive contact — reset

            # Skip SEEK-INFO entirely when carrying objects — the retriever
            # should deliver cargo, not wander looking for info.
            # Also skip after too many consecutive seek attempts to break
            # the loop where the seek target moves away repeatedly.
            if (
                self._fruitless_explore_steps > _seek_interval
                and self.carrying_objects == 0
                and self._seek_info_attempts < self._MAX_SEEK_ATTEMPTS
            ):
                seek_target = self._find_nearest_info_source(pos_tuple)
                if seek_target is not None:
                    # If the target agent is inside a warehouse, redirect to a
                    # walkable cell just outside the entrance.  The retriever
                    # only needs communication proximity — not actual entry.
                    # Without this, move_towards would route through the
                    # entrance (Case 2) and the door-unblocking mechanism would
                    # push the agent off, causing an oscillation loop.
                    _WH_CELL_TYPES = {
                        CellType.WAREHOUSE,
                        CellType.WAREHOUSE_ENTRANCE,
                        CellType.WAREHOUSE_EXIT,
                    }
                    if self.model.grid.get_cell_type(*seek_target) in _WH_CELL_TYPES:
                        wh = self.model.get_nearest_warehouse_to(seek_target)
                        entrance = wh.get("entrance")
                        if entrance:
                            for dx, dy in [(0, -1), (-1, 0), (1, 0), (0, 1)]:
                                nx, ny = entrance[0] + dx, entrance[1] + dy
                                if self.model.grid.get_cell_type(nx, ny) == CellType.FREE:
                                    seek_target = (nx, ny)
                                    break
                    # Only re-assign if target actually changed — avoids
                    # clearing the cached path every single step.
                    if seek_target != self._explore_target:
                        self._explore_target = seek_target
                        self.target_position = seek_target
                        self.path = []
                        self.state = AgentState.EXPLORING
                        print(
                            f"{self.tag} SEEK-INFO: idle for "
                            f"{self._fruitless_explore_steps} steps, "
                            f"heading to agent near {seek_target} "
                            f"for map data exchange"
                        )
                    # Reset so the agent returns to frontier exploration
                    # after one retarget interval.  Without this the counter
                    # grows unboundedly and SEEK-INFO re-fires every 15 steps,
                    # trapping the agent for 100+ steps.
                    self._fruitless_explore_steps = 0
                    self._seek_info_attempts += 1
                    return
            nearby_positions = []
            for a in nearby_agents:
                if a.unique_id == self.unique_id or a.pos is None:
                    continue
                nearby_positions.append(pos_to_tuple(a.pos))
                # Also include their exploration/movement targets so the penalty
                # covers where they're GOING, not just where they ARE now.
                a_target = getattr(a, "_explore_target", None) or getattr(
                    a, "target_position", None
                )
                if a_target is not None:
                    nearby_positions.append((a_target[0], a_target[1]))

            # --- Strategy 1: head toward UNKNOWN boundary cells ---
            # If the retriever's local map still has unexplored areas, head there
            # to expand coverage and potentially spot new objects.
            # Uses FrontierExplorer to find clusters of frontier cells so the
            # retriever targets large unexplored regions instead of zigzagging
            # around the nearest boundary cell.

            # Expire old unreachable_targets so the dict doesn't grow
            # unbounded and blacklisted frontiers become available again.
            _cur_step = self.model.current_step
            for _p in [p for p, s in self.unreachable_targets.items() if _cur_step - s >= 200]:
                del self.unreachable_targets[_p]

            if self._SMART_EXPLORE:
                from backend.algorithms.exploration import FrontierExplorer

                frontiers = FrontierExplorer.find_frontiers(
                    self.local_map,
                    min_cluster_size=1,
                )
                if frontiers:
                    # Filter out warehouse cells, unwalkable centroids,
                    # and blacklisted unreachable targets so LOOP /
                    # NO-PROGRESS abandonment is respected.
                    _WH = (
                        CellType.WAREHOUSE,
                        CellType.WAREHOUSE_ENTRANCE,
                        CellType.WAREHOUSE_EXIT,
                    )
                    valid = [
                        f
                        for f in frontiers
                        if self.model.grid.is_walkable(*f[0])
                        and self.model.grid.get_cell_type(*f[0]) not in _WH
                        and f[0] not in self.unreachable_targets
                    ]
                    if valid:
                        # Early game: spread retrievers to different map
                        # quadrants.  At step 1 no agent has a target yet,
                        # so peer_targets is empty and scoring converges on
                        # the same centroid.  Detect clustering by agent
                        # positions instead.
                        if self.model.current_step <= 20:
                            peer_positions = [
                                pos_to_tuple(a.pos)
                                for a in nearby_agents
                                if a.unique_id != self.unique_id and a.pos is not None
                            ]
                            clustered = any(
                                abs(pp[0] - pos_tuple[0]) + abs(pp[1] - pos_tuple[1]) <= 8
                                for pp in peer_positions
                            )
                            if clustered:
                                W = self.model.grid.width
                                H = self.model.grid.height
                                corners = [
                                    (0, 0),
                                    (W - 1, 0),
                                    (0, H - 1),
                                    (W - 1, H - 1),
                                ]
                                # Each retriever picks a different corner
                                # based on its index among nearby retrievers
                                # (no global model access — only peers in
                                # communication range).
                                ret_ids = sorted(
                                    a.unique_id
                                    for a in nearby_agents
                                    if getattr(a, "role", None) == "retriever"
                                )
                                if self.unique_id not in ret_ids:
                                    ret_ids.append(self.unique_id)
                                    ret_ids.sort()
                                my_idx = ret_ids.index(self.unique_id)
                                corners.sort(
                                    key=lambda c: abs(c[0] - pos_tuple[0])
                                    + abs(c[1] - pos_tuple[1]),
                                    reverse=True,
                                )
                                target = corners[my_idx % len(corners)]
                                self._explore_target = target
                                self.target_position = target
                                self.path = []
                                self.state = AgentState.EXPLORING
                                print(
                                    f"{self.tag} EXPLORE: "
                                    f"spread to {target} "
                                    f"(cluster deconfliction)"
                                )
                                return

                        # Soft deconfliction: if another nearby retriever
                        # already targets a frontier within 6 cells, prefer
                        # frontiers further away.  If no alternative exists,
                        # fall through to normal scoring.
                        _DECONFLICT_DIST = 6
                        peer_targets: list = []
                        for a in nearby_agents:
                            if a.unique_id == self.unique_id:
                                continue
                            pt = getattr(a, "_explore_target", None) or getattr(
                                a, "target_position", None
                            )
                            if pt is not None:
                                peer_targets.append((pt[0], pt[1]))

                        if peer_targets:
                            deconf_valid = [
                                f
                                for f in valid
                                if all(
                                    abs(f[0][0] - pt[0]) + abs(f[0][1] - pt[1]) > _DECONFLICT_DIST
                                    for pt in peer_targets
                                )
                            ]
                            if deconf_valid:
                                valid = deconf_valid

                        # Coverage callback: ratio of explored cells in
                        # a window around each frontier centroid.  Frontiers
                        # in largely-unseen areas get a higher score.
                        # Blended approach: personal vision counts fully,
                        # communicated terrain (local_map non-zero but
                        # vision_explored=0) counts partially.
                        import numpy as np

                        _H, _W = self.local_map.shape
                        # Communicated-cell weight — how much to count
                        # terrain learned from peers vs personally seen.
                        # Detect scout presence from nearby agents (no
                        # global model access).
                        _has_scouts = any(
                            getattr(a, "role", None) == "scout"
                            for a in nearby_agents
                        )
                        _comm_weight = 0.6 if _has_scouts else 1.0

                        def _explored_ratio(fx: int, fy: int) -> float:
                            r = 5
                            y0, y1 = max(0, fy - r), min(_H, fy + r + 1)
                            x0, x1 = max(0, fx - r), min(_W, fx + r + 1)
                            vis = self.vision_explored[y0:y1, x0:x1]
                            comm = (self.local_map[y0:y1, x0:x1] != 0).astype(np.float32)
                            blended = np.maximum(vis.astype(np.float32), comm * _comm_weight)
                            total = blended.size
                            return float(np.sum(blended) / total) if total else 1.0

                        # Build global peer-target list for area division.
                        # Only include targets from non-retriever agents
                        # (scouts, coordinators) — retriever-to-retriever
                        # deconfliction is handled by the local
                        # _DECONFLICT_DIST filter and nearby_positions penalty.
                        _cs = self.model.current_step
                        # Filter peer targets to non-retriever agents only
                        # using nearby agent info (no global model access).
                        _nearby_retriever_ids = {
                            a.unique_id
                            for a in nearby_agents
                            if getattr(a, "role", None) == "retriever"
                        }
                        _nearby_retriever_ids.add(self.unique_id)
                        _global_targets = [
                            pos
                            for aid, pos in self.peer_explore_targets.items()
                            if aid not in _nearby_retriever_ids
                            and _cs - self.peer_explore_targets_step.get(aid, 0)
                            <= self._explore_target_ttl
                        ]

                        # Dampen momentum after 6 steps on the same target
                        # so stale unreachable targets lose their bonus and
                        # other frontiers can win on raw utility.
                        _momentum_target = (
                            self._explore_target if self._explore_steps <= 6 else None
                        )
                        best = FrontierExplorer.select_best_frontier(
                            valid,
                            pos_tuple,
                            nearby_positions,
                            grid_size=(self.model.grid.width, self.model.grid.height),
                            explored_ratio_at=_explored_ratio,
                            all_peer_targets=_global_targets,
                            current_target=_momentum_target,
                        )
                        if best:
                            old_target = self._explore_target
                            self._explore_target = best
                            self.target_position = best
                            if old_target != best:
                                self.path = []
                                print(f"{self.tag} EXPLORE: " f"frontier target {best}")
                            self.state = AgentState.EXPLORING
                            return

            # --- Strategy 2: centroid-biased walkable cell (fallback) ---
            # Bias toward the centroid of all unknown (unexplored) cells so the
            # retriever naturally drifts toward the least-covered region of the map.
            import numpy as np

            _unk_mask = self.local_map == 0

            unk_coords = np.argwhere(_unk_mask)
            # Compute centroid of unknown area (or map centre if fully explored)
            if len(unk_coords) > 0:
                centroid_x = float(np.mean(unk_coords[:, 0]))
                centroid_y = float(np.mean(unk_coords[:, 1]))
            else:
                centroid_x = self.model.grid.width / 2.0
                centroid_y = self.model.grid.height / 2.0

            candidates = []
            _BAD = (
                CellType.OBSTACLE,
                CellType.WAREHOUSE,
                CellType.WAREHOUSE_ENTRANCE,
                CellType.WAREHOUSE_EXIT,
            )
            for dx in range(-12, 13):
                for dy in range(-12, 13):
                    cx, cy = pos_tuple[0] + dx, pos_tuple[1] + dy
                    if 0 <= cx < self.model.grid.width and 0 <= cy < self.model.grid.height:
                        ct = self.model.grid.get_cell_type(cx, cy)
                        if ct not in _BAD and self.model.grid.is_walkable(cx, cy):
                            candidates.append((cx, cy))

            if candidates:
                # Score: prefer cells closer to unknown centroid (lower = better)
                # with a secondary penalty to keep agents spread apart.
                def _score(cell: tuple) -> float:
                    cdist = abs(cell[0] - centroid_x) + abs(cell[1] - centroid_y)
                    agent_penalty = 0.0
                    if nearby_positions:
                        min_ad = min(
                            abs(cell[0] - ap[0]) + abs(cell[1] - ap[1]) for ap in nearby_positions
                        )
                        agent_penalty = max(0, 8 - min_ad)  # penalty when close to peers
                    return cdist + agent_penalty

                candidates.sort(key=_score)
                old_target = self._explore_target
                self._explore_target = candidates[0]
                self.target_position = self._explore_target
                if old_target != self._explore_target:
                    self.path = []
                    print(f"{self.tag} EXPLORE: " f"centroid-biased target {self._explore_target}")
                self.state = AgentState.EXPLORING

            # --- Fallback 3: farthest walkable corner (never idle) ---
            # If the 25-cell search radius found nothing, head to the farthest
            # walkable corner of the map.  This guarantees the retriever NEVER
            # goes idle while the simulation is still running.
            else:
                corners = [
                    (0, 0),
                    (self.model.grid.width - 1, 0),
                    (0, self.model.grid.height - 1),
                    (self.model.grid.width - 1, self.model.grid.height - 1),
                ]
                best_corner = max(
                    corners,
                    key=lambda c: abs(c[0] - pos_tuple[0]) + abs(c[1] - pos_tuple[1]),
                )
                self._explore_target = best_corner
                self.target_position = best_corner
                self.path = []
                self.state = AgentState.EXPLORING
                print(f"{self.tag} EXPLORE: " f"corner fallback target {best_corner}")
        else:
            self.state = AgentState.EXPLORING
            # Safety net: if target_position was cleared by stuck/loop
            # detection while _explore_target still exists, re-apply it
            # so the agent doesn't sit still for one or more steps.
            if self.target_position is None and self._explore_target is not None:
                self.target_position = self._explore_target
                self.path = []

    def _find_nearest_info_source(self, my_pos: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """Find the nearest coordinator (preferred) or any other agent.

        Used by the seek-info strategy: when the retriever has been
        exploring fruitlessly, it heads toward another agent to trigger
        a MapDataMessage exchange and learn about recently discovered
        objects.

        Searches all agents visible to the model (not limited to comm
        range) — same approach the scout uses in its SEEK-COORD logic.
        Returns the position of the best candidate, or None.
        """
        best_pos: Optional[Tuple[int, int]] = None
        best_dist = float("inf")
        best_is_coord = False

        for agent in self.model.agents:
            if agent.unique_id == self.unique_id or agent.pos is None:
                continue
            a_pos = pos_to_tuple(agent.pos)
            dist = abs(a_pos[0] - my_pos[0]) + abs(a_pos[1] - my_pos[1])
            is_coord = getattr(agent, "role", None) == "coordinator"
            # Prefer coordinators; among same type, prefer closer
            if is_coord and not best_is_coord:
                best_pos, best_dist, best_is_coord = a_pos, dist, True
            elif is_coord == best_is_coord and dist < best_dist:
                best_pos, best_dist, best_is_coord = a_pos, dist, is_coord
        return best_pos

    # ------------------------------------------------------------------
    # Energy depletion: drop cargo
    # ------------------------------------------------------------------

    def step(self) -> None:
        """Override base step to drop cargo when energy is depleted."""
        if self.energy <= 0:
            if self.carrying_objects > 0:
                self._drop_cargo()
            return

        super().step()

    def _find_drop_cells(self, count: int) -> List[Tuple[int, int]]:
        """Find *count* valid cells to drop objects near the agent.

        Rules:
        - Must be walkable and FREE (no obstacles, warehouse cells, or
          existing objects).
        - Starts from the agent's own cell, then spirals outward.
        """
        pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        candidates: List[Tuple[int, int]] = []

        # Check own cell first
        ct = self.model.grid.get_cell_type(*pos)
        if ct == CellType.FREE and pos not in self.known_objects:
            candidates.append(pos)

        # Spiral outward in rings of increasing radius
        radius = 1
        while len(candidates) < count and radius <= 10:
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if abs(dx) != radius and abs(dy) != radius:
                        continue  # only perimeter of ring
                    nx, ny = pos[0] + dx, pos[1] + dy
                    if not self.model.grid.is_walkable(nx, ny):
                        continue
                    cell_t = self.model.grid.get_cell_type(nx, ny)
                    if cell_t != CellType.FREE:
                        continue
                    if (nx, ny) in self.known_objects:
                        continue
                    candidates.append((nx, ny))
                    if len(candidates) >= count:
                        break
                if len(candidates) >= count:
                    break
            radius += 1

        return candidates[:count]

    def _drop_cargo(self) -> None:
        """Drop all carried objects on valid nearby cells and broadcast to
        nearby agents so they can claim the dropped cargo."""
        if self.carrying_objects <= 0:
            return

        drop_cells = self._find_drop_cells(self.carrying_objects)
        dropped_positions: List[Tuple[int, int]] = []

        for cell in drop_cells:
            # Place object back on the grid
            self.model.grid.place_object(*cell)
            # Remove from retrieved_objects to keep bookkeeping consistent
            self.model.grid.retrieved_objects.discard(cell)
            # Add to our own knowledge so it propagates via MapDataMessage
            self.known_objects[cell] = 1.0
            self.known_objects_step[cell] = self.model.current_step
            dropped_positions.append(cell)
            self.carrying_objects -= 1

        # Reset move cost
        self.energy_consumption["move"] = 0.6

        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        print(
            f"{self.tag} CARGO DROP: energy depleted, "
            f"dropped {len(dropped_positions)} objects at {dropped_positions}"
        )

        # Broadcast dropped-object positions to all nearby agents
        nearby = self.get_nearby_agents(self.communication_radius)
        target_ids = [a.unique_id for a in nearby if a.unique_id != self.unique_id]

        if target_ids:
            for obj_pos in dropped_positions:
                drop_msg = RetrieverEventMessage(
                    sender_id=self.unique_id,
                    timestamp=self.model.current_step,
                    retriever_id=self.unique_id,
                    event_type="cargo_dropped",
                    position=my_pos,
                    object_position=obj_pos,
                    carrying_count=0,
                )
                self.model.comm_manager.send_message(drop_msg, target_ids)
            print(
                f"{self.tag} CARGO DROP: notified agents {target_ids} "
                f"about {len(dropped_positions)} dropped objects"
            )

    # ------------------------------------------------------------------
    # Act
    # ------------------------------------------------------------------

    def step_act(self) -> None:
        if self.energy <= 0:
            return

        # ── Door-unblocking ──────────────────────────────────────────────
        # If the retriever is sitting on a warehouse entrance/exit and is NOT
        # in a warehouse sub-sequence, move off immediately so it doesn't jam
        # the door for other agents.  Move AWAY from the warehouse interior
        # to avoid oscillating back toward the door.
        if self._wh_step is None and self.pos:
            _pos = pos_to_tuple(self.pos)
            _ct = self.model.grid.get_cell_type(*_pos)
            if _ct in (CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT):
                # Use the warehouse station as reference for the "away_from"
                # direction so the agent moves OUTSIDE.
                nearest_wh = self.model.get_nearest_warehouse_to(_pos)
                wh_center = nearest_wh.get("deposit_cell", nearest_wh.get("entrance", _pos))
                if self._try_move_off_cell(avoid_warehouse=True, away_from=wh_center):
                    self.path = []
                    return

        # OPTION 1b: Peer broadcast runs FIRST so newly_spotted_objects is still
        # populated before _send_status_to_coordinators() clears it.
        # When delivering (full), push spotted-object info to peer retrievers
        # even if no coordinator is nearby.  Peers can then self-assign immediately
        # rather than waiting for the next passive MapDataMessage relay.
        if self.newly_spotted_objects and self.carrying_objects >= self.carrying_capacity:
            if self._PEER_BROADCAST:
                self._broadcast_spotted_to_peers()

        # OPTION 1: Communicate status to coordinators (non-blocking — movement still runs).
        # Must run AFTER peer broadcast so both channels see the full spotted list
        # before _send_status_to_coordinators() clears newly_spotted_objects.
        if self.should_communicate_this_step:
            self._send_status_to_coordinators()

        # Consume both event lists here — all communication channels above have already
        # processed them.  Clearing unconditionally before the warehouse-step early-return
        # ensures neither list accumulates across steps regardless of which branch fires.
        # (When a coordinator was present, _send_status_to_coordinators already cleared
        # both lists; these are harmless no-ops in that case.  In retriever-only mode —
        # no coordinator ever nearby — pending_events would grow unbounded without this
        # unconditional drain, because _send_status_to_coordinators never fires.)
        self.newly_spotted_objects = []
        self.pending_events = []

        # OPTION 2: Handle warehouse sub-sequence
        if self._wh_step is not None and self._wh_station is not None:
            self._execute_warehouse_step()
            return

        # OPTION 3: Move toward current target
        if self.target_position:
            self.move_towards(self.target_position)
            my_pos = pos_to_tuple(self.pos) if self.pos else None

            # Arrived at retrieval target
            if self.state == AgentState.RETRIEVING and my_pos and my_pos == self.target_position:
                self._try_pickup_object()

        else:
            # No target — force exploration regardless of current state.
            # This catches dead-ends where state is RETRIEVING but the
            # target was cleared (stuck/loop detection, stale-task prune,
            # failed claim).  Without this, only IDLE/EXPLORING triggered
            # re-exploration and the agent would freeze.
            self._update_explore_target()

    def _execute_warehouse_step(self) -> None:
        """
        Drive the warehouse navigation sub-state machine.
        Sequence:
          approach → (at entrance) → deposit_cell OR recharge_cell
                   → (at interior cell) → action
                   → exit → (at exit) → done
        """
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        station = self._wh_station
        assert station is not None

        if self._wh_step == "approach":
            # ── Opportunistic detour during approach ──────────────────
            # If the retriever still has spare carrying capacity AND knows
            # about a nearby unclaimed object, abort the warehouse run,
            # pick up the object first, then resume delivery later.
            # This prevents wasted trips when objects are discovered (or
            # reported by peers) while the retriever is already heading
            # to the warehouse.
            if (
                self.carrying_objects < self.carrying_capacity
                and self.state == AgentState.DELIVERING
                and (self._OPPORTUNISTIC_PICKUP or self._AUTONOMOUS_PICKUP)
            ):
                self._try_opportunistic_pickup()
                if self.task_queue:
                    # Successfully claimed something — abort warehouse
                    # sequence and let P3 handle the new task next step.
                    print(
                        f"{self.tag} WH-DETOUR: aborting approach to "
                        f"pick up {self.task_queue[0]} en route"
                    )
                    self._wh_step = None
                    self._wh_station = None
                    self.state = AgentState.RETRIEVING
                    self.target_position = self.task_queue[0]
                    self.path = []
                    return

            entrance = station["entrance"]
            cell_type = self.model.grid.get_cell_type(*my_pos)
            # Transition once the agent is ON the entrance OR inside (interior).
            # WAREHOUSE_EXIT is NOT accepted: agents enter ONLY through the entrance.
            at_or_inside = (
                my_pos == entrance
                or cell_type == CellType.WAREHOUSE
                or cell_type == CellType.WAREHOUSE_ENTRANCE
            )
            if at_or_inside:
                self._wh_approach_steps = 0
                if self.carrying_objects > 0:
                    # Must deposit — head to deposit cell
                    self._wh_step = "deposit_cell"
                    interior = station["deposit_cell"]
                    self.target_position = interior
                    if my_pos != interior:
                        self.move_towards(interior)
                elif self.energy >= self.max_energy * 0.80:
                    # Enough energy — skip recharge entirely, exit immediately
                    print(
                        f"{self.tag} WH: energy sufficient "
                        f"({self.energy:.1f}/{self.max_energy}), skipping recharge"
                    )
                    self._wh_step = "exit"
                    self._wh_exit_stuck = 0
                    exit_cell = station["exit"]
                    self.target_position = exit_cell
                    if my_pos != exit_cell:
                        self.move_towards(exit_cell)
                else:
                    # Need recharge — join queue near exit (FIFO slot)
                    queue_cell = self.model.get_queue_slot(station)
                    self._wh_step = "recharge_cell"
                    self.target_position = queue_cell
                    if my_pos != queue_cell:
                        self.move_towards(queue_cell)
            else:
                self._wh_approach_steps += 1

                # ── Dynamic warehouse re-evaluation ────────────────────────
                # If the agent discovered new warehouses since the sequence
                # started, re-evaluate whether a closer one exists and switch.
                if len(self.known_warehouses) > self._wh_known_wh_count:
                    self._wh_known_wh_count = len(self.known_warehouses)
                    visible_entrances = [
                        wh
                        for wh in self.known_warehouses
                        if self.model.grid.get_cell_type(*wh) == CellType.WAREHOUSE_ENTRANCE
                    ]
                    better = self.model.get_best_warehouse_for(
                        pos=my_pos,
                        known_entrances=visible_entrances,
                        agent_energy=self.energy,
                    )
                    better_ent = better["entrance"]
                    if better_ent != entrance:
                        old_dist = self.model._path_distance(my_pos, entrance)
                        new_dist = self.model._path_distance(my_pos, better_ent)
                        if new_dist < old_dist:
                            print(
                                f"{self.tag} DYNAMIC-REROUTE: discovered closer "
                                f"warehouse {better_ent} (dist={new_dist:.0f}) "
                                f"vs current {entrance} (dist={old_dist:.0f})"
                            )
                            self._wh_station = better
                            self._wh_approach_steps = 0
                            self.target_position = better_ent
                            self.path = []
                            self.stuck_counter = 0
                            station = better
                            entrance = better_ent

                # ── Congestion reroute ─────────────────────────────────────
                # If the target warehouse is too crowded and there is an
                # alternative with less congestion, reroute — provided the
                # agent has enough energy to reach it.
                if self._WH_CONGESTION_REROUTE and self._wh_approach_steps >= 6:
                    heading_count = self._count_agents_heading_to(entrance)
                    if heading_count >= self._WH_CONGESTION_THRESHOLD:
                        alt = self.model.get_best_warehouse_for(
                            pos=my_pos,
                            known_entrances=[
                                wh
                                for wh in self.known_warehouses
                                if self.model.grid.get_cell_type(*wh) == CellType.WAREHOUSE_ENTRANCE
                            ],
                            excluded_entrance=entrance,
                            agent_energy=self.energy,
                        )
                        alt_ent = alt["entrance"]
                        if alt_ent != entrance:
                            alt_heading = self._count_agents_heading_to(alt_ent)
                            if alt_heading < heading_count:
                                print(
                                    f"{self.tag} REROUTE: {entrance} congested "
                                    f"({heading_count} agents) → {alt_ent} "
                                    f"({alt_heading} agents)"
                                )
                                self._wh_station = alt
                                self._wh_approach_steps = 0
                                self.target_position = alt_ent
                                self.path = []
                                self.stuck_counter = 0

                # If the entrance cell is occupied, ask the blocker to move
                blocker = self._get_agent_at_pos(entrance)
                if blocker is not None:
                    self._send_clear_way_request(entrance, blocker)
                self.move_towards(entrance)

        elif self._wh_step == "deposit_cell":
            deposit = station["deposit_cell"]
            # Accept the target deposit cell OR any interior WAREHOUSE cell
            # (avoids deadlock when another agent is blocking the exact cell)
            cell_type = self.model.grid.get_cell_type(*my_pos)
            at_deposit = my_pos == deposit or cell_type == CellType.WAREHOUSE
            if at_deposit:
                # Deliver objects
                delivered = self.carrying_objects
                self.model.objects_retrieved += delivered
                self.total_delivered += delivered
                self.carrying_objects = 0
                self._fruitless_explore_steps = 0
                self.energy_consumption["move"] = 0.6  # normal move cost
                print(
                    f"{self.tag} DELIVERY: "
                    f"delivered {delivered} → total "
                    f"{self.model.objects_retrieved}/{self.model.total_objects}"
                )
                self.pending_events.append("object_delivered")
                # If low energy, recharge while still inside (use FIFO queue slot)
                if self.energy < self.max_energy * 0.80:
                    queue_cell = self.model.get_queue_slot(station)
                    self._wh_step = "recharge_cell"
                    self.target_position = queue_cell
                    self.state = AgentState.RECHARGING
                    # Move toward recharge immediately
                    if my_pos != queue_cell:
                        self.move_towards(queue_cell)
                else:
                    self._wh_step = "exit"
                    self._wh_exit_stuck = 0
                    self.target_position = station["exit"]
                    # Move toward exit immediately
                    if my_pos != station["exit"]:
                        self.move_towards(station["exit"])
            else:
                self.move_towards(deposit)

        elif self._wh_step == "recharge_cell":
            # target_position holds the assigned queue slot (set during approach transition)
            recharge = self.target_position or station["recharge_cell"]
            # Accept the target cell OR any interior WAREHOUSE cell
            cell_type = self.model.grid.get_cell_type(*my_pos)
            # Only recharge when exactly at the assigned queue slot
            # (removes the broad fallback that caused premature recharging on any interior cell)
            at_recharge = my_pos == recharge and cell_type not in (
                CellType.WAREHOUSE_ENTRANCE,
                CellType.WAREHOUSE_EXIT,
            )
            if at_recharge:
                # Recharge here
                rate = self.model.config.warehouse.recharge_rate
                self.recharge_energy(rate)
                if self.energy >= self.max_energy * 0.90:
                    print(f"{self.tag} RECHARGED: heading to exit")
                    self._wh_step = "exit"
                    self._wh_exit_stuck = 0
                    self.target_position = station["exit"]
                    self.state = AgentState.RECHARGING
                    # Move toward exit immediately
                    if my_pos != station["exit"]:
                        self.move_towards(station["exit"])
                # else stay and keep recharging
            else:
                self.move_towards(recharge)

        elif self._wh_step == "exit":
            exit_cell = station["exit"]
            cell_type = self.model.grid.get_cell_type(*my_pos)
            # Finish when on the exit cell or already outside (not inside WH).
            # Agents MUST exit through the exit cell — never through the entrance.
            left_wh = my_pos == exit_cell or cell_type not in (
                CellType.WAREHOUSE,
                CellType.WAREHOUSE_ENTRANCE,
                CellType.WAREHOUSE_EXIT,
            )
            if left_wh:
                print(f"{self.tag} EXIT: exited warehouse")
                # Save station ref before clearing — needed for move-off below
                exit_entrance = station.get("entrance") if station else None
                self._wh_step = None
                self._wh_station = None
                self._wh_exit_stuck = 0
                self._wh_exit_last_pos = None
                # Clear stale target/path BEFORE any branch sets a new one,
                # so the retriever never routes back to the exit cell.
                self.target_position = None
                self.path = []
                self.pending_events.append("idle")
                # If there are pending tasks, head there immediately instead of
                # exploring a random cell (which caused the "wander first" bug)
                if self.task_queue:
                    self.state = AgentState.RETRIEVING
                    self.target_position = self.task_queue[0]
                    self.path = []  # invalidate cached path — new target
                elif self._try_self_assign_visible():
                    # Proactively self-assign from accumulated map knowledge
                    # so the retriever never idles after delivery when objects
                    # are known but no coordinator is nearby to give orders.
                    self.state = AgentState.RETRIEVING
                    self.target_position = self.task_queue[0]
                    self.path = []
                else:
                    self.state = AgentState.EXPLORING
                    self._update_explore_target()
                # Move off the door cell immediately so it doesn't block.
                # Prefer stepping AWAY from the warehouse interior so we
                # don't oscillate back toward the entrance.
                if my_pos == exit_cell:
                    self._try_move_off_cell(avoid_warehouse=True, away_from=exit_entrance)
                    self.path = []
            else:
                # Track truly stuck steps (position unchanged)
                if my_pos == self._wh_exit_last_pos:
                    self._wh_exit_stuck += 1
                else:
                    self._wh_exit_stuck = 0
                self._wh_exit_last_pos = my_pos

                # Ask blocker on exit cell to move (advance notice)
                blocker = self._get_agent_at_pos(exit_cell)
                if blocker is not None:
                    self._send_clear_way_request(exit_cell, blocker)

                # When stuck for several steps, use cooperative negotiation
                # to resolve the jam — all nearby agents negotiate
                # non-conflicting moves based on cargo priority.
                if self._wh_exit_stuck >= 3:
                    self.path = []  # force replan
                    if self._cooperative_unstick(my_pos, exit_cell):
                        return  # negotiation moved us

                self.move_towards(exit_cell)

    # ------------------------------------------------------------------
    # Pickup
    # ------------------------------------------------------------------

    def _try_pickup_object(self) -> None:
        """Try to pick up the object at current position."""
        if self.carrying_objects >= self.carrying_capacity:
            self.state = AgentState.DELIVERING
            self._start_warehouse_sequence("deliver")
            return

        pos_tuple = pos_to_tuple(self.pos) if self.pos else None
        if pos_tuple and pos_tuple in self.model.grid.objects:
            success = self.model.grid.retrieve_object(*pos_tuple)
            if success:
                self.carrying_objects += 1
                self.energy_consumption["move"] = 0.6 + self.carrying_objects * 0.2
                print(
                    f"{self.tag} PICKUP: {pos_tuple} "
                    f"(carrying {self.carrying_objects}/{self.carrying_capacity})"
                )
                self.pending_events.append("object_picked")
                self._fruitless_explore_steps = 0
                self.model.comm_manager.release_claim(pos_tuple, self.unique_id)
                # Remove from task queue and tombstone the object so peers
                # don't re-add it via relay messages.
                if pos_tuple in self.task_queue:
                    self.task_queue.remove(pos_tuple)
                self.known_objects.pop(pos_tuple, None)
                self.known_objects_step.pop(pos_tuple, None)
                self.known_objects_cleared[pos_tuple] = self.model.current_step
                # Update local_map so we don't broadcast a stale OBJECT cell
                self.local_map[pos_tuple[1], pos_tuple[0]] = CellType.FREE

                if self.carrying_objects >= self.carrying_capacity:
                    # Release remaining claims so peers can grab those objects
                    # immediately without waiting for the stale-claim timeout.
                    for remaining_task in list(self.task_queue):
                        self.model.comm_manager.release_claim(remaining_task, self.unique_id)
                        print(
                            f"{self.tag} FULL-RELEASE: releasing claim {remaining_task} "
                            f"(full after pickup, offering to peers)"
                        )
                    self.task_queue.clear()
                    self.state = AgentState.DELIVERING
                    self._start_warehouse_sequence("deliver")
                else:
                    # Continue with next task in queue
                    if self.task_queue:
                        self.state = AgentState.RETRIEVING
                        self.target_position = self.task_queue[0]
                    else:
                        self.state = AgentState.EXPLORING
                        self.target_position = None
        else:
            # Object gone, remove from queue and tombstone
            if pos_tuple:
                self.model.comm_manager.release_claim(pos_tuple, self.unique_id)
                if pos_tuple in self.task_queue:
                    self.task_queue.remove(pos_tuple)
                self.known_objects.pop(pos_tuple, None)
                self.known_objects_step.pop(pos_tuple, None)
                self.known_objects_cleared[pos_tuple] = self.model.current_step
                self.local_map[pos_tuple[1], pos_tuple[0]] = CellType.FREE
            print(f"{self.tag} PICKUP: object gone at {pos_tuple}")
            self.state = AgentState.EXPLORING
            self.target_position = None

    # ------------------------------------------------------------------
    # Communication helpers
    # ------------------------------------------------------------------

    def _broadcast_spotted_to_peers(self, nearby_agents: Optional[List] = None) -> None:
        """
        When this retriever is full (or in delivery mode) and has spotted objects it
        cannot collect, explicitly notify nearby peer RETRIEVERS so they can react
        immediately without relying solely on the passive MapDataMessage relay.

        This complements ``_send_status_to_coordinators``, which only sends
        ``object_spotted`` events to coordinators.  If no coordinator is nearby the
        spotted objects would otherwise only propagate via MapDataMessage at the
        next communication step.
        """
        if not self.newly_spotted_objects:
            return
        if nearby_agents is None:
            nearby_agents = self.get_nearby_agents(self.communication_radius)
        peer_ids = [
            a.unique_id
            for a in nearby_agents
            if getattr(a, "role", None) == "retriever" and a.unique_id != self.unique_id
        ]
        if not peer_ids:
            return
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        for obj_pos in self.newly_spotted_objects:
            spot_msg = RetrieverEventMessage(
                sender_id=self.unique_id,
                timestamp=self.model.current_step,
                retriever_id=self.unique_id,
                event_type="object_spotted",
                position=my_pos,
                object_position=obj_pos,
                carrying_count=self.carrying_objects,
            )
            self.model.comm_manager.send_message(spot_msg, peer_ids)
            print(
                f"{self.tag} PEER-SPOT: full, broadcasting spotted {obj_pos} "
                f"to peer retrievers {peer_ids}"
            )
        self.consume_energy(self.energy_consumption["communicate"])

    def _send_status_to_coordinators(self) -> None:
        """Send TaskStatusMessage + pending events + spotted objects to nearby coordinators."""
        if not self.pos:
            return

        nearby = self.get_nearby_agents(self.communication_radius)
        coordinators = [a for a in nearby if getattr(a, "role", None) == "coordinator"]
        if not coordinators:
            self.should_communicate_this_step = False
            return

        coord_ids = [getattr(c, "unique_id", 0) for c in coordinators]
        my_pos = pos_to_tuple(self.pos)

        # Always send task status (anti-race-condition: declare tasks before assignment)
        status_msg = TaskStatusMessage(
            sender_id=self.unique_id,
            timestamp=self.model.current_step,
            retriever_id=self.unique_id,
            task_queue=list(self.task_queue),
            carrying_objects=self.carrying_objects,
            energy_level=self.energy,
            position=my_pos,
        )
        self.model.comm_manager.send_message(status_msg, coord_ids)

        # Send pending event messages
        for event_type in self.pending_events:
            event_msg = RetrieverEventMessage(
                sender_id=self.unique_id,
                timestamp=self.model.current_step,
                retriever_id=self.unique_id,
                event_type=event_type,
                position=my_pos,
                object_position=self.task_queue[0] if self.task_queue else None,
                carrying_count=self.carrying_objects,
            )
            self.model.comm_manager.send_message(event_msg, coord_ids)

        # Notify coordinator about newly spotted objects
        for obj_pos in self.newly_spotted_objects:
            spot_msg = RetrieverEventMessage(
                sender_id=self.unique_id,
                timestamp=self.model.current_step,
                retriever_id=self.unique_id,
                event_type="object_spotted",
                position=my_pos,
                object_position=obj_pos,
                carrying_count=self.carrying_objects,
            )
            self.model.comm_manager.send_message(spot_msg, coord_ids)

        if self.pending_events or self.newly_spotted_objects:
            print(
                f"{self.tag} -> COORD {coord_ids}: "
                f"status (queue={len(self.task_queue)}, "
                f"carrying={self.carrying_objects}, "
                f"events={self.pending_events}, "
                f"spotted={len(self.newly_spotted_objects)})"
            )

        self.log_message(
            direction="sent",
            message_type="task_status",
            details=(
                f"queue={len(self.task_queue)}, "
                f"carrying={self.carrying_objects}, "
                f"events={self.pending_events}"
            ),
            target_ids=coord_ids,
        )

        # Clear consumed lists
        self.pending_events = []
        self.newly_spotted_objects = []

        self.consume_energy(self.energy_consumption["communicate"])
        self.last_communication_step = self.model.current_step
