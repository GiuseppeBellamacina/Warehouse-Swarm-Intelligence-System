"""
Scout Agent - Fast explorer with wide vision
"""

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import numpy as np

from backend.agents.base_agent import AgentState, BaseAgent, pos_to_tuple
from backend.algorithms.exploration import FrontierExplorer
from backend.algorithms.pathfinding import AStarPathfinder
from backend.core.communication import ObjectLocationMessage
from backend.core.decision_maker import ActionType, UtilityFunctions
from backend.core.grid_manager import CellType

if TYPE_CHECKING:
    from backend.core.warehouse_model import WarehouseModel


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
        model: "WarehouseModel",
        vision_radius: int = 3,
        communication_radius: int = 2,
        max_energy: float = 500.0,
        speed: float = 1.5,
        behavior: Optional[dict] = None,
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
                "base": 0.0,
                "move": 1.0,
                "communicate": 0.0,
            },
        )

        # ── Behavior params (overridable from UI / config) ─────────────
        _b = behavior or {}
        self._RECENT_TARGET_TTL: int = _b.get("recent_target_ttl", 50)
        self._RESCAN_AGE: int = _b.get("rescan_age", 120)
        self._DISCOVERY_TIMEOUT: int = _b.get("discovery_timeout", 80)
        self._ANTI_CLUSTER_DIST: int = _b.get("anti_cluster_distance", 8)
        self._TARGET_HYSTERESIS: int = _b.get("target_hysteresis", 15)
        self._STUCK_THRESHOLD: int = _b.get("stuck_threshold", 8)
        self._RECHARGE_THRESHOLD: float = _b.get("recharge_threshold", 0.25)
        self._FAR_FRONTIER_ENABLED: bool = _b.get("far_frontier_enabled", True)
        self._STALE_COVERAGE_PATROL: bool = _b.get("stale_coverage_patrol", True)
        self._ANTI_CLUSTERING: bool = _b.get("anti_clustering", True)
        self._SEEK_COORDINATOR: bool = _b.get("seek_coordinator", True)
        self._TARGET_LOCK_DURATION: int = _b.get("target_lock_duration", 12)
        self._MIN_FRONTIER_CLUSTER_SIZE: int = _b.get("min_frontier_cluster_size", 5)

        self.state = AgentState.EXPLORING
        self.pathfinder = AStarPathfinder(model.grid)

        # The base-class loop detector fires when position_history[-10:] has <= 3
        # unique entries.  Scouts move 2x per step so they write 2 entries/step,
        # meaning 10 entries = 5 real steps — far too sensitive.  Double the
        # history window so the same window covers ~10 real steps instead.
        self.max_history_length = 30

        # Track discovered objects to communicate
        self.newly_discovered_objects: List[Tuple[Tuple[int, int], float]] = []
        self._discovery_age: int = 0  # steps without a coordinator to receive discoveries

        # Track repeated failures on same target
        self.last_failed_target: Optional[Tuple[int, int]] = None
        self.consecutive_failures_on_target = 0

        # Last known coordinator position — used to seek the coordinator when
        # discoveries are pending but no coordinator is currently in comms range.
        self.last_seen_coordinator_pos: Optional[Tuple[int, int]] = None

        # Warehouse recharge sub-state machine (avoids getting stuck on entrance/exit)
        # Values: None | "approach" | "recharge" | "exit"
        self._scout_wh_step: Optional[str] = None
        self._scout_wh_station: Optional[dict] = None

        # Recently-reached targets: blacklisted for _RECENT_TARGET_TTL steps after arrival
        # so the scout does not immediately oscillate back to a just-explored cell.
        self._recent_targets: Dict[Tuple[int, int], int] = {}

        # Target lock: step at which the current target was committed to.
        # While locked, step_decide skips the full frontier re-evaluation to
        # prevent erratic direction changes when entering a new zone.
        self._target_lock_step: int = 0

        # Coverage map: tracks the model step at which each cell was last physically
        # within this scout's vision radius.  When all frontier exploration is
        # exhausted, cells absent from vision for _RESCAN_AGE steps become valid
        # re-exploration targets, letting the scout cycle the full map continuously.
        # NOTE: this map is intentionally NOT shared via MapDataMessage — it is a
        # private urgency signal that does not affect shared topology knowledge.
        H = model.grid.height
        W = model.grid.width
        self._coverage_step: np.ndarray = np.zeros((H, W), dtype=np.int32)

        # Setup decision making
        self._setup_decision_maker()

    def _setup_decision_maker(self) -> None:
        """Setup utility functions for decision making"""
        self.decision_maker.register_utility_function(
            ActionType.EXPLORE, UtilityFunctions.explore_utility
        )
        self.decision_maker.register_utility_function(
            ActionType.RECHARGE, UtilityFunctions.recharge_utility
        )

    def step_sense(self) -> None:
        """Perceive environment and track newly discovered objects"""
        # Store old known objects
        old_objects = set(self.known_objects.keys())

        # Call parent sense
        super().step_sense()

        # Check for newly discovered objects
        new_objects = set(self.known_objects.keys())
        discovered = new_objects - old_objects

        if discovered:
            print(
                f"{self.tag} SENSE: Discovered {len(discovered)} new objects at positions: {list(discovered)}"
            )
            # Add to list of objects to communicate
            for obj_pos in discovered:
                obj_value = self.known_objects.get(obj_pos, 1.0)
                self.newly_discovered_objects.append((obj_pos, obj_value))

        # Update last known coordinator position whenever one is within vision
        # (use vision_radius so we track coordinator movements sooner than comm range)
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        for agent in self.model.agents:
            if getattr(agent, "role", None) == "coordinator" and agent.pos:
                c_pos = pos_to_tuple(agent.pos)
                dist = abs(c_pos[0] - my_pos[0]) + abs(c_pos[1] - my_pos[1])
                if dist <= self.vision_radius:
                    self.last_seen_coordinator_pos = c_pos
                    break

        # Mark all cells currently within vision as surveyed in the coverage map.
        # The coverage map is local-only (never broadcast) and decays over time;
        # cells not seen for _RESCAN_AGE steps become re-exploration targets so
        # the scout continuously cycles through the full map.
        cs = self.model.current_step
        r = self.vision_radius
        H, W = self._coverage_step.shape
        y0 = max(0, my_pos[1] - r)
        y1 = min(H, my_pos[1] + r + 1)
        x0 = max(0, my_pos[0] - r)
        x1 = min(W, my_pos[0] + r + 1)
        self._coverage_step[y0:y1, x0:x1] = cs

    def step_decide(self) -> None:
        """Decide next action based on utility"""
        # Reset communication flag
        self.should_communicate_this_step = False

        # If the recharge sub-machine is already running, let step_act handle it
        if self._scout_wh_step is not None:
            return

        # ---- Priority 1: Recharge if critically low (< 25 %) ----
        # MUST be checked before discoveries so a scout carrying pending discoveries
        # never starves to death while looping on the coordinator-broadcast path.
        if self.energy < self.max_energy * self._RECHARGE_THRESHOLD:
            if self._scout_wh_step is None:
                # Start the warehouse recharge sub-state machine
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                visible_entrances = [
                    wh
                    for wh in self.known_warehouses
                    if self.model.grid.get_cell_type(*wh) == CellType.WAREHOUSE_ENTRANCE
                ]
                station = self.model.get_best_warehouse_for(
                    pos=my_pos,
                    known_entrances=visible_entrances,
                    agent_energy=self.energy,
                )
                self._scout_wh_station = station
                self._scout_wh_step = "approach"
                self.state = AgentState.RECHARGING
                self.target_position = station.get("entrance")
                print(
                    f"{self.tag} LOW-E ({self.energy:.1f}), "
                    f"heading to WH entrance {self.target_position}"
                )
            # Sub-machine runs in step_act — just return here
            return

        # ---- Priority 2: Communicate discoveries to any nearby coordinator ----
        if self.newly_discovered_objects:
            # _discovery_age counts steps WITHOUT a known coordinator destination.
            # While we are actively heading somewhere don't count — reset instead.
            if self.last_seen_coordinator_pos:
                self._discovery_age = 0  # we have a destination; no timeout
            else:
                self._discovery_age += 1
            nearby = self.get_nearby_agents(self.communication_radius)
            coordinators = [a for a in nearby if getattr(a, "role", None) == "coordinator"]
            if coordinators:
                self.should_communicate_this_step = True
                return
            # No coordinator in range — head toward the last known coordinator position
            # so the scout can hand off its discoveries without just waiting idly.
            if self._SEEK_COORDINATOR and self.last_seen_coordinator_pos:
                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                dist_to_saved = abs(self.last_seen_coordinator_pos[0] - my_pos[0]) + abs(
                    self.last_seen_coordinator_pos[1] - my_pos[1]
                )
                # Arrived at last known position but coordinator not here → it moved;
                # do a wide scan first to find the coordinator's new position before
                # giving up and falling through to exploration.
                if dist_to_saved <= max(1, self.communication_radius):
                    self.last_seen_coordinator_pos = None
                    # Wide scan: check all coordinators within 3× comm radius
                    new_coord_pos = self._wide_scan_for_coordinator()
                    if new_coord_pos:
                        self.last_seen_coordinator_pos = new_coord_pos
                        print(
                            f"{self.tag} SEEK-COORD: old pos stale, "
                            f"found coordinator at {new_coord_pos} via wide scan"
                        )
                    else:
                        print(
                            f"{self.tag} SEEK-COORD: reached stale pos, "
                            f"coordinator not found nearby — resuming exploration"
                        )
                    # Fall through (if new_coord_pos set, next step will use it)
                else:
                    if self.target_position != self.last_seen_coordinator_pos:
                        self.target_position = self.last_seen_coordinator_pos
                        self.path = []
                    self.state = AgentState.MOVING_TO_TARGET
                    print(
                        f"{self.tag} SEEK-COORD: heading to last known "
                        f"coordinator pos {self.last_seen_coordinator_pos} "
                        f"({len(self.newly_discovered_objects)} discoveries pending)"
                    )
                    return
            # No known coordinator position — keep discoveries, but discard after timeout
            # of being truly unable to locate any coordinator.
            if self._discovery_age > self._DISCOVERY_TIMEOUT:
                print(
                    f"{self.tag} TIMEOUT: discarding "
                    f"{len(self.newly_discovered_objects)} stale discoveries"
                )
                self.newly_discovered_objects = []
                self._discovery_age = 0
        else:
            self._discovery_age = 0

        # ---- Priority 3: Frontier-based exploration ----
        self.state = AgentState.EXPLORING
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        current_step = self.model.current_step

        # Target lock: if we already have a valid target that was recently
        # committed to, skip the full frontier re-evaluation.  This prevents
        # the scout from flipping targets every step when entering a new zone
        # with many frontiers popping up.  The lock is released when:
        #   - the target is reached or cleared (target_position becomes None)
        #   - the scout has been stuck for too long
        #   - the lock duration expires (ensures the scout isn't stuck forever)
        if (
            self.target_position is not None
            and current_step - self._target_lock_step < self._TARGET_LOCK_DURATION
            and self.consecutive_failures_on_target < self._STUCK_THRESHOLD
        ):
            self.state = AgentState.MOVING_TO_TARGET
            return

        # Prune expired recent-target entries to keep the dict small.
        self._recent_targets = {
            pos: step
            for pos, step in self._recent_targets.items()
            if current_step - step < self._RECENT_TARGET_TTL
        }

        # Expire unreachable_targets entries that are older than their 30-step TTL
        # so the dict doesn't grow unbounded (base_agent never auto-purges it).
        for pos in [p for p, s in self.unreachable_targets.items() if current_step - s >= 30]:
            del self.unreachable_targets[pos]

        frontiers = FrontierExplorer.find_frontiers(
            self.local_map, min_cluster_size=self._MIN_FRONTIER_CLUSTER_SIZE
        )
        # If the high threshold filtered everything out, retry with minimum=1
        # so the scout still has *something* to explore.
        if not frontiers:
            frontiers = FrontierExplorer.find_frontiers(self.local_map, min_cluster_size=1)

        # Filter blacklisted / stale frontiers and recently-reached targets.
        # Skipping recently-reached targets prevents immediate oscillation back
        # to a frontier the scout just finished exploring.
        valid_frontiers = []
        for frontier_pos, cluster_size in frontiers:
            if frontier_pos in self.unreachable_targets:
                failed_step = self.unreachable_targets[frontier_pos]
                if current_step - failed_step < 30:
                    continue
                del self.unreachable_targets[frontier_pos]
            if frontier_pos in self._recent_targets:
                continue  # visited too recently
            valid_frontiers.append((frontier_pos, cluster_size))

        # Anti-clustering: prefer frontiers far from other scouts
        nearby = self.get_nearby_agents(self.communication_radius)
        scout_positions = [
            pos_to_tuple(a.pos)
            for a in nearby
            if getattr(a, "role", None) == "scout" and a.pos and pos_to_tuple(a.pos) != my_pos
        ]
        if self._ANTI_CLUSTERING:
            anti_clustered = [
                f
                for f in valid_frontiers
                if all(
                    abs(f[0][0] - sp[0]) + abs(f[0][1] - sp[1]) >= self._ANTI_CLUSTER_DIST
                    for sp in scout_positions
                )
            ]
            frontiers_to_use = anti_clustered if anti_clustered else valid_frontiers
        else:
            frontiers_to_use = valid_frontiers

        if frontiers_to_use:
            if self._FAR_FRONTIER_ENABLED:
                # Prefer frontiers that require actual travel (> 2× vision radius) so the
                # scout pushes into genuinely new territory rather than hopping between
                # tiny adjacent clusters it can already partially see.
                min_dist = max(self.vision_radius * 2, 8)
                far_frontiers = [
                    f
                    for f in frontiers_to_use
                    if abs(f[0][0] - my_pos[0]) + abs(f[0][1] - my_pos[1]) >= min_dist
                ]
                # When ONLY nearby frontiers remain, skip them and jump directly to
                # stale-coverage patrol.  This avoids the A→B→A oscillation between
                # a handful of adjacent residual clusters.
                if not far_frontiers:
                    if self._STALE_COVERAGE_PATROL:
                        self._pick_stale_coverage_target(my_pos)
                        if self.target_position is not None:
                            return  # heading to a stale area
                    # No stale area either — fall through and let nearby frontiers run
                    frontiers_to_use = frontiers_to_use  # keep as-is (already near-only)
                else:
                    frontiers_to_use = far_frontiers

            nearby_positions = [pos_to_tuple(a.pos) for a in nearby if a.pos]
            best = FrontierExplorer.select_best_frontier(frontiers_to_use, my_pos, nearby_positions)
            if best:
                # Only update target when the new best is genuinely far from the
                # current one — prevents jittering between two nearby candidates.
                if (
                    not self.target_position
                    or abs(best[0] - self.target_position[0])
                    + abs(best[1] - self.target_position[1])
                    > self._TARGET_HYSTERESIS
                ):
                    self.target_position = best
                    self.path = []
                    self.consecutive_failures_on_target = 0
                    self._target_lock_step = current_step
                self.state = AgentState.MOVING_TO_TARGET
                return

        # ---- Fallback 1: navigate towards a random unknown boundary cell ----
        self._pick_unexplored_target(my_pos)

        # ---- Fallback 2: re-explore stale areas (cyclic full-map coverage) ----
        # Triggered only when _pick_unexplored_target found nothing (map fully
        # explored).  The scout seeks the oldest-unseen explored cell so it
        # continuously re-sweeps the warehouse for newly-appeared objects.
        if self.target_position is None and self._STALE_COVERAGE_PATROL:
            self._pick_stale_coverage_target(my_pos)

    def _pick_stale_coverage_target(self, my_pos: Tuple[int, int]) -> None:
        """
        Re-exploration fallback: target the best stale cell balancing age and distance.

        When the whole map has been explored (no frontiers, no UNKNOWN boundary
        cells), this method finds cells that have been absent from the scout's
        vision for at least _RESCAN_AGE steps and picks the one with the best
        score = age / (dist + 1).  This creates an organic patrol pattern that
        covers the full warehouse cyclically and detects newly-appeared objects.
        """
        current_step = self.model.current_step
        threshold_step = current_step - self._RESCAN_AGE

        _WH_TYPES = (
            CellType.WAREHOUSE,
            CellType.WAREHOUSE_ENTRANCE,
            CellType.WAREHOUSE_EXIT,
            CellType.OBSTACLE,
        )

        # Find all explored cells (local_map != UNKNOWN) that are stale.
        stale_mask = (self.local_map != 0) & (self._coverage_step <= threshold_step)
        stale_ys, stale_xs = np.where(stale_mask)

        if len(stale_ys) == 0:
            return  # everything covered recently

        # Compute age and distance for all stale cells vectorised
        ages = (current_step - self._coverage_step[stale_ys, stale_xs]).astype(np.float32)
        dists = (np.abs(stale_xs - my_pos[0]) + np.abs(stale_ys - my_pos[1])).astype(np.float32)

        # Score = age / (dist + 1): prefer old AND nearby cells over
        # the old pure-farthest heuristic which caused pathologically long traversals.
        scores = ages / (dists + 1.0)
        sort_idx = np.argsort(-scores)[:50]

        best_pos: Optional[Tuple[int, int]] = None
        best_score = -1.0

        for i in sort_idx.tolist():
            cx, cy = int(stale_xs[i]), int(stale_ys[i])
            pos = (cx, cy)
            if not self.model.grid.is_walkable(*pos):
                continue
            if self.model.grid.get_cell_type(*pos) in _WH_TYPES:
                continue
            if pos in self.unreachable_targets:
                continue
            best_pos = pos
            best_score = float(scores[i])
            break  # take the top-scoring walkable cell

        if best_pos is None:
            return

        age = current_step - self._coverage_step[best_pos[1], best_pos[0]]
        self.target_position = best_pos
        self.path = []
        self.state = AgentState.MOVING_TO_TARGET
        self._target_lock_step = current_step
        print(
            f"{self.tag} RESCAN: cycling to stale area at {best_pos} "
            f"(not seen for {age} steps, score={best_score:.1f})"
        )

    def _wide_scan_for_coordinator(self) -> Optional[Tuple[int, int]]:
        """
        Scan all agents within 3× communication_radius to find the nearest coordinator.
        Used after the saved coordinator position turns out to be stale, so the scout
        can update its heading without falling back to blind exploration.
        Returns the nearest coordinator position found, or None.
        """
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        # Use a generous radius (quarter of grid diagonal) so the scout can detect
        # coordinators that drifted away from the last-seen position.
        search_radius = max(12, self.communication_radius * 4)
        best_pos: Optional[Tuple[int, int]] = None
        best_dist = float("inf")
        for agent in self.model.agents:
            if getattr(agent, "role", None) == "coordinator" and agent.pos:
                c_pos = pos_to_tuple(agent.pos)
                dist = abs(c_pos[0] - my_pos[0]) + abs(c_pos[1] - my_pos[1])
                if dist <= search_radius and dist < best_dist:
                    best_dist = dist
                    best_pos = c_pos
        return best_pos

    def _pick_unexplored_target(self, my_pos: Tuple[int, int]) -> None:
        """Navigate towards a random UNKNOWN boundary cell via A* when no frontier found.
        Uses numpy vectorisation instead of Python loops.
        """
        # Boolean mask: UNKNOWN cells (value 0)
        unknown_mask = self.local_map == 0
        if not np.any(unknown_mask):
            self.target_position = None
            self.state = AgentState.EXPLORING
            return

        # Pad and check 4-neighbours to find boundary UNKNOWN cells
        # (UNKNOWN cells adjacent to at least one explored cell)
        padded = np.pad(self.local_map, 1, mode="constant", constant_values=0)
        has_explored_neighbour = np.zeros_like(unknown_mask)
        for dy, dx in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            has_explored_neighbour |= (
                padded[1 + dy : padded.shape[0] - 1 + dy, 1 + dx : padded.shape[1] - 1 + dx] != 0
            )

        boundary_mask = unknown_mask & has_explored_neighbour
        b_ys, b_xs = np.where(boundary_mask)

        if len(b_ys) == 0:
            self.target_position = None
            self.state = AgentState.EXPLORING
            return

        # Shuffle and try up to 20 candidates
        indices = np.arange(len(b_ys))
        np.random.shuffle(indices)
        for idx in indices[:20]:
            candidate = (int(b_xs[idx]), int(b_ys[idx]))
            if candidate in self.unreachable_targets:
                continue
            if not self.model.grid.is_walkable(*candidate):
                continue
            self.target_position = candidate
            self.path = []
            self.state = AgentState.MOVING_TO_TARGET
            self._target_lock_step = self.model.current_step
            return

        # Absolute fallback
        self.target_position = None
        self.state = AgentState.EXPLORING

    def step_act(self) -> None:
        """Execute decided action: COMMUNICATE or MOVE (not both)"""
        if self.energy <= 0:
            return

        # OPTION 1: Communicate newly discovered objects to coordinators
        if self.should_communicate_this_step and self.newly_discovered_objects:
            sent = self._broadcast_discovered_objects()
            # Only consume the list when at least one coordinator actually received it.
            # If the coordinator moved out of range between decide and act, the objects
            # are preserved for the next step rather than silently discarded.
            if sent:
                self.newly_discovered_objects = []
                self._discovery_age = 0
            return  # Don't move this step regardless (avoid double-action)

        # OPTION 2: Warehouse recharge sub-state machine
        if self._scout_wh_step is not None:
            self._execute_scout_recharge_step()
            return

        # OPTION 3: Move based on state (scouts move 2× per step when speed > 1)
        moves_per_step = 2 if self.speed > 1.0 else 1

        for _ in range(moves_per_step):
            if self.energy <= 0:
                break

            if self.state == AgentState.MOVING_TO_TARGET and self.target_position:
                moved = self.move_towards(self.target_position)

                my_pos = pos_to_tuple(self.pos) if self.pos else None
                if my_pos and my_pos == self.target_position:
                    # Remember this target so we don't immediately loop back to it.
                    self._recent_targets[self.target_position] = self.model.current_step
                    self.target_position = None
                    self.state = AgentState.EXPLORING
                    self.consecutive_failures_on_target = 0
                    break

                if not moved:
                    self.consecutive_failures_on_target += 1
                    if self.consecutive_failures_on_target >= self._STUCK_THRESHOLD:
                        print(f"{self.tag} STUCK: giving up on " f"{self.target_position}")
                        if self.target_position:
                            self.unreachable_targets[self.target_position] = self.model.current_step
                        self.target_position = None
                        self.path = []
                        self.state = AgentState.EXPLORING
                        self.consecutive_failures_on_target = 0
                else:
                    self.consecutive_failures_on_target = 0

            elif self.state == AgentState.EXPLORING:
                # No target set — compute one immediately instead of random-walking.
                # BUT: if position_history is empty, base_agent just fired the loop
                # detector this same sub-step and cleared the path.  Don't pick a
                # new target immediately — the area is congested right now.  Let
                # step_decide handle it cleanly next step.
                if not self.position_history:
                    break  # skip this sub-step entirely

                my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)

                # Fallback 1: boundary of unexplored area
                self._pick_unexplored_target(my_pos)

                # Fallback 2: oldest unvisited explored cell (cyclic patrol)
                if self.target_position is None and self._STALE_COVERAGE_PATROL:
                    self._pick_stale_coverage_target(my_pos)

                if self.target_position:
                    self.move_towards(self.target_position)
                else:
                    # Last resort: move toward the centroid of all UNKNOWN cells
                    # in the local map — purely directional, no randomness.
                    unknown_ys, unknown_xs = np.where(self.local_map == 0)
                    if len(unknown_xs) > 0:
                        cx = int(np.mean(unknown_xs))
                        cy = int(np.mean(unknown_ys))
                        # Set as temporary target so move_towards can path-find;
                        # it will be overwritten by step_decide next step.
                        self.target_position = (cx, cy)
                        self.path = []
                        self.move_towards(self.target_position)
                        self.target_position = None  # don't persist stale centroid

            elif self.state == AgentState.RECHARGING:
                if self.target_position:
                    self.move_towards(self.target_position)
                break  # Only one move per step when recharging

    def _execute_scout_recharge_step(self) -> None:
        """
        Three-phase warehouse recharge: approach entrance → recharge at interior cell → exit.
        Ensures the scout never stays parked on entrance/exit cells.
        """
        my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
        station = self._scout_wh_station or {}

        if self._scout_wh_step == "approach":
            entrance = station.get("entrance")
            if not entrance:
                self._scout_wh_step = None
                self._scout_wh_station = None
                self.state = AgentState.EXPLORING
                return
            cell_type = self.model.grid.get_cell_type(*my_pos)
            at_or_inside = (
                my_pos == entrance
                or cell_type == CellType.WAREHOUSE
                or cell_type == CellType.WAREHOUSE_ENTRANCE
                or cell_type == CellType.WAREHOUSE_EXIT
            )
            if at_or_inside:
                if self.energy >= self.max_energy * 0.80:
                    # Enough energy — skip recharge, exit immediately
                    print(
                        f"{self.tag} WH: energy sufficient "
                        f"({self.energy:.1f}/{self.max_energy}), skipping recharge"
                    )
                    exit_cell = station.get("exit") or entrance
                    self._scout_wh_step = "exit"
                    self.target_position = exit_cell
                    if my_pos != exit_cell:
                        self.move_towards(exit_cell)
                else:
                    # Need recharge — join FIFO queue near exit
                    queue_cell = self.model.get_queue_slot(station)
                    self._scout_wh_step = "recharge"
                    self.target_position = queue_cell
                    print(f"{self.tag} WH: at entrance, joining queue at {queue_cell}")
                    if my_pos != queue_cell:
                        self.move_towards(queue_cell)
            else:
                # If the entrance cell is occupied, ask the blocker to move
                blocker = self._get_agent_at_pos(entrance)
                if blocker is not None:
                    self._send_clear_way_request(entrance, blocker)
                self.move_towards(entrance)

        elif self._scout_wh_step == "recharge":
            # target_position holds the assigned FIFO queue slot (set during approach)
            recharge_cell = self.target_position or station.get("recharge_cell")
            # Only recharge when exactly at the assigned FIFO queue slot
            cell_type = self.model.grid.get_cell_type(*my_pos)
            at_recharge = (
                recharge_cell is not None
                and my_pos == recharge_cell
                and cell_type
                not in (
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                )
            )
            if at_recharge:
                rate = self.model.config.warehouse.recharge_rate
                self.recharge_energy(rate)
                if self.energy >= self.max_energy * 0.90:
                    exit_cell = station.get("exit") or station.get("entrance")
                    self._scout_wh_step = "exit"
                    self.target_position = exit_cell
                    print(f"{self.tag} WH: recharged, heading to exit {exit_cell}")
                    # Move toward exit immediately
                    if exit_cell and my_pos != exit_cell:
                        self.move_towards(exit_cell)
            else:
                if recharge_cell:
                    self.move_towards(recharge_cell)
                # recharge_cell should always be set by approach; if not, just wait

        elif self._scout_wh_step == "exit":
            exit_cell = station.get("exit") or station.get("entrance")
            cell_type = self.model.grid.get_cell_type(*my_pos)
            # Finish when on the exit cell OR when already outside warehouse
            left_wh = (
                not exit_cell
                or my_pos == exit_cell
                or cell_type
                not in (
                    CellType.WAREHOUSE,
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                )
            )
            if left_wh:
                print(f"{self.tag} WH: exited, resuming exploration")
                self._scout_wh_step = None
                self._scout_wh_station = None
                self.state = AgentState.EXPLORING
                self.target_position = None
                self.path = []
                # Move off the exit cell immediately so it doesn't block
                if my_pos == exit_cell and self.pos:
                    for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                        np_ = (my_pos[0] + dx, my_pos[1] + dy)
                        if (
                            0 <= np_[0] < self.model.grid.width
                            and 0 <= np_[1] < self.model.grid.height
                        ):
                            nc = self.model.grid.get_cell_type(*np_)
                            if nc not in (
                                CellType.WAREHOUSE,
                                CellType.WAREHOUSE_ENTRANCE,
                                CellType.WAREHOUSE_EXIT,
                                CellType.OBSTACLE,
                            ) and self.model.grid.is_cell_empty(np_):
                                self.model.grid.move_agent(self, np_)
                                break
            else:
                if exit_cell:
                    self.move_towards(exit_cell)

    def _broadcast_discovered_objects(self) -> bool:
        """Send object location messages to nearby coordinators.

        Returns True if at least one coordinator received the broadcast,
        False if no coordinator was in range (caller should NOT clear the list).
        """
        # Get nearby coordinators
        nearby = self.get_nearby_agents(self.communication_radius)
        coordinators = [a for a in nearby if getattr(a, "role", None) == "coordinator"]

        if not coordinators:
            return False

        coordinator_ids = [
            getattr(c, "unique_id", 0) for c in coordinators if hasattr(c, "unique_id")
        ]
        print(
            f"{self.tag} COMM: Broadcasting "
            f"{len(self.newly_discovered_objects)} objects to "
            f"coordinators {coordinator_ids}"
        )

        # Send messages about each newly discovered object
        for obj_pos, obj_value in self.newly_discovered_objects:
            message = ObjectLocationMessage(
                sender_id=self.unique_id or 0,
                timestamp=self.model.current_step,
                object_position=obj_pos,
                object_value=obj_value,
            )
            if coordinator_ids:
                self.model.comm_manager.send_message(message, coordinator_ids)
                print(
                    f"{self.tag} -> COORD {coordinator_ids}: "
                    f"object at {obj_pos} (v={obj_value:.1f})"
                )

        # Log message for UI
        if self.newly_discovered_objects:
            self.log_message(
                direction="sent",
                message_type="object_location",
                details=f"Broadcast {len(self.newly_discovered_objects)} objects",
                target_ids=coordinator_ids,
            )

        # Consume energy for broadcast
        self.consume_energy(self.energy_consumption["communicate"] * len(coordinators))
        self.last_communication_step = self.model.current_step
        return True
