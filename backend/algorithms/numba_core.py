"""
Numba JIT-compiled core functions for performance-critical operations.

These functions are extracted from grid_manager, pathfinding, communication,
and exploration modules to leverage Numba's @njit compilation.
"""

import numpy as np
from numba import njit


@njit(cache=True)
def bresenham_line_of_sight(
    x0: int, y0: int, x1: int, y1: int, cell_types: np.ndarray, width: int, height: int
) -> bool:
    """
    Check line-of-sight using Bresenham's algorithm with obstacle occlusion.

    Traces a line from (x0, y0) to (x1, y1). Any intermediate cell that is
    an OBSTACLE (value 2) blocks the ray and returns False.

    cell_types is indexed [x, y] with shape (width, height).
    """
    OBSTACLE = 2

    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy

    cx, cy = x0, y0
    while cx != x1 or cy != y1:
        # Check all intermediate cells (not the origin)
        if cx != x0 or cy != y0:
            if 0 <= cx < width and 0 <= cy < height:
                if cell_types[cx, cy] == OBSTACLE:
                    return False

        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            cx += sx
        if e2 < dx:
            err += dx
            cy += sy

    return True


@njit(cache=True)
def get_visible_cells_numba(
    x: int, y: int, vision_radius: int, cell_types: np.ndarray, width: int, height: int
) -> np.ndarray:
    """
    Get all visible cells within vision radius using Manhattan distance and
    Bresenham ray-casting for obstacle occlusion.

    Returns an Nx3 int32 array where each row is (x, y, cell_type).
    cell_types is indexed [x, y] with shape (width, height).
    """
    # Max possible cells within Manhattan distance
    max_cells = (2 * vision_radius + 1) * (2 * vision_radius + 1)
    result = np.empty((max_cells, 3), dtype=np.int32)
    count = 0

    for dx in range(-vision_radius, vision_radius + 1):
        for dy in range(-vision_radius, vision_radius + 1):
            # Manhattan distance check
            if abs(dx) + abs(dy) > vision_radius:
                continue

            nx = x + dx
            ny = y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue

            # Observer's own cell is always visible
            if dx == 0 and dy == 0:
                result[count, 0] = nx
                result[count, 1] = ny
                result[count, 2] = cell_types[nx, ny]
                count += 1
                continue

            # Check line-of-sight with Bresenham occlusion
            if bresenham_line_of_sight(x, y, nx, ny, cell_types, width, height):
                result[count, 0] = nx
                result[count, 1] = ny
                result[count, 2] = cell_types[nx, ny]
                count += 1

    return result[:count]


@njit(cache=True)
def euclidean_heuristic(ax: int, ay: int, bx: int, by: int) -> float:
    """Euclidean distance heuristic for A*."""
    dx = ax - bx
    dy = ay - by
    return np.sqrt(dx * dx + dy * dy)


@njit(cache=True)
def manhattan_distance(ax: int, ay: int, bx: int, by: int) -> int:
    """Manhattan distance between two points."""
    return abs(ax - bx) + abs(ay - by)


@njit(cache=True)
def chebyshev_distance(ax: int, ay: int, bx: int, by: int) -> int:
    """Chebyshev distance between two points."""
    return max(abs(ax - bx), abs(ay - by))


@njit(cache=True)
def extract_explored_cells_numba(local_map: np.ndarray) -> np.ndarray:
    """
    Extract all explored (non-UNKNOWN) cells from a map.

    local_map is indexed [y, x] with shape (height, width).
    Returns an Nx3 int32 array where each row is (x, y, cell_type).
    """
    height, width = local_map.shape
    # Count non-zero cells first
    count = 0
    for y in range(height):
        for x in range(width):
            if local_map[y, x] != 0:
                count += 1

    result = np.empty((count, 3), dtype=np.int32)
    idx = 0
    for y in range(height):
        for x in range(width):
            ct = local_map[y, x]
            if ct != 0:
                result[idx, 0] = x
                result[idx, 1] = y
                result[idx, 2] = ct
                idx += 1

    return result


@njit(cache=True)
def apply_shared_map_data_numba(local_map: np.ndarray, shared: np.ndarray) -> np.ndarray:
    """
    Apply shared map data to local map. Only updates UNKNOWN (0) cells.

    local_map: (height, width) int8 array
    shared: Nx3 int32 array where each row is (x, y, cell_type)
    Returns updated local_map copy.
    """
    updated = local_map.copy()
    height, width = updated.shape

    for i in range(shared.shape[0]):
        x = shared[i, 0]
        y = shared[i, 1]
        ct = shared[i, 2]
        if 0 <= x < width and 0 <= y < height:
            if updated[y, x] == 0:
                updated[y, x] = ct

    return updated


