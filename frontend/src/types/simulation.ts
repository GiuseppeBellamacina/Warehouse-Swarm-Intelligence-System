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
  /** Row-major flat array (0=unknown, 1=explored) — same size as grid width×height */
  explored?: number[];
  /** Objects this agent knows about */
  known_objects?: Position[];
  /** Warehouse cells this agent knows about */
  known_warehouses?: Position[];
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
  /** Row-major flat array (0=unexplored, 1=explored by at least one agent) */
  global_explored?: number[];
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

// ── Behavior parameter interfaces (mirror backend Pydantic schemas) ──────────

export interface ScoutBehaviorParams {
  recent_target_ttl: number;
  rescan_age: number;
  discovery_timeout: number;
  anti_cluster_distance: number;
  target_hysteresis: number;
  stuck_threshold: number;
  recharge_threshold: number;
  far_frontier_enabled: boolean;
  stale_coverage_patrol: boolean;
  anti_clustering: boolean;
  seek_coordinator: boolean;
}

export interface CoordinatorBehaviorParams {
  boredom_threshold: number;
  pos_max_age: number;
  recharge_threshold: number;
  centroid_object_bias: number;
  sync_rate_limit: number;
  seek_retrievers: boolean;
  boredom_patrol: boolean;
  object_biased_centroid: boolean;
}

export interface RetrieverBehaviorParams {
  recharge_threshold: number;
  stale_claim_age: number;
  explore_retarget_interval: number;
  opportunistic_pickup: boolean;
  task_queue_reorder: boolean;
  self_assign_from_shared_map: boolean;
  peer_broadcast: boolean;
  smart_explore: boolean;
}

export interface SimulationAgentsConfig {
  scouts: AgentRoleParams;
  coordinators: AgentRoleParams;
  retrievers: AgentRoleParams;
  scout_behavior: ScoutBehaviorParams;
  coordinator_behavior: CoordinatorBehaviorParams;
  retriever_behavior: RetrieverBehaviorParams;
}

export const DEFAULT_SCOUT_BEHAVIOR: ScoutBehaviorParams = {
  recent_target_ttl: 50,
  rescan_age: 120,
  discovery_timeout: 80,
  anti_cluster_distance: 8,
  target_hysteresis: 15,
  stuck_threshold: 8,
  recharge_threshold: 0.25,
  far_frontier_enabled: true,
  stale_coverage_patrol: true,
  anti_clustering: true,
  seek_coordinator: true,
};

export const DEFAULT_COORDINATOR_BEHAVIOR: CoordinatorBehaviorParams = {
  boredom_threshold: 20,
  pos_max_age: 25,
  recharge_threshold: 0.2,
  centroid_object_bias: 0.4,
  sync_rate_limit: 10,
  seek_retrievers: true,
  boredom_patrol: true,
  object_biased_centroid: true,
};

export const DEFAULT_RETRIEVER_BEHAVIOR: RetrieverBehaviorParams = {
  recharge_threshold: 0.2,
  stale_claim_age: 45,
  explore_retarget_interval: 15,
  opportunistic_pickup: true,
  task_queue_reorder: true,
  self_assign_from_shared_map: true,
  peer_broadcast: true,
  smart_explore: true,
};

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
  scout_behavior: { ...DEFAULT_SCOUT_BEHAVIOR },
  coordinator_behavior: { ...DEFAULT_COORDINATOR_BEHAVIOR },
  retriever_behavior: { ...DEFAULT_RETRIEVER_BEHAVIOR },
};
