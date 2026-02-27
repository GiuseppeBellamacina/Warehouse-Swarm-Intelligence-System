"""
Scout Agent - Fast explorer with wide vision
"""

from typing import List, Optional, Tuple, TYPE_CHECKING

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
from backend.algorithms.exploration import FrontierExplorer, RandomWalkExplorer
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import ObjectLocationMessage
from backend.core.decision_maker import ActionType, UtilityFunctions

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
        vision_radius: int = 7,
        communication_radius: int = 20,
        max_energy: float = 100.0,
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
                "base": 0.08,  # Lower base consumption (efficient)
                "move": 0.4,  # Lower move cost (lighter agent)
                "communicate": 0.15,
            },
        )

        self.state = AgentState.EXPLORING
        self.pathfinder = AStarPathfinder(model.grid)
        self.previous_direction: Optional[Tuple[int, int]] = None

        # Track discovered objects to communicate
        self.newly_discovered_objects: List[Tuple[Tuple[int, int], float]] = []
        self.pending_communication_steps = 0  # Track how long objects are pending
        
        # Track repeated failures on same target
        self.last_failed_target: Optional[Tuple[int, int]] = None
        self.consecutive_failures_on_target = 0
        
        # Track coordinator contact for reunion behavior
        self.last_coordinator_contact_step = 0
        self.searching_for_coordinator = False
        self.known_coordinator_position: Optional[Tuple[int, int]] = None

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
            print(f"[SCOUT {self.unique_id}] SENSE: Discovered {len(discovered)} new objects at positions: {list(discovered)}")
            # Add to list of objects to communicate
            for obj_pos in discovered:
                obj_value = self.known_objects.get(obj_pos, 1.0)
                self.newly_discovered_objects.append((obj_pos, obj_value))

    def step_decide(self) -> None:
        """Decide next action based on utility"""
        # Reset communication flag
        self.should_communicate_this_step = False
        
        # Priority 1: Communicate newly discovered objects to coordinators (if any nearby)
        if self.newly_discovered_objects:
            # Increment pending counter
            self.pending_communication_steps += 1
            
            # If pending too long (50 steps), discard and continue
            if self.pending_communication_steps > 50:
                print(f"[SCOUT {self.unique_id}] TIMEOUT: Discarding {len(self.newly_discovered_objects)} pending discoveries (no coordinator found for 50 steps)")
                self.newly_discovered_objects = []
                self.pending_communication_steps = 0
            else:
                # Check if there are coordinators nearby
                nearby = self.get_nearby_agents(self.communication_radius)
                coordinators = [
                    a for a in nearby 
                    if hasattr(a, "role") and getattr(a, "role", None) == "coordinator"
                ]
                
                if coordinators:
                    # Communicate instead of move this step
                    coord_ids = [getattr(c, "unique_id", "?") for c in coordinators]
                    print(f"[SCOUT {self.unique_id}] DECIDE: Will communicate {len(self.newly_discovered_objects)} objects to coordinators {coord_ids}")
                    self.should_communicate_this_step = True
                    return
                # If no coordinators nearby, continue exploring and keep objects for later
        
        # Priority 2: Check if need to find coordinator (if too long without contact)
        steps_without_coordinator = self.model.current_step - self.last_coordinator_contact_step
        if steps_without_coordinator > 80 and not self.searching_for_coordinator:
            # Check if coordinator is nearby right now
            nearby = self.get_nearby_agents(self.communication_radius)
            coordinators = [a for a in nearby 
                          if hasattr(a, "role") and getattr(a, "role", None) == "coordinator"]
            
            if coordinators:
                # Found coordinator, update contact time
                self.last_coordinator_contact_step = self.model.current_step
                coord = coordinators[0]
                if coord.pos:
                    self.known_coordinator_position = pos_to_tuple(coord.pos)
                print(f"[SCOUT {self.unique_id}] REUNION: Found coordinator {coord.unique_id} nearby")
            else:
                # No coordinator nearby, start searching
                print(f"[SCOUT {self.unique_id}] REUNION: No coordinator contact for {steps_without_coordinator} steps, searching")
                self.searching_for_coordinator = True
                
                # If we know coordinator's last position, go there
                if self.known_coordinator_position:
                    self.target_position = self.known_coordinator_position
                    self.state = AgentState.MOVING_TO_TARGET
                    return
        
        # If searching for coordinator and found one, stop searching
        if self.searching_for_coordinator:
            nearby = self.get_nearby_agents(self.communication_radius)
            coordinators = [a for a in nearby 
                          if hasattr(a, "role") and getattr(a, "role", None) == "coordinator"]
            if coordinators:
                self.searching_for_coordinator = False
                self.last_coordinator_contact_step = self.model.current_step
                coord = coordinators[0]
                if coord.pos:
                    self.known_coordinator_position = pos_to_tuple(coord.pos)
                print(f"[SCOUT {self.unique_id}] REUNION: Reunited with coordinator {coord.unique_id}!")
                self.target_position = None  # Clear target to resume normal exploration
        
        # Priority 3: Check if need to recharge
        if self.energy < 30:
            closest_wh = self.get_closest_warehouse()
            if closest_wh:
                print(f"[SCOUT {self.unique_id}] STATE: Low energy ({self.energy:.1f}), heading to warehouse at {closest_wh}")
                self.state = AgentState.RECHARGING
                self.target_position = closest_wh
                return

        # Priority 3: Explore
        self.state = AgentState.EXPLORING

        # Find frontiers in local map
        frontiers = FrontierExplorer.find_frontiers(self.local_map)
        
        # Filter out unreachable frontiers (recently failed targets)
        current_step = self.model.current_step
        valid_frontiers = []
        blacklisted_count = 0
        for frontier in frontiers:
            # frontier is ((x, y), cluster_size) - extract position
            frontier_pos, cluster_size = frontier
            
            # Check if this frontier position was recently unreachable (within last 30 steps)
            if frontier_pos in self.unreachable_targets:
                failed_step = self.unreachable_targets[frontier_pos]
                if current_step - failed_step < 30:
                    blacklisted_count += 1
                    continue  # Skip this unreachable frontier
                else:
                    # Enough time has passed, remove from blacklist
                    del self.unreachable_targets[frontier_pos]
            valid_frontiers.append(frontier)

        # If all frontiers are blacklisted, clear old entries and re-filter
        if blacklisted_count > 0 and len(valid_frontiers) == 0:
            print(f"[SCOUT {self.unique_id}] EXPLORE: All {blacklisted_count} frontiers blacklisted, clearing old entries")
            # Clear blacklist entries older than 15 steps
            to_remove = [pos for pos, step in self.unreachable_targets.items() 
                        if current_step - step > 15]
            for pos in to_remove:
                del self.unreachable_targets[pos]
            
            # Re-filter frontiers against UPDATED blacklist
            valid_frontiers = []
            for frontier in frontiers:
                frontier_pos, cluster_size = frontier
                # Check if still blacklisted after clearing old entries
                if frontier_pos not in self.unreachable_targets:
                    valid_frontiers.append(frontier)
            
            # If still no valid frontiers after clearing, force random walk
            if len(valid_frontiers) == 0:
                print(f"[SCOUT {self.unique_id}] STUCK: No reachable frontiers, forcing random walk")
                self.target_position = None
                self.state = AgentState.EXPLORING
                self.consecutive_failures_on_target = 0  # Reset to try again later
                return

        if valid_frontiers:
            # Get nearby agent positions for coordination
            nearby = self.get_nearby_agents()
            nearby_positions = [pos_to_tuple(a.pos) for a in nearby if a.pos]
            
            # Identify nearby scouts for anti-clustering
            nearby_scouts = [a for a in nearby 
                            if hasattr(a, "role") and getattr(a, "role", None) == "scout"]
            scout_positions = [pos_to_tuple(a.pos) for a in nearby_scouts if a.pos]

            # Select best frontier with anti-clustering
            my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
            
            # Filter out frontiers too close to other scouts (within 8 cells)
            min_scout_distance = 8
            anti_clustered_frontiers = []
            for frontier_pos, cluster_size in valid_frontiers:
                too_close_to_scout = False
                for scout_pos in scout_positions:
                    if scout_pos == my_pos:
                        continue  # Skip self
                    dist = abs(frontier_pos[0] - scout_pos[0]) + abs(frontier_pos[1] - scout_pos[1])
                    if dist < min_scout_distance:
                        too_close_to_scout = True
                        break
                
                if not too_close_to_scout:
                    anti_clustered_frontiers.append((frontier_pos, cluster_size))
            
            # Use anti-clustered frontiers if available, otherwise use all
            frontiers_to_use = anti_clustered_frontiers if anti_clustered_frontiers else valid_frontiers
            
            best_frontier = FrontierExplorer.select_best_frontier(
                frontiers_to_use, my_pos, nearby_positions
            )

            if best_frontier:
                # Only update target if significantly different or we don't have one
                if not self.target_position or abs(best_frontier[0] - self.target_position[0]) > 5 or abs(best_frontier[1] - self.target_position[1]) > 5:
                    self.target_position = best_frontier
                    self.path = []  # Clear old path
                    self.consecutive_failures_on_target = 0  # Reset failure counter
                self.state = AgentState.MOVING_TO_TARGET
            else:
                # No good frontier, continue with random walk
                self.target_position = None
                self.state = AgentState.EXPLORING
        else:
            # No frontiers visible, use random walk with momentum
            self.target_position = None
            self.state = AgentState.EXPLORING

    def step_act(self) -> None:
        """Execute decided action: COMMUNICATE or MOVE (not both)"""
        if self.energy <= 0:
            return

        # OPTION 1: Communicate newly discovered objects to coordinators
        if self.should_communicate_this_step and self.newly_discovered_objects:
            self._broadcast_discovered_objects()
            # Clear the list after broadcasting
            self.newly_discovered_objects = []
            self.pending_communication_steps = 0  # Reset counter
            return  # Don't move this step

        # OPTION 2: Recharge at warehouse
        if self.is_at_warehouse():
            recharge_rate = self.model.config.warehouse.recharge_rate
            old_energy = self.energy
            self.recharge_energy(recharge_rate)
            # Only log if actually recharged and reached threshold
            if old_energy < self.max_energy * 0.9 and self.energy >= self.max_energy * 0.9:
                print(f"[SCOUT {self.unique_id}] STATE: Fully recharged ({old_energy:.1f} -> {self.energy:.1f}), resuming exploration")
                self.state = AgentState.EXPLORING
            return

        # OPTION 3: Move based on state (scouts can move multiple times per step)
        moves_per_step = int(self.speed)  # Base moves
        
        # For speed > 1.0, do 2 moves per step (fast scout)
        if self.speed > 1.0:
            moves_per_step = 2
        
        for move_num in range(moves_per_step):
            if self.energy <= 0:
                break
                
            if self.target_position and self.state == AgentState.MOVING_TO_TARGET:
                self.move_towards(self.target_position)

                # Reached target, find new frontier
                my_pos = pos_to_tuple(self.pos) if self.pos else None
                if my_pos and my_pos == self.target_position:
                    self.target_position = None
                    self.state = AgentState.EXPLORING
                    break

            elif self.state == AgentState.EXPLORING:
                # Random walk exploration with momentum
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
                # Move towards warehouse
                if self.target_position:
                    self.move_towards(self.target_position)
                    break  # Don't do extra moves when recharging

    def _broadcast_discovered_objects(self) -> None:
        """Send object location messages to nearby coordinators"""
        # Get nearby coordinators
        nearby = self.get_nearby_agents(self.communication_radius)
        coordinators = [
            a for a in nearby if hasattr(a, "role") and getattr(a, "role", None) == "coordinator"
        ]

        if not coordinators:
            return

        coordinator_ids = [
            getattr(c, "unique_id", 0) for c in coordinators if hasattr(c, "unique_id")
        ]
        print(f"[SCOUT {self.unique_id}] COMM: Broadcasting {len(self.newly_discovered_objects)} objects to {len(coordinators)} coordinator(s) {coordinator_ids}")

        # Update coordinator contact time and position
        self.last_coordinator_contact_step = self.model.current_step
        self.searching_for_coordinator = False
        if coordinators[0].pos:
            self.known_coordinator_position = pos_to_tuple(coordinators[0].pos)

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
                print(f"[SCOUT {self.unique_id}] -> [COORD {coordinator_ids}]: Object at {obj_pos} (value={obj_value:.1f})")
        
        # Log message for UI
        if self.newly_discovered_objects:
            self.log_message(
                direction="sent",
                message_type="object_location",
                details=f"Broadcast {len(self.newly_discovered_objects)} objects",
                target_ids=coordinator_ids
            )

        # Consume energy for broadcast
        self.consume_energy(self.energy_consumption["communicate"] * len(coordinators))
        self.last_communication_step = self.model.current_step
