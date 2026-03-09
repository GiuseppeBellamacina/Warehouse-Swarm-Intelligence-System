"""
Retriever Agent - Heavy lifter for object collection and delivery
"""

import random
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
        _b = behavior or {}
        self._RECHARGE_THRESHOLD: float = _b.get("recharge_threshold", 0.20)
        self._STALE_CLAIM_AGE: int = _b.get("stale_claim_age", 45)
        self._EXPLORE_RETARGET: int = _b.get("explore_retarget_interval", 15)
        self._OPPORTUNISTIC_PICKUP: bool = _b.get("opportunistic_pickup", True)
        self._TASK_QUEUE_REORDER: bool = _b.get("task_queue_reorder", True)
        self._SELF_ASSIGN: bool = _b.get("self_assign_from_shared_map", True)
        self._PEER_BROADCAST: bool = _b.get("peer_broadcast", True)
        self._SMART_EXPLORE: bool = _b.get("smart_explore", True)

        self.carrying_capacity = carrying_capacity
        self.carrying_objects = 0
        self.state = AgentState.IDLE
        self.pathfinder = AStarPathfinder(model.grid)

        # Ordered task queue assigned by coordinator (list of object positions)
        self.task_queue: List[Tuple[int, int]] = []

        # Warehouse navigation sub-phases
        # Possible values: None | "approach" | "deposit_cell" | "recharge_cell" | "exit"
        self._wh_step: Optional[str] = None
        self._wh_station: Optional[Dict[str, Any]] = None

        # Pending events to report to coordinator
        self.pending_events: List[str] = []

        # Newly spotted objects to report (coordinator decides whether to assign them)
        self.newly_spotted_objects: List[Tuple[int, int]] = []

        # Exploration target when idle
        self._explore_target: Optional[Tuple[int, int]] = None
        self._explore_steps: int = 0  # steps since last new explore target

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
        stale_tasks = [t for t in list(self.task_queue) if t not in self.model.grid.objects]
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
                # React to "object_spotted" broadcasts from PEER retrievers that are
                # full and cannot collect the cargo themselves.  Add the object to
                # known_objects so P4 (or P3b opportunistic) can self-assign to it
                # in the next decide phase.
                if message.event_type != "object_spotted":
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

            next_target = self.task_queue[0]
            # Skip if object is gone from the world
            if next_target not in self.model.grid.objects and next_target not in self.known_objects:
                self.task_queue.pop(0)
                return
            # Claim the object
            pos_tuple = pos_to_tuple(self.pos) if self.pos else (0, 0)
            distance = abs(next_target[0] - pos_tuple[0]) + abs(next_target[1] - pos_tuple[1])
            can_claim = self.model.comm_manager.try_claim_object(
                next_target, self.unique_id, self.model.current_step, distance, self.energy
            )
            if can_claim:
                self.state = AgentState.RETRIEVING
                self.target_position = next_target
                self.path = []  # invalidate cached path — new target
            else:
                # Object claimed by someone else, abort
                print(
                    f"{self.tag} SKIP: task {next_target} " f"already claimed, removing from queue"
                )
                self.task_queue.pop(0)
                self.model.comm_manager.release_claim(next_target, self.unique_id)
            # ---- P3b: opportunistic nearby objects while travelling ----
            # Even if we already have a primary task, try to claim unclaimed objects
            # that are close by and fit in the remaining carrying slots.
            if self.state == AgentState.RETRIEVING and self._wh_step is None:
                if self._OPPORTUNISTIC_PICKUP:
                    self._try_opportunistic_pickup()
            return

        # ---- P4: No tasks — self-assign from full known_objects map before exploring ----
        # Uses the entire accumulated knowledge base (vision + shared map from all
        # nearby agents), not just currently visible cells.  This is the hive-mind
        # behaviour: if any peer has spotted objects and shared the info, the retriever
        # will proactively head there without waiting for a coordinator assignment.
        if self._wh_step is None:
            if self._SELF_ASSIGN and self._try_self_assign_visible():
                return  # claimed something, P3 will handle it next step
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
            if obj_pos not in self.model.grid.objects:
                continue  # already picked up by someone else
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
          1. Grid truth check    — skip objects already gone from model.grid.objects.
          2. Global claim check  — skip objects already locked in CommunicationManager.
          3. Peer queue scan     — read task_queue of every nearby retriever directly;
             skip if any peer has the object queued (handles coordinator-assigned but
             not yet claimed items that haven't propagated through comm yet).
          4. Atomic try_claim    — CommunicationManager.try_claim_object() is the
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
            # layer 1: grid truth check (object may have been picked up already)
            if obj_pos not in self.model.grid.objects:
                # Prune stale entry so we don't keep re-checking it
                del self.known_objects[obj_pos]
                continue
            # layer 2: global claim check — skip only if the claim is FRESH.
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

        # Check if we've reached the current explore target
        if self._explore_target and pos_tuple == self._explore_target:
            self._explore_target = None

        # Pick a new target every 15 steps or when we don't have one
        self._explore_steps += 1
        if self._explore_target is None or self._explore_steps > self._EXPLORE_RETARGET:
            self._explore_steps = 0

            # Gather nearby coordinator positions for anti-clustering
            nearby_agents = self.get_nearby_agents(self.communication_radius)
            coord_positions = [
                pos_to_tuple(a.pos)
                for a in nearby_agents
                if getattr(a, "role", None) == "coordinator" and a.pos
            ]

            # --- Strategy 1: head toward UNKNOWN boundary cells ---
            # If the retriever's local map still has unexplored areas, head there
            # to expand coverage and potentially spot new objects.
            if self._SMART_EXPLORE:
                import numpy as _np

                unknown_mask = self.local_map == 0
                if _np.any(unknown_mask):
                    padded = _np.pad(self.local_map, 1, mode="constant", constant_values=0)
                    has_explored = _np.zeros_like(unknown_mask)
                    for dy, dx in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                        has_explored |= (
                            padded[
                                1 + dy : padded.shape[0] - 1 + dy, 1 + dx : padded.shape[1] - 1 + dx
                            ]
                            != 0
                        )
                    boundary = unknown_mask & has_explored
                    b_ys, b_xs = _np.where(boundary)
                    if len(b_ys) > 0:
                        dists = _np.abs(b_xs - pos_tuple[0]) + _np.abs(b_ys - pos_tuple[1])
                        if coord_positions:
                            # Anti-clustering: prefer boundary cells far from
                            # coordinators.  score = dist_from_me - 0.5 * dist_from_coord
                            # Lower score → better (close to me AND far from coord).
                            coord_bonus = _np.zeros(len(b_ys), dtype=_np.float64)
                            for cp in coord_positions:
                                coord_bonus += _np.abs(b_xs - cp[0]) + _np.abs(b_ys - cp[1])
                            scores = dists.astype(_np.float64) - 0.5 * coord_bonus
                            best_idx = int(_np.argmin(scores))
                        else:
                            best_idx = int(_np.argmin(dists))
                        target = (int(b_xs[best_idx]), int(b_ys[best_idx]))
                        if self.model.grid.is_walkable(*target):
                            self._explore_target = target
                            self.target_position = target
                            self.path = []
                            self.state = AgentState.EXPLORING
                            return

            # --- Strategy 2: random walkable cell (fallback) ---
            candidates = []
            for dx in range(-12, 13):
                for dy in range(-12, 13):
                    cx, cy = pos_tuple[0] + dx, pos_tuple[1] + dy
                    if 0 <= cx < self.model.grid.width and 0 <= cy < self.model.grid.height:
                        ct = self.model.grid.get_cell_type(cx, cy)
                        if ct not in (
                            CellType.OBSTACLE,
                            CellType.WAREHOUSE,
                            CellType.WAREHOUSE_ENTRANCE,
                            CellType.WAREHOUSE_EXIT,
                        ) and self.model.grid.is_walkable(cx, cy):
                            candidates.append((cx, cy))
            if candidates:
                if coord_positions:
                    # Pick the cell farthest from coordinators
                    def _min_coord_dist(cell: tuple) -> int:
                        return min(
                            abs(cell[0] - cp[0]) + abs(cell[1] - cp[1]) for cp in coord_positions
                        )

                    candidates.sort(key=_min_coord_dist, reverse=True)
                    self._explore_target = candidates[0]
                else:
                    self._explore_target = random.choice(candidates)
                self.target_position = self._explore_target
                self.path = []
                self.state = AgentState.EXPLORING
            else:
                self.state = AgentState.IDLE
                self.target_position = None
        else:
            self.state = AgentState.EXPLORING

    # ------------------------------------------------------------------
    # Act
    # ------------------------------------------------------------------

    def step_act(self) -> None:
        if self.energy <= 0:
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

        elif self.state == AgentState.IDLE:
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
            entrance = station["entrance"]
            cell_type = self.model.grid.get_cell_type(*my_pos)
            # Transition once the agent is ON the entrance OR inside (any WH cell).
            # Note: WAREHOUSE_ENTRANCE is NOT interior — we specifically check for that
            # case so the agent also moves off the entrance in the same step.
            at_or_inside = (
                my_pos == entrance
                or cell_type == CellType.WAREHOUSE
                or cell_type == CellType.WAREHOUSE_ENTRANCE
                or cell_type == CellType.WAREHOUSE_EXIT
            )
            if at_or_inside:
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
                self.carrying_objects = 0
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
            # Finish when on the exit cell OR when already outside (not inside WH)
            left_wh = my_pos == exit_cell or cell_type not in (
                CellType.WAREHOUSE,
                CellType.WAREHOUSE_ENTRANCE,
                CellType.WAREHOUSE_EXIT,
            )
            if left_wh:
                print(f"{self.tag} EXIT: exited warehouse")
                self._wh_step = None
                self._wh_station = None
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
                # Move off the exit cell immediately so it doesn't block
                if self.target_position and my_pos == exit_cell:
                    self.move_towards(self.target_position)
            else:
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
                self.model.comm_manager.release_claim(pos_tuple, self.unique_id)
                # Remove from task queue
                if pos_tuple in self.task_queue:
                    self.task_queue.remove(pos_tuple)
                if pos_tuple in self.known_objects:
                    del self.known_objects[pos_tuple]

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
            # Object gone, remove from queue
            if pos_tuple:
                self.model.comm_manager.release_claim(pos_tuple, self.unique_id)
                if pos_tuple in self.task_queue:
                    self.task_queue.remove(pos_tuple)
                if pos_tuple in self.known_objects:
                    del self.known_objects[pos_tuple]
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
