"""
Grid manager with cell types and spatial queries
"""

from enum import IntEnum
from typing import List, Optional, Set, Tuple

import numpy as np
from scipy.spatial import KDTree

from backend.core.framework import MultiGrid


class CellType(IntEnum):
    """Cell types on the grid"""

    UNKNOWN = 0
    FREE = 1
    OBSTACLE = 2
    WAREHOUSE = 3
    WAREHOUSE_ENTRANCE = 4
    WAREHOUSE_EXIT = 5
    OBJECT_ZONE = 6
    OBJECT = 7


class GridManager(MultiGrid):
    """
    Extended Mesa MultiGrid with cell types and spatial indexing

    Features:
    - Cell type tracking (obstacles, warehouses, object zones)
    - Efficient spatial queries using KDTree
    - Vision cone/radius queries
    - Pathfinding support
    """

    def __init__(self, width: int, height: int, torus: bool = False):
        super().__init__(width, height, torus)

        # Cell type grid (initialized to FREE)
        self.cell_types = np.ones((width, height), dtype=np.int8) * CellType.FREE

        # Object tracking
        self.objects: Set[Tuple[int, int]] = set()
        self.retrieved_objects: Set[Tuple[int, int]] = set()

        # Spatial index for agents (rebuilt each step)
        self._agent_positions: List[Tuple[int, int]] = []
        self._kdtree: Optional[KDTree] = None
        self._kdtree_stale = True

    def set_cell_type(self, x: int, y: int, cell_type: CellType) -> None:
        """Set the type of a cell"""
        if 0 <= x < self.width and 0 <= y < self.height:
            self.cell_types[x, y] = cell_type

    def get_cell_type(self, x: int, y: int) -> CellType:
        """Get the type of a cell"""
        if 0 <= x < self.width and 0 <= y < self.height:
            return CellType(self.cell_types[x, y])
        return CellType.UNKNOWN

    def is_walkable(self, x: int, y: int) -> bool:
        """Check if a cell is walkable (not an obstacle)"""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False

        cell_type = self.get_cell_type(x, y)
        return cell_type not in [CellType.OBSTACLE, CellType.UNKNOWN]

    def place_obstacle(self, x: int, y: int) -> None:
        """Place an obstacle at the given position"""
        self.set_cell_type(x, y, CellType.OBSTACLE)

    def place_object(self, x: int, y: int) -> None:
        """Place an object at the given position"""
        self.set_cell_type(x, y, CellType.OBJECT)
        self.objects.add((x, y))

    def retrieve_object(self, x: int, y: int) -> bool:
        """
        Retrieve an object from the given position

        Returns:
            True if object was successfully retrieved
        """
        if (x, y) in self.objects:
            self.objects.remove((x, y))
            self.retrieved_objects.add((x, y))
            self.set_cell_type(x, y, CellType.FREE)
            return True
        return False

    def get_neighbors_in_radius(
        self, x: int, y: int, radius: float, include_center: bool = False
    ) -> List[Tuple[int, int]]:
        """
        Get all cells within a given radius

        Args:
            x, y: Center position
            radius: Search radius
            include_center: Whether to include the center cell

        Returns:
            List of (x, y) positions within radius
        """
        neighbors = []
        radius_sq = radius * radius

        for dx in range(-int(radius), int(radius) + 1):
            for dy in range(-int(radius), int(radius) + 1):
                if not include_center and dx == 0 and dy == 0:
                    continue

                nx, ny = x + dx, y + dy

                if 0 <= nx < self.width and 0 <= ny < self.height:
                    dist_sq = dx * dx + dy * dy
                    if dist_sq <= radius_sq:
                        neighbors.append((nx, ny))

        return neighbors

    def update_agent_spatial_index(self, agent_positions: List[Tuple[int, int]]) -> None:
        """
        Update the spatial index for agent proximity queries

        Args:
            agent_positions: List of current agent positions
        """
        self._agent_positions = agent_positions
        self._kdtree_stale = True

    def get_agents_in_radius(self, x: int, y: int, radius: float) -> List[int]:
        """
        Get indices of agents within radius using KDTree

        Args:
            x, y: Query position
            radius: Search radius

        Returns:
            List of agent indices within radius
        """
        if not self._agent_positions:
            return []

        # Rebuild KDTree if needed
        if self._kdtree_stale:
            self._kdtree = KDTree(self._agent_positions)
            self._kdtree_stale = False

        # Query within radius
        if self._kdtree is None:
            return []

        indices = self._kdtree.query_ball_point([x, y], radius)
        return indices

    def _has_line_of_sight(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        """
        Check line-of-sight using Bresenham's algorithm with obstacle occlusion.

        Traces a line from (x0, y0) to (x1, y1).  Any intermediate cell (i.e.
        every cell except the origin) that is an OBSTACLE blocks the ray and
        returns False.  The target cell itself is NOT tested for obstruction so
        that observers can "see" the obstacle that blocks them.

        Args:
            x0, y0: Observer position
            x1, y1: Target position

        Returns:
            True if there is clear line-of-sight
        """
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy

        cx, cy = x0, y0
        while (cx, cy) != (x1, y1):
            # Check all intermediate cells (not the origin)
            if (cx, cy) != (x0, y0):
                if (
                    0 <= cx < self.width
                    and 0 <= cy < self.height
                    and self.cell_types[cx, cy] == CellType.OBSTACLE
                ):
                    return False

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                cx += sx
            if e2 < dx:
                err += dx
                cy += sy

        return True

    def get_visible_cells(
        self, x: int, y: int, vision_radius: int
    ) -> List[Tuple[int, int, CellType]]:
        """
        Get all visible cells within vision radius using Manhattan distance and
        Bresenham ray-casting for obstacle occlusion.

        Args:
            x, y: Observer position
            vision_radius: Vision range (Manhattan distance)

        Returns:
            List of (x, y, cell_type) tuples for all visible cells
        """
        visible = []

        for dx in range(-vision_radius, vision_radius + 1):
            for dy in range(-vision_radius, vision_radius + 1):
                # Manhattan distance check
                if abs(dx) + abs(dy) > vision_radius:
                    continue

                nx, ny = x + dx, y + dy
                if not (0 <= nx < self.width and 0 <= ny < self.height):
                    continue

                # Observer's own cell is always visible
                if dx == 0 and dy == 0:
                    visible.append((nx, ny, self.get_cell_type(nx, ny)))
                    continue

                # Check line-of-sight with Bresenham occlusion
                if self._has_line_of_sight(x, y, nx, ny):
                    visible.append((nx, ny, self.get_cell_type(nx, ny)))

        return visible

    _WH_TYPES = frozenset(
        {CellType.WAREHOUSE, CellType.WAREHOUSE_ENTRANCE, CellType.WAREHOUSE_EXIT}
    )

    def flood_fill_warehouse(self, start_x: int, start_y: int) -> List[Tuple[int, int, CellType]]:
        """Return all warehouse cells (WAREHOUSE, ENTRANCE, EXIT) connected
        to *(start_x, start_y)* via 4-directional adjacency.

        If the starting cell is not a warehouse-type cell the result is empty.
        """
        ct = self.get_cell_type(start_x, start_y)
        if ct not in self._WH_TYPES:
            return []

        visited: Set[Tuple[int, int]] = set()
        stack = [(start_x, start_y)]
        result: List[Tuple[int, int, CellType]] = []

        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in visited:
                continue
            visited.add((cx, cy))
            cell = self.get_cell_type(cx, cy)
            if cell not in self._WH_TYPES:
                continue
            result.append((cx, cy, cell))
            for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < self.width and 0 <= ny < self.height and (nx, ny) not in visited:
                    stack.append((nx, ny))

        return result

    def get_nearest_object(self, x: int, y: int) -> Optional[Tuple[int, int, float]]:
        """
        Find the nearest unretrieved object

        Args:
            x, y: Query position

        Returns:
            Tuple of (obj_x, obj_y, distance) or None if no objects
        """
        if not self.objects:
            return None

        min_dist = float("inf")
        nearest = None

        for obj_x, obj_y in self.objects:
            dist = np.sqrt((x - obj_x) ** 2 + (y - obj_y) ** 2)
            if dist < min_dist:
                min_dist = dist
                nearest = (obj_x, obj_y, dist)

        return nearest

    def get_coverage_percentage(self, explored_map: np.ndarray) -> float:
        """
        Calculate percentage of explorable area that has been explored

        Args:
            explored_map: 2D array marking explored cells

        Returns:
            Coverage percentage (0-100)
        """
        # Count explorable cells (not obstacles)
        explorable = np.sum(self.cell_types != CellType.OBSTACLE)

        if explorable == 0:
            return 100.0

        # Count explored cells
        explored = np.sum(explored_map > 0)

        return (explored / explorable) * 100.0
