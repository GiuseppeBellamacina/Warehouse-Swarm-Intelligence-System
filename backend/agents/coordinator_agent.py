"""
Coordinator Agent - Strategic planner managing task assignments
"""

from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
from backend.algorithms.exploration import FrontierExplorer
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import ObjectLocationMessage, TaskAssignmentMessage, RetrieverEventMessage
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
        
        # Track retriever states to know who's available
        self.retriever_states: Dict[int, str] = {}  # retriever_id -> state ("idle", "busy", etc)
        
        # Track which objects are currently being collected to prevent double-assignment
        self.objects_being_collected: Set[Tuple[int, int]] = set()
        
        # Flag for communication decision
        self.tasks_to_assign: List[Tuple[int, Tuple[int, int], float]] = []  # (retriever_id, obj_pos, priority)
        
        # Track recharge attempts to avoid getting stuck
        self.recharge_attempt_start: Optional[int] = None

    def process_received_messages(self) -> None:
        """Process messages and handle object discoveries + retriever events"""
        super().process_received_messages()

        # Get messages
        messages = self.model.comm_manager.get_messages(self.unique_id)

        for message in messages:
            if isinstance(message, ObjectLocationMessage):
                # Scout discovered an object
                obj_pos = message.object_position
                obj_value = message.object_value

                # Only add if not already assigned or being collected
                if obj_pos not in self.assigned_tasks.values() and obj_pos not in self.objects_being_collected:
                    self.known_objects[obj_pos] = obj_value
                    print(f"[COORD {self.unique_id}] <- [SCOUT {message.sender_id}]: Received object location {obj_pos} (value={obj_value:.1f})")
                    
                    # Log message for UI
                    self.log_message(
                        direction="received",
                        message_type="object_location",
                        details=f"Object at {obj_pos} (value={obj_value:.1f})",
                        target_ids=[message.sender_id]
                    )
                else:
                    print(f"[COORD {self.unique_id}] SKIP: Object at {obj_pos} already assigned/being collected")
                    
            elif isinstance(message, RetrieverEventMessage):
                # Retriever sent status update
                retriever_id = message.retriever_id
                event_type = message.event_type
                
                print(f"[COORD {self.unique_id}] <- [RETRIEVER {retriever_id}]: Event '{event_type}' at {message.position}")
                
                # Log message for UI
                self.log_message(
                    direction="received",
                    message_type="retriever_event",
                    details=f"{event_type} at {message.position}",
                    target_ids=[retriever_id]
                )
                
                if event_type == "object_picked":
                    # Object was picked up, mark as being collected
                    if message.object_position:
                        self.objects_being_collected.add(message.object_position)
                        # Remove from known objects
                        if message.object_position in self.known_objects:
                            del self.known_objects[message.object_position]
                        
                elif event_type == "object_delivered":
                    # Objects delivered, retriever might be available
                    self.retriever_states[retriever_id] = "idle"
                    
                elif event_type == "task_completed":
                    # Task completed, remove from assigned tasks
                    if retriever_id in self.assigned_tasks:
                        completed_obj = self.assigned_tasks[retriever_id]
                        del self.assigned_tasks[retriever_id]
                        # Remove from objects being collected
                        if completed_obj in self.objects_being_collected:
                            self.objects_being_collected.discard(completed_obj)
                    
                elif event_type == "idle":
                    # Retriever is idle and available
                    self.retriever_states[retriever_id] = "idle"
                    
                elif event_type == "busy":
                    # Retriever is busy
                    self.retriever_states[retriever_id] = "busy"

    def step_decide(self) -> None:
        """Decide on task assignments and own actions"""
        # Reset communication flag
        self.should_communicate_this_step = False
        self.tasks_to_assign = []
        
        # Check if need to recharge
        if self.energy < 50:  # Increased threshold to recharge earlier
            if self.state != AgentState.RECHARGING:
                # Just entered recharge mode - find closest warehouse
                closest_wh = self.get_closest_warehouse()
                if closest_wh:
                    print(f"[COORD {self.unique_id}] STATE: Low energy ({self.energy:.1f}), heading to warehouse at {closest_wh}")
                    self.state = AgentState.RECHARGING
                    self.target_position = closest_wh
                    self.recharge_attempt_start = self.model.current_step
                    self.was_recharging_at_warehouse = False  # Reset flag
            else:
                # Already in recharge mode - check if stuck
                if self.recharge_attempt_start is not None:
                    steps_attempting = self.model.current_step - self.recharge_attempt_start
                    if steps_attempting > 50:
                        # Stuck trying to recharge for too long - try different warehouse
                        print(f"[COORD {self.unique_id}] EMERGENCY: Cannot reach warehouse after {steps_attempting} steps, trying alternative")
                        # Reset and try to find any warehouse zone
                        self.state = AgentState.IDLE
                        self.target_position = None
                        self.recharge_attempt_start = None
                        return
            return
        
        # If recharged, clear recharge attempt tracker
        if self.recharge_attempt_start is not None:
            self.recharge_attempt_start = None
            if self.state == AgentState.RECHARGING:
                self.state = AgentState.IDLE

        # Identify available retrievers nearby
        self._identify_available_retrievers()

        # Plan task assignments
        self._plan_task_assignments()
        
        # Priority 1: If we have tasks to assign, communicate instead of move
        if self.tasks_to_assign:
            self.should_communicate_this_step = True
            return

        # Priority 2: If idle, explore
        if self.state == AgentState.IDLE or self.state == AgentState.EXPLORING:
            self._decide_exploration()

    def _identify_available_retrievers(self) -> None:
        """Find nearby retrievers that need tasks"""
        nearby = self.get_nearby_agents(self.communication_radius)

        self.available_retrievers = []

        for agent in nearby:
            if hasattr(agent, "role") and getattr(agent, "role", None) == "retriever":
                agent_id = getattr(agent, "unique_id", None)
                if agent_id is None:
                    continue
                
                # Check retriever's actual state directly from agent (more reliable than cached state)
                agent_state = getattr(agent, "state", None)
                
                # Consider retrievers that are idle, exploring, or even recharging (if they have enough energy)
                # Don't assign to retrievers that are actively retrieving or delivering
                is_available = False
                
                if agent_state == AgentState.IDLE:
                    is_available = True
                elif agent_state == AgentState.EXPLORING:
                    # Retriever doing light exploration, can be assigned
                    is_available = True
                elif agent_state == AgentState.RECHARGING:
                    # If recharging but has good energy now, can be assigned
                    if getattr(agent, "energy", 0) > 60:
                        is_available = True
                
                if is_available:
                    # Also check if has enough energy for a task
                    if getattr(agent, "energy", 0) > 50:
                        # Check if not already assigned
                        if agent_id not in self.assigned_tasks:
                            # Check if not carrying objects (should deliver first)
                            if getattr(agent, "carrying_objects", 0) == 0:
                                self.available_retrievers.append(agent_id)
        
        if self.available_retrievers:
            print(f"[COORD {self.unique_id}] SCAN: Found {len(self.available_retrievers)} available retriever(s): {self.available_retrievers}")

    def _plan_task_assignments(self) -> None:
        """Plan task assignments based on proximity and priority"""
        if not self.known_objects or not self.available_retrievers:
            return

        # Get retriever positions for distance calculations
        retriever_positions = {}
        for agent in self.model.agents:
            if hasattr(agent, "role") and getattr(agent, "role", None) == "retriever":
                agent_id = getattr(agent, "unique_id", None)
                if agent_id in self.available_retrievers and agent.pos:
                    retriever_positions[agent_id] = pos_to_tuple(agent.pos)

        # Create list of (retriever_id, object_pos, distance, object_value) tuples
        assignments = []
        for retriever_id, ret_pos in retriever_positions.items():
            for obj_pos, obj_value in self.known_objects.items():
                # Skip if object already being collected
                if obj_pos in self.objects_being_collected:
                    continue
                    
                # Calculate distance (Manhattan)
                distance = abs(obj_pos[0] - ret_pos[0]) + abs(obj_pos[1] - ret_pos[1])
                
                # Priority = value / (distance + 1)
                priority = obj_value / (distance + 1)
                
                assignments.append((retriever_id, obj_pos, distance, priority))

        # Sort by priority (higher is better)
        assignments.sort(key=lambda x: x[3], reverse=True)

        # Assign tasks greedily (best match first, no conflicts)
        assigned_retrievers = set()
        assigned_objects = set()

        for retriever_id, obj_pos, distance, priority in assignments:
            # Skip if retriever or object already assigned in this round
            if retriever_id in assigned_retrievers or obj_pos in assigned_objects:
                continue

            # Assign this task
            self.tasks_to_assign.append((retriever_id, obj_pos, priority))
            assigned_retrievers.add(retriever_id)
            assigned_objects.add(obj_pos)

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

        # OPTION 1: Assign tasks (communicate)
        if self.should_communicate_this_step and self.tasks_to_assign:
            self._send_task_assignments()
            return  # Don't move this step

        # OPTION 2: Recharge at warehouse
        if self.is_at_warehouse():
            recharge_rate = self.model.config.warehouse.recharge_rate
            old_energy = self.energy
            self.recharge_energy(recharge_rate)
            self.was_recharging_at_warehouse = True  # Flag that we were recharging inside
            if self.energy >= self.max_energy * 0.9:
                print(f"[COORD {self.unique_id}] RECHARGE: Fully recharged ({old_energy:.1f} -> {self.energy:.1f}), exiting warehouse")
                # Fully charged, find exit if we don't have one
                if not self.target_position:
                    self.state = AgentState.RECHARGING
                    my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
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
                                            print(f"[COORD {self.unique_id}] EXIT: Found exit at {check_pos}")
                                            exit_found = True
                                            break
                                if exit_found:
                                    break
                        if exit_found:
                            break
                    
                    if not exit_found:
                        from backend.algorithms.exploration import RandomWalkExplorer
                        new_pos = RandomWalkExplorer.get_random_walk_direction(
                            my_pos, getattr(self, 'previous_direction', None),
                            self.model.grid, momentum=0.3
                        )
                        if new_pos != my_pos:
                            self.target_position = new_pos
                # Don't return - let it move
            else:
                # Still recharging
                return
        
        # OPTION 3: Check if finished exiting warehouse after recharge
        # Only transition to IDLE if we were actually AT the warehouse (not traveling TO it)
        if self.state == AgentState.RECHARGING and not self.is_at_warehouse():
            if getattr(self, 'was_recharging_at_warehouse', False):
                # Successfully exited warehouse, now go idle/explore
                print(f"[COORD {self.unique_id}] EXIT: Successfully exited warehouse, now IDLE")
                self.state = AgentState.IDLE
                self.target_position = None
                self.was_recharging_at_warehouse = False

        # OPTION 4: Move based on state
        if self.state == AgentState.RECHARGING:
            if self.target_position:
                self.move_towards(self.target_position)

        elif self.state == AgentState.EXPLORING:
            if self.target_position:
                self.move_towards(self.target_position)

                # Reached target (convert pos to tuple for comparison)
                my_pos = pos_to_tuple(self.pos) if self.pos else None
                if my_pos and my_pos == self.target_position:
                    self.target_position = None
                    self.state = AgentState.IDLE
        
        elif self.state == AgentState.IDLE:
            # Do light exploration when idle to maintain map coverage
            from backend.algorithms.exploration import RandomWalkExplorer
            my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
            new_pos = RandomWalkExplorer.get_random_walk_direction(
                my_pos,
                getattr(self, 'idle_direction', None),
                self.model.grid,
                momentum=0.3  # Low momentum for coordinators
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

    def _send_task_assignments(self) -> None:
        """Send task assignments to retrievers"""
        print(f"[COORD {self.unique_id}] ASSIGN: Sending {len(self.tasks_to_assign)} task(s)")
        
        for retriever_id, obj_pos, priority in self.tasks_to_assign:
            # Create task assignment message
            message = TaskAssignmentMessage(
                sender_id=self.unique_id or 0,
                timestamp=self.model.current_step,
                target_id=retriever_id,
                task_type="retrieve",
                target_position=obj_pos,
                priority=priority,
            )

            print(f"[COORD {self.unique_id}] -> [RETRIEVER {retriever_id}]: Retrieve object at {obj_pos} (priority={priority:.2f})")

            # Send to retriever
            self.model.comm_manager.send_message(message, [retriever_id])
            
            # Log message for UI
            self.log_message(
                direction="sent",
                message_type="task_assignment",
                details=f"Retrieve at {obj_pos} (priority={priority:.2f})",
                target_ids=[retriever_id]
            )

            # Track assignment
            self.assigned_tasks[retriever_id] = obj_pos
            self.objects_being_collected.add(obj_pos)
            
            # Remove from known objects
            if obj_pos in self.known_objects:
                del self.known_objects[obj_pos]
            
            # Mark retriever as busy
            self.retriever_states[retriever_id] = "busy"

            # Consume energy for coordination
            self.consume_energy(self.energy_consumption["communicate"])
        
        # Clear tasks after sending
        self.tasks_to_assign = []
        self.last_communication_step = self.model.current_step
