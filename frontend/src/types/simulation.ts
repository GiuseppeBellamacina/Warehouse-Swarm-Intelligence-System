// Type definitions for simulation data

export interface Position {
  x: number;
  y: number;
}

export interface AgentMessage {
  step: number;
  direction: "sent" | "received";
  type: string;
  details: string;
  targets: number[];
}

export interface Agent {
  id: number;
  role: "scout" | "coordinator" | "retriever";
  x: number;
  y: number;
  energy: number;
  max_energy: number;
  state: string;
  carrying: number;
  vision_radius: number;
  communication_radius: number;
  recent_messages: AgentMessage[];
  path: Position[];
}

export interface SimObject {
  x: number;
  y: number;
  retrieved: boolean;
}

export interface WallData {
  start: { x: number; y: number };
  end: { x: number; y: number };
}

export interface BoxData {
  top_left: { x: number; y: number };
  width: number;
  height: number;
}

export interface Obstacle {
  type: "wall" | "box";
  data: WallData | BoxData;
}

export interface Warehouse {
  x: number;
  y: number;
  width: number;
  height: number;
  cells?: Position[]; // Optional: individual warehouse cells for multi-zone warehouses
  entrances: Position[];
  exits: Position[];
}

export interface Grid {
  width: number;
  height: number;
  warehouse: Warehouse;
  obstacles: Obstacle[];
}

export interface Metrics {
  objects_retrieved: number;
  total_objects: number;
  retrieval_progress: number;
  average_energy: number;
  active_agents: number;
}

export interface SimulationState {
  step: number;
  agents: Agent[];
  objects: SimObject[];
  grid?: Grid;
  metrics: Metrics;
  status?: {
    running: boolean;
    paused: boolean;
  };
}

/** Legacy verbose format kept for compatibility with MapEditor internal use */
export interface SimulationConfig {
  simulation: {
    grid_width: number;
    grid_height: number;
    timestep_duration_ms: number;
    max_steps: number;
    seed?: number;
  };
  warehouse: Record<string, unknown>;
  obstacles: Record<string, unknown>[];
  objects: Record<string, unknown>;
  agents: Record<string, unknown>;
  logging?: Record<string, unknown>;
}

// ── Compact grid-based format (A/B) ──────────────────────────────────────────

export interface GridScenarioMetadata {
  grid_size: number;
  num_warehouses: number;
  num_objects: number;
  max_steps: number;
  seed?: number;
}

export interface GridWarehouse {
  id: number;
  side: string;
  entrance: [number, number]; // [row, col]
  exit: [number, number]; // [row, col]
  area: [number, number][]; // [[row, col], ...]
}

/**
 * Compact grid-based scenario config (new A/B format).
 *
 * Grid cell encoding:
 *   0 = free / empty
 *   1 = wall / obstacle
 *   2 = warehouse interior
 *   3 = warehouse entrance
 *   4 = warehouse exit
 *
 * Objects are stored separately in ``objects`` and are NOT encoded in the grid.
 */
export interface GridScenarioConfig {
  metadata: GridScenarioMetadata;
  grid: number[][]; // grid[row][col]
  warehouses: GridWarehouse[];
  objects: [number, number][]; // [[row, col], ...]
}

export interface AgentRoleParams {
  count: number;
  vision_radius: number;
  communication_radius: number;
  max_energy: number;
  speed: number;
  carrying_capacity: number;
}

export interface SimulationAgentsConfig {
  scouts: AgentRoleParams;
  coordinators: AgentRoleParams;
  retrievers: AgentRoleParams;
}

/** Default agent configuration matching the backend SimulationAgentsConfig defaults */
export const DEFAULT_AGENTS_CONFIG: SimulationAgentsConfig = {
  scouts: {
    count: 1,
    vision_radius: 3,
    communication_radius: 2,
    max_energy: 500,
    speed: 1.5,
    carrying_capacity: 0,
  },
  coordinators: {
    count: 1,
    vision_radius: 2,
    communication_radius: 3,
    max_energy: 500,
    speed: 1.0,
    carrying_capacity: 0,
  },
  retrievers: {
    count: 3,
    vision_radius: 2,
    communication_radius: 2,
    max_energy: 500,
    speed: 1.0,
    carrying_capacity: 2,
  },
};
