"""
Utility-based decision making system for agents
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np


class ActionType(Enum):
    """Possible agent actions"""

    EXPLORE = "explore"
    RETRIEVE = "retrieve"
    RECHARGE = "recharge"
    DELIVER = "deliver"
    ASSIST = "assist"
    IDLE = "idle"


@dataclass
class Action:
    """Represents a possible action with utility score"""

    action_type: ActionType
    utility: float
    target_position: Optional[Tuple[int, int]] = None
    metadata: Optional[Dict] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class DecisionMaker:
    """
    Utility-based decision making for agents

    Each agent evaluates multiple possible actions and selects the one
    with highest utility score.
    """

    def __init__(self):
        self.utility_functions: Dict[ActionType, Callable] = {}
        self.action_history: List[Action] = []

    def register_utility_function(self, action_type: ActionType, function: Callable) -> None:
        """
        Register a utility function for an action type

        Args:
            action_type: Type of action
            function: Function that computes utility score
        """
        self.utility_functions[action_type] = function

    def evaluate_actions(self, available_actions: List[ActionType], context: Dict) -> List[Action]:
        """
        Evaluate utility of all available actions

        Args:
            available_actions: List of actions to consider
            context: Dictionary with agent state and environment info

        Returns:
            List of Action objects sorted by utility (highest first)
        """
        actions = []

        for action_type in available_actions:
            if action_type in self.utility_functions:
                utility_func = self.utility_functions[action_type]
                utility, target, metadata = utility_func(context)

                actions.append(
                    Action(
                        action_type=action_type,
                        utility=utility,
                        target_position=target,
                        metadata=metadata,
                    )
                )

        # Sort by utility (descending)
        actions.sort(key=lambda a: a.utility, reverse=True)
        return actions

    def select_best_action(self, available_actions: List[ActionType], context: Dict) -> Action:
        """
        Select the action with highest utility

        Args:
            available_actions: List of actions to consider
            context: Dictionary with agent state and environment info

        Returns:
            Best action to take
        """
        evaluated = self.evaluate_actions(available_actions, context)

        if evaluated:
            best_action = evaluated[0]
            self.action_history.append(best_action)
            return best_action

        # Fallback to IDLE if no valid actions
        return Action(ActionType.IDLE, utility=0.0)


class UtilityFunctions:
    """
    Collection of utility functions for different agent roles
    """

    @staticmethod
    def explore_utility(context: Dict) -> Tuple[float, Optional[Tuple[int, int]], Dict]:
        """
        Utility for exploration action

        Higher utility for:
        - Nearby unexplored areas (frontiers)
        - Low agent density in target area

        Returns:
            (utility_score, target_position, metadata)
        """
        frontiers = context.get("frontiers", [])
        agent_position = context.get("position", (0, 0))
        nearby_agents = context.get("nearby_agents", 0)

        if not frontiers:
            return (0.0, None, {})

        # Find nearest frontier
        best_frontier = None
        min_dist = float("inf")

        for frontier_pos, cluster_size in frontiers:
            dist = np.sqrt(
                (frontier_pos[0] - agent_position[0]) ** 2
                + (frontier_pos[1] - agent_position[1]) ** 2
            )

            if dist < min_dist:
                min_dist = dist
                best_frontier = (frontier_pos, cluster_size)

        if best_frontier is None:
            return (0.0, None, {})

        frontier_pos, cluster_size = best_frontier

        # Utility = cluster_size / (distance + 1) - agent_penalty
        agent_penalty = nearby_agents * 0.5
        utility = (cluster_size / (min_dist + 1)) - agent_penalty

        return (utility, frontier_pos, {"cluster_size": cluster_size})

    @staticmethod
    def retrieve_utility(context: Dict) -> Tuple[float, Optional[Tuple[int, int]], Dict]:
        """
        Utility for object retrieval action

        Higher utility for:
        - Closer objects
        - Shorter path back to warehouse
        - Higher object value
        - Sufficient energy for round trip

        Returns:
            (utility_score, target_position, metadata)
        """
        known_objects = context.get("known_objects", [])
        agent_position = context.get("position", (0, 0))
        warehouse_position = context.get("warehouse_position", (0, 0))
        energy = context.get("energy", 100.0)
        carrying = context.get("carrying", 0)
        carrying_capacity = context.get("carrying_capacity", 1)

        # Can't retrieve if at capacity
        if carrying >= carrying_capacity:
            return (0.0, None, {})

        if not known_objects:
            return (0.0, None, {})

        best_utility = 0.0
        best_object = None
        best_metadata = {}

        for obj_pos, obj_value in known_objects:
            # Distance to object
            dist_to_obj = np.sqrt(
                (obj_pos[0] - agent_position[0]) ** 2 + (obj_pos[1] - agent_position[1]) ** 2
            )

            # Distance from object to warehouse
            dist_to_warehouse = np.sqrt(
                (warehouse_position[0] - obj_pos[0]) ** 2
                + (warehouse_position[1] - obj_pos[1]) ** 2
            )

            total_dist = dist_to_obj + dist_to_warehouse

            # Energy check (need 2.5x distance for safety margin)
            energy_needed = total_dist * 2.5
            if energy < energy_needed:
                continue  # Not enough energy

            # Utility calculation
            utility = obj_value / (total_dist + 1)

            if utility > best_utility:
                best_utility = utility
                best_object = obj_pos
                best_metadata = {
                    "value": obj_value,
                    "distance": dist_to_obj,
                    "total_distance": total_dist,
                }

        return (best_utility, best_object, best_metadata)

    @staticmethod
    def recharge_utility(context: Dict) -> Tuple[float, Optional[Tuple[int, int]], Dict]:
        """
        Utility for recharging at warehouse

        Higher utility for:
        - Lower current energy
        - Closer to warehouse

        Returns:
            (utility_score, warehouse_position, metadata)
        """
        energy = context.get("energy", 100.0)
        max_energy = context.get("max_energy", 100.0)
        agent_position = context.get("position", (0, 0))
        warehouse_position = context.get("warehouse_position", (0, 0))

        # Energy deficit
        energy_deficit = max_energy - energy

        if energy_deficit < 10:
            return (0.0, None, {})  # Energy is fine

        # Distance to warehouse
        dist = np.sqrt(
            (warehouse_position[0] - agent_position[0]) ** 2
            + (warehouse_position[1] - agent_position[1]) ** 2
        )

        # Higher utility when energy is lower
        utility = energy_deficit / (dist + 1)

        return (utility, warehouse_position, {"energy_deficit": energy_deficit})

    @staticmethod
    def deliver_utility(context: Dict) -> Tuple[float, Optional[Tuple[int, int]], Dict]:
        """
        Utility for delivering carried objects to warehouse

        Only relevant when carrying objects

        Returns:
            (utility_score, warehouse_position, metadata)
        """
        carrying = context.get("carrying", 0)
        agent_position = context.get("position", (0, 0))
        warehouse_position = context.get("warehouse_position", (0, 0))

        if carrying == 0:
            return (0.0, None, {})

        # Distance to warehouse
        dist = np.sqrt(
            (warehouse_position[0] - agent_position[0]) ** 2
            + (warehouse_position[1] - agent_position[1]) ** 2
        )

        # Very high utility when carrying objects
        utility = carrying * 100.0 / (dist + 1)

        return (utility, warehouse_position, {"carrying": carrying})
