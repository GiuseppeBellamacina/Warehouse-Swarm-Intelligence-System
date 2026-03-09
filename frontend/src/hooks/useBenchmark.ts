// Benchmark data collection & session management hook

import { useState, useCallback, useRef, useEffect } from "react";
import { SimulationState } from "../types/simulation";

// ── Data model ───────────────────────────────────────────────────────────────

export interface BenchmarkSnapshot {
  step: number;
  objectsRetrieved: number;
  totalObjects: number;
  retrievalProgress: number;
  averageEnergy: number;
  activeAgents: number;
}

export interface BenchmarkRun {
  id: string;
  label: string;
  /** ISO timestamp when recording started */
  startedAt: string;
  /** ISO timestamp when recording ended */
  endedAt: string | null;
  /** Config name or "custom" */
  configName: string;
  /** Grid dimensions */
  gridSize: [number, number];
  /** Agent counts */
  agents: { scouts: number; coordinators: number; retrievers: number };
  /** Total objects in the scenario */
  totalObjects: number;
  /** Seed used */
  seed: number | null;
  /** Max steps configured */
  maxSteps: number;
  /** Per-step snapshots collected during the run */
  snapshots: BenchmarkSnapshot[];
  /** Summary computed on finalise */
  summary: BenchmarkRunSummary | null;
}

export interface BenchmarkRunSummary {
  totalSteps: number;
  objectsRetrieved: number;
  totalObjects: number;
  completionPct: number;
  avgEnergyOverall: number;
  /** obj retrieved per 100 steps */
  efficiency: number;
  /** step at which first object was retrieved */
  firstRetrievalStep: number | null;
  /** step at which last object was retrieved (or last recorded step) */
  lastRetrievalStep: number | null;
}

// ── Hook ─────────────────────────────────────────────────────────────────────

const STORAGE_KEY = "wsis-benchmark-runs";

function loadRuns(): BenchmarkRun[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as BenchmarkRun[]) : [];
  } catch {
    return [];
  }
}

function saveRuns(runs: BenchmarkRun[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(runs));
}

function computeSummary(snapshots: BenchmarkSnapshot[]): BenchmarkRunSummary {
  if (snapshots.length === 0) {
    return {
      totalSteps: 0,
      objectsRetrieved: 0,
      totalObjects: 0,
      completionPct: 0,
      avgEnergyOverall: 0,
      efficiency: 0,
      firstRetrievalStep: null,
      lastRetrievalStep: null,
    };
  }
  const last = snapshots[snapshots.length - 1];
  const avgEnergy =
    snapshots.reduce((s, sn) => s + sn.averageEnergy, 0) / snapshots.length;

  let firstRetrievalStep: number | null = null;
  let lastRetrievalStep: number | null = null;
  for (const sn of snapshots) {
    if (sn.objectsRetrieved > 0) {
      if (firstRetrievalStep === null) firstRetrievalStep = sn.step;
      lastRetrievalStep = sn.step;
    }
  }

  return {
    totalSteps: last.step,
    objectsRetrieved: last.objectsRetrieved,
    totalObjects: last.totalObjects,
    completionPct:
      last.totalObjects > 0
        ? (last.objectsRetrieved / last.totalObjects) * 100
        : 0,
    avgEnergyOverall: avgEnergy,
    efficiency: last.step > 0 ? (last.objectsRetrieved / last.step) * 100 : 0,
    firstRetrievalStep,
    lastRetrievalStep,
  };
}

export const useBenchmark = () => {
  const [runs, setRuns] = useState<BenchmarkRun[]>(loadRuns);
  const [recording, setRecording] = useState(false);
  const activeRunRef = useRef<BenchmarkRun | null>(null);
  const prevRetrievedRef = useRef<number>(-1);

  // Persist whenever runs change
  useEffect(() => {
    saveRuns(runs);
  }, [runs]);

  /** Start recording a new benchmark run. */
  const startRecording = useCallback(
    (opts: {
      configName: string;
      gridSize: [number, number];
      agents: { scouts: number; coordinators: number; retrievers: number };
      totalObjects: number;
      seed: number | null;
      maxSteps: number;
      label?: string;
    }) => {
      const id = crypto.randomUUID();
      const run: BenchmarkRun = {
        id,
        label:
          opts.label ||
          `Run ${new Date().toLocaleString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`,
        startedAt: new Date().toISOString(),
        endedAt: null,
        configName: opts.configName,
        gridSize: opts.gridSize,
        agents: opts.agents,
        totalObjects: opts.totalObjects,
        seed: opts.seed,
        maxSteps: opts.maxSteps,
        snapshots: [],
        summary: null,
      };
      activeRunRef.current = run;
      prevRetrievedRef.current = -1;
      setRecording(true);
    },
    [],
  );

  /** Feed a simulation state tick — called by App on every state update. */
  const recordTick = useCallback((state: SimulationState) => {
    const run = activeRunRef.current;
    if (!run) return;
    const snap: BenchmarkSnapshot = {
      step: state.step,
      objectsRetrieved: state.metrics.objects_retrieved,
      totalObjects: state.metrics.total_objects,
      retrievalProgress: state.metrics.retrieval_progress,
      averageEnergy: state.metrics.average_energy,
      activeAgents: state.metrics.active_agents,
    };
    // Avoid duplicate steps
    const last = run.snapshots[run.snapshots.length - 1];
    if (last && last.step === snap.step) return;
    run.snapshots.push(snap);
  }, []);

  /** Stop recording and persist the run. */
  const stopRecording = useCallback(() => {
    const run = activeRunRef.current;
    if (!run) return;
    run.endedAt = new Date().toISOString();
    run.summary = computeSummary(run.snapshots);
    setRuns((prev) => [...prev, run]);
    activeRunRef.current = null;
    prevRetrievedRef.current = -1;
    setRecording(false);
  }, []);

  /** Discard the current recording without saving. */
  const cancelRecording = useCallback(() => {
    activeRunRef.current = null;
    prevRetrievedRef.current = -1;
    setRecording(false);
  }, []);

  /** Delete a saved run. */
  const deleteRun = useCallback((id: string) => {
    setRuns((prev) => prev.filter((r) => r.id !== id));
  }, []);

  /** Delete all saved runs. */
  const clearAllRuns = useCallback(() => {
    setRuns([]);
  }, []);

  /** Rename a run label. */
  const renameRun = useCallback((id: string, label: string) => {
    setRuns((prev) => prev.map((r) => (r.id === id ? { ...r, label } : r)));
  }, []);

  /** Export all runs as JSON. */
  const exportRunsJSON = useCallback(() => {
    const blob = new Blob([JSON.stringify(runs, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `benchmark-runs-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [runs]);

  /** Import runs from a JSON file (merges, no duplicates by id). */
  const importRunsJSON = useCallback((file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const imported = JSON.parse(
          e.target?.result as string,
        ) as BenchmarkRun[];
        if (!Array.isArray(imported)) return;
        setRuns((prev) => {
          const ids = new Set(prev.map((r) => r.id));
          const newRuns = imported.filter((r) => r.id && !ids.has(r.id));
          return [...prev, ...newRuns];
        });
      } catch {
        // invalid JSON — silently ignored
      }
    };
    reader.readAsText(file);
  }, []);

  return {
    runs,
    recording,
    activeRun: activeRunRef.current,
    startRecording,
    recordTick,
    stopRecording,
    cancelRecording,
    deleteRun,
    clearAllRuns,
    renameRun,
    exportRunsJSON,
    importRunsJSON,
  };
};
