// Hook to record simulation state snapshots indexed by step number,
// enabling timeline scrubbing / replay.

import { useCallback, useRef, useState } from "react";
import { SimulationState } from "../types/simulation";
import { TrailHistory } from "../components/GridCanvas";

/** Maximum number of snapshots kept in memory. Oldest are discarded. */
const MAX_SNAPSHOTS = 5000;

export interface StepHistory {
  /** Record a new tick (call on every simulation_state event). */
  recordTick: (state: SimulationState) => void;
  /** Clear all recorded history. */
  clear: () => void;

  /** Minimum step number stored. */
  minStep: number;
  /** Maximum step number stored (= latest). */
  maxStep: number;
  /** Total number of snapshots stored. */
  count: number;

  /** Currently selected step for playback (null = live). */
  viewStep: number | null;
  /** Set the step to view. Pass null to return to live mode. */
  setViewStep: (step: number | null) => void;

  /** Get the SimulationState for the currently viewed step (or null if unavailable). */
  getViewState: () => SimulationState | null;

  /** Compute agent trails from stored snapshots up to (and including) the given step. */
  computeTrailUpTo: (step: number) => TrailHistory;
}

export const useStepHistory = (): StepHistory => {
  // We store snapshots in a Map keyed by step number.
  // Using a ref avoids re-renders on every tick — only the slider UI
  // needs to re-render when min/max change.
  const snapshotsRef = useRef<Map<number, SimulationState>>(new Map());
  const [minStep, setMinStep] = useState(0);
  const [maxStep, setMaxStep] = useState(0);
  const [count, setCount] = useState(0);
  const [viewStep, setViewStep] = useState<number | null>(null);

  const recordTick = useCallback((state: SimulationState) => {
    const map = snapshotsRef.current;
    const step = state.step;

    // Deep-clone to avoid referencing mutated objects
    map.set(step, JSON.parse(JSON.stringify(state)));

    // Evict oldest if over budget
    if (map.size > MAX_SNAPSHOTS) {
      const oldest = map.keys().next().value;
      if (oldest !== undefined) map.delete(oldest);
    }

    // Update bounds
    let lo = Infinity;
    let hi = -Infinity;
    for (const k of map.keys()) {
      if (k < lo) lo = k;
      if (k > hi) hi = k;
    }
    setMinStep(lo === Infinity ? 0 : lo);
    setMaxStep(hi === -Infinity ? 0 : hi);
    setCount(map.size);
  }, []);

  const clear = useCallback(() => {
    snapshotsRef.current.clear();
    setMinStep(0);
    setMaxStep(0);
    setCount(0);
    setViewStep(null);
  }, []);

  const getViewState = useCallback((): SimulationState | null => {
    if (viewStep === null) return null;
    return snapshotsRef.current.get(viewStep) ?? null;
  }, [viewStep]);

  const computeTrailUpTo = useCallback((targetStep: number): TrailHistory => {
    const trail: TrailHistory = new Map();
    const map = snapshotsRef.current;
    // Collect step keys up to targetStep, sorted ascending
    const keys: number[] = [];
    for (const k of map.keys()) {
      if (k <= targetStep) keys.push(k);
    }
    keys.sort((a, b) => a - b);
    for (const k of keys) {
      const snap = map.get(k);
      if (!snap) continue;
      for (const agent of snap.agents) {
        let t = trail.get(agent.id);
        if (!t) {
          t = [];
          trail.set(agent.id, t);
        }
        const last = t.length > 0 ? t[t.length - 1] : null;
        if (!last || last.x !== agent.x || last.y !== agent.y) {
          t.push({ x: agent.x, y: agent.y });
        }
      }
    }
    return trail;
  }, []);

  return {
    recordTick,
    clear,
    minStep,
    maxStep,
    count,
    viewStep,
    setViewStep,
    getViewState,
    computeTrailUpTo,
  };
};