@njit(cache=True)
def compute_exploration_potential_numba(
    ax: int,
    ay: int,
    local_map: np.ndarray,
    nearby_x: np.ndarray,
    nearby_y: np.ndarray,
    sample_radius: int,
) -> tuple:
    """
    Compute potential field for exploration and return target direction.

    local_map: (height, width) array, UNKNOWN = 0.
    nearby_x, nearby_y: 1D arrays of nearby agent positions.
    Returns (target_x, target_y).
    """
    height, width = local_map.shape
    force_x = 0.0
    force_y = 0.0

    # Attractive force towards unexplored areas
    for dx in range(-sample_radius, sample_radius + 1):
        for dy in range(-sample_radius, sample_radius + 1):
            gx = ax + dx
            gy = ay + dy
            if 0 <= gy < height and 0 <= gx < width:
                if local_map[gy, gx] == 0:
                    dist = max(np.sqrt(float(dx * dx + dy * dy)), 1.0)
                    force_x += dx / (dist * dist)
                    force_y += dy / (dist * dist)

    # Repulsive force from other agents
    n_agents = nearby_x.shape[0]
    for i in range(n_agents):
        dx = ax - nearby_x[i]
        dy = ay - nearby_y[i]
        dist = max(np.sqrt(float(dx * dx + dy * dy)), 1.0)
        if dist < 10:
            force_x += dx / (dist * dist) * 5.0
            force_y += dy / (dist * dist) * 5.0

    # Normalize and apply
    force_magnitude = np.sqrt(force_x * force_x + force_y * force_y)

    if force_magnitude > 0:
        force_x /= force_magnitude
        force_y /= force_magnitude
        target_x = int(ax + force_x * 3)
        target_y = int(ay + force_y * 3)
        return (target_x, target_y)

    return (ax, ay)


@njit(cache=True)
def compute_collision_cone_numba(
    agent_x: int,
    agent_y: int,
    agent_vx: float,
    agent_vy: float,
    other_x: int,
    other_y: int,
    other_vx: float,
    other_vy: float,
    agent_radius: float,
    time_horizon: float,
) -> tuple:
    """
    Compute if a collision cone exists.

    Returns (has_collision, avoid_x, avoid_y).
    """
    rel_pos_x = other_x - agent_x
    rel_pos_y = other_y - agent_y
    rel_vel_x = agent_vx - other_vx
    rel_vel_y = agent_vy - other_vy

    dist = np.sqrt(float(rel_pos_x * rel_pos_x + rel_pos_y * rel_pos_y))

    if dist < 0.1:
        return (True, -float(rel_pos_x), -float(rel_pos_y))

    if rel_vel_x * rel_vel_x + rel_vel_y * rel_vel_y < 0.01:
        return (False, 0.0, 0.0)

    dot_product = rel_pos_x * rel_vel_x + rel_pos_y * rel_vel_y
    if dot_product >= 0:
        return (False, 0.0, 0.0)

    t = -dot_product / (rel_vel_x * rel_vel_x + rel_vel_y * rel_vel_y)
    if t > time_horizon:
        return (False, 0.0, 0.0)

    closest_dist = np.sqrt((rel_pos_x + t * rel_vel_x) ** 2 + (rel_pos_y + t * rel_vel_y) ** 2)

    if closest_dist > agent_radius * 2:
        return (False, 0.0, 0.0)

    avoid_x = -rel_pos_y / dist
    avoid_y = rel_pos_x / dist
    return (True, avoid_x, avoid_y)


@njit(cache=True)
def get_neighbors_in_radius_numba(
    x: int, y: int, radius: int, width: int, height: int
) -> np.ndarray:
    """
    Get all cells within a given radius (Euclidean).

    Returns an Nx2 int32 array of (x, y) positions.
    """
    radius_sq = radius * radius
    max_cells = (2 * radius + 1) * (2 * radius + 1)
    result = np.empty((max_cells, 2), dtype=np.int32)
    count = 0

    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            nx = x + dx
            ny = y + dy
            if 0 <= nx < width and 0 <= ny < height:
                if dx * dx + dy * dy <= radius_sq:
                    result[count, 0] = nx
                    result[count, 1] = ny
                    count += 1

    return result[:count]


@njit(cache=True)
def is_walkable_for_agent_numba(x: int, y: int, nav_map: np.ndarray) -> bool:
    """
    Check walkability using agent navigation map.

    nav_map: (height, width) int8 array.
    OBSTACLE = 2 blocks; UNKNOWN = 0 is treated as walkable.
    """
    height, width = nav_map.shape
    if not (0 <= x < width and 0 <= y < height):
        return False
    cell = nav_map[y, x]
    return cell != 2  # CellType.OBSTACLE = 2
