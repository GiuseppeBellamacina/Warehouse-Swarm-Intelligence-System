"""
Metrics collection system for simulation analysis
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class StepMetrics:
    """Metrics for a single simulation step"""

    step: int
    timestamp: float

    # Agent metrics
    total_agents: int
    active_agents: int  # Agents with energy > 0
    avg_energy: float
    min_energy: float
    max_energy: float

    # Task metrics
    objects_retrieved: int
    objects_remaining: int
    total_objects: int
    retrieval_rate: float  # objects/step

    # Coverage metrics
    cells_explored: int
    total_cells: int
    coverage_percentage: float

    # Efficiency metrics
    total_distance_traveled: float
    avg_distance_per_agent: float
    energy_efficiency: float  # objects_retrieved / total_energy_consumed

    # Agent state distribution
    idle_agents: int
    exploring_agents: int
    retrieving_agents: int
    delivering_agents: int
    recharging_agents: int

    # Communication metrics
    messages_sent: int
    avg_messages_per_agent: float


@dataclass
class SimulationMetrics:
    """Complete simulation metrics"""

    # Simulation info
    simulation_id: str
    start_time: float
    end_time: Optional[float] = None
    total_steps: int = 0

    # Configuration
    grid_size: tuple = (0, 0)
    num_scouts: int = 0
    num_coordinators: int = 0
    num_retrievers: int = 0
    total_objects: int = 0

    # Step-by-step metrics
    step_metrics: List[StepMetrics] = field(default_factory=list)

    # Aggregate metrics
    total_retrieval_time: float = 0.0  # Steps to complete
    avg_energy_per_step: float = 0.0
    total_distance: float = 0.0
    peak_coverage: float = 0.0
    retrieval_efficiency: float = 0.0  # objects / (agents * steps)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON export"""
        return asdict(self)

    def to_json(self, filepath: Path) -> None:
        """Export metrics to JSON file"""
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics"""
        if not self.step_metrics:
            return {}

        return {
            "simulation_id": self.simulation_id,
            "total_steps": self.total_steps,
            "duration_seconds": self.end_time - self.start_time if self.end_time else 0,
            "grid_size": self.grid_size,
            "total_agents": self.num_scouts + self.num_coordinators + self.num_retrievers,
            "agents_breakdown": {
                "scouts": self.num_scouts,
                "coordinators": self.num_coordinators,
                "retrievers": self.num_retrievers,
            },
            "retrieval_stats": {
                "total_objects": self.total_objects,
                "retrieved": self.step_metrics[-1].objects_retrieved if self.step_metrics else 0,
                "retrieval_rate": self.step_metrics[-1].retrieval_rate if self.step_metrics else 0,
                "completion_percentage": (
                    (self.step_metrics[-1].objects_retrieved / self.total_objects * 100)
                    if self.total_objects > 0 and self.step_metrics
                    else 0
                ),
            },
            "efficiency": {
                "avg_energy": self.avg_energy_per_step,
                "total_distance": self.total_distance,
                "retrieval_efficiency": self.retrieval_efficiency,
                "energy_efficiency": (
                    self.step_metrics[-1].energy_efficiency if self.step_metrics else 0
                ),
            },
            "coverage": {
                "peak_coverage": self.peak_coverage,
                "final_coverage": (
                    self.step_metrics[-1].coverage_percentage if self.step_metrics else 0
                ),
            },
        }


class MetricsCollector:
    """Collects and manages simulation metrics"""

    def __init__(self, simulation_id: str, grid_size: tuple, config: dict):
        self.metrics = SimulationMetrics(
            simulation_id=simulation_id,
            start_time=time.time(),
            grid_size=grid_size,
            num_scouts=config.get("scouts", 0),
            num_coordinators=config.get("coordinators", 0),
            num_retrievers=config.get("retrievers", 0),
            total_objects=config.get("total_objects", 0),
        )

        self.cumulative_energy_consumed = 0.0
        self.cumulative_distance = 0.0
        self.previous_positions: Dict[int, tuple] = {}

    def collect_step_metrics(self, model) -> StepMetrics:
        """
        Collect metrics for current step

        Args:
            model: WarehouseModel instance

        Returns:
            StepMetrics for this step
        """
        agents = list(model.agents)

        # Agent energy metrics
        energies = [a.energy for a in agents if hasattr(a, "energy")]
        avg_energy = sum(energies) / len(energies) if energies else 0
        min_energy = min(energies) if energies else 0
        max_energy = max(energies) if energies else 0
        active_agents = sum(1 for e in energies if e > 0)

        # Track distance traveled this step
        step_distance = 0.0
        for agent in agents:
            if not hasattr(agent, "unique_id") or not hasattr(agent, "pos"):
                continue

            agent_id = agent.unique_id
            current_pos = (agent.pos[0], agent.pos[1]) if agent.pos else None

            if agent_id in self.previous_positions and current_pos:
                prev_pos = self.previous_positions[agent_id]
                distance = abs(current_pos[0] - prev_pos[0]) + abs(current_pos[1] - prev_pos[1])
                step_distance += distance

            if current_pos:
                self.previous_positions[agent_id] = current_pos

        self.cumulative_distance += step_distance

        # Coverage metrics
        explored_cells = model.grid.cell_types[model.grid.cell_types != 0].size
        total_cells = model.grid.width * model.grid.height
        coverage = (explored_cells / total_cells * 100) if total_cells > 0 else 0

        # Task metrics
        objects_retrieved = model.objects_retrieved
        objects_remaining = len(model.grid.objects)
        total_objects = model.total_objects

        # Calculate retrieval rate (objects per step)
        retrieval_rate = objects_retrieved / max(model.current_step, 1)

        # Energy efficiency (objects per unit energy consumed)
        # Approximate energy consumed this step
        step_energy = sum(getattr(a, "energy_consumption", {}).get("base", 0.1) for a in agents)
        self.cumulative_energy_consumed += step_energy
        energy_efficiency = (
            objects_retrieved / self.cumulative_energy_consumed
            if self.cumulative_energy_consumed > 0
            else 0
        )

        # Agent state distribution
        state_counts = {
            "idle": 0,
            "exploring": 0,
            "retrieving": 0,
            "delivering": 0,
            "recharging": 0,
        }

        for agent in agents:
            if hasattr(agent, "state"):
                state_name = str(agent.state).split(".")[-1].lower()
                if "idle" in state_name:
                    state_counts["idle"] += 1
                elif "explor" in state_name:
                    state_counts["exploring"] += 1
                elif "retriev" in state_name:
                    state_counts["retrieving"] += 1
                elif "deliver" in state_name:
                    state_counts["delivering"] += 1
                elif "recharg" in state_name:
                    state_counts["recharging"] += 1

        # Communication metrics (would need to be tracked by comm_manager)
        messages_sent = 0  # TODO: track in CommunicationManager

        step_metrics = StepMetrics(
            step=model.current_step,
            timestamp=time.time(),
            total_agents=len(agents),
            active_agents=active_agents,
            avg_energy=avg_energy,
            min_energy=min_energy,
            max_energy=max_energy,
            objects_retrieved=objects_retrieved,
            objects_remaining=objects_remaining,
            total_objects=total_objects,
            retrieval_rate=retrieval_rate,
            cells_explored=explored_cells,
            total_cells=total_cells,
            coverage_percentage=coverage,
            total_distance_traveled=self.cumulative_distance,
            avg_distance_per_agent=self.cumulative_distance / len(agents) if agents else 0,
            energy_efficiency=energy_efficiency,
            idle_agents=state_counts["idle"],
            exploring_agents=state_counts["exploring"],
            retrieving_agents=state_counts["retrieving"],
            delivering_agents=state_counts["delivering"],
            recharging_agents=state_counts["recharging"],
            messages_sent=messages_sent,
            avg_messages_per_agent=messages_sent / len(agents) if agents else 0,
        )

        self.metrics.step_metrics.append(step_metrics)
        self.metrics.total_steps = model.current_step

        # Update peak coverage
        if coverage > self.metrics.peak_coverage:
            self.metrics.peak_coverage = coverage

        return step_metrics

    def finalize(self) -> SimulationMetrics:
        """Finalize metrics at end of simulation"""
        self.metrics.end_time = time.time()

        # Calculate aggregate metrics
        if self.metrics.step_metrics:
            self.metrics.avg_energy_per_step = sum(
                m.avg_energy for m in self.metrics.step_metrics
            ) / len(self.metrics.step_metrics)

            self.metrics.total_distance = self.cumulative_distance

            # Retrieval efficiency: objects / (agents * steps)
            total_agents = (
                self.metrics.num_scouts
                + self.metrics.num_coordinators
                + self.metrics.num_retrievers
            )
            if total_agents > 0 and self.metrics.total_steps > 0:
                final_retrieved = self.metrics.step_metrics[-1].objects_retrieved
                self.metrics.retrieval_efficiency = final_retrieved / (
                    total_agents * self.metrics.total_steps
                )

        return self.metrics

    def export(self, output_dir: Path, format: str = "json") -> Path:
        """
        Export metrics to file

        Args:
            output_dir: Directory to save metrics
            format: Export format ("json", "csv")

        Returns:
            Path to exported file
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        if format == "json":
            filepath = output_dir / f"metrics_{self.metrics.simulation_id}.json"
            self.metrics.to_json(filepath)
            return filepath
        elif format == "csv":
            # TODO: Export to CSV
            raise NotImplementedError("CSV export not yet implemented")
        else:
            raise ValueError(f"Unsupported format: {format}")
