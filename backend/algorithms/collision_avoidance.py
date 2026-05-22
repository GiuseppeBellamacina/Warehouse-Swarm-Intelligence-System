"""
Collision avoidance using Velocity Obstacles and cell reservation
"""

from typing import List, Optional, Tuple

import numpy as np

from backend.algorithms.numba_core import compute_collision_cone_numba


class VelocityObstacles:
    """
    Velocity Obstacles (VO) collision avoidance

    Computes collision cones in velocity space and selects safe velocities
    """

    @staticmethod
    def compute_collision_cone(
        agent_pos: Tuple[int, int],
        agent_vel: Tuple[float, float],
        other_pos: Tuple[int, int],
        other_vel: Tuple[float, float],
        agent_radius: float = 0.5,
        time_horizon: float = 2.0,
    ) -> Optional[Tuple[float, float]]:
        """
        Compute if a collision cone exists. Delegates to Numba.
        """
        has_collision, avoid_x, avoid_y = compute_collision_cone_numba(
            agent_pos[0],
            agent_pos[1],
            agent_vel[0],
            agent_vel[1],
            other_pos[0],
            other_pos[1],
            other_vel[0],
            other_vel[1],
            agent_radius,
            time_horizon,
        )
        if has_collision:
            return (avoid_x, avoid_y)
        return None

    @staticmethod
    def select_safe_velocity(
        agent_pos: Tuple[int, int],
        desired_vel: Tuple[float, float],
        nearby_agents: List[Tuple[Tuple[int, int], Tuple[float, float]]],
        max_speed: float = 1.5,
    ) -> Tuple[float, float]:
        """
        Select a safe velocity avoiding collision cones

        Args:
            agent_pos: Agent position
            desired_vel: Desired velocity towards goal
            nearby_agents: List of (position, velocity) for nearby agents
            max_speed: Maximum speed

        Returns:
            Safe velocity vector
        """
        # Start with desired velocity
        safe_vel = desired_vel

        # Check each nearby agent
        avoidance_vectors = []

        for other_pos, other_vel in nearby_agents:
            avoid = VelocityObstacles.compute_collision_cone(
                agent_pos, desired_vel, other_pos, other_vel
            )

            if avoid:
                avoidance_vectors.append(avoid)

        # Combine avoidance vectors
        if avoidance_vectors:
            # Average avoidance directions
            avg_avoid_x = np.mean([v[0] for v in avoidance_vectors])
            avg_avoid_y = np.mean([v[1] for v in avoidance_vectors])

            # Blend with desired velocity
            blend_factor = 0.6  # 60% avoidance, 40% desired
            safe_vel = (
                desired_vel[0] * (1 - blend_factor) + avg_avoid_x * blend_factor,
                desired_vel[1] * (1 - blend_factor) + avg_avoid_y * blend_factor,
            )

        # Clamp to max speed
        speed = np.sqrt(safe_vel[0] ** 2 + safe_vel[1] ** 2)
        if speed > max_speed:
            safe_vel = (safe_vel[0] / speed * max_speed, safe_vel[1] / speed * max_speed)

        return (float(safe_vel[0]), float(safe_vel[1]))


class CellReservationSystem:
    """
    Grid-based cell reservation to prevent collisions

    Agents reserve cells they plan to move to, preventing conflicts
    """

    def __init__(self):
        self.reservations: dict[Tuple[int, int], int] = {}  # cell -> agent_id
        self.agent_priorities: dict[int, int] = {}  # agent_id -> priority

    def set_priority(self, agent_id: int, priority: int) -> None:
        """
        Set priority for an agent

        Higher priority agents can override lower priority reservations
        """
        self.agent_priorities[agent_id] = priority

    def try_reserve(self, agent_id: int, cell: Tuple[int, int]) -> bool:
        """
        Try to reserve a cell for movement

        Args:
            agent_id: ID of agent requesting reservation
            cell: Cell to reserve

        Returns:
            True if reservation successful
        """
        if cell not in self.reservations:
            # Cell free, reserve it
            self.reservations[cell] = agent_id
            return True

        # Cell already reserved, check priority
        current_holder = self.reservations[cell]

        agent_priority = self.agent_priorities.get(agent_id, 0)
        holder_priority = self.agent_priorities.get(current_holder, 0)

        if agent_priority > holder_priority:
            # Override reservation
            self.reservations[cell] = agent_id
            return True

        # Can't reserve
        return False

    def release(self, agent_id: int) -> None:
        """Release all reservations held by agent"""
        cells_to_remove = [cell for cell, holder in self.reservations.items() if holder == agent_id]

        for cell in cells_to_remove:
            del self.reservations[cell]

    def clear_all(self) -> None:
        """Clear all reservations (call at end of step)"""
        self.reservations.clear()

    def is_reserved(self, cell: Tuple[int, int]) -> bool:
        """Check if a cell is reserved"""
        return cell in self.reservations

    def get_holder(self, cell: Tuple[int, int]) -> Optional[int]:
        """Get the agent ID that reserved a cell"""
        return self.reservations.get(cell)


class CollisionAvoidance:
    """
    Hybrid collision avoidance combining VO and cell reservation
    """

    @staticmethod
    def get_safe_move(
        agent_id: int,
        current_pos: Tuple[int, int],
        target_pos: Tuple[int, int],
        nearby_agents: List[Tuple[int, Tuple[int, int]]],
        reservation_system: CellReservationSystem,
        grid_manager,
        agent_priority: int = 0,
    ) -> Optional[Tuple[int, int]]:
        """
        Get a safe next move towards target using hybrid avoidance

        Args:
            agent_id: Agent ID
            current_pos: Current position
            target_pos: Desired target position
            nearby_agents: List of (agent_id, position) for nearby agents
            reservation_system: Cell reservation system
            grid_manager: Grid manager
            agent_priority: Priority level

        Returns:
            Safe next position or None if no safe move
        """
        # Calculate desired direction
        dx = target_pos[0] - current_pos[0]
        dy = target_pos[1] - current_pos[1]

        # Normalize direction
        dist = max(np.sqrt(dx * dx + dy * dy), 1.0)
        dx /= dist
        dy /= dist

        # Generate candidate moves (prefer diagonal towards goal)
        candidates = []

        # Exact direction
        move_x = int(np.round(dx))
        move_y = int(np.round(dy))
        if move_x != 0 or move_y != 0:
            candidates.append((current_pos[0] + move_x, current_pos[1] + move_y))

        # Adjacent moves
        if move_x != 0:
            candidates.append((current_pos[0] + move_x, current_pos[1]))
        if move_y != 0:
            candidates.append((current_pos[0], current_pos[1] + move_y))

        # Diagonal alternatives
        candidates.append((current_pos[0] + 1, current_pos[1] + 1))
        candidates.append((current_pos[0] + 1, current_pos[1] - 1))
        candidates.append((current_pos[0] - 1, current_pos[1] + 1))
        candidates.append((current_pos[0] - 1, current_pos[1] - 1))

        # Try each candidate
        for candidate in candidates:
            # Check walkable
            if not grid_manager.is_walkable(*candidate):
                continue

            # Check not occupied by other agent
            occupied = False
            for other_id, other_pos in nearby_agents:
                if other_id != agent_id and other_pos == candidate:
                    occupied = True
                    break

            if occupied:
                continue

            # Try to reserve
            if reservation_system.try_reserve(agent_id, candidate):
                return candidate

        # No safe move found
        return None
