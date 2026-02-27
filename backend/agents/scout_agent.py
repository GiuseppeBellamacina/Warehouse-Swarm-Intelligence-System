"""
Scout Agent - Fast explorer with wide vision
"""

from typing import Optional, Tuple

from backend.agents.base_agent import BaseAgent, AgentState, pos_to_tuple
from backend.algorithms.exploration import FrontierExplorer, RandomWalkExplorer
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.decision_maker import ActionType, UtilityFunctions
from backend.core.communication import ObjectLocationMessage


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
        model,
        vision_radius: int = 7,
        communication_radius: int = 20,
        max_energy: float = 100.0,
        speed: float = 1.5
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
                'base': 0.08,  # Lower base consumption (efficient)
                'move': 0.4,    # Lower move cost (lighter agent)
                'communicate': 0.15
            }
        )
        
        self.state = AgentState.EXPLORING
        self.pathfinder = AStarPathfinder(model.grid)
        self.previous_direction: Optional[Tuple[int, int]] = None
        
        # Setup decision making
        self._setup_decision_maker()
    
    def _setup_decision_maker(self) -> None:
        """Setup utility functions for decision making"""
        self.decision_maker.register_utility_function(
            ActionType.EXPLORE,
            UtilityFunctions.explore_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.RECHARGE,
            UtilityFunctions.recharge_utility
        )
    
    def step_decide(self) -> None:
        """Decide next action based on utility"""
        # Check if need to recharge
        if self.energy < 30:
            self.state = AgentState.RECHARGING
            self.target_position = self.model.warehouse_position
            return
        
        # Otherwise explore
        self.state = AgentState.EXPLORING
        
        # Find frontiers
        frontiers = FrontierExplorer.find_frontiers(self.local_map)
        
        if frontiers:
            # Get nearby agent positions for coordination
            nearby = self.get_nearby_agents()
            nearby_positions = [pos_to_tuple(a.pos) for a in nearby if a.pos]
            
            # Select best frontier
            my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
            best_frontier = FrontierExplorer.select_best_frontier(
                frontiers,
                my_pos,
                nearby_positions
            )
            
            if best_frontier:
                self.target_position = best_frontier
                self.state = AgentState.MOVING_TO_TARGET
        else:
            # No frontiers, use random walk
            self.target_position = None
            self.state = AgentState.EXPLORING
    
    def step_act(self) -> None:
        """Execute decided action"""
        if self.energy <= 0:
            return
        
        # Recharge at warehouse
        if self.is_at_warehouse():
            recharge_rate = self.model.config.warehouse.recharge_rate
            self.recharge_energy(recharge_rate)
            if self.energy >= self.max_energy * 0.9:
                self.state = AgentState.EXPLORING
            return
        
        # Move towards target
        if self.target_position and self.state == AgentState.MOVING_TO_TARGET:
            self.move_towards(self.target_position)
            
            # Reached target, find new frontier
            my_pos = pos_to_tuple(self.pos) if self.pos else None
            if my_pos and my_pos == self.target_position:
                self.target_position = None
                self.state = AgentState.EXPLORING
        
        elif self.state == AgentState.EXPLORING:
            # Random walk exploration
            my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
            new_pos = RandomWalkExplorer.get_random_walk_direction(
                my_pos,
                self.previous_direction,
                self.model.grid
            )
            
            if new_pos != my_pos:
                old_pos = my_pos
                self.move_towards(new_pos)
                my_pos_after = pos_to_tuple(self.pos) if self.pos else my_pos
                if my_pos_after != old_pos:
                    self.previous_direction = (
                        my_pos_after[0] - old_pos[0],
                        my_pos_after[1] - old_pos[1]
                    )
        
        elif self.state == AgentState.RECHARGING:
            # Move towards warehouse
            if self.target_position:
                self.move_towards(self.target_position)
        
        # Broadcast discovered objects to coordinators
        self._broadcast_discovered_objects()
    
    def _broadcast_discovered_objects(self) -> None:
        """Send object location messages to nearby coordinators"""
        if not self.known_objects:
            return
        
        # Only broadcast periodically to save energy
        if self.model.current_step % 10 != 0:
            return
        
        # Get nearby coordinators
        nearby = self.get_nearby_agents(self.communication_radius)
        coordinators = [a for a in nearby if hasattr(a, 'role') and getattr(a, 'role', None) == "coordinator"]
        
        if not coordinators:
            return
        
        # Send messages about each known object
        for obj_pos, obj_value in list(self.known_objects.items()):
            message = ObjectLocationMessage(
                sender_id=self.unique_id or 0,
                timestamp=self.model.current_step,
                object_position=obj_pos,
                object_value=obj_value
            )
            
            coordinator_ids = [getattr(c, 'unique_id', 0) for c in coordinators if hasattr(c, 'unique_id')]
            if coordinator_ids:
                self.model.comm_manager.send_message(message, coordinator_ids)
        
        # Consume energy for broadcast
        self.consume_energy(self.energy_consumption['communicate'] * len(coordinators))
