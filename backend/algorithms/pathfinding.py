"""
A* pathfinding with dynamic obstacle avoidance
"""

import heapq
from typing import List, Optional, Set, Tuple

import numpy as np

from backend.core.grid_manager import CellType


class AStarPathfinder:
    """
    A* pathfinding algorithm for grid-based navigation

    Features:
    - Diagonal movement
    - Dynamic obstacle avoidance
    - Agent avoidance with penalty
    """

    def __init__(self, grid_manager):
        self.grid = grid_manager

    def heuristic(self, a: Tuple[int, int], b: Tuple[int, int]) -> float:
        """
        Euclidean distance heuristic

        Args:
            a: Start position
            b: Goal position

        Returns:
            Estimated distance
        """
        return np.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

    def _is_walkable_for_agent(
        self,
        x: int,
        y: int,
        agent_local_map: Optional[np.ndarray] = None,
    ) -> bool:
        """Check walkability using the agent's local map when available.

        In fog-of-war mode (map_unknown), the agent's local map treats
        UNKNOWN cells as *potentially walkable* so A* can plan paths
        through unexplored territory.  When the agent later discovers
        an obstacle, the path is invalidated and recomputed.

        Falls back to the global ``grid.is_walkable()`` when no local
        map is provided (map_known mode or legacy callers).
        """
        if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
            return False
        if agent_local_map is not None:
            cell = int(agent_local_map[y, x])
            # UNKNOWN (0) is treated as walkable; only OBSTACLE blocks
            return cell != CellType.OBSTACLE
        return self.grid.is_walkable(x, y)

    def get_neighbors(
        self,
        pos: Tuple[int, int],
        forbidden_pos: Optional[Set[Tuple[int, int]]] = None,
        agent_local_map: Optional[np.ndarray] = None,
    ) -> List[Tuple[Tuple[int, int], float]]:
        """
        Get walkable neighbors with cost.

        Args:
            pos: Current position
            forbidden_pos: Positions that must NOT be used as intermediate nodes.
                           These cells are still walkable in general, but the
                           pathfinder will not route *through* them.
            agent_local_map: Optional agent local map for fog-of-war pathfinding.
                             When provided, UNKNOWN cells are treated as walkable.

        Returns:
            List of ((neighbor_x, neighbor_y), cost) tuples
        """
        x, y = pos
        neighbors = []

        # 8-directional movement
        directions = [
            (0, 1, 1.0),  # N
            (1, 0, 1.0),  # E
            (0, -1, 1.0),  # S
            (-1, 0, 1.0),  # W
            (1, 1, 1.414),  # NE (diagonal)
            (1, -1, 1.414),  # SE
            (-1, 1, 1.414),  # NW
            (-1, -1, 1.414),  # SW
        ]

        for dx, dy, cost in directions:
            nx, ny = x + dx, y + dy

            if not self._is_walkable_for_agent(nx, ny, agent_local_map):
                continue
            # Prevent diagonal corner-cutting: both adjacent cardinal
            # cells must be walkable for the agent to squeeze through.
            if dx != 0 and dy != 0:
                if not self._is_walkable_for_agent(
                    x + dx, y, agent_local_map
                ) or not self._is_walkable_for_agent(x, y + dy, agent_local_map):
                    continue
            if forbidden_pos and (nx, ny) in forbidden_pos:
                continue
            neighbors.append(((nx, ny), cost))

        return neighbors

    def find_path(
        self,
        start: Tuple[int, int],
        goal: Tuple[int, int],
        avoid_positions: Optional[Set[Tuple[int, int]]] = None,
        forbidden_types: Optional[Set[CellType]] = None,
        agent_local_map: Optional[np.ndarray] = None,
    ) -> Optional[List[Tuple[int, int]]]:
        """
        Find shortest path using A* algorithm.

        Args:
            start: Start position
            goal: Goal position
            avoid_positions: Positions to avoid (other agents) — adds cost penalty
            forbidden_types: CellType values that must NOT appear as *intermediate*
                             nodes in the path.  The goal cell is always allowed
                             regardless of its type.
            agent_local_map: Optional agent local map for fog-of-war pathfinding.
                             When provided, UNKNOWN cells are treated as walkable
                             so A* can plan through unexplored territory.

        Returns:
            List of positions from start to goal, or None if no path
        """
        if avoid_positions is None:
            avoid_positions = set()

        # Build the set of forbidden intermediate positions (goal is always reachable)
        forbidden_pos: Set[Tuple[int, int]] = set()
        if forbidden_types:
            for x in range(self.grid.width):
                for y in range(self.grid.height):
                    if self.grid.get_cell_type(x, y) in forbidden_types:
                        forbidden_pos.add((x, y))
            forbidden_pos.discard(goal)  # goal is always reachable
            forbidden_pos.discard(start)  # start must be expandable

        # Priority queue: (f_score, counter, position)
        counter = 0
        open_set: list = [(0.0, counter, start)]
        counter += 1

        # Track visited nodes
        came_from = {}

        # Cost from start (use float type)
        g_score: dict = {start: 0.0}

        # Estimated total cost
        f_score: dict = {start: self.heuristic(start, goal)}

        open_set_hash = {start}

        while open_set:
            _, _, current = heapq.heappop(open_set)
            open_set_hash.discard(current)

            # Reached goal
            if current == goal:
                return self._reconstruct_path(came_from, current)

            # Explore neighbors
            for neighbor, move_cost in self.get_neighbors(
                current, forbidden_pos or None, agent_local_map
            ):
                # Add penalty for positions to avoid (other agents)
                penalty = 10.0 if neighbor in avoid_positions else 0.0

                tentative_g_score = g_score[current] + move_cost + penalty

                if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g_score
                    f = tentative_g_score + self.heuristic(neighbor, goal)
                    f_score[neighbor] = f

                    if neighbor not in open_set_hash:
                        heapq.heappush(open_set, (f, counter, neighbor))
                        counter += 1
                        open_set_hash.add(neighbor)

        # No path found
        return None

    def _reconstruct_path(self, came_from: dict, current: Tuple[int, int]) -> List[Tuple[int, int]]:
        """Reconstruct path from came_from dict"""
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path

    def smooth_path(self, path: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """
        Smooth path by removing unnecessary waypoints

        Args:
            path: Original path

        Returns:
            Smoothed path
        """
        if len(path) <= 2:
            return path

        smoothed = [path[0]]
        current_idx = 0

        while current_idx < len(path) - 1:
            # Try to skip ahead as far as possible
            for i in range(len(path) - 1, current_idx, -1):
                if self._has_line_of_sight(path[current_idx], path[i]):
                    smoothed.append(path[i])
                    current_idx = i
                    break
            else:
                # No line of sight, just go to next waypoint
                current_idx += 1
                if current_idx < len(path):
                    smoothed.append(path[current_idx])

        return smoothed

    def _has_line_of_sight(self, start: Tuple[int, int], end: Tuple[int, int]) -> bool:
        """
        Check if there's a clear line of sight between two points

        Uses Bresenham's line algorithm
        """
        x0, y0 = start
        x1, y1 = end

        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy

        x, y = x0, y0

        while True:
            # Check if current position is walkable
            if not self.grid.is_walkable(x, y):
                return False

            if x == x1 and y == y1:
                break

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy

        return True
