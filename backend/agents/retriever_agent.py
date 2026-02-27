"""
Retriever Agent - Heavy lifter for object collection and delivery
"""

from typing import List, Optional, Tuple, TYPE_CHECKING

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import TaskAssignmentMessage, RetrieverEventMessage
from backend.core.decision_maker import ActionType, UtilityFunctions
from backend.core.grid_manager import CellType

if TYPE_CHECKING:
    from backend.core.warehouse_model import WarehouseModel


class RetrieverAgent(BaseAgent):
    """
    Retriever agent specialized in object collection and delivery

    Characteristics:
    - Carrying capacity for objects
    - Moderate speed
    - Utility-based decision making (retrieve vs recharge vs deliver)
    - Receives task assignments from coordinators
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
                "move": 0.6,  # Higher cost (heavier when carrying)
                "communicate": 0.2,
            },
        )

        self.carrying_capacity = carrying_capacity
        self.carrying_objects = 0
        self.state = AgentState.IDLE
        self.pathfinder = AStarPathfinder(model.grid)

        # Task assignment from coordinator
        self.assigned_target: Optional[Tuple[int, int]] = None

        # Event tracking for communication
        self.pending_events: List[str] = []  # Events to communicate to coordinators

        # Setup decision making
        self._setup_decision_maker()

    def _setup_decision_maker(self) -> None:
        """Setup utility functions for decision making"""
        self.decision_maker.register_utility_function(
            ActionType.RETRIEVE, UtilityFunctions.retrieve_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.RECHARGE, UtilityFunctions.recharge_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.DELIVER, UtilityFunctions.deliver_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.EXPLORE, UtilityFunctions.explore_utility
        )

    def process_received_messages(self) -> None:
        """Process task assignments from coordinators"""
        super().process_received_messages()

        messages = self.model.comm_manager.get_messages(self.unique_id)

        for message in messages:
            if isinstance(message, TaskAssignmentMessage):
                if message.target_id == self.unique_id:
                    # Received task assignment
                    if message.task_type == "retrieve":
                        print(f"[RETRIEVER {self.unique_id}] <- [COORD {message.sender_id}]: Received task to retrieve object at {message.target_position} (priority={message.priority:.2f})")
                        
                        # Log message for UI
                        self.log_message(
                            direction="received",
                            message_type="task_assignment",
                            details=f"Retrieve at {message.target_position} (priority={message.priority:.2f})",
                            target_ids=[message.sender_id]
                        )
                        
                        self.assigned_target = message.target_position
                        # Add to known objects
                        if message.target_position:
                            self.known_objects[message.target_position] = message.priority
                        # Register event: now busy with assigned task
                        self.pending_events.append("busy")

    def step_decide(self) -> None:
        """Decide next action using utility-based decision making"""
        # Reset communication flag
        self.should_communicate_this_step = False
        
        # Priority 0: Communicate pending events to coordinators if any
        if self.pending_events:
            # Check if there are coordinators nearby
            nearby = self.get_nearby_agents(self.communication_radius)
            coordinators = [
                a for a in nearby 
                if hasattr(a, "role") and getattr(a, "role", None) == "coordinator"
            ]
            
            if coordinators:
                # Communicate instead of move this step
                self.should_communicate_this_step = True
                return
        
        # Build context for decision making
        context = {
            "position": self.pos,
            "energy": self.energy,
            "max_energy": self.max_energy,
            "carrying": self.carrying_objects,
            "carrying_capacity": self.carrying_capacity,
            "warehouse_position": self.get_closest_warehouse(),
            "known_objects": list(self.known_objects.items()),
            "frontiers": [],  # Not used for retrievers
        }

        # Priority 1: Deliver if carrying objects
        if self.carrying_objects > 0:
            closest_wh = self.get_closest_warehouse()
            if closest_wh:
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                dist = abs(closest_wh[0] - my_pos[0]) + abs(closest_wh[1] - my_pos[1])
                # Only log every 10 steps to reduce spam
                if self.model.current_step % 10 == 0:
                    print(f"[RETRIEVER {self.unique_id}] STATE: Carrying {self.carrying_objects} object(s), heading to warehouse at {closest_wh} (distance: {dist})")
            self.state = AgentState.DELIVERING
            self.target_position = closest_wh
            return

        # Priority 2: Recharge if low energy (consider distance to warehouse + current task)
        if self.pos:
            pos_tuple = pos_to_tuple(self.pos)
            warehouse_pos = self.get_closest_warehouse()
            if warehouse_pos:
                distance_to_warehouse = abs(warehouse_pos[0] - pos_tuple[0]) + abs(
                    warehouse_pos[1] - pos_tuple[1]
                )
                
                # Calculate total energy needed for round trip if we have a target
                energy_for_return = distance_to_warehouse * self.energy_consumption["move"] * 1.5
                
                if self.state == AgentState.RETRIEVING and self.target_position:
                    # If already retrieving, include distance to target + return
                    distance_to_target = abs(self.target_position[0] - pos_tuple[0]) + abs(
                        self.target_position[1] - pos_tuple[1]
                    )
                    target_to_warehouse = abs(self.target_position[0] - warehouse_pos[0]) + abs(
                        self.target_position[1] - warehouse_pos[1]
                    )
                    energy_needed = (distance_to_target + target_to_warehouse) * self.energy_consumption["move"] * 1.5
                else:
                    # Just need to get back
                    energy_needed = energy_for_return

                # Only recharge if critically low (below 30) or can't complete current mission
                critical_low = 30  # Increased from 20 to prevent energy death
                if self.energy < critical_low or (self.state == AgentState.RETRIEVING and self.energy < energy_needed):
                    print(f"[RETRIEVER {self.unique_id}] STATE: Low energy ({self.energy:.1f}), need recharge (distance to warehouse: {distance_to_warehouse})")
                    self.state = AgentState.RECHARGING
                    self.target_position = warehouse_pos
                    self.was_recharging_at_warehouse = False  # Reset flag
                    return

        # Priority 3: Use assigned target if available and can claim it
        if self.assigned_target and self.assigned_target in self.known_objects:
            # Try to claim the object
            pos_tuple = pos_to_tuple(self.pos) if self.pos else (0, 0)
            distance = abs(self.assigned_target[0] - pos_tuple[0]) + abs(
                self.assigned_target[1] - pos_tuple[1]
            )

            can_claim = self.model.comm_manager.try_claim_object(
                self.assigned_target, self.unique_id, self.model.current_step, distance, self.energy
            )

            if can_claim:
                print(f"[RETRIEVER {self.unique_id}] TARGET: Claimed assigned object at {self.assigned_target}, moving to retrieve")
                self.state = AgentState.RETRIEVING
                self.target_position = self.assigned_target
                return
            else:
                # Someone else claimed it
                print(f"[RETRIEVER {self.unique_id}] TARGET: Assigned object at {self.assigned_target} already claimed by another agent")
                self.assigned_target = None

        # Priority 4: Evaluate available actions
        available_actions = [ActionType.RETRIEVE, ActionType.RECHARGE]

        if self.known_objects:
            # Filter out claimed objects
            available_objects = [
                obj_pos
                for obj_pos in self.known_objects.keys()
                if not self.model.comm_manager.is_object_claimed(obj_pos, self.unique_id)
            ]

            if available_objects:
                best_action = self.decision_maker.select_best_action(available_actions, context)

                if best_action.action_type == ActionType.RETRIEVE and best_action.target_position:
                    # Try to claim before committing
                    pos_tuple = pos_to_tuple(self.pos) if self.pos else (0, 0)
                    distance = abs(best_action.target_position[0] - pos_tuple[0]) + abs(
                        best_action.target_position[1] - pos_tuple[1]
                    )

                    can_claim = self.model.comm_manager.try_claim_object(
                        best_action.target_position,
                        self.unique_id,
                        self.model.current_step,
                        distance,
                        self.energy,
                    )

                    if can_claim:
                        self.state = AgentState.RETRIEVING
                        self.target_position = best_action.target_position
                    else:
                        self.state = AgentState.IDLE
                elif best_action.action_type == ActionType.RECHARGE:
                    self.state = AgentState.RECHARGING
                    self.target_position = self.get_closest_warehouse()
                else:
                    self.state = AgentState.IDLE
            else:
                self.state = AgentState.IDLE
        else:
            # No known objects, idle
            self.state = AgentState.IDLE

    def step_act(self) -> None:
        """Execute decided action: COMMUNICATE or MOVE (not both)"""
        if self.energy <= 0:
            return

        # OPTION 0: Check if standing on an object (opportunistic pickup)
        if self.carrying_objects < self.carrying_capacity:
            pos_tuple = pos_to_tuple(self.pos) if self.pos else None
            if pos_tuple and pos_tuple in self.model.grid.objects:
                # Opportunistically pick up object we're standing on
                print(f"[RETRIEVER {self.unique_id}] PICKUP: Opportunistic pickup at {pos_tuple}")
                self._try_pickup_object()
                # Don't return, continue with normal behavior

        # OPTION 1: Communicate pending events to coordinators
        if self.should_communicate_this_step and self.pending_events:
            self._send_event_to_coordinators()
            return  # Don't move this step

        # OPTION 2: Handle warehouse interactions
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        if self.is_at_warehouse():
            # Deliver objects first if carrying any
            if self.carrying_objects > 0:
                delivered_count = self.carrying_objects
                self.model.objects_retrieved += self.carrying_objects
                self.carrying_objects = 0
                print(f"[RETRIEVER {self.unique_id}] DELIVERY: Delivered {delivered_count} object(s)! Total progress: {self.model.objects_retrieved}/{self.model.total_objects}")
                
                # Register event: objects delivered
                self.pending_events.append("object_delivered")
                
                self.state = AgentState.IDLE
                self.assigned_target = None
                self.target_position = None
                
                # IMMEDIATELY move out of entrance/exit to avoid blocking
                cell_type = self.model.grid.get_cell_type(*my_pos)
                if cell_type in [CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT]:
                    for dx, dy in [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)]:
                        exit_pos = (my_pos[0] + dx, my_pos[1] + dy)
                        if (0 <= exit_pos[0] < self.model.grid.width and 
                            0 <= exit_pos[1] < self.model.grid.height):
                            exit_cell_type = self.model.grid.get_cell_type(*exit_pos)
                            if (exit_cell_type not in [CellType.OBSTACLE, 
                                                        CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, 
                                                        CellType.WAREHOUSE_EXIT] and
                                self.model.grid.is_cell_empty(exit_pos)):
                                self.model.grid.move_agent(self, exit_pos)
                                print(f"[RETRIEVER {self.unique_id}] EXIT: Moved out of entrance/exit to {exit_pos} to unblock")
                                return
            
            # Recharge if energy low
            if self.energy < self.max_energy * 0.9:
                recharge_rate = self.model.config.warehouse.recharge_rate
                self.recharge_energy(recharge_rate)
                self.was_recharging_at_warehouse = True  # Flag that we're recharging inside
                return  # Stay and recharge
            
            # Fully charged, need to exit - but only find exit if we don't have one
            if not self.target_position or self.target_position in self.model.warehouse_position:
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                # Find nearest non-warehouse cell that is walkable and reachable
                exit_found = False
                for distance in range(1, 5):
                    for dx in range(-distance, distance + 1):
                        for dy in range(-distance, distance + 1):
                            if abs(dx) != distance and abs(dy) != distance:
                                continue
                            check_pos = (my_pos[0] + dx, my_pos[1] + dy)
                            if (0 <= check_pos[0] < self.model.grid.width and 
                                0 <= check_pos[1] < self.model.grid.height):
                                cell_type = self.model.grid.get_cell_type(*check_pos)
                                if (cell_type not in [CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT] and
                                    self.model.grid.is_walkable(*check_pos)):
                                    test_path = self.pathfinder.find_path(my_pos, check_pos)
                                    if test_path and len(test_path) > 0:
                                        self.target_position = check_pos
                                        self.state = AgentState.RECHARGING  # Stay in recharging until we exit
                                        print(f"[RETRIEVER {self.unique_id}] EXIT: Found exit at {check_pos}, moving out")
                                        exit_found = True
                                        break
                        if exit_found:
                            break
                    if exit_found:
                        break
                
                if not exit_found:
                    from backend.algorithms.exploration import RandomWalkExplorer
                    new_pos = RandomWalkExplorer.get_random_walk_direction(
                        my_pos, getattr(self, 'idle_direction', None), 
                        self.model.grid, momentum=0.3
                    )
                    if new_pos != my_pos:
                        self.target_position = new_pos
                        self.state = AgentState.RECHARGING
                        print(f"[RETRIEVER {self.unique_id}] EXIT: Using random walk to exit")
            # Don't return - let it move towards target
        else:
            # Check if stuck near warehouse while trying to deliver
            if self.state == AgentState.DELIVERING and self.carrying_objects > 0:
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
                    check_pos = (my_pos[0] + dx, my_pos[1] + dy)
                    if (0 <= check_pos[0] < self.model.grid.width and 
                        0 <= check_pos[1] < self.model.grid.height):
                        cell_type = self.model.grid.get_cell_type(*check_pos)
                        if cell_type in [CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT]:
                            delivered_count = self.carrying_objects
                            self.model.objects_retrieved += self.carrying_objects
                            self.carrying_objects = 0
                            print(f"[RETRIEVER {self.unique_id}] DELIVERY: Delivered {delivered_count} object(s) adjacent to warehouse! Total progress: {self.model.objects_retrieved}/{self.model.total_objects}")
                            self.pending_events.append("object_delivered")
                            self.state = AgentState.IDLE
                            self.target_position = None
                            self.assigned_target = None
                            break

        # OPTION 2.5: Check if we've exited warehouse after recharge
        # Only transition to IDLE if we were actually AT the warehouse (not traveling TO it)
        if self.state == AgentState.RECHARGING and not self.is_at_warehouse():
            if getattr(self, 'was_recharging_at_warehouse', False):
                # Successfully exited warehouse after recharging
                print(f"[RETRIEVER {self.unique_id}] EXIT: Successfully exited warehouse after recharge, now IDLE")
                self.state = AgentState.IDLE
                self.target_position = None
                self.assigned_target = None
                self.pending_events.append("idle")
                self.was_recharging_at_warehouse = False

        # OPTION 3: If IDLE and no target, stay mostly idle to conserve energy
        # Only do very occasional light exploration to avoid being completely static
        if self.state == AgentState.IDLE and not self.target_position:
            # Move only occasionally (every 20 steps) to slightly update position
            # This conserves energy while waiting for coordinator assignments
            if self.model.current_step % 20 == 0:
                from backend.algorithms.exploration import RandomWalkExplorer
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                new_pos = RandomWalkExplorer.get_random_walk_direction(
                    my_pos, 
                    getattr(self, 'idle_direction', None), 
                    self.model.grid,
                    momentum=0.3  # Low momentum
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
            # Otherwise just stay idle and conserve energy
            return

        # OPTION 4: Move towards target
        if self.target_position:
            my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
            # Log if stuck delivering
            if self.state == AgentState.DELIVERING and self.carrying_objects > 0:
                wh_pos = self.get_closest_warehouse()
                if wh_pos:
                    dist_to_wh = abs(my_pos[0] - wh_pos[0]) + abs(my_pos[1] - wh_pos[1])
                    if dist_to_wh <= 2 and self.model.current_step % 5 == 0:
                        print(f"[RETRIEVER {self.unique_id}] DEBUG: At distance {dist_to_wh} from warehouse {wh_pos}, is_at_warehouse={self.is_at_warehouse()}, target={self.target_position}")
            self.move_towards(self.target_position)

            # Check if reached target (convert pos to tuple for comparison)
            my_pos = pos_to_tuple(self.pos) if self.pos else None
            if my_pos and my_pos == self.target_position:
                if self.state == AgentState.RETRIEVING:
                    # Try to pick up object
                    self._try_pickup_object()
                elif self.state in [AgentState.DELIVERING, AgentState.RECHARGING]:
                    # Should be at warehouse, handled above
                    pass

    def _try_pickup_object(self) -> None:
        """Try to pick up object at current position"""
        if self.carrying_objects >= self.carrying_capacity:
            # At capacity, can't pick up more
            self.state = AgentState.DELIVERING
            self.target_position = self.get_closest_warehouse()
            return

        # Check if there's an object here
        pos_tuple = pos_to_tuple(self.pos) if self.pos else None
        if pos_tuple and pos_tuple in self.model.grid.objects:
            # Pick up object
            success = self.model.grid.retrieve_object(*pos_tuple)
            if success:
                self.carrying_objects += 1
                print(f"[RETRIEVER {self.unique_id}] PICKUP: Successfully picked up object at {pos_tuple} (now carrying {self.carrying_objects}/{self.carrying_capacity})")

                # Register event: object picked up
                self.pending_events.append("object_picked")

                # Release claim and remove from known objects
                self.model.comm_manager.release_claim(pos_tuple, self.unique_id)
                if pos_tuple in self.known_objects:
                    del self.known_objects[pos_tuple]

                # Clear assigned target if this was it
                if self.assigned_target == pos_tuple:
                    self.assigned_target = None
                    # Register event: task completed
                    self.pending_events.append("task_completed")

                # Increase move cost when carrying
                self.energy_consumption["move"] = 0.6 + (self.carrying_objects * 0.2)

                # Decide next action
                if self.carrying_objects >= self.carrying_capacity:
                    # At capacity, deliver
                    self.state = AgentState.DELIVERING
                    self.target_position = self.get_closest_warehouse()
                else:
                    # Can carry more, look for another object
                    self.state = AgentState.IDLE
                    self.target_position = None
                    # Register event: now idle
                    self.pending_events.append("idle")
        else:
            # No object here (maybe already taken by another agent)
            print(f"[RETRIEVER {self.unique_id}] PICKUP: No object at {pos_tuple} (already taken or moved)")
            # Release claim
            if pos_tuple:
                self.model.comm_manager.release_claim(pos_tuple, self.unique_id)
            if self.pos in self.known_objects:
                del self.known_objects[self.pos]
            self.state = AgentState.IDLE
            self.target_position = None
            self.assigned_target = None
            # Register event: now idle (task failed)
            self.pending_events.append("idle")

    def _send_event_to_coordinators(self) -> None:
        """Send event notifications to nearby coordinators"""
        if not self.pending_events or not self.pos:
            return
        
        # Get nearby coordinators
        nearby = self.get_nearby_agents(self.communication_radius)
        coordinators = [
            a for a in nearby 
            if hasattr(a, "role") and getattr(a, "role", None) == "coordinator"
        ]
        
        if not coordinators:
            return
        
        coordinator_ids = [
            getattr(c, "unique_id", 0) for c in coordinators if hasattr(c, "unique_id")
        ]
        
        if self.pending_events:
            print(f"[RETRIEVER {self.unique_id}] -> [COORD {coordinator_ids}]: Sending {len(self.pending_events)} event(s): {self.pending_events}")
        
        # Send event message for each pending event
        for event_type in self.pending_events:
            message = RetrieverEventMessage(
                sender_id=self.unique_id,
                timestamp=self.model.current_step,
                retriever_id=self.unique_id,
                event_type=event_type,
                position=pos_to_tuple(self.pos),
                object_position=self.assigned_target,
                carrying_count=self.carrying_objects,
            )
            
            if coordinator_ids:
                self.model.comm_manager.send_message(message, coordinator_ids)
        
        # Log message for UI
        if self.pending_events:
            self.log_message(
                direction="sent",
                message_type="retriever_event",
                details=f"Events: {', '.join(self.pending_events)}",
                target_ids=coordinator_ids
            )
        
        # Clear pending events after sending
        self.pending_events = []
        
        # Consume energy for communication
        self.consume_energy(self.energy_consumption["communicate"])
        self.last_communication_step = self.model.current_step
