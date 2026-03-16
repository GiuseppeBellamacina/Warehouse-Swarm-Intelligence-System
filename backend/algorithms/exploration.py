"""
Frontier-based exploration strategies
"""

from typing import Callable, List, Optional, Tuple, cast

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
        local_map: np.ndarray,
        min_cluster_size: int = 3,
        unexplored_mask: Optional[np.ndarray] = None,
    ) -> List[Tuple[Tuple[int, int], int]]:
        """
        Find frontier clusters in the local map (vectorised with numpy).

        A frontier cell is:
        - Explored (FREE)
        - Adjacent to at least one UNKNOWN cell (8-connected)

        Args:
            local_map: Agent's local exploration map
            min_cluster_size: Minimum size for a frontier cluster
            unexplored_mask: Optional boolean mask (True = unexplored). When
                provided, this overrides the default ``local_map == 0`` check
                for determining which cells are "unknown".  Useful when the
                terrain is pre-known but objects still need visual scanning.

        Returns:
            List of (centroid_position, cluster_size) tuples
        """
        if unexplored_mask is not None:
            # Use caller-supplied mask: "unexplored" = True in the mask
            unexp = unexplored_mask.astype(np.int8)  # 1 = unexplored
            padded_unexp = np.pad(unexp, 1, mode="constant", constant_values=1)
        else:
            unexp = None
            padded_unexp = None

        # Pad with UNKNOWN (0) so boundary cells have correct neighbours
        padded = np.pad(local_map, 1, mode="constant", constant_values=0)

        # Boolean mask: cell is FREE in the original map
        is_free = local_map == CellType.FREE

        # Boolean mask: at least one 8-neighbour is UNKNOWN (value 0)
        # Check all 8 shifts on the padded array (offset +1 due to padding)
        has_unknown_neighbour = np.zeros_like(is_free)
        for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
            if padded_unexp is not None:
                has_unknown_neighbour |= (
                    padded_unexp[
                        1 + dy : padded_unexp.shape[0] - 1 + dy,
                        1 + dx : padded_unexp.shape[1] - 1 + dx,
                    ]
                    > 0
                )
            else:
                has_unknown_neighbour |= (
                    padded[1 + dy : padded.shape[0] - 1 + dy, 1 + dx : padded.shape[1] - 1 + dx]
                    == 0
                )

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

            # The geometric centroid may land on a wall or obstacle.
            # If so, snap to the nearest cell that actually belongs to
            # this cluster so downstream consumers never discard a large
            # frontier just because its centroid is unwalkable.
            if labeled_array[cy, cx] != cid:
                members_y, members_x = np.where(labeled_array == cid)
                dists = np.abs(members_x - cx) + np.abs(members_y - cy)
                nearest = np.argmin(dists)
                cx, cy = int(members_x[nearest]), int(members_y[nearest])

            frontiers.append(((cx, cy), int(sizes[idx])))

        return frontiers

    @staticmethod
    def select_best_frontier(
        frontiers: List[Tuple[Tuple[int, int], int]],
        agent_position: Tuple[int, int],
        nearby_agent_positions: Optional[List[Tuple[int, int]]] = None,
        grid_size: Optional[Tuple[int, int]] = None,
        explored_ratio_at: Optional[Callable[[int, int], float]] = None,
        all_peer_targets: Optional[List[Tuple[int, int]]] = None,
        current_target: Optional[Tuple[int, int]] = None,
        unknown_mass_at: Optional[Callable[[int, int], int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Select the best frontier to explore

        Scoring function:
        utility = effective_size / (distance^0.4 + 1) - agent_penalty
                  - global_penalty + coverage_bonus + momentum_bonus

        effective_size = base_size * max(1 - explored_ratio, 0.05)
        where base_size = unknown_mass (if callback provided) or cluster_size
        coverage_bonus = (1 - explored_ratio) * 5.0

        Using unknown_mass (actual UNKNOWN cells behind the frontier)
        prevents the scout from fixating on tiny isolated frontiers in
        recondite corners when large unexplored areas exist elsewhere.

        Args:
            frontiers: List of (position, cluster_size)
            agent_position: Current agent position
            nearby_agent_positions: Positions of nearby agents
            grid_size: (width, height) of the grid — enables quadrant bias
            explored_ratio_at: callable(x, y) → float 0-1 giving the local
                explored ratio around (x,y).  When provided, frontiers in
                poorly-explored zones get a bonus.
            all_peer_targets: exploration targets of ALL known agents (from
                relayed MapDataMessages).  Adds a soft penalty to avoid
                frontiers already targeted by another agent anywhere on the map.
            current_target: agent's current exploration target — adds a
                directional momentum bonus so the agent continues pushing
                in the same direction instead of flipping back and forth.
            unknown_mass_at: callable(x, y) → int giving the count of UNKNOWN
                cells in a radius around (x,y).  When provided, replaces
                cluster_size as the base for effective_size so frontiers
                leading to large unexplored regions are preferred.

        Returns:
            Best frontier position or None
        """
        if not frontiers:
            return None

        if nearby_agent_positions is None:
            nearby_agent_positions = []
        if all_peer_targets is None:
            all_peer_targets = []

        # Precompute direction vector from agent to current target (for momentum)
        _has_momentum = False
        _dx_ct = 0.0
        _dy_ct = 0.0
        _mag_ct = 0.0
        if current_target is not None and current_target != tuple(agent_position):
            _dx_ct = float(current_target[0] - agent_position[0])
            _dy_ct = float(current_target[1] - agent_position[1])
            _mag_ct = np.sqrt(_dx_ct**2 + _dy_ct**2)
            if _mag_ct > 0:
                _has_momentum = True

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

            # Global deconfliction: soft penalty for frontiers near any known
            # peer's exploration target (learned via relay).  Weaker than local
            # penalty because the data can be slightly stale.
            global_penalty = 0.0
            for pt in all_peer_targets:
                pt_dist = abs(frontier_pos[0] - pt[0]) + abs(frontier_pos[1] - pt[1])
                if pt_dist < 8:
                    global_penalty += max(0.0, (8 - pt_dist) / 8) * 1.5

            # Coverage: scale effective cluster size by how unexplored the
            # surrounding area is.  An 89%-explored frontier of size 21 has
            # only ~2.3 genuinely-new cells; treating it as size 21 causes
            # agents to flock there instead of pushing into fully-unseen
            # regions.  A small additive bonus further nudges toward the
            # least-explored zones without dominating the distance term.
            effective_size = float(cluster_size)
            coverage_bonus = 0.0
            if explored_ratio_at is not None:
                local_ratio = explored_ratio_at(frontier_pos[0], frontier_pos[1])
                unexplored_frac = max(1.0 - local_ratio, 0.05)
                # Use unknown_mass as base when available: actual UNKNOWN
                # cell count behind the frontier gives a much better
                # signal than the boundary cluster_size alone.
                base_size = float(cluster_size)
                if unknown_mass_at is not None:
                    mass = unknown_mass_at(frontier_pos[0], frontier_pos[1])
                    base_size = max(float(mass), float(cluster_size))
                effective_size = base_size * unexplored_frac
                coverage_bonus = (1.0 - local_ratio) * 5.0

            # Momentum: bonus for frontiers aligned with the agent's current
            # heading direction.  Prevents "nibbling" where the agent scans a
            # few cells, the explored_ratio rises, and it flips to a distant
            # frontier instead of continuing deeper into unexplored territory.
            momentum_bonus = 0.0
            if _has_momentum:
                dx_f = float(frontier_pos[0] - agent_position[0])
                dy_f = float(frontier_pos[1] - agent_position[1])
                mag_f = np.sqrt(dx_f**2 + dy_f**2)
                if mag_f > 0:
                    cos_sim = (_dx_ct * dx_f + _dy_ct * dy_f) / (_mag_ct * mag_f)
                    momentum_bonus = max(0.0, cos_sim) * 3.0

            # Calculate utility.
            # Use dist^0.4 instead of dist^1 so that a large distant cluster
            # is properly preferred over a tiny cluster right next to the agent.
            utility = (
                (effective_size / (dist**0.4 + 1))
                - agent_penalty * 2
                - global_penalty
                + coverage_bonus
                + momentum_bonus
            )

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
