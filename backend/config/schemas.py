"""
Configuration schemas using Pydantic for JSON validation
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Position(BaseModel):
    """2D position on the grid"""

    x: int = Field(ge=0)
    y: int = Field(ge=0)


class EntranceExit(BaseModel):
    """Warehouse entrance or exit point"""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    direction: Optional[Literal["north", "south", "east", "west"]] = None


class WallObstacle(BaseModel):
    """Linear wall obstacle"""

    type: Literal["wall"] = "wall"
    start: Position
    end: Position


class BoxObstacle(BaseModel):
    """Rectangular box obstacle"""

    type: Literal["box"] = "box"
    top_left: Position
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SpawnZone(BaseModel):
    """Zone where objects can spawn with probability"""

    x_range: tuple[int, int]
    y_range: tuple[int, int]
    probability: float = Field(ge=0.0, le=1.0, default=1.0)

    @field_validator("x_range", "y_range")
    @classmethod
    def validate_range(cls, v):
        if v[0] >= v[1]:
            raise ValueError("Range start must be less than end")
        return v


class EnergyConsumption(BaseModel):
    """Energy consumption parameters"""

    base: float = Field(ge=0.0, default=0.0)
    move: float = Field(ge=0.0, default=1.0)
    communicate: float = Field(ge=0.0, default=0.0)


class AgentParameters(BaseModel):
    """Per-agent configuration parameters"""

    vision_radius: int = Field(ge=1, default=3)
    communication_radius: int = Field(ge=1, default=2)
    max_energy: float = Field(gt=0, default=500.0)
    energy_consumption: EnergyConsumption = Field(default_factory=EnergyConsumption)
    speed: float = Field(gt=0, default=1.0)
    carrying_capacity: int = Field(ge=0, default=1)


class AgentConfig(BaseModel):
    """Agent configuration"""

    count: int = Field(ge=0, default=5)
    spawn_location: Optional[Position] = None
    parameters: AgentParameters = Field(default_factory=AgentParameters)
    exploration_strategy: Literal["frontier", "random", "potential_field"] = "frontier"
    decision_model: Literal["utility", "rule_based"] = "utility"


class MultiRoleAgentConfig(BaseModel):
    """Configuration for multi-role agent system"""

    scouts: AgentConfig
    coordinators: AgentConfig
    retrievers: AgentConfig


class WarehouseConfig(BaseModel):
    """Warehouse configuration"""

    position: Position
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    warehouse_cells: Optional[List[Position]] = (
        None  # Override single warehouse with multiple cells
    )
    entrances: List[EntranceExit] = Field(min_length=1)
    exits: List[EntranceExit] = Field(default_factory=list)
    recharge_rate: float = Field(ge=0.0, default=5.0)


class ObjectsConfig(BaseModel):
    """Object spawning configuration"""

    count: int = Field(gt=0)
    spawn_zones: List[SpawnZone] = Field(min_length=1)
    value_distribution: Optional[dict] = None


class SimulationConfig(BaseModel):
    """Simulation parameters"""

    grid_width: int = Field(gt=0, default=100)
    grid_height: int = Field(gt=0, default=100)
    timestep_duration_ms: int = Field(gt=0, default=50)
    max_steps: int = Field(gt=0, default=500)
    seed: Optional[int] = None


class LoggingConfig(BaseModel):
    """Logging and metrics configuration"""

    enabled: bool = True
    log_interval: int = Field(gt=0, default=10)
    metrics: List[str] = Field(default_factory=lambda: ["coverage", "energy", "objects_retrieved"])


class ScenarioConfig(BaseModel):
    """Complete scenario configuration"""

    simulation: SimulationConfig
    warehouse: WarehouseConfig
    obstacles: List[WallObstacle | BoxObstacle] = Field(default_factory=list)
    objects: ObjectsConfig
    agents: MultiRoleAgentConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("warehouse")
    @classmethod
    def validate_warehouse_in_bounds(cls, v, info):
        if "simulation" in info.data:
            sim = info.data["simulation"]
            if v.position.x + v.width > sim.grid_width:
                raise ValueError("Warehouse exceeds grid width")
            if v.position.y + v.height > sim.grid_height:
                raise ValueError("Warehouse exceeds grid height")
        return v


# ── New compact grid-based format ──────────────────────────────────────────────


class GridScenarioMetadata(BaseModel):
    """Metadata for compact grid-based scenario"""

    grid_size: int = Field(gt=0)
    num_warehouses: int = Field(ge=1)
    num_objects: int = Field(ge=1)
    max_steps: int = Field(gt=0, default=500)
    seed: Optional[int] = None


class GridWarehouse(BaseModel):
    """Single warehouse definition inside a grid scenario"""

    id: int
    side: str  # e.g. "north", "south", "east", "west"
    entrance: List[int]  # [row, col]
    exit: List[int]  # [row, col]
    area: List[List[int]]  # [[row, col], ...]


class GridScenarioConfig(BaseModel):
    """Compact grid-based scenario (New A/B format)

    Grid cell values:
        0 = free / empty
        1 = wall / obstacle
        2 = warehouse interior
        3 = warehouse entrance
        4 = warehouse exit
    Objects are stored separately in ``objects`` and NOT encoded in the grid.
    """

    metadata: GridScenarioMetadata
    grid: List[List[int]]  # grid[row][col] — row-major
    warehouses: List[GridWarehouse]
    objects: List[List[int]]  # [[row, col], ...]


class ScoutBehaviorParams(BaseModel):
    """Tunable behavioral parameters for scout agents (beyond physical stats)."""

    recent_target_ttl: int = Field(
        default=50, ge=1, description="Steps a reached target is blacklisted to prevent oscillation"
    )
    rescan_age: int = Field(
        default=120,
        ge=10,
        description="Steps without vision before a cell becomes re-eligible for patrol",
    )
    discovery_timeout: int = Field(
        default=80,
        ge=10,
        description="Steps without coordinator before discarding stale discoveries",
    )
    anti_cluster_distance: int = Field(
        default=8,
        ge=0,
        description="Min Manhattan distance from other scouts for frontier selection",
    )
    target_hysteresis: int = Field(
        default=15,
        ge=0,
        description="Min Manhattan distance before switching to new frontier target",
    )
    stuck_threshold: int = Field(
        default=8, ge=1, description="Consecutive move failures before giving up on target"
    )
    recharge_threshold: float = Field(
        default=0.25, ge=0.05, le=0.5, description="Energy fraction triggering recharge"
    )
    far_frontier_enabled: bool = Field(
        default=True, description="Prefer distant frontiers over nearby ones"
    )
    stale_coverage_patrol: bool = Field(
        default=True, description="Re-explore cells not seen for rescan_age steps"
    )
    anti_clustering: bool = Field(default=True, description="Avoid frontiers near other scouts")
    seek_coordinator: bool = Field(
        default=True, description="Move toward last-known coordinator to deliver discoveries"
    )
    seek_coordinator_delay: int = Field(
        default=25, ge=1, description="Steps of isolation before scout heads toward coordinator"
    )
    target_lock_duration: int = Field(
        default=12, ge=1, description="Min steps to commit to a frontier target before switching"
    )
    min_frontier_cluster_size: int = Field(
        default=5, ge=1, description="Min contiguous unknown cells to qualify as a frontier cluster"
    )


class CoordinatorBehaviorParams(BaseModel):
    """Tunable behavioral parameters for coordinator agents."""

    boredom_threshold: int = Field(
        default=20, ge=5, description="Idle steps before forcing waypoint patrol"
    )
    pos_max_age: int = Field(
        default=25, ge=5, description="Max age (steps) of retriever positions for centroid"
    )
    recharge_threshold: float = Field(
        default=0.20, ge=0.05, le=0.5, description="Energy fraction triggering recharge"
    )
    centroid_object_bias: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Weight of pending-object centroid vs retriever centroid (0=only retrievers, 1=only objects)",
    )
    sync_rate_limit: int = Field(
        default=10, ge=1, description="Min steps between coordinator-to-coordinator syncs"
    )
    seek_retrievers: bool = Field(
        default=True, description="Move toward retrievers when tasks exist but none in range"
    )
    boredom_patrol: bool = Field(
        default=True, description="Force waypoint cycling after idle threshold"
    )
    object_biased_centroid: bool = Field(
        default=True, description="Bias positioning toward pending objects"
    )


class RetrieverBehaviorParams(BaseModel):
    """Tunable behavioral parameters for retriever agents."""

    recharge_threshold: float = Field(
        default=0.20, ge=0.05, le=0.5, description="Energy fraction triggering recharge"
    )
    stale_claim_age: int = Field(
        default=45, ge=10, description="Steps before a peer's claim is considered stale"
    )
    explore_retarget_interval: int = Field(
        default=15, ge=1, description="Steps between picking new idle explore targets"
    )
    opportunistic_pickup: bool = Field(
        default=True, description="Claim nearby unclaimed objects while traveling"
    )
    task_queue_reorder: bool = Field(
        default=True, description="Re-sort task queue by distance every step (nearest first)"
    )
    self_assign_from_shared_map: bool = Field(
        default=True, description="Claim objects learned from peers, not just vision"
    )
    peer_broadcast: bool = Field(
        default=True, description="Full retrievers push spotted objects to peer retrievers"
    )
    smart_explore: bool = Field(
        default=True, description="Head toward UNKNOWN boundary when idle (vs random walk)"
    )
    warehouse_congestion_reroute: bool = Field(
        default=True,
        description="Reroute to a less crowded warehouse when the target entrance is congested",
    )
    warehouse_congestion_threshold: int = Field(
        default=3,
        ge=1,
        description="Agents heading to a warehouse before it is considered congested",
    )
    jam_priority: bool = Field(
        default=True,
        description="In traffic jams, retrievers carrying more objects get movement priority",
    )


class AgentRoleParams(BaseModel):
    """Per-role agent parameters used with the new grid format"""

    count: int = Field(ge=0, default=1)
    vision_radius: int = Field(ge=1, default=3)
    communication_radius: int = Field(ge=1, default=2)
    max_energy: float = Field(gt=0, default=500.0)
    speed: float = Field(gt=0, default=1.0)
    carrying_capacity: int = Field(ge=0, default=0)


class SimulationAgentsConfig(BaseModel):
    """Agent composition config passed alongside a GridScenarioConfig"""

    scouts: AgentRoleParams = Field(
        default_factory=lambda: AgentRoleParams(
            count=1, vision_radius=3, communication_radius=2, speed=2, carrying_capacity=0
        )
    )
    coordinators: AgentRoleParams = Field(
        default_factory=lambda: AgentRoleParams(
            count=1, vision_radius=2, communication_radius=3, carrying_capacity=0
        )
    )
    retrievers: AgentRoleParams = Field(
        default_factory=lambda: AgentRoleParams(
            count=3, vision_radius=2, communication_radius=2, carrying_capacity=2
        )
    )

    scout_behavior: ScoutBehaviorParams = Field(default_factory=ScoutBehaviorParams)
    coordinator_behavior: CoordinatorBehaviorParams = Field(
        default_factory=CoordinatorBehaviorParams
    )
    retriever_behavior: RetrieverBehaviorParams = Field(default_factory=RetrieverBehaviorParams)
