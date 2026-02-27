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

export interface Obstacle {
  type: "wall" | "box";
  data: any;
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

export interface SimulationConfig {
  simulation: {
    grid_width: number;
    grid_height: number;
    timestep_duration_ms: number;
    max_steps: number;
    seed?: number;
  };
  warehouse: any;
  obstacles: any[];
  objects: any;
  agents: any;
  logging?: any;
}
