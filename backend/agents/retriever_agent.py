"""
Retriever Agent - Heavy lifter for object collection and delivery
"""

import random
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
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

    Rules:
    - NEVER decides autonomously to retrieve an object; only follows task_queue
      assigned by coordinator.
    - Always declares task_queue to nearby coordinators so they don't over-assign.
    - Enters warehouse ONLY through entrance cell, exits ONLY through exit cell.
    - Deposits at deposit_cell (nearest WH interior cell to entrance).
    - Recharges at recharge_cell (farthest WH interior cell from entrance).
    - When idle (no tasks), wanders locally via A* — does NOT return to warehouse.
    - Reports newly spotted objects to coordinator (coordinator decides pickup).
    """

    def __init__(
        self,
        unique_id: int,
        model: "WarehouseModel",
        vision_radius: int = 5,
        communication_radius: int = 15,
        max_energy: float = 100.0,
        speed: float = 1.0,
        carrying_capacity: int = 2,
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
                "base": 0.1,
                "move": 0.6,
                "communicate": 0.2,
            },
        )

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
        """Handle TaskAssignmentMessage from coordinator."""
        super().process_received_messages()

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
                            f"[RETRIEVER {self.unique_id}] <- [COORD "
                            f"{message.sender_id}]: queued task {target} "
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
                            f"[RETRIEVER {self.unique_id}] <- [COORD "
                            f"{message.sender_id}]: queued unknown task {target}"
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
            if self.carrying_objects >= self.carrying_capacity or not self.task_queue:
                self._start_warehouse_sequence("deliver")
                return
            # else: still have capacity AND queued tasks — fall through to P3

        # ---- P2: Recharge if energy critically low ----
        if self.energy < self.max_energy * 0.20 and self._wh_step is None:
            self._start_warehouse_sequence("recharge")
            return

        # ---- P3: Execute next task in queue ----
        # Guard: never re-decide tasks while inside a warehouse sub-sequence
        if self.task_queue and self.carrying_objects < self.carrying_capacity and self._wh_step is None:
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
            else:
                # Object claimed by someone else, abort
                print(
                    f"[RETRIEVER {self.unique_id}] SKIP: task {next_target} "
                    f"already claimed, removing from queue"
                )
                self.task_queue.pop(0)
                self.model.comm_manager.release_claim(next_target, self.unique_id)
            # ---- P3b: opportunistic nearby objects while travelling ----
            # Even if we already have a primary task, try to claim unclaimed objects
            # that are close by and fit in the remaining carrying slots.
            if self.state == AgentState.RETRIEVING and self._wh_step is None:
                self._try_opportunistic_pickup()
            return

        # ---- P4: No tasks — explore locally (don't go warehouse) ----
        if self._wh_step is None:
            self._update_explore_target()

    def _try_opportunistic_pickup(self) -> None:
        """
        Scan known objects near the current position and try to self-claim any that
        are unclaimed, unqueued, and within a small radius.  The claim goes through
        the shared CommunicationManager so it is race-safe: if another retriever or
        coordinator already locked the object, try_claim_object returns False and we
        skip it cleanly.

        After claiming, the object is appended to task_queue.  The next
        TaskStatusMessage broadcast (sent every communication step) will inform the
        coordinator, which then skips re-assigning that object.
        """
        spare = self.carrying_capacity - self.carrying_objects - len(self.task_queue)
        if spare <= 0:
            return  # no room for extra items

        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        # Radius: half of vision so we don't deviate far from the current path
        radius = max(2, self.vision_radius // 2)

        candidates: List[Tuple[int, Tuple[int, int]]] = []
        for obj_pos in list(self.known_objects.keys()):
            if obj_pos in self.task_queue:
                continue  # already queued
            if obj_pos not in self.model.grid.objects:
                continue  # already picked up by someone else
            dist = abs(obj_pos[0] - my_pos[0]) + abs(obj_pos[1] - my_pos[1])
            if dist <= radius:
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
                    f"[RETRIEVER {self.unique_id}] OPP: claimed nearby {obj_pos} "
                    f"dist={dist} (queue depth={len(self.task_queue)})"
                )
                claimed += 1
            # If claim fails, object is already taken — nothing to do

        if claimed:
            # Force a status broadcast next act so coordinator sees the new queue ASAP
            self.should_communicate_this_step = True

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
            wh for wh in self.known_warehouses
            if self.model.grid.get_cell_type(*wh) == CellType.WAREHOUSE_ENTRANCE
        ]

        station = self.model.get_best_warehouse_for(
            pos=pos_tuple,
            known_entrances=visible_entrances,
        )

        self._wh_station = station
        self._wh_step = "approach"
        self.target_position = station["entrance"]

        if purpose == "deliver":
            self.state = AgentState.DELIVERING
        else:
            self.state = AgentState.RECHARGING

        print(
            f"[RETRIEVER {self.unique_id}] WH-SEQ: starting {purpose} → "
            f"entrance={self._wh_station['entrance']}"
        )

    def _update_explore_target(self) -> None:
        """Pick a new local exploration A* target if current one is reached/stale."""
        pos_tuple = pos_to_tuple(self.pos) if self.pos else (0, 0)

        # Check if we've reached the current explore target
        if self._explore_target and pos_tuple == self._explore_target:
            self._explore_target = None

        # Pick a new target every 15 steps or when we don't have one
        self._explore_steps += 1
        if self._explore_target is None or self._explore_steps > 15:
            self._explore_steps = 0
            # Candidate cells: walkable, not warehouse, within 8 cells
            candidates = []
            for dx in range(-8, 9):
                for dy in range(-8, 9):
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
                self._explore_target = random.choice(candidates)
                self.target_position = self._explore_target
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

        # OPTION 1: Communicate status to coordinators (non-blocking — movement still runs)
        if self.should_communicate_this_step:
            self._send_status_to_coordinators()

        # OPTION 2: Handle warehouse sub-sequence
        if self._wh_step is not None and self._wh_station is not None:
            self._execute_warehouse_step()
            return

        # OPTION 3: Move toward current target
        if self.target_position:
            self.move_towards(self.target_position)
            my_pos = pos_to_tuple(self.pos) if self.pos else None

            # Arrived at retrieval target
            if (
                self.state == AgentState.RETRIEVING
                and my_pos
                and my_pos == self.target_position
            ):
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
                        f"[RETRIEVER {self.unique_id}] WH: energy sufficient "
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
                    f"[RETRIEVER {self.unique_id}] DELIVERY: "
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
            at_recharge = (
                my_pos == recharge
                and cell_type not in (
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                )
            )
            if at_recharge:
                # Recharge here
                rate = self.model.config.warehouse.recharge_rate
                self.recharge_energy(rate)
                if self.energy >= self.max_energy * 0.90:
                    print(f"[RETRIEVER {self.unique_id}] RECHARGED: heading to exit")
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
            left_wh = (
                my_pos == exit_cell
                or cell_type not in (
                    CellType.WAREHOUSE,
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                )
            )
            if left_wh:
                print(f"[RETRIEVER {self.unique_id}] EXIT: exited warehouse")
                self._wh_step = None
                self._wh_station = None
                self.pending_events.append("idle")
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
                    f"[RETRIEVER {self.unique_id}] PICKUP: {pos_tuple} "
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
                    # Clear remaining tasks (will need to deliver first)
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
            print(f"[RETRIEVER {self.unique_id}] PICKUP: object gone at {pos_tuple}")
            self.state = AgentState.EXPLORING
            self.target_position = None

    # ------------------------------------------------------------------
    # Communication helpers
    # ------------------------------------------------------------------

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
                f"[RETRIEVER {self.unique_id}] -> COORD {coord_ids}: "
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
