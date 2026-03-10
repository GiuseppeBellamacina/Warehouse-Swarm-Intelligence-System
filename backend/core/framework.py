"""
Custom lightweight framework for multi-agent simulations
Provides Agent, Model, MultiGrid, and DataCollector functionality
"""

import random
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd


class Agent:
    """Base agent class with unique identifier and model reference"""

    def __init__(self, unique_id: int, model: "Model"):
        """
        Initialize agent

        Args:
            unique_id: Unique identifier for the agent
            model: Reference to the model containing this agent
        """
        self.unique_id = unique_id
        self.model = model
        self.pos: Optional[Tuple[int, int]] = None

    def step(self) -> None:
        """Execute one step of agent behavior. Override in subclasses."""
        pass


class Model:
    """
    Base model class for simulations

    Provides agent scheduling, step counter, and random number generation
    """

    def __init__(self, seed: Optional[int] = None):
        """
        Initialize model

        Args:
            seed: Random seed for reproducibility
        """
        if seed is not None:
            random.seed(seed)

        self.schedule: List[Agent] = []
        self.current_step = 0
        self.running = True

    @property
    def agents(self) -> List[Agent]:
        """Get all agents"""
        return self.schedule

    def add_agent(self, agent: Agent) -> None:
        """Add an agent to the schedule"""
        self.schedule.append(agent)

    def remove_agent(self, agent: Agent) -> None:
        """Remove an agent from the schedule"""
        if agent in self.schedule:
            self.schedule.remove(agent)

    def step(self) -> None:
        """Execute one step of the simulation"""
        self.current_step += 1

        for agent in self.schedule[:]:
            if self.running:
                agent.step()


class MultiGrid:
    """
    Grid for spatial management of agents on a 2D grid
    Each cell can contain at most one agent to prevent overlapping
    """

    def __init__(self, width: int, height: int, torus: bool = False):
        """
        Initialize grid

        Args:
            width: Grid width
            height: Grid height
            torus: Whether the grid wraps around edges
        """
        self.width = width
        self.height = height
        self.torus = torus

        # Grid stores one agent per cell (or None)
        self.grid: List[List[Optional[Agent]]] = [
            [None for _ in range(height)] for _ in range(width)
        ]

    def place_agent(self, agent: Agent, pos: Tuple[int, int]) -> None:
        """
        Place an agent on the grid (fails if cell is occupied)

        Args:
            agent: Agent to place
            pos: (x, y) position
        """
        x, y = pos

        # Handle torus wrapping
        if self.torus:
            x = x % self.width
            y = y % self.height

        # Check bounds
        if not self.out_of_bounds((x, y)):
            # Remove from old position if exists
            if agent.pos is not None:
                self.remove_agent(agent)

            # Only place if cell is empty
            if self.grid[x][y] is None:
                self.grid[x][y] = agent
                agent.pos = (x, y)

    def remove_agent(self, agent: Agent) -> None:
        """Remove an agent from the grid"""
        if agent.pos is not None:
            x, y = agent.pos
            if self.grid[x][y] == agent:
                self.grid[x][y] = None
            agent.pos = None

    def move_agent(self, agent: Agent, pos: Tuple[int, int]) -> None:
        """Move an agent to a new position"""
        self.place_agent(agent, pos)

    def swap_agents(self, agent_a: Agent, agent_b: Agent) -> None:
        """Atomically swap positions of two agents on the grid."""
        pos_a = agent_a.pos
        pos_b = agent_b.pos
        if pos_a is None or pos_b is None:
            return
        # Clear both cells
        self.grid[pos_a[0]][pos_a[1]] = None
        self.grid[pos_b[0]][pos_b[1]] = None
        # Place at swapped positions
        self.grid[pos_a[0]][pos_a[1]] = agent_b
        self.grid[pos_b[0]][pos_b[1]] = agent_a
        agent_a.pos = pos_b
        agent_b.pos = pos_a

    def get_cell_list_contents(self, cell_list: List[Tuple[int, int]]) -> List[Agent]:
        """
        Get all agents in the specified cells

        Args:
            cell_list: List of (x, y) positions

        Returns:
            List of agents in those cells
        """
        agents = []
        for x, y in cell_list:
            if not self.out_of_bounds((x, y)):
                agent = self.grid[x][y]
                if agent is not None:
                    agents.append(agent)
        return agents

    def out_of_bounds(self, pos: Tuple[int, int]) -> bool:
        """Check if position is out of bounds"""
        x, y = pos
        return x < 0 or x >= self.width or y < 0 or y >= self.height

    def is_cell_empty(self, pos: Tuple[int, int]) -> bool:
        """Check if a cell is empty (no agent)"""
        x, y = pos
        if self.out_of_bounds((x, y)):
            return False
        return self.grid[x][y] is None


class DataCollector:
    """Collects data from model and agents over time"""

    def __init__(
        self,
        model_reporters: Optional[Dict[str, Callable]] = None,
        agent_reporters: Optional[Dict[str, Callable]] = None,
    ):
        """
        Initialize data collector

        Args:
            model_reporters: Dict of {name: function} to collect model-level data
            agent_reporters: Dict of {name: function} to collect agent-level data
        """
        self.model_reporters = model_reporters or {}
        self.agent_reporters = agent_reporters or {}

        self.model_vars: Dict[int, Dict[str, Any]] = {}
        self.agent_vars: Dict[int, List[Dict[str, Any]]] = {}

    def collect(self, model: Model) -> None:
        """
        Collect data for the current step

        Args:
            model: Model to collect data from
        """
        step = model.current_step

        # Collect model-level data
        self.model_vars[step] = {}
        for name, reporter in self.model_reporters.items():
            self.model_vars[step][name] = reporter(model)

        # Collect agent-level data
        if self.agent_reporters:
            self.agent_vars[step] = []
            for agent in model.schedule:
                agent_data = {"AgentID": agent.unique_id}
                for name, reporter in self.agent_reporters.items():
                    agent_data[name] = reporter(agent)
                self.agent_vars[step].append(agent_data)

    def get_model_vars_dataframe(self) -> pd.DataFrame:
        """Get model variables as pandas DataFrame"""
        if not self.model_vars:
            return pd.DataFrame()

        df = pd.DataFrame.from_dict(self.model_vars, orient="index")
        df.index.name = "Step"
        return df

    def get_agent_vars_dataframe(self) -> pd.DataFrame:
        """Get agent variables as pandas DataFrame"""
        if not self.agent_vars:
            return pd.DataFrame()

        records = []
        for step, agents in self.agent_vars.items():
            for agent_data in agents:
                records.append({"Step": step, **agent_data})

        df = pd.DataFrame(records)
        if not df.empty:
            df.set_index(["Step", "AgentID"], inplace=True)
        return df
