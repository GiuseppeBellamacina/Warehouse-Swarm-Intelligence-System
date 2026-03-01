// Main App Component

import React, { useState, useRef, useEffect, useCallback } from "react";
import { useSimulation } from "./hooks/useSimulation";
import { GridCanvas } from "./components/GridCanvas";
import { ControlPanel } from "./components/ControlPanel";
import { MetricsDisplay } from "./components/MetricsDisplay";
import { MapEditor } from "./components/MapEditor";
import { AgentList } from "./components/AgentList";
import "./index.css";

type ViewMode = "simulation" | "editor";

/** Vertical drag handle between two resizable panels */
const DragHandle: React.FC<{ onDrag: (dx: number) => void }> = ({ onDrag }) => {
  const dragging = useRef(false);
  const lastX = useRef(0);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    dragging.current = true;
    lastX.current = e.clientX;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    e.preventDefault();
  }, []);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const dx = e.clientX - lastX.current;
      lastX.current = e.clientX;
      onDrag(dx);
    };
    const onMouseUp = () => {
      dragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [onDrag]);

  return (
    <div
      onMouseDown={onMouseDown}
      className="w-1.5 flex-shrink-0 bg-gray-700 hover:bg-blue-500 active:bg-blue-400 cursor-col-resize transition-colors duration-150 rounded"
      title="Trascina per ridimensionare"
    />
  );
};

function App() {
  const {
    state,
    connected,
    isRunning,
    isPaused,
    isLoaded,
    loadConfig,
    startSimulation,
    uploadConfig: _uploadConfig,
    pauseSimulation,
    resumeSimulation,
    stopSimulation,
    resetSimulation,
    setSimulationSpeed,
  } = useSimulation();

  const [viewMode, setViewMode] = useState<ViewMode>("simulation");
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null);

  // Panel widths in pixels — initialised as % of viewport so the map gets ~50%,
  // agents ~15%, metrics ~15%, controls ~20%.
  const [agentsW, setAgentsW] = useState(() =>
    Math.round(window.innerWidth * 0.15),
  );
  const [metricsW, setMetricsW] = useState(() =>
    Math.round(window.innerWidth * 0.15),
  );
  const [controlsW, setControlsW] = useState(() =>
    Math.round(window.innerWidth * 0.2),
  );

  const MIN = 120;
  const MAX = 700;
  const clamp = (v: number) => Math.max(MIN, Math.min(MAX, v));

  return (
    <div className="h-screen bg-gray-900 text-white flex flex-col overflow-hidden">
      {/* ── Header ── */}
      <header className="flex-shrink-0 px-6 py-3 border-b border-gray-700 flex items-center gap-4">
        <div>
          <h1 className="text-xl font-bold leading-tight">
            Warehouse Swarm Intelligence System
          </h1>
          <p className="text-gray-400 text-xs">
            Multi-Agent Object Retrieval Simulation
          </p>
        </div>
        {/* Connection badge */}
        <span
          className={`ml-auto text-xs px-2 py-1 rounded-full font-medium ${
            connected
              ? "bg-green-900 text-green-300"
              : "bg-red-900 text-red-300"
          }`}
        >
          {connected ? "● Connected" : "○ Disconnected"}
        </span>
      </header>

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-row overflow-hidden min-h-0 gap-0 p-2">
        {/* Panel 1: Agent list */}
        <div
          className="flex-shrink-0 flex flex-col overflow-hidden bg-gray-800 rounded-lg"
          style={{ width: agentsW }}
        >
          <div className="flex-1 overflow-y-auto">
            {state && state.agents && state.agents.length > 0 ? (
              <AgentList
                agents={state.agents}
                selectedAgentId={selectedAgentId}
                onSelectAgent={setSelectedAgentId}
              />
            ) : (
              <div className="p-4">
                <h2 className="text-base font-bold mb-3">Agents</h2>
                <div className="text-center text-gray-500 py-8 text-sm">
                  No active agents
                </div>
              </div>
            )}
          </div>
        </div>

        <DragHandle onDrag={(dx) => setAgentsW((w) => clamp(w + dx))} />

        {/* Panel 2: Map / Editor  (flex-1 → fills remaining space) */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          {/* Tabs */}
          <div className="flex-shrink-0 flex gap-1 mb-1">
            {(["simulation", "editor"] as ViewMode[]).map((mode) => (
              <button
                key={mode}
                onClick={() => setViewMode(mode)}
                className={`flex-1 py-1.5 px-3 rounded font-medium text-sm transition ${
                  viewMode === mode
                    ? "bg-gray-700 text-white"
                    : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                }`}
              >
                {mode === "simulation" ? "Simulation" : "Map Editor"}
              </button>
            ))}
          </div>

          {/* Content */}
          {viewMode === "simulation" ? (
            <div className="flex-1 bg-gray-800 rounded-lg overflow-hidden flex items-center justify-center p-2 min-h-0">
              {state && state.grid ? (
                <GridCanvas state={state} selectedAgentId={selectedAgentId} />
              ) : (
                <div className="flex flex-col items-center justify-center text-center gap-3">
                  <svg
                    className="h-12 w-12 text-gray-600"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
                    />
                  </svg>
                  <p className="text-gray-400 text-sm">
                    Load a configuration to start
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              <MapEditor onExport={loadConfig} />
            </div>
          )}
        </div>

        <DragHandle onDrag={(dx) => setMetricsW((w) => clamp(w - dx))} />

        {/* Panel 3: Metrics */}
        <div
          className="flex-shrink-0 flex flex-col overflow-hidden bg-gray-800 rounded-lg"
          style={{ width: metricsW }}
        >
          <div className="flex-1 overflow-y-auto">
            <MetricsDisplay state={state} />
          </div>
        </div>

        <DragHandle onDrag={(dx) => setControlsW((w) => clamp(w - dx))} />

        {/* Panel 4: Controls */}
        <div
          className="flex-shrink-0 flex flex-col overflow-hidden bg-gray-800 rounded-lg"
          style={{ width: controlsW }}
        >
          <div className="flex-1 overflow-y-auto">
            <ControlPanel
              connected={connected}
              isRunning={isRunning}
              isPaused={isPaused}
              isLoaded={isLoaded}
              onLoad={loadConfig}
              onStartRun={startSimulation}
              onPause={pauseSimulation}
              onResume={resumeSimulation}
              onStop={stopSimulation}
              onReset={resetSimulation}
              onSpeedChange={setSimulationSpeed}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
