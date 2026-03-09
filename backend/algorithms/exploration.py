"""
Frontier-based exploration strategies
"""

from typing import List, Optional, Tuple, cast

import numpy as np
from scipy.ndimage import label

from backend.core.grid_manager import CellType


class FrontierExplorer:
    """
    Frontier-based exploration strategy

    Frontiers are boundaries between explored and unexplored areas.
    Agents navigate to frontiers to expand explored territory.
    """

    @staticmethod
    def find_frontiers(
        local_map: np.ndarray, min_cluster_size: int = 3
    ) -> List[Tuple[Tuple[int, int], int]]:
        """
        Find frontier clusters in the local map (vectorised with numpy).

        A frontier cell is:
        - Explored (FREE)
        - Adjacent to at least one UNKNOWN cell (8-connected)

        Args:
            local_map: Agent's local exploration map
            min_cluster_size: Minimum size for a frontier cluster

        Returns:
            List of (centroid_position, cluster_size) tuples
        """
        # Pad with UNKNOWN (0) so boundary cells have correct neighbours
        padded = np.pad(local_map, 1, mode="constant", constant_values=0)

        # Boolean mask: cell is FREE in the original map
        is_free = local_map == CellType.FREE

        # Boolean mask: at least one 8-neighbour is UNKNOWN (value 0)
        # Check all 8 shifts on the padded array (offset +1 due to padding)
        has_unknown_neighbour = np.zeros_like(is_free)
        for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1),
                        (-1, -1), (-1, 1), (1, -1), (1, 1)]:
            has_unknown_neighbour |= padded[1 + dy: padded.shape[0] - 1 + dy,
                                            1 + dx: padded.shape[1] - 1 + dx] == 0

        frontier_map = is_free & has_unknown_neighbour

        # Cluster frontiers using connected components
        label_result = cast(Tuple[np.ndarray, int], label(frontier_map))
        labeled_array, num_features = label_result

        if num_features == 0:
            return []

        # Vectorised centroid + size computation for all clusters at once
        cluster_ids = np.arange(1, num_features + 1)
        sizes = np.bincount(labeled_array.ravel(), minlength=num_features + 1)[1:]

        # Filter small clusters early
        large_enough = sizes >= min_cluster_size
        if not np.any(large_enough):
            return []

        # Compute centroids using sum of coordinates
        all_ys, all_xs = np.where(labeled_array > 0)
        all_labels = labeled_array[all_ys, all_xs]

        sum_x = np.bincount(all_labels, weights=all_xs, minlength=num_features + 1)[1:]
        sum_y = np.bincount(all_labels, weights=all_ys, minlength=num_features + 1)[1:]

        frontiers = []
        for cid in cluster_ids[large_enough]:
            idx = cid - 1
            cx = int(sum_x[idx] / sizes[idx])
            cy = int(sum_y[idx] / sizes[idx])
            frontiers.append(((cx, cy), int(sizes[idx])))

        return frontiers

    @staticmethod
    def select_best_frontier(
        frontiers: List[Tuple[Tuple[int, int], int]],
        agent_position: Tuple[int, int],
        nearby_agent_positions: Optional[List[Tuple[int, int]]] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Select the best frontier to explore

        Scoring function:
        utility = cluster_size / (distance + 1) - agent_penalty

        Args:
            frontiers: List of (position, cluster_size)
            agent_position: Current agent position
            nearby_agent_positions: Positions of nearby agents

        Returns:
            Best frontier position or None
        """
        if not frontiers:
            return None

        if nearby_agent_positions is None:
            nearby_agent_positions = []

        best_frontier = None
        best_utility = -float("inf")

        for frontier_pos, cluster_size in frontiers:
            # Distance to frontier
            dist = np.sqrt(
                (frontier_pos[0] - agent_position[0]) ** 2
                + (frontier_pos[1] - agent_position[1]) ** 2
            )

            # Count nearby agents targeting similar area
            agent_penalty = 0
            for other_pos in nearby_agent_positions:
                other_dist = np.sqrt(
                    (frontier_pos[0] - other_pos[0]) ** 2 + (frontier_pos[1] - other_pos[1]) ** 2
                )
                if other_dist < 10:  # Within 10 cells
                    agent_penalty += 0.5

            # Calculate utility.
            # Use dist^0.4 instead of dist^1 so that a large distant cluster
            # is properly preferred over a tiny cluster right next to the agent.
            # With linear distance: cluster_size=3 at dist=2 (score=1.0) beats
            # cluster_size=15 at dist=25 (score=0.58) — wrong direction.
            # With dist^0.4: those same examples score 1.24 vs 2.97 respectively.
            utility = (cluster_size / (dist ** 0.4 + 1)) - agent_penalty * 2

            if utility > best_utility:
                best_utility = utility
                best_frontier = frontier_pos

        return best_frontier


class RandomWalkExplorer:
    """
    Random walk exploration with momentum

    Fallback strategy when no frontiers are visible
    """

    @staticmethod
    def get_random_walk_direction(
        current_position: Tuple[int, int],
        previous_direction: Optional[Tuple[int, int]],
        grid_manager,
        momentum: float = 0.7,
    ) -> Tuple[int, int]:
        """
        Get next direction for random walk with momentum

        Args:
            current_position: Current position
            previous_direction: Previous movement direction
            grid_manager: Grid manager for walkability checks
            momentum: Probability of continuing in same direction

        Returns:
            Target position for next step
        """
        x, y = current_position

        # If we have momentum and random check passes, try to continue
        if previous_direction and np.random.random() < momentum:
            dx, dy = previous_direction
            new_pos = (x + dx, y + dy)
            if grid_manager.is_walkable(*new_pos):
                return new_pos

        # Otherwise pick random walkable neighbor
        directions = [
            (0, 1),  # N
            (1, 0),  # E
            (0, -1),  # S
            (-1, 0),  # W
            (1, 1),  # NE
            (1, -1),  # SE
            (-1, 1),  # NW
            (-1, -1),  # SW
        ]

        np.random.shuffle(directions)

        for dx, dy in directions:
            new_pos = (x + dx, y + dy)
            if grid_manager.is_walkable(*new_pos):
                return new_pos

        # If all else fails, stay in place
        return current_position


class PotentialFieldExplorer:
    """
    Potential field exploration

    Uses attractive and repulsive forces to guide exploration
    """

    @staticmethod
    def compute_exploration_potential(
        agent_position: Tuple[int, int],
        local_map: np.ndarray,
        nearby_agent_positions: List[Tuple[int, int]],
        sample_radius: int = 20,
    ) -> Tuple[int, int]:
        """
        Compute potential field and return direction

        Args:
            agent_position: Current position
            local_map: Local exploration map
            nearby_agent_positions: Nearby agents
            sample_radius: Radius to sample for potential

        Returns:
            Target position to move towards
        """
        ax, ay = agent_position

        # Sample grid around agent
        force_x = 0.0
        force_y = 0.0

        # Attractive force towards unexplored areas
        for dx in range(-sample_radius, sample_radius + 1):
            for dy in range(-sample_radius, sample_radius + 1):
                x, y = ax + dx, ay + dy

                if 0 <= y < local_map.shape[0] and 0 <= x < local_map.shape[1]:
                    if local_map[y, x] == CellType.UNKNOWN:
                        # Attractive force inversely proportional to distance
                        dist = max(np.sqrt(dx * dx + dy * dy), 1.0)
                        force_x += dx / (dist * dist)
                        force_y += dy / (dist * dist)

        # Repulsive force from other agents
        for other_x, other_y in nearby_agent_positions:
            dx = ax - other_x
            dy = ay - other_y
            dist = max(np.sqrt(dx * dx + dy * dy), 1.0)

            if dist < 10:  # Only repel if close
                force_x += dx / (dist * dist) * 5.0
                force_y += dy / (dist * dist) * 5.0

        # Normalize and apply
        force_magnitude = np.sqrt(force_x * force_x + force_y * force_y)

        if force_magnitude > 0:
            force_x /= force_magnitude
            force_y /= force_magnitude

            # Move in direction of force
            target_x = int(ax + force_x * 3)
            target_y = int(ay + force_y * 3)

            return (target_x, target_y)

        # No force, stay in place
        return agent_position
