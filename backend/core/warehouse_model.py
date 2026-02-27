"""
Warehouse simulation model using Mesa framework
"""

from typing import Dict, Tuple
import random
import numpy as np
from mesa import Model
from mesa.datacollection import DataCollector

from backend.core.grid_manager import GridManager, CellType
from backend.core.communication import CommunicationManager, CoordinationSystem
from backend.config.schemas import ScenarioConfig


class WarehouseModel(Model):
    """
    Main simulation model for multi-agent warehouse system
    
    Manages:
    - Grid and environment
    - Agent scheduling
    - Communication between agents
    - Metrics collection
    - Simulation state
    """
    
    # Stages for staged activation (prevents race conditions)
    STAGE_SENSE = 0
    STAGE_COMMUNICATE = 1
    STAGE_DECIDE = 2
    STAGE_ACT = 3
    
    def __init__(self, config: ScenarioConfig):
        # Initialize Mesa Model with rng (random number generator)
        rng_seed = config.simulation.seed if config.simulation.seed is not None else None
        super().__init__(rng=rng_seed)
        
        self.config = config
        
        # Set random seed for reproducibility (NumPy)
        if config.simulation.seed is not None:
            np.random.seed(config.simulation.seed)
        
        # Initialize grid
        self.grid = GridManager(
            config.simulation.grid_width,
            config.simulation.grid_height,
            torus=False
        )
        
        # Communication and coordination
        self.comm_manager = CommunicationManager()
        self.coordination = CoordinationSystem()
        
        # Simulation state
        self.running = True
        self.current_step = 0
        self.max_steps = config.simulation.max_steps
        
        # Warehouse info
        self.warehouse_position = (
            config.warehouse.position.x,
            config.warehouse.position.y
        )
        self.warehouse_entrances = []
        self.warehouse_exits = []
        
        # Objects tracking
        self.total_objects = config.objects.count
        self.objects_retrieved = 0
        
        # Initialize environment
        self._setup_warehouse()
        self._setup_obstacles()
        self._spawn_objects()
        
        # Agents will be added by external code
        # (see agents/*.py for agent implementations)
        self.scouts = []
        self.coordinators = []
        self.retrievers = []
        
        # Metrics collection
        self._setup_data_collector()
    
    def _setup_warehouse(self) -> None:
        """Setup warehouse cells on the grid"""
        cfg = self.config.warehouse
        
        # Mark warehouse area
        for x in range(cfg.position.x, cfg.position.x + cfg.width):
            for y in range(cfg.position.y, cfg.position.y + cfg.height):
                self.grid.set_cell_type(x, y, CellType.WAREHOUSE)
        
        # Mark entrances
        for entrance in cfg.entrances:
            self.grid.set_cell_type(
                entrance.x,
                entrance.y,
                CellType.WAREHOUSE_ENTRANCE
            )
            self.warehouse_entrances.append((entrance.x, entrance.y))
        
        # Mark exits (or use entrances if no exits specified)
        exits = cfg.exits if cfg.exits else cfg.entrances
        for exit_point in exits:
            self.grid.set_cell_type(
                exit_point.x,
                exit_point.y,
                CellType.WAREHOUSE_EXIT
            )
            self.warehouse_exits.append((exit_point.x, exit_point.y))
    
    def _setup_obstacles(self) -> None:
        """Setup obstacles on the grid"""
        for obstacle in self.config.obstacles:
            if obstacle.type == "wall":
                # Draw line from start to end
                x0, y0 = obstacle.start.x, obstacle.start.y
                x1, y1 = obstacle.end.x, obstacle.end.y
                
                # Bresenham's line algorithm
                dx = abs(x1 - x0)
                dy = abs(y1 - y0)
                sx = 1 if x0 < x1 else -1
                sy = 1 if y0 < y1 else -1
                err = dx - dy
                
                x, y = x0, y0
                while True:
                    self.grid.place_obstacle(x, y)
                    
                    if x == x1 and y == y1:
                        break
                    
                    e2 = 2 * err
                    if e2 > -dy:
                        err -= dy
                        x += sx
                    if e2 < dx:
                        err += dx
                        y += sy
                        
            elif obstacle.type == "box":
                # Fill rectangle
                for x in range(
                    obstacle.top_left.x,
                    obstacle.top_left.x + obstacle.width
                ):
                    for y in range(
                        obstacle.top_left.y,
                        obstacle.top_left.y + obstacle.height
                    ):
                        self.grid.place_obstacle(x, y)
    
    def _spawn_objects(self) -> None:
        """Spawn objects in designated zones"""
        objects_placed = 0
        max_attempts = self.total_objects * 10
        attempts = 0
        
        # Calculate total probability weight
        total_prob = sum(zone.probability for zone in self.config.objects.spawn_zones)
        
        while objects_placed < self.total_objects and attempts < max_attempts:
            attempts += 1
            
            # Select zone based on probability
            rand = random.random() * total_prob
            cumulative = 0.0
            selected_zone = self.config.objects.spawn_zones[0]
            
            for zone in self.config.objects.spawn_zones:
                cumulative += zone.probability
                if rand <= cumulative:
                    selected_zone = zone
                    break
            
            # Random position in zone
            x = random.randint(selected_zone.x_range[0], selected_zone.x_range[1] - 1)
            y = random.randint(selected_zone.y_range[0], selected_zone.y_range[1] - 1)
            
            # Check if position is valid (not obstacle, not warehouse, not another object)
            if self.grid.is_walkable(x, y):
                cell_type = self.grid.get_cell_type(x, y)
                if cell_type not in [
                    CellType.WAREHOUSE,
                    CellType.WAREHOUSE_ENTRANCE,
                    CellType.WAREHOUSE_EXIT,
                    CellType.OBJECT
                ]:
                    self.grid.place_object(x, y)
                    objects_placed += 1
        
        if objects_placed < self.total_objects:
            print(f"Warning: Only placed {objects_placed}/{self.total_objects} objects")
    
    def _setup_data_collector(self) -> None:
        """Setup Mesa data collector for metrics"""
        self.datacollector = DataCollector(
            model_reporters={
                "Objects Retrieved": lambda m: m.objects_retrieved,
                "Total Objects": lambda m: m.total_objects,
                "Retrieval Progress": lambda m: m.objects_retrieved / m.total_objects if m.total_objects > 0 else 0,
                "Average Energy": lambda m: np.mean([getattr(a, 'energy', 0) for a in m.agents]) if m.agents else 0,
                "Active Agents": lambda m: len([a for a in m.agents if getattr(a, 'energy', 0) > 0]),
                "Step": lambda m: m.current_step,
            },
            agent_reporters={
                "Energy": lambda a: getattr(a, 'energy', 0),
                "State": lambda a: getattr(a, 'state', None).value if hasattr(getattr(a, 'state', None), 'value') else "unknown",
                "X": lambda a: a.pos[0] if a.pos else 0,
                "Y": lambda a: a.pos[1] if a.pos else 0,
            }
        )
    
    def add_agent(self, agent) -> None:
        """
        Add an agent to the simulation
        
        Args:
            agent: Agent instance to add
        """
        self.register_agent(agent)
        
        # Track by role for easy access
        if hasattr(agent, 'role'):
            if agent.role == "scout":
                self.scouts.append(agent)
            elif agent.role == "coordinator":
                self.coordinators.append(agent)
            elif agent.role == "retriever":
                self.retrievers.append(agent)
    
    def get_agent_positions(self) -> Dict[int, Tuple[int, int]]:
        """Get dictionary of all agent positions"""
        positions = {}
        for agent in self.agents:
            if agent.pos:
                agent_id = getattr(agent, 'unique_id', None)
                if agent_id is not None:
                    positions[agent_id] = agent.pos
        return positions
    
    def step(self) -> None:
        """
        Advance simulation by one step
        
        Stages:
        1. SENSE: Agents perceive environment
        2. COMMUNICATE: Agents exchange information
        3. DECIDE: Agents make decisions
        4. ACT: Agents execute actions
        """
        # Update spatial index for proximity queries
        agent_positions = [
            agent.pos for agent in self.agents if agent.pos
        ]
        self.grid.update_agent_spatial_index(agent_positions)
        
        # Step all agents
        for agent in list(self.agents):
            agent.step()
        
        # Collect data
        self.datacollector.collect(self)
        
        # Update step counter
        self.current_step += 1
        
        # Check termination conditions
        if self.current_step >= self.max_steps:
            self.running = False
        
        if self.objects_retrieved >= self.total_objects:
            self.running = False
            print(f"All objects retrieved in {self.current_step} steps!")
    
    def get_state_dict(self) -> Dict:
        """
        Get current simulation state as dictionary (for WebSocket)
        
        Returns:
            Dictionary with agents, objects, and metrics
        """
        agents_data = []
        for agent in self.agents:
            if agent.pos:
                agent_data = {
                    'id': agent.unique_id,
                    'role': getattr(agent, 'role', 'unknown'),
                    'x': agent.pos[0],
                    'y': agent.pos[1],
                    'energy': getattr(agent, 'energy', 0),
                    'state': getattr(agent, 'state', 'unknown'),
                    'carrying': getattr(agent, 'carrying_objects', 0),
                }
                agents_data.append(agent_data)
        
        objects_data = [
            {'x': x, 'y': y, 'retrieved': False}
            for x, y in self.grid.objects
        ]
        
        # Add retrieved objects
        objects_data.extend([
            {'x': x, 'y': y, 'retrieved': True}
            for x, y in self.grid.retrieved_objects
        ])
        
        avg_energy = np.mean([getattr(a, 'energy', 0) for a in self.agents]) if self.agents else 0
        
        return {
            'step': self.current_step,
            'agents': agents_data,
            'objects': objects_data,
            'metrics': {
                'objects_retrieved': self.objects_retrieved,
                'total_objects': self.total_objects,
                'retrieval_progress': self.objects_retrieved / self.total_objects if self.total_objects > 0 else 0,
                'average_energy': float(avg_energy),
                'active_agents': len([a for a in self.agents if getattr(a, 'energy', 0) > 0]),
            }
        }
