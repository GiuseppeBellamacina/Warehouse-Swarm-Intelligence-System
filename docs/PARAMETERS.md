# Agent Parameters Reference

All parameters listed below can be modified from the **Control Panel** UI before loading a simulation (via the ⚙ _Agent & Config Overrides_ and 🧠 _Behavior Tuning_ accordion panels), or supplied programmatically through the `POST /api/simulation/load` JSON body under the `agents` key.

---

## Physical Parameters (per role)

These control the agent's physical capabilities on the grid.

| Parameter              | Scout | Coordinator | Retriever | Description                                          |
| ---------------------- | ----- | ----------- | --------- | ---------------------------------------------------- |
| `count`                | 1     | 1           | 3         | Number of agents of this role                        |
| `vision_radius`        | 3     | 2           | 2         | How many cells the agent can see in each direction   |
| `communication_radius` | 2     | 3           | 2         | Range (Manhattan) for message exchange               |
| `max_energy`           | 500   | 500         | 500       | Total energy pool; depleted by movement              |
| `speed`                | 1.5   | 1.0         | 1.0       | Movement multiplier (scouts get 2 moves/step at 1.5) |
| `carrying_capacity`    | 0     | 0           | 2         | Max objects a retriever can carry at once            |

---

## Scout Behavior Parameters

Control how scouts explore, communicate, and avoid redundant work.

| Parameter               | Default | Range    | Description                                                                      |
| ----------------------- | ------- | -------- | -------------------------------------------------------------------------------- |
| `recent_target_ttl`     | 50      | ≥ 1      | Steps a reached frontier is blacklisted to prevent oscillation                   |
| `rescan_age`            | 120     | ≥ 10     | Steps without vision before a cell becomes re-eligible for stale-coverage patrol |
| `discovery_timeout`     | 80      | ≥ 10     | Steps without a coordinator before discarding undelivered discoveries            |
| `anti_cluster_distance` | 8       | ≥ 0      | Min Manhattan distance from other scouts when selecting frontiers                |
| `target_hysteresis`     | 15      | ≥ 0      | Min Manhattan distance before switching to a new best frontier (prevents jitter) |
| `stuck_threshold`       | 8       | ≥ 1      | Consecutive move failures before giving up on the current target                 |
| `recharge_threshold`    | 0.25    | 0.05–0.5 | Energy fraction (of `max_energy`) that triggers warehouse recharge               |

### Scout Feature Toggles

| Toggle                  | Default | Effect when disabled                                                                              |
| ----------------------- | ------- | ------------------------------------------------------------------------------------------------- |
| `far_frontier_enabled`  | ✅      | Scout uses all frontiers equally instead of preferring distant ones                               |
| `stale_coverage_patrol` | ✅      | Scout never re-explores already-seen cells; relies on frontiers only                              |
| `anti_clustering`       | ✅      | Scouts ignore proximity to other scouts when choosing frontiers                                   |
| `seek_coordinator`      | ✅      | Scout won't actively move toward coordinators to deliver discoveries; waits for passive encounter |

---

## Coordinator Behavior Parameters

Control strategic positioning and retriever management.

| Parameter              | Default | Range    | Description                                                                                                     |
| ---------------------- | ------- | -------- | --------------------------------------------------------------------------------------------------------------- |
| `boredom_threshold`    | 20      | ≥ 5      | Consecutive idle steps before forcing a waypoint patrol cycle                                                   |
| `pos_max_age`          | 25      | ≥ 5      | Max age (steps) of a retriever's reported position before it's considered stale                                 |
| `recharge_threshold`   | 0.20    | 0.05–0.5 | Energy fraction triggering warehouse recharge                                                                   |
| `centroid_object_bias` | 0.4     | 0.0–1.0  | Weight of pending-object centroid vs retriever centroid (0 = pure retriever centroid, 1 = pure object centroid) |
| `sync_rate_limit`      | 10      | ≥ 1      | Minimum steps between coordinator-to-coordinator sync messages                                                  |

### Coordinator Feature Toggles

| Toggle                   | Default | Effect when disabled                                                                  |
| ------------------------ | ------- | ------------------------------------------------------------------------------------- |
| `seek_retrievers`        | ✅      | Coordinator stays at centroid even when tasks exist but no retrievers are in range    |
| `boredom_patrol`         | ✅      | Coordinator never forces waypoint patrol; stays at centroid indefinitely              |
| `object_biased_centroid` | ✅      | Coordinator positions purely on retriever centroid, ignoring pending object locations |

---

## Retriever Behavior Parameters

Control object collection, cooperation, and idle exploration.

| Parameter                   | Default | Range    | Description                                                                |
| --------------------------- | ------- | -------- | -------------------------------------------------------------------------- |
| `recharge_threshold`        | 0.20    | 0.05–0.5 | Energy fraction triggering warehouse recharge                              |
| `stale_claim_age`           | 45      | ≥ 10     | Steps before a peer's object claim is considered stale (free to take over) |
| `explore_retarget_interval` | 15      | ≥ 1      | Steps between picking a new idle exploration target                        |

### Retriever Feature Toggles

| Toggle                        | Default | Effect when disabled                                                                                         |
| ----------------------------- | ------- | ------------------------------------------------------------------------------------------------------------ |
| `opportunistic_pickup`        | ✅      | Retriever only collects coordinator-assigned or self-assigned tasks, never nearby bonus objects              |
| `task_queue_reorder`          | ✅      | Task queue stays in FIFO order rather than re-sorting by distance each step                                  |
| `self_assign_from_shared_map` | ✅      | Retriever waits for coordinator assignment; does not proactively claim objects from shared map knowledge     |
| `peer_broadcast`              | ✅      | Full retrievers don't push newly spotted objects to peer retrievers; info spreads only via passive map relay |
| `smart_explore`               | ✅      | Idle retriever wanders randomly instead of heading toward nearest unexplored boundary                        |

---

## API Usage Example

```json
POST /api/simulation/load
{
  "scenario": { ... },
  "agents": {
    "scouts":       { "count": 2, "vision_radius": 4, "communication_radius": 3, "max_energy": 600, "speed": 1.5, "carrying_capacity": 0 },
    "coordinators": { "count": 1, "vision_radius": 3, "communication_radius": 4, "max_energy": 500, "speed": 1.0, "carrying_capacity": 0 },
    "retrievers":   { "count": 4, "vision_radius": 2, "communication_radius": 2, "max_energy": 500, "speed": 1.0, "carrying_capacity": 3 },
    "scout_behavior": {
      "recent_target_ttl": 30,
      "rescan_age": 80,
      "far_frontier_enabled": false,
      "anti_clustering": true
    },
    "coordinator_behavior": {
      "boredom_threshold": 15,
      "centroid_object_bias": 0.6,
      "boredom_patrol": true
    },
    "retriever_behavior": {
      "stale_claim_age": 30,
      "opportunistic_pickup": false,
      "smart_explore": true
    }
  }
}
```

> **Note:** Omitted behavior fields default to the values shown in the tables above. You only need to send the fields you want to override.
