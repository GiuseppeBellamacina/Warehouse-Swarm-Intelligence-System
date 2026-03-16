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
  type_index: number;
  role: "scout" | "coordinator" | "retriever";
  x: number;
  y: number;
  energy: number;
  max_energy: number;
  state: string;
  carrying: number;
  delivered: number;
  vision_radius: number;
  communication_radius: number;
  recent_messages: AgentMessage[];
  path: Position[];
  /** Row-major flat array (0=unknown, 1=explored) — same size as grid width×height */
  explored?: number[];
  /** Row-major flat array (0=not scanned, 1=scanned by vision) — used when map_known is true */
  object_explored?: number[];
  /** Objects this agent knows about */
  known_objects?: Position[];
  /** Far-away objects the retriever is unsure about (will verify only when idle) */
  dubious_objects?: Position[];
  /** Dubious objects promoted to the task queue — agent is actively travelling to verify */
  verifying_objects?: Position[];
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
  messages_sent: number;
}

export interface SimulationState {
  step: number;
  agents: Agent[];
  objects: SimObject[];
  grid?: Grid;
  metrics: Metrics;
  /** Row-major flat array (0=unexplored, 1=explored by at least one agent) */
  global_explored?: number[];
  /** Row-major flat array (0=not scanned, 1=scanned) — global union of vision_explored */
  global_object_explored?: number[];
  /** Whether agents started with full map knowledge */
  map_known?: boolean;
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
  target_lock_duration: number;
  min_frontier_cluster_size: number;
  seek_coordinator_delay: number;
  far_frontier_enabled: boolean;
  stale_coverage_patrol: boolean;
  anti_clustering: boolean;
  seek_coordinator: boolean;
  zone_divisions: number;
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
  smart_explore: boolean;
  explore_retarget_interval: number;
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
  warehouse_congestion_reroute: boolean;
  warehouse_congestion_threshold: number;
  jam_priority: boolean;
  autonomous_pickup: boolean;
}

export interface SimulationAgentsConfig {
  scouts: AgentRoleParams;
  coordinators: AgentRoleParams;
  retrievers: AgentRoleParams;
  scout_behavior: ScoutBehaviorParams;
  coordinator_behavior: CoordinatorBehaviorParams;
  retriever_behavior: RetrieverBehaviorParams;
  map_known?: boolean;
}

/**
 * Fetch the canonical default agent configuration from the backend.
 * The backend Pydantic schemas are the single source of truth.
 */
export async function fetchAgentDefaults(
  backendUrl: string,
): Promise<SimulationAgentsConfig> {
  const res = await fetch(`${backendUrl}/api/defaults`);
  if (!res.ok) throw new Error(`Failed to fetch defaults: ${res.status}`);
  return (await res.json()) as SimulationAgentsConfig;
}
