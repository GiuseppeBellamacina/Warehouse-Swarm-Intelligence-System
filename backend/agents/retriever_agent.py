"""
Retriever Agent - Heavy lifter for object collection and delivery
"""

from typing import Optional, Tuple

from backend.agents.base_agent import BaseAgent, AgentState, pos_to_tuple
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.decision_maker import ActionType, UtilityFunctions
from backend.core.communication import TaskAssignmentMessage


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
        model,
        vision_radius: int = 5,
        communication_radius: int = 15,
        max_energy: float = 100.0,
        speed: float = 1.0,
        carrying_capacity: int = 2
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
                'base': 0.1,
                'move': 0.6,  # Higher cost (heavier when carrying)
                'communicate': 0.2
            }
        )
        
        self.carrying_capacity = carrying_capacity
        self.carrying_objects = 0
        self.state = AgentState.IDLE
        self.pathfinder = AStarPathfinder(model.grid)
        
        # Task assignment from coordinator
        self.assigned_target: Optional[Tuple[int, int]] = None
        
        # Setup decision making
        self._setup_decision_maker()
    
    def _setup_decision_maker(self) -> None:
        """Setup utility functions for decision making"""
        self.decision_maker.register_utility_function(
            ActionType.RETRIEVE,
            UtilityFunctions.retrieve_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.RECHARGE,
            UtilityFunctions.recharge_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.DELIVER,
            UtilityFunctions.deliver_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.EXPLORE,
            UtilityFunctions.explore_utility
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
                        self.assigned_target = message.target_position
                        # Add to known objects
                        if message.target_position:
                            self.known_objects[message.target_position] = message.priority
    
    def step_decide(self) -> None:
        """Decide next action using utility-based decision making"""
        # Build context for decision making
        context = {
            'position': self.pos,
            'energy': self.energy,
            'max_energy': self.max_energy,
            'carrying': self.carrying_objects,
            'carrying_capacity': self.carrying_capacity,
            'warehouse_position': self.model.warehouse_position,
            'known_objects': list(self.known_objects.items()),
            'frontiers': [],  # Not used for retrievers
        }
        
        # Priority 1: Deliver if carrying objects
        if self.carrying_objects > 0:
            self.state = AgentState.DELIVERING
            self.target_position = self.model.warehouse_position
            return
        
        # Priority 2: Recharge if low energy
        if self.energy < 25:
            self.state = AgentState.RECHARGING
            self.target_position = self.model.warehouse_position
            return
        
        # Priority 3: Use assigned target if available
        if self.assigned_target and self.assigned_target in self.known_objects:
            self.state = AgentState.RETRIEVING
            self.target_position = self.assigned_target
            return
        
        # Priority 4: Evaluate available actions
        available_actions = [ActionType.RETRIEVE, ActionType.RECHARGE]
        
        if self.known_objects:
            best_action = self.decision_maker.select_best_action(
                available_actions,
                context
            )
            
            if best_action.action_type == ActionType.RETRIEVE:
                self.state = AgentState.RETRIEVING
                self.target_position = best_action.target_position
            elif best_action.action_type == ActionType.RECHARGE:
                self.state = AgentState.RECHARGING
                self.target_position = best_action.target_position
            else:
                self.state = AgentState.IDLE
        else:
            # No known objects, idle
            self.state = AgentState.IDLE
    
    def step_act(self) -> None:
        """Execute decided action"""
        if self.energy <= 0:
            return
        
        # Handle warehouse interactions
        if self.is_at_warehouse():
            # Recharge
            recharge_rate = self.model.config.warehouse.recharge_rate
            self.recharge_energy(recharge_rate)
            
            # Deliver objects
            if self.carrying_objects > 0:
                self.model.objects_retrieved += self.carrying_objects
                self.carrying_objects = 0
                print(f"Agent {self.unique_id} delivered objects! Total: {self.model.objects_retrieved}/{self.model.total_objects}")
                self.state = AgentState.IDLE
                self.target_position = None
                self.assigned_target = None
            
            # Done recharging
            if self.state == AgentState.RECHARGING and self.energy >= self.max_energy * 0.9:
                self.state = AgentState.IDLE
                self.target_position = None
            
            return
        
        # Move towards target
        if self.target_position:
            self.move_towards(self.target_position)
            
            # Check if reached target
            if self.pos == self.target_position:
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
            self.target_position = self.model.warehouse_position
            return
        
        # Check if there's an object here
        pos_tuple = pos_to_tuple(self.pos) if self.pos else None
        if pos_tuple and pos_tuple in self.model.grid.objects:
            # Pick up object
            success = self.model.grid.retrieve_object(*pos_tuple)
            if success:
                self.carrying_objects += 1
                
                # Remove from known objects
                if pos_tuple in self.known_objects:
                    del self.known_objects[pos_tuple]
                
                # Clear assigned target if this was it
                if self.assigned_target == pos_tuple:
                    self.assigned_target = None
                
                # Increase move cost when carrying
                self.energy_consumption['move'] = 0.6 + (self.carrying_objects * 0.2)
                
                # Decide next action
                if self.carrying_objects >= self.carrying_capacity:
                    # At capacity, deliver
                    self.state = AgentState.DELIVERING
                    self.target_position = self.model.warehouse_position
                else:
                    # Can carry more, look for another object
                    self.state = AgentState.IDLE
                    self.target_position = None
        else:
            # No object here (maybe already taken by another agent)
            if self.pos in self.known_objects:
                del self.known_objects[self.pos]
            self.state = AgentState.IDLE
            self.target_position = None
            self.assigned_target = None
