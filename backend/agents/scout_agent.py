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
        # All keys are guaranteed present by Pydantic (ScoutBehaviorParams).
        if behavior is None:
            from backend.config.schemas import ScoutBehaviorParams

            behavior = ScoutBehaviorParams().model_dump()
        _b = behavior
        self._RECENT_TARGET_TTL: int = _b["recent_target_ttl"]
        self._RESCAN_AGE: int = _b["rescan_age"]
        self._DISCOVERY_TIMEOUT: int = _b["discovery_timeout"]
        self._ANTI_CLUSTER_DIST: int = _b["anti_cluster_distance"]
        self._TARGET_HYSTERESIS: int = _b["target_hysteresis"]
        self._STUCK_THRESHOLD: int = _b["stuck_threshold"]
        self._RECHARGE_THRESHOLD: float = _b["recharge_threshold"]
        self._FAR_FRONTIER_ENABLED: bool = _b["far_frontier_enabled"]
        self._STALE_COVERAGE_PATROL: bool = _b["stale_coverage_patrol"]
        self._ANTI_CLUSTERING: bool = _b["anti_clustering"]
        self._SEEK_COORDINATOR: bool = _b["seek_coordinator"]
        self._SEEK_COORDINATOR_DELAY: int = _b["seek_coordinator_delay"]
        self._TARGET_LOCK_DURATION: int = _b["target_lock_duration"]
        self._MIN_FRONTIER_CLUSTER_SIZE: int = _b["min_frontier_cluster_size"]
        self._ZONE_DIVISIONS: int = _b["zone_divisions"]

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

        # Exploration target — mirrors retriever/coordinator convention so the
        # base_agent communication layer can broadcast it for area division.
        self._explore_target: Optional[Tuple[int, int]] = None

        # Last known coordinator position — used to seek the coordinator when
        # discoveries are pending but no coordinator is currently in comms range.
        self.last_seen_coordinator_pos: Optional[Tuple[int, int]] = None
        self._last_seen_coordinator_step: int = 0

        # Step at which this scout last had ANY agent within communication range.
        # Used to decide whether passive relay (MapDataMessage) is likely propagating
        # discoveries, avoiding the need to actively seek a coordinator.
        self._last_agent_contact_step: int = 0

        # Warehouse recharge sub-state machine (avoids getting stuck on entrance/exit)
        # Values: None | "approach" | "recharge" | "exit"
        self._scout_wh_step: Optional[str] = None
        self._scout_wh_station: Optional[dict] = None

        # Deferred seek-coordinator: when True, the scout will seek the
        # coordinator as soon as it finishes (or abandons) its current
        # exploration path — instead of aborting mid-path.
        self._pending_seek_coordinator: bool = False

        # Seek-coordinator cooldown: after a failed wide-scan, the scout
        # pauses seek attempts until this step to avoid permanent give-up.
        self._seek_cooldown_until: int = 0

        # Recently-reached targets: blacklisted for _RECENT_TARGET_TTL steps after arrival
        # so the scout does not immediately oscillate back to a just-explored cell.
        self._recent_targets: Dict[Tuple[int, int], int] = {}

        # Target lock: step at which the current target was committed to.
        # While locked, step_decide skips the full frontier re-evaluation to
        # prevent erratic direction changes when entering a new zone.
        self._target_lock_step: int = 0

        # Zone-based exploration: current target zone (zi, zj) index
        self._current_zone: Optional[Tuple[int, int]] = None
        self._zone_switch_step: int = 0  # step at which we last switched zone

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

    # ── Zone-based macro routing helpers ──────────────────────────────

    def _zone_grid(self) -> Tuple[int, int]:
        """Return (n_cols, n_rows) for the current zone_divisions setting.

        ``zone_divisions`` is the *total number* of zones.  This method
        finds the (cols, rows) factorisation that makes each zone as
        square as possible given the grid dimensions.
        """
        n = self._ZONE_DIVISIONS
        if n <= 1:
            return (1, 1)
        W, H = self.model.grid.width, self.model.grid.height
        best_cols, best_rows = 1, n
        best_diff = float("inf")
        for c in range(1, n + 1):
            if n % c == 0:
                r = n // c
                # aspect ratio of each zone cell
                zw = W / c
                zh = H / r
                diff = abs(zw - zh)
                if diff < best_diff:
                    best_diff = diff
                    best_cols, best_rows = c, r
        return best_cols, best_rows

    def _get_zone_bounds(self, zone: Tuple[int, int]) -> Tuple[int, int, int, int]:
        """Return (x0, y0, x1, y1) cell bounds for the given zone index."""
        W, H = self.model.grid.width, self.model.grid.height
        n_cols, n_rows = self._zone_grid()
        zone_w = W / n_cols
        zone_h = H / n_rows
        x0 = int(zone[0] * zone_w)
        y0 = int(zone[1] * zone_h)
        x1 = min(int((zone[0] + 1) * zone_w), W)
        y1 = min(int((zone[1] + 1) * zone_h), H)
        return x0, y0, x1, y1

    def _select_target_zone(self, my_pos: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """Pick the zone with the most unexplored area.

        Divides the map into ``zone_divisions`` blocks (cols × rows) and
        returns the ``(col, row)`` index of the best zone to explore.

        Anti-flip-flop: the scout must stay in a zone for at least
        ``_TARGET_LOCK_DURATION * 2`` steps before it can switch, unless
        the zone is fully explored (<5 % unexplored).
        """
        W, H = self.model.grid.width, self.model.grid.height
        n_cols, n_rows = self._zone_grid()
        if n_cols <= 1 and n_rows <= 1:
            return (0, 0)
        zone_w = W / n_cols
        zone_h = H / n_rows
        _is_mk = getattr(self.model, "map_known", False)
        src = self.vision_explored if _is_mk else self.local_map

        # Collect (zone_index, unexplored_ratio, dist_from_agent) for every zone
        zones: List[Tuple[Tuple[int, int], float, float]] = []
        for zi in range(n_cols):
            for zj in range(n_rows):
                x0 = int(zi * zone_w)
                y0 = int(zj * zone_h)
                x1 = min(int((zi + 1) * zone_w), W)
                y1 = min(int((zj + 1) * zone_h), H)
                patch = src[y0:y1, x0:x1]
                total = patch.size
                if total == 0:
                    continue
                unexplored = total - int(np.count_nonzero(patch))
                ratio = unexplored / total
                cx = (x0 + x1) / 2.0
                cy = (y0 + y1) / 2.0
                dist = abs(cx - my_pos[0]) + abs(cy - my_pos[1])
                zones.append(((zi, zj), ratio, dist))

        if not zones:
            return None

        cs = self.model.current_step

        # Anti-flip-flop: minimum stay in current zone
        min_stay = self._TARGET_LOCK_DURATION * 2
        if self._current_zone is not None and cs - self._zone_switch_step < min_stay:
            cur = next((z for z in zones if z[0] == self._current_zone), None)
            if cur and cur[1] > 0.05:
                return self._current_zone

        # Hysteresis: stay in current zone if still productive
        if self._current_zone is not None:
            cur = next((z for z in zones if z[0] == self._current_zone), None)
            if cur and cur[1] > 0.15:
                best_ratio = max(z[1] for z in zones)
                if cur[1] >= best_ratio - 0.10:
                    return self._current_zone

        max_dist = float(W + H)
        # Score: unexplored ratio dominant, small distance discount
        best = max(zones, key=lambda z: z[1] - (z[2] / max_dist) * 0.15)
        return best[0]

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
        cs = self.model.current_step
        for agent in self.model.agents:
            if getattr(agent, "role", None) == "coordinator" and agent.pos:
                c_pos = pos_to_tuple(agent.pos)
                dist = abs(c_pos[0] - my_pos[0]) + abs(c_pos[1] - my_pos[1])
                if dist <= self.vision_radius:
                    self.last_seen_coordinator_pos = c_pos
                    self._last_seen_coordinator_step = cs
                    break

        # Also check relayed coordinator positions (received from other agents
        # via MapDataMessage).  Use the freshest one if it is newer than what
        # we have from direct vision.
        for cid, c_pos in self.coordinator_positions.items():
            c_step = self.coordinator_positions_step.get(cid, 0)
            if c_step > self._last_seen_coordinator_step:
                self.last_seen_coordinator_pos = (int(c_pos[0]), int(c_pos[1]))
                self._last_seen_coordinator_step = c_step

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
                self._explore_target = None  # not exploring while recharging
                self.state = AgentState.RECHARGING
                self.target_position = station.get("entrance")
                print(
                    f"{self.tag} LOW-E ({self.energy:.1f}), "
                    f"heading to WH entrance {self.target_position}"
                )
            # Sub-machine runs in step_act — just return here
            return

        # ---- Priority 2: Communicate discoveries to any nearby coordinator ----
        #
        # MapDataMessage (broadcast every step to all agents in comm range)
        # already carries known_objects, so discoveries propagate passively
        # through ANY agent chain: scout → retriever → coordinator.  The scout
        # only needs to actively seek a coordinator when it has been truly
        # isolated (no agent contact) for _SEEK_COORDINATOR_DELAY steps.
        if self.newly_discovered_objects:
            nearby = self.get_nearby_agents(self.communication_radius)

            # Track contact with ANY agent — passive relay is working if recent.
            if nearby:
                self._last_agent_contact_step = self.model.current_step

            coordinators = [a for a in nearby if getattr(a, "role", None) == "coordinator"]
            if coordinators:
                # Coordinator right here — hand-deliver via ObjectLocationMessage
                self.should_communicate_this_step = True
                self._pending_seek_coordinator = False
                return

            # Passive relay likely active: met some agent recently → keep exploring.
            steps_since_contact = self.model.current_step - self._last_agent_contact_step
            if steps_since_contact < self._SEEK_COORDINATOR_DELAY:
                pass  # fall through to exploration (Priority 3)
            elif (
                self._SEEK_COORDINATOR
                and self.last_seen_coordinator_pos
                and self.model.current_step >= self._seek_cooldown_until
            ):
                # Defer seek until the current exploration path completes —
                # the cells being discovered have high value and should be
                # completed before diverting to the coordinator.

                # Decide whether to defer seek
                _should_defer = (
                    self.target_position is not None
                    and self._explore_target is not None
                    and not self._pending_seek_coordinator
                )
                if _should_defer:
                    # Mark deferred seek — will fire after path completes
                    self._pending_seek_coordinator = True
                    print(
                        f"{self.tag} SEEK-COORD: deferred — finishing current "
                        f"exploration path to {self.target_position} first "
                        f"({len(self.newly_discovered_objects)} discoveries pending)"
                    )
                    # Fall through to Priority 3 (keep moving toward target)
                elif self._pending_seek_coordinator or self.target_position is None:
                    # Current path completed (or no path) — now seek coordinator
                    self._pending_seek_coordinator = False
                    my_pos = pos_to_tuple(self.pos) if self.pos else (0, 0)
                    dist_to_saved = abs(self.last_seen_coordinator_pos[0] - my_pos[0]) + abs(
                        self.last_seen_coordinator_pos[1] - my_pos[1]
                    )
                    if dist_to_saved <= max(1, self.communication_radius):
                        new_coord_pos = self._wide_scan_for_coordinator()
                        if new_coord_pos:
                            self.last_seen_coordinator_pos = new_coord_pos
                            print(
                                f"{self.tag} SEEK-COORD: old pos stale, "
                                f"found coordinator at {new_coord_pos} via wide scan"
                            )
                        else:
                            # Cooldown instead of permanent give-up
                            self._seek_cooldown_until = self.model.current_step + 60
                            print(
                                f"{self.tag} SEEK-COORD: reached stale pos, "
                                f"coordinator not found — cooldown for 60 steps"
                            )
                    else:
                        if self.target_position != self.last_seen_coordinator_pos:
                            self.target_position = self.last_seen_coordinator_pos
                            self.path = []
                        self._explore_target = None  # not exploring while seeking
                        self.state = AgentState.MOVING_TO_TARGET
                        print(
                            f"{self.tag} SEEK-COORD: isolated for {steps_since_contact} steps, "
                            f"heading to coordinator "
                            f"({len(self.newly_discovered_objects)} discoveries pending)"
                        )
                        return

            # Discard discoveries after prolonged total isolation
            self._discovery_age = self._discovery_age + 1 if not nearby else 0
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

        # Expire unreachable_targets entries that are older than their TTL
        # so the dict doesn't grow unbounded (base_agent never auto-purges it).
        for pos in [p for p, s in self.unreachable_targets.items() if current_step - s >= 200]:
            del self.unreachable_targets[pos]

        frontiers = FrontierExplorer.find_frontiers(
            self.local_map,
            min_cluster_size=self._MIN_FRONTIER_CLUSTER_SIZE,
        )
        # If the high threshold filtered everything out, retry with minimum=1
        # so the scout still has *something* to explore.
        if not frontiers:
            frontiers = FrontierExplorer.find_frontiers(
                self.local_map, min_cluster_size=1, unexplored_mask=_unexp_mask
            )

        # Filter blacklisted / stale frontiers and recently-reached targets.
        _is_map_known = getattr(self.model, "map_known", False)
        _filt_map = self.local_map
        _filt_vis = self.vision_explored
        _filt_H, _filt_W = _filt_map.shape

        def _zone_ratio(fx: int, fy: int) -> float:
            """Explored ratio in 11×11 window around (fx, fy)."""
            r = 5
            y0, y1 = max(0, fy - r), min(_filt_H, fy + r + 1)
            x0, x1 = max(0, fx - r), min(_filt_W, fx + r + 1)
            p = _filt_vis[y0:y1, x0:x1] if _is_map_known else _filt_map[y0:y1, x0:x1]
            return int(np.count_nonzero(p)) / p.size if p.size else 1.0

        valid_frontiers = []
        for frontier_pos, cluster_size in frontiers:
            if frontier_pos in self.unreachable_targets:
                continue
            if frontier_pos in self._recent_targets:
                continue
            if not self.model.grid.is_walkable(*frontier_pos):
                continue
            # Reject frontiers in well-explored zones
            if _zone_ratio(frontier_pos[0], frontier_pos[1]) > 0.85:
                continue
            valid_frontiers.append((frontier_pos, cluster_size))

        # Filter tiny frontiers (size 1-2) when larger ones (≥5) exist.
        # Tiny frontiers are often isolated single-cell corners that waste
        # the scout's time when large unexplored areas exist elsewhere.
        if valid_frontiers:
            large_frontiers = [f for f in valid_frontiers if f[1] >= 5]
            if large_frontiers:
                valid_frontiers = large_frontiers

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

        # ── Zone-based macro routing (map_known only) ──
        # In map_known, all frontiers are visible globally — zone routing
        # hard-filters frontiers to the most unexplored block so the scout
        # doesn't nibble residual edges.
        # In unknown mode, zone routing is not used: the natural frontier
        # following + _zone_ratio filter is sufficient.
        _zone_waypoint: Optional[Tuple[int, int]] = None
        if self._ZONE_DIVISIONS > 1 and _is_map_known:
            new_zone = self._select_target_zone(my_pos)
            if new_zone is not None and new_zone != self._current_zone:
                old_label = str(self._current_zone) if self._current_zone else "None"
                # Log full zone stats on every switch
                _zc, _zr = self._zone_grid()
                _zw = self.model.grid.width / _zc
                _zh = self.model.grid.height / _zr
                _zsrc = self.vision_explored
                _zparts = []
                for _zi in range(_zc):
                    for _zj in range(_zr):
                        _zx0 = int(_zi * _zw)
                        _zy0 = int(_zj * _zh)
                        _zx1 = min(int((_zi + 1) * _zw), self.model.grid.width)
                        _zy1 = min(int((_zj + 1) * _zh), self.model.grid.height)
                        _zp = _zsrc[_zy0:_zy1, _zx0:_zx1]
                        _zunk = _zp.size - int(np.count_nonzero(_zp))
                        _zpct = _zunk / _zp.size * 100 if _zp.size else 0
                        _zmk = "*" if (_zi, _zj) == new_zone else " "
                        _zparts.append(f"{_zmk}({_zi},{_zj}):{_zpct:.0f}%")
                print(
                    f"{self.tag} ZONE-SWITCH step={current_step}: "
                    f"{old_label} → {new_zone}  "
                    f"(stayed {current_step - self._zone_switch_step} steps)  "
                    f"grid={_zc}x{_zr}  "
                    f"[{'  '.join(_zparts)}]"
                )
                self._current_zone = new_zone
                self._zone_switch_step = current_step
            if self._current_zone is not None and frontiers_to_use:
                zx0, zy0, zx1, zy1 = self._get_zone_bounds(self._current_zone)
                zone_frontiers = [
                    f for f in frontiers_to_use if zx0 <= f[0][0] < zx1 and zy0 <= f[0][1] < zy1
                ]
                if zone_frontiers:
                    frontiers_to_use = zone_frontiers
                else:
                    # Zone fully explored — compute waypoint for momentum
                    zone_patch = self.vision_explored[zy0:zy1, zx0:zx1]
                    unk_ys, unk_xs = np.where(zone_patch == 0)
                    if len(unk_ys) > 0:
                        _zone_waypoint = (
                            int(np.mean(unk_xs)) + zx0,
                            int(np.mean(unk_ys)) + zy0,
                        )

        # ── Far-frontier filter (unknown only) ──
        # Prefer frontiers that require actual travel so the scout pushes
        # into new territory rather than hopping between tiny nearby clusters.
        if frontiers_to_use and self._FAR_FRONTIER_ENABLED:
            min_dist = max(self.vision_radius * 2, 8)
            far_frontiers = [
                f
                for f in frontiers_to_use
                if abs(f[0][0] - my_pos[0]) + abs(f[0][1] - my_pos[1]) >= min_dist
            ]
            if far_frontiers:
                frontiers_to_use = far_frontiers
            elif self._STALE_COVERAGE_PATROL:
                # All frontiers are nearby — check if they're in
                # mostly-explored zones (residual edges).
                has_unexplored = any(_zone_ratio(f[0][0], f[0][1]) < 0.7 for f in frontiers_to_use)
                if not has_unexplored:
                    self._pick_stale_coverage_target(my_pos)
                    if self.target_position is not None:
                        return

        if frontiers_to_use:

            nearby_positions = [pos_to_tuple(a.pos) for a in nearby if a.pos]

            # Build a coverage callback so select_best_frontier prefers
            # frontiers in poorly-explored zones of the grid.
            _local_map = self.local_map
            _vis_exp = self.vision_explored
            _is_map_known = getattr(self.model, "map_known", False)
            _H, _W = _local_map.shape

            def _explored_ratio(x: int, y: int) -> float:
                """Return the explored ratio in a window around (x, y)."""
                r = 5
                y0, y1 = max(0, y - r), min(_H, y + r + 1)
                x0, x1 = max(0, x - r), min(_W, x + r + 1)
                patch = _vis_exp[y0:y1, x0:x1] if _is_map_known else _local_map[y0:y1, x0:x1]
                total = patch.size
                if total == 0:
                    return 1.0
                explored = int(np.count_nonzero(patch))
                return explored / total

            def _unknown_mass(x: int, y: int) -> int:
                """Count UNKNOWN cells in a 7-cell radius around (x, y)."""
                r = 7
                y0, y1 = max(0, y - r), min(_H, y + r + 1)
                x0, x1 = max(0, x - r), min(_W, x + r + 1)
                patch = _vis_exp[y0:y1, x0:x1]
                return int(patch.size - np.count_nonzero(patch))

            # Build global peer-target list for area division.
            # Exclude agents currently in comm range — handled by nearby_positions.
            _cs = self.model.current_step
            _nearby_ids = {a.unique_id for a in nearby}
            _global_targets = [
                pos
                for aid, pos in self.peer_explore_targets.items()
                if aid not in _nearby_ids
                and _cs - self.peer_explore_targets_step.get(aid, 0) <= self._explore_target_ttl
            ]

            best = FrontierExplorer.select_best_frontier(
                frontiers_to_use,
                my_pos,
                nearby_positions,
                grid_size=(self.model.grid.width, self.model.grid.height),
                explored_ratio_at=_explored_ratio,
                all_peer_targets=_global_targets,
                current_target=_zone_waypoint or self.target_position,
            )
            if best:
                # Shift target deeper into the unexplored region: compute
                # centroid of unknown cells in a wide window around the
                # frontier and aim there instead of the frontier edge.
                _deep_r = 6
                _fy, _fx = best[1], best[0]
                _dy0 = max(0, _fy - _deep_r)
                _dy1 = min(_H, _fy + _deep_r + 1)
                _dx0 = max(0, _fx - _deep_r)
                _dx1 = min(_W, _fx + _deep_r + 1)
                _src = _vis_exp if _is_map_known else _local_map
                _win = _src[_dy0:_dy1, _dx0:_dx1]
                _unk_rows, _unk_cols = np.where(_win == 0)
                if len(_unk_rows) > 3:
                    _deep_x = int(np.mean(_unk_cols)) + _dx0
                    _deep_y = int(np.mean(_unk_rows)) + _dy0
                    _deep_x = max(0, min(_deep_x, _W - 1))
                    _deep_y = max(0, min(_deep_y, _H - 1))
                    if self.model.grid.is_walkable(_deep_x, _deep_y):
                        best = (_deep_x, _deep_y)

                # Only update target when the new best is genuinely far from the
                # current one — prevents jittering between two nearby candidates.
                if (
                    not self.target_position
                    or abs(best[0] - self.target_position[0])
                    + abs(best[1] - self.target_position[1])
                    > self._TARGET_HYSTERESIS
                ):
                    self.target_position = best
                    self._explore_target = best
                    self.path = []
                    self.consecutive_failures_on_target = 0
                    self._target_lock_step = current_step
                self.state = AgentState.MOVING_TO_TARGET
                return

        # Zone centroid fallback (map_known only): if zone routing is active
        # but no frontier was selected, head to the centroid of unexplored
        # cells inside the target zone.
        if self._current_zone is not None and self._ZONE_DIVISIONS > 1 and _is_map_known:
            zx0, zy0, zx1, zy1 = self._get_zone_bounds(self._current_zone)
            zone_patch = self.vision_explored[zy0:zy1, zx0:zx1]
            unk_ys, unk_xs = np.where(zone_patch == 0)
            if len(unk_ys) > 0:
                cx = int(np.mean(unk_xs)) + zx0
                cy = int(np.mean(unk_ys)) + zy0
                cx = max(0, min(cx, self.model.grid.width - 1))
                cy = max(0, min(cy, self.model.grid.height - 1))
                if self.model.grid.is_walkable(cx, cy) and (cx, cy) not in self.unreachable_targets:
                    self.target_position = (cx, cy)
                    self._explore_target = (cx, cy)
                    self.path = []
                    self._target_lock_step = current_step
                    self.state = AgentState.MOVING_TO_TARGET
                    print(f"{self.tag} ZONE: centroid ({cx},{cy}) " f"in zone {self._current_zone}")
                    return

        # ---- Fallback 1: navigate towards a random unknown boundary cell ----
        self._pick_unexplored_target(my_pos)

        # ---- Fallback 2: re-explore stale areas (cyclic full-map coverage) ----
        # Triggered only when _pick_unexplored_target found nothing (map fully
        # explored).  The scout seeks the oldest-unseen explored cell so it
        # continuously re-sweeps the warehouse for newly-appeared objects.
        if self.target_position is None and self._STALE_COVERAGE_PATROL:
            self._pick_stale_coverage_target(my_pos)

        # ---- Fallback 3: never idle — head to farthest walkable corner ----
        # Guarantees the scout always has a destination even when every other
        # heuristic fails (fully explored, no stale cells).
        if self.target_position is None:
            W, H = self.model.grid.width, self.model.grid.height
            corners = [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]
            corners.sort(
                key=lambda c: abs(c[0] - my_pos[0]) + abs(c[1] - my_pos[1]),
                reverse=True,
            )
            for cx, cy in corners:
                if (cx, cy) not in self.unreachable_targets:
                    self.target_position = (cx, cy)
                    self.path = []
                    self.state = AgentState.MOVING_TO_TARGET
                    self._target_lock_step = current_step
                    break

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
        # When map_known, unscanned cells (vision_explored==0) take ABSOLUTE
        # priority — the scout must scan all terrain before re-patrolling.
        is_map_known = getattr(self.model, "map_known", False)
        if is_map_known:
            unseen_mask = (self.local_map != 0) & (self.vision_explored == 0)
            if np.any(unseen_mask):
                # Only target never-scanned cells; skip stale re-patrol
                stale_mask = unseen_mask
            else:
                # All cells scanned — normal stale coverage patrol
                stale_mask = (self.local_map != 0) & (self._coverage_step <= threshold_step)
        else:
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
        """Navigate towards the best UNKNOWN boundary cell.

        Prefers boundary cells in the direction of the centroid of all
        unexplored area — this naturally steers the scout toward the
        largest unvisited region (e.g. if only the top half is explored,
        the centroid of unknown cells sits in the bottom half).

        Uses numpy vectorisation for performance.
        """
        # Boolean mask: cells considered "unknown" for exploration purposes
        unknown_mask = self.local_map == 0
        if not np.any(unknown_mask):
            self.target_position = None
            self.state = AgentState.EXPLORING
            return

        # Compute centroid of all unknown cells — this points toward the
        # largest unexplored region of the map.
        unk_ys, unk_xs = np.where(unknown_mask)
        centroid_x = float(np.mean(unk_xs))
        centroid_y = float(np.mean(unk_ys))

        # Pad and check 4-neighbours to find boundary cells
        # (unknown cells adjacent to at least one explored/scanned cell)
        explored_mask = ~unknown_mask
        padded_explored = np.pad(
            explored_mask.astype(np.int8), 1, mode="constant", constant_values=0
        )
        has_explored_neighbour = np.zeros_like(unknown_mask)
        for dy, dx in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            has_explored_neighbour |= (
                padded_explored[
                    1 + dy : padded_explored.shape[0] - 1 + dy,
                    1 + dx : padded_explored.shape[1] - 1 + dx,
                ]
                > 0
            )

        boundary_mask = unknown_mask & has_explored_neighbour
        b_ys, b_xs = np.where(boundary_mask)

        if len(b_ys) == 0:
            # No boundary cells — target the centroid of unknown area directly
            cx, cy = int(centroid_x), int(centroid_y)
            cx = max(0, min(cx, self.model.grid.width - 1))
            cy = max(0, min(cy, self.model.grid.height - 1))
            if self.model.grid.is_walkable(cx, cy) and (cx, cy) not in self.unreachable_targets:
                self.target_position = (cx, cy)
                self.path = []
                self.state = AgentState.MOVING_TO_TARGET
                self._target_lock_step = self.model.current_step
            else:
                self.target_position = None
                self.state = AgentState.EXPLORING
            return

        # Score each boundary cell: prefer cells close to the unknown-area
        # centroid (pushes toward the biggest unexplored region).
        # Add a small distance penalty from agent to avoid picking a cell
        # very far away when a closer one is in the same direction.
        b_xs_f = b_xs.astype(np.float32)
        b_ys_f = b_ys.astype(np.float32)
        dist_to_centroid = np.abs(b_xs_f - centroid_x) + np.abs(b_ys_f - centroid_y)
        dist_from_agent = np.abs(b_xs_f - my_pos[0]) + np.abs(b_ys_f - my_pos[1])
        # Lower score = better (close to centroid, not too far from agent)
        scores = dist_to_centroid + dist_from_agent * 0.3
        sort_idx = np.argsort(scores)

        for idx in sort_idx[:30]:
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

        # OPTION 3: Move based on state (one move per step to prevent
        #           visual teleportation / jumping over other agents)
        for _ in range(1):
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
                    # Last resort: move toward the centroid of all unscanned cells
                    # — purely directional, no randomness.
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
