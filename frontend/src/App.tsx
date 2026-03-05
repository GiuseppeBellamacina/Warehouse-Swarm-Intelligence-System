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
    backendStatus,
    wakeBackend,
    loadConfig,
    startSimulation,
    pauseSimulation,
    resumeSimulation,
    stopSimulation,
    resetSimulation,
    setSimulationSpeed,
  } = useSimulation();

  const [viewMode, setViewMode] = useState<ViewMode>("simulation");
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null);

  // Wake-up button cooldown (10 s)
  const [wakeCooldown, setWakeCooldown] = useState(0);
  const cooldownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleWake = useCallback(() => {
    wakeBackend();
    setWakeCooldown(10);
    cooldownRef.current = setInterval(() => {
      setWakeCooldown((prev) => {
        if (prev <= 1) {
          clearInterval(cooldownRef.current!);
          cooldownRef.current = null;
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  }, [wakeBackend]);

  useEffect(
    () => () => {
      if (cooldownRef.current) clearInterval(cooldownRef.current);
    },
    [],
  );

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
      {/* ── Free-hosting notice ── */}
      {!connected && (
        <div
          className={`flex-shrink-0 border-b px-6 py-4 flex items-center gap-5 ${
            backendStatus === "waking"
              ? "bg-yellow-950 border-yellow-700"
              : "bg-amber-950 border-amber-800"
          }`}
        >
          {/* Icon / spinner */}
          <div className="flex-shrink-0">
            {backendStatus === "waking" ? (
              <svg
                className="animate-spin h-7 w-7 text-yellow-400"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v4l3-3-3-3v4a10 10 0 100 20v-4l-3 3 3 3v-4a8 8 0 01-8-8z"
                />
              </svg>
            ) : (
              <span className="text-3xl leading-none select-none">🌙</span>
            )}
          </div>

          {/* Text */}
          <div className="flex-1 min-w-0">
            <p
              className={`font-bold text-sm ${backendStatus === "waking" ? "text-yellow-300" : "text-amber-300"}`}
            >
              {backendStatus === "waking"
                ? "Avvio del backend in corso…"
                : "Il backend è in sleep (hosting gratuito)"}
            </p>
            <p
              className={`text-xs mt-0.5 ${backendStatus === "waking" ? "text-yellow-400/80" : "text-amber-400/80"}`}
            >
              {backendStatus === "waking"
                ? "Il server Render si sta riavviando. L'operazione richiede circa 30–60 secondi — la pagina si aggiornerà automaticamente."
                : "Dopo un periodo di inattività il server va in sleep. Premi il pulsante per risvegliarlo, poi attendi il riavvio (30–60 s)."}
            </p>
          </div>

          {/* Wake-up button — inline in the banner */}
          {backendStatus !== "waking" && (
            <button
              onClick={handleWake}
              disabled={wakeCooldown > 0}
              className="flex-shrink-0 flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg font-semibold
                bg-amber-500 hover:bg-amber-400 active:bg-amber-300
                disabled:bg-gray-600 disabled:cursor-not-allowed
                text-gray-900 disabled:text-gray-400
                transition-colors shadow-md"
            >
              {wakeCooldown > 0 ? (
                <>
                  <svg
                    className="animate-spin h-4 w-4"
                    xmlns="http://www.w3.org/2000/svg"
                    fill="none"
                    viewBox="0 0 24 24"
                  >
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                    />
                  </svg>
                  <span>Riprova tra {wakeCooldown}s</span>
                </>
              ) : (
                <>
                  <span>⚡</span>
                  <span>Wake up</span>
                </>
              )}
            </button>
          )}
        </div>
      )}
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
        {/* Backend status + wake-up */}
        <div className="ml-auto flex items-center gap-2">
          <span
            className={`text-xs px-2 py-1 rounded-full font-medium ${
              connected
                ? "bg-green-900 text-green-300"
                : backendStatus === "waking"
                  ? "bg-yellow-900 text-yellow-300"
                  : "bg-red-900 text-red-300"
            }`}
          >
            {connected
              ? "● Connected"
              : backendStatus === "waking"
                ? "◌ Starting…"
                : "○ Disconnected"}
          </span>
        </div>
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
