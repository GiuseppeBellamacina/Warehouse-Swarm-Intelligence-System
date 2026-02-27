"""
Coordinator Agent - Strategic planner managing task assignments
"""

from typing import Dict, List, Tuple, TYPE_CHECKING

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
from backend.algorithms.exploration import FrontierExplorer
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import ObjectLocationMessage, TaskAssignmentMessage

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

    def process_received_messages(self) -> None:
        """Process messages and handle object discoveries"""
        super().process_received_messages()

        # Get messages
        messages = self.model.comm_manager.get_messages(self.unique_id)

        for message in messages:
            if isinstance(message, ObjectLocationMessage):
                # Register discovered object
                obj_pos = message.object_position
                obj_value = message.object_value

                # Only add if not already assigned
                if obj_pos not in self.assigned_tasks.values():
                    self.known_objects[obj_pos] = obj_value

    def step_decide(self) -> None:
        """Decide on task assignments and own actions"""
        # Check if need to recharge
        if self.energy < 40:
            self.state = AgentState.RECHARGING
            self.target_position = self.model.warehouse_position
            return

        # Identify available retrievers nearby
        self._identify_available_retrievers()

        # Assign tasks to available retrievers
        self._assign_tasks()

        # If idle, explore
        if self.state == AgentState.IDLE or self.state == AgentState.EXPLORING:
            self._decide_exploration()

    def _identify_available_retrievers(self) -> None:
        """Find nearby retrievers that need tasks"""
        nearby = self.get_nearby_agents(self.communication_radius)

        self.available_retrievers = []

        for agent in nearby:
            if hasattr(agent, "role") and getattr(agent, "role", None) == "retriever":
                # Check if retriever is idle or exploring (not already on task)
                if hasattr(agent, "state") and getattr(agent, "state", None) in [
                    AgentState.IDLE,
                    AgentState.EXPLORING,
                ]:
                    # Check if has enough energy
                    if getattr(agent, "energy", 0) > 40:
                        agent_id = getattr(agent, "unique_id", None)
                        if agent_id is not None:
                            self.available_retrievers.append(agent_id)

    def _assign_tasks(self) -> None:
        """Assign retrieval tasks to available retrievers"""
        if not self.known_objects or not self.available_retrievers:
            return

        # Sort objects by value (or closeness)
        sorted_objects = sorted(self.known_objects.items(), key=lambda x: x[1], reverse=True)

        # Assign tasks
        for retriever_id in self.available_retrievers[:]:
            if not sorted_objects:
                break

            # Get best available object
            obj_pos, obj_value = sorted_objects.pop(0)

            # Create task assignment message
            message = TaskAssignmentMessage(
                sender_id=self.unique_id or 0,
                timestamp=self.model.current_step,
                target_id=retriever_id,
                task_type="retrieve",
                target_position=obj_pos,
                priority=obj_value,
            )

            # Send to retriever
            self.model.comm_manager.send_message(message, [retriever_id])

            # Track assignment
            self.assigned_tasks[retriever_id] = obj_pos
            del self.known_objects[obj_pos]
            self.available_retrievers.remove(retriever_id)

            # Consume energy for coordination
            self.consume_energy(self.energy_consumption["communicate"])

    def _decide_exploration(self) -> None:
        """Decide exploration action when idle"""
        frontiers = FrontierExplorer.find_frontiers(self.local_map)

        if frontiers:
            nearby = self.get_nearby_agents()
            nearby_positions = [pos_to_tuple(a.pos) for a in nearby if a.pos]

            my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
            best_frontier = FrontierExplorer.select_best_frontier(
                frontiers, my_pos, nearby_positions
            )

            if best_frontier:
                self.target_position = best_frontier
                self.state = AgentState.EXPLORING
        else:
            self.state = AgentState.IDLE

    def step_act(self) -> None:
        """Execute decided action"""
        if self.energy <= 0:
            return

        # Recharge at warehouse
        if self.is_at_warehouse():
            recharge_rate = self.model.config.warehouse.recharge_rate
            self.recharge_energy(recharge_rate)
            if self.energy >= self.max_energy * 0.9:
                self.state = AgentState.IDLE
            return

        # Move based on state
        if self.state == AgentState.RECHARGING:
            if self.target_position:
                self.move_towards(self.target_position)

        elif self.state == AgentState.EXPLORING:
            if self.target_position:
                self.move_towards(self.target_position)

                # Reached target
                if self.pos == self.target_position:
                    self.target_position = None
                    self.state = AgentState.IDLE
