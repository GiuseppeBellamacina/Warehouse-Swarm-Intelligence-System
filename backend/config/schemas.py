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
            count=1, vision_radius=3, communication_radius=2, speed=1.5, carrying_capacity=0
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
