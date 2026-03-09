// Main App Component

import React, { useState, useRef, useEffect, useCallback } from "react";
import { useSimulation } from "./hooks/useSimulation";
import { useBenchmark } from "./hooks/useBenchmark";
import { GridCanvas } from "./components/GridCanvas";
import { ControlPanel } from "./components/ControlPanel";
import { MetricsDisplay } from "./components/MetricsDisplay";
import { BenchmarkPanel } from "./components/BenchmarkPanel";
import { MapEditor } from "./components/MapEditor";
import { AgentList } from "./components/AgentList";
import "./index.css";

type ViewMode = "simulation" | "editor";
type MobileTab = "dashboard" | "agents" | "metrics" | "controls" | "benchmark";
type MetricsPanelView = "metrics" | "benchmark";

/** Hook: returns true when viewport width < breakpoint (default 768) */
function useIsMobile(breakpoint = 768) {
  const [mobile, setMobile] = useState(() => window.innerWidth < breakpoint);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const handler = (e: MediaQueryListEvent) => setMobile(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [breakpoint]);
  return mobile;
}

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
      className="w-1 flex-shrink-0 group cursor-col-resize flex items-center justify-center"
      title="Drag to resize"
    >
      <div className="w-0.5 h-8 rounded-full bg-gray-600/60 group-hover:bg-blue-400/80 group-hover:h-12 group-active:bg-blue-400 transition-all duration-200" />
    </div>
  );
};

function App() {
  const {
    state,
    connected,
    isRunning,
    isPaused,
    isLoaded,
    isStopped,
    backendStatus,
    wakeBackend,
    setBackendOffline,
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
  const isMobile = useIsMobile();
  const [mobileTab, setMobileTab] = useState<MobileTab>("dashboard");
  const [metricsPanelView, setMetricsPanelView] =
    useState<MetricsPanelView>("metrics");

  // ── Benchmark ──
  const benchmark = useBenchmark();

  /** Feed every simulation state tick into the benchmark recorder. */
  useEffect(() => {
    if (benchmark.recording && state) {
      benchmark.recordTick(state);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, benchmark.recording, benchmark.recordTick]);

  /** Auto-stop recording when simulation stops or completes. */
  useEffect(() => {
    if (benchmark.recording && isStopped) {
      benchmark.stopRecording();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStopped, benchmark.recording, benchmark.stopRecording]);

  const handleStartRecording = useCallback(() => {
    if (!state) return;
    const agents = state.agents;
    const scoutAgents = agents.filter((a) => a.role === "scout");
    const coordAgents = agents.filter((a) => a.role === "coordinator");
    const retrieverAgents = agents.filter((a) => a.role === "retriever");

    const avgParam = (
      arr: typeof agents,
      fn: (a: (typeof agents)[0]) => number,
    ) => (arr.length > 0 ? arr.reduce((s, a) => s + fn(a), 0) / arr.length : 0);

    benchmark.startRecording({
      configName: state.grid
        ? `${state.grid.width}×${state.grid.height}`
        : "unknown",
      gridSize: state.grid ? [state.grid.width, state.grid.height] : [0, 0],
      agents: {
        scouts: scoutAgents.length,
        coordinators: coordAgents.length,
        retrievers: retrieverAgents.length,
      },
      agentParams: {
        scouts: {
          visionRadius: avgParam(scoutAgents, (a) => a.vision_radius),
          communicationRadius: avgParam(
            scoutAgents,
            (a) => a.communication_radius,
          ),
          speed: 1,
          maxEnergy: avgParam(scoutAgents, (a) => a.max_energy),
        },
        coordinators: {
          visionRadius: avgParam(coordAgents, (a) => a.vision_radius),
          communicationRadius: avgParam(
            coordAgents,
            (a) => a.communication_radius,
          ),
          speed: 1,
          maxEnergy: avgParam(coordAgents, (a) => a.max_energy),
        },
        retrievers: {
          visionRadius: avgParam(retrieverAgents, (a) => a.vision_radius),
          communicationRadius: avgParam(
            retrieverAgents,
            (a) => a.communication_radius,
          ),
          speed: 1,
          maxEnergy: avgParam(retrieverAgents, (a) => a.max_energy),
        },
      },
      totalObjects: state.metrics.total_objects,
      seed: null,
      maxSteps: 0,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, benchmark.startRecording]);

  // Wake-up retry loop: ping every 10 s, up to 10 attempts (100 s total)
  const MAX_WAKE_ATTEMPTS = 10;
  const WAKE_INTERVAL_SEC = 10;

  const [wakeLoopActive, setWakeLoopActive] = useState(false);
  const [wakeAttempt, setWakeAttempt] = useState(0);
  const [wakeCountdown, setWakeCountdown] = useState(0);
  const wakeTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wakeStateRef = useRef({ active: false, attempt: 0, countdown: 0 });

  const stopWakeLoop = useCallback(
    (succeeded: boolean) => {
      if (wakeTimerRef.current) clearInterval(wakeTimerRef.current);
      wakeTimerRef.current = null;
      wakeStateRef.current = { active: false, attempt: 0, countdown: 0 };
      setWakeLoopActive(false);
      setWakeAttempt(0);
      setWakeCountdown(0);
      if (!succeeded) setBackendOffline();
    },
    [setBackendOffline],
  );

  const handleWake = useCallback(() => {
    if (wakeStateRef.current.active) return;
    wakeStateRef.current = {
      active: true,
      attempt: 1,
      countdown: WAKE_INTERVAL_SEC,
    };
    setWakeLoopActive(true);
    setWakeAttempt(1);
    setWakeCountdown(WAKE_INTERVAL_SEC);

    // First attempt fires immediately
    wakeBackend().then((ok) => {
      if (ok) stopWakeLoop(true);
    });

    wakeTimerRef.current = setInterval(() => {
      if (!wakeStateRef.current.active) return;
      wakeStateRef.current.countdown -= 1;
      setWakeCountdown(wakeStateRef.current.countdown);

      if (wakeStateRef.current.countdown <= 0) {
        const next = wakeStateRef.current.attempt + 1;
        if (next > MAX_WAKE_ATTEMPTS) {
          stopWakeLoop(false);
          return;
        }
        wakeStateRef.current.attempt = next;
        wakeStateRef.current.countdown = WAKE_INTERVAL_SEC;
        setWakeAttempt(next);
        setWakeCountdown(WAKE_INTERVAL_SEC);
        wakeBackend().then((ok) => {
          if (ok) stopWakeLoop(true);
        });
      }
    }, 1000);
  }, [wakeBackend, stopWakeLoop]);

  // Stop the retry loop as soon as the WebSocket reconnects
  useEffect(() => {
    if (connected && wakeStateRef.current.active) stopWakeLoop(true);
  }, [connected, stopWakeLoop]);

  useEffect(
    () => () => {
      if (wakeTimerRef.current) clearInterval(wakeTimerRef.current);
    },
    [],
  );

  // Panel widths in pixels — initialised as % of viewport.
  // Agents 18% | Map 42.5% (flex-1) | Metrics 18% | Controls 22.5%
  const [agentsW, setAgentsW] = useState(() =>
    Math.round(window.innerWidth * 0.18),
  );
  const [metricsW, setMetricsW] = useState(() =>
    Math.round(window.innerWidth * 0.18),
  );
  const [controlsW, setControlsW] = useState(() =>
    Math.round(window.innerWidth * 0.225),
  );

  const MIN = 120;
  const MAX = 700;
  const clamp = (v: number) => Math.max(MIN, Math.min(MAX, v));

  return (
    <div className="h-screen bg-[#0f1117] text-gray-100 flex flex-col overflow-hidden">
      {/* ── Free-hosting notice ── */}
      {!connected && (
        <div className="flex-shrink-0 border-b border-gray-800/60 bg-gray-900/70 backdrop-blur-sm px-4 md:px-6 py-3 md:py-4 flex items-center gap-3 md:gap-5">
          {/* Icon / spinner */}
          <div className="flex-shrink-0">
            {backendStatus === "waking" ? (
              <div className="relative w-8 h-8 md:w-9 md:h-9 flex items-center justify-center">
                <svg
                  className="animate-spin absolute inset-0 h-8 w-8 md:h-9 md:w-9 text-blue-500/60"
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
                    strokeWidth="3"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8v4l3-3-3-3v4a10 10 0 100 20v-4l-3 3 3 3v-4a8 8 0 01-8-8z"
                  />
                </svg>
                <div className="w-3.5 h-3.5 md:w-4 md:h-4 rounded-full bg-blue-500/40 animate-pulse" />
              </div>
            ) : (
              <div className="w-8 h-8 md:w-9 md:h-9 rounded-lg bg-gray-800/80 border border-gray-700/50 flex items-center justify-center">
                <svg
                  className="w-4 h-4 md:w-5 md:h-5 text-gray-500"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z"
                  />
                </svg>
              </div>
            )}
          </div>

          {/* Text */}
          <div className="flex-1 min-w-0">
            <p className="font-semibold text-xs md:text-sm text-gray-200">
              {backendStatus === "waking"
                ? "Avvio del backend in corso\u2026"
                : "Il backend \u00e8 in sleep"}
            </p>
            <p className="text-[10px] md:text-xs mt-0.5 text-gray-500 hidden sm:block">
              {backendStatus === "waking"
                ? wakeLoopActive
                  ? `Tentativo ${wakeAttempt}/${MAX_WAKE_ATTEMPTS} \u2014 prossima richiesta tra ${wakeCountdown > 0 ? `${wakeCountdown}s` : "\u2026"}`
                  : "Il server si sta riavviando, attendi 30\u201360 secondi\u2026"
                : "Dopo un periodo di inattivit\u00e0 il server va in sleep. Risveglialo per continuare."}
            </p>
          </div>

          {/* Wake-up button / retry-loop progress */}
          {wakeLoopActive ? (
            <div className="flex-shrink-0 flex items-center gap-2 md:gap-3">
              <p className="text-xs font-mono text-blue-400/80">
                {wakeCountdown > 0 ? `${wakeCountdown}s` : "\u2026"}
              </p>
              <button
                onClick={() => stopWakeLoop(false)}
                className="text-xs px-2.5 md:px-3 py-1.5 rounded-md bg-gray-800/80 border border-gray-700/50 hover:bg-gray-700/80 text-gray-400 hover:text-gray-300 transition-colors"
              >
                Annulla
              </button>
            </div>
          ) : backendStatus === "offline" || backendStatus === "unknown" ? (
            <button
              onClick={handleWake}
              className="flex-shrink-0 flex items-center gap-1.5 md:gap-2 text-xs px-3 md:px-4 py-1.5 md:py-2 rounded-lg font-semibold
                bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500
                text-white transition-all shadow-md shadow-blue-900/30"
            >
              <svg
                className="w-3.5 h-3.5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"
                />
              </svg>
              <span>Wake up</span>
            </button>
          ) : null}
        </div>
      )}
      {/* ── Header ── */}
      <header className="flex-shrink-0 px-3 md:px-5 py-2 md:py-2.5 border-b border-gray-800/80 bg-gray-900/60 backdrop-blur-md flex items-center gap-3 md:gap-4">
        <div className="flex items-center gap-2 md:gap-3">
          <img
            src="/favicon.svg"
            alt="Logo"
            className="w-7 h-7 md:w-8 md:h-8 rounded-lg shadow-lg shadow-blue-900/30"
          />
          <div>
            <h1 className="text-xs md:text-sm font-bold leading-tight tracking-tight">
              Warehouse Swarm Intelligence
            </h1>
            <p className="text-gray-500 text-[9px] md:text-[10px] tracking-wide hidden sm:block">
              Multi-Agent Object Retrieval
            </p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span
            className={`text-[10px] px-2 md:px-2.5 py-0.5 md:py-1 rounded-full font-medium border ${
              connected
                ? "bg-emerald-950/60 text-emerald-400 border-emerald-800/50"
                : backendStatus === "waking"
                  ? "bg-yellow-950/60 text-yellow-400 border-yellow-800/50"
                  : "bg-red-950/60 text-red-400 border-red-800/50"
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
      {isMobile ? (
        /* ═══ MOBILE LAYOUT: dashboard + detail tabs ═══ */
        <>
          <div className="flex-1 flex flex-col overflow-hidden min-h-0">
            {mobileTab === "dashboard" && (
              <div className="flex-1 flex flex-col overflow-hidden min-h-0 p-1.5 gap-1.5">
                {/* Sim / Editor toggle */}
                <div className="flex-shrink-0 flex gap-0.5 bg-gray-900/60 p-0.5 rounded-lg border border-gray-800/50">
                  {(["simulation", "editor"] as ViewMode[]).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setViewMode(mode)}
                      className={`flex-1 py-1 px-2 rounded-md font-medium text-[11px] transition-all duration-200 ${
                        viewMode === mode
                          ? "bg-gray-700/80 text-white shadow-sm"
                          : "text-gray-500 hover:text-gray-300"
                      }`}
                    >
                      {mode === "simulation" ? "Simulation" : "Map Editor"}
                    </button>
                  ))}
                </div>

                {/* Grid area — takes remaining space */}
                {viewMode === "simulation" ? (
                  <div className="flex-1 bg-gray-900/70 border border-gray-800/60 rounded-xl overflow-hidden flex items-center justify-center p-1 min-h-0 backdrop-blur-sm">
                    {state && state.grid ? (
                      <GridCanvas
                        state={state}
                        selectedAgentId={selectedAgentId}
                      />
                    ) : (
                      <div className="flex flex-col items-center justify-center text-center gap-2">
                        <svg
                          className="h-8 w-8 text-gray-700"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={1.5}
                            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
                          />
                        </svg>
                        <p className="text-gray-600 text-xs">
                          Load a configuration to start
                        </p>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="flex-1 overflow-auto rounded-xl border border-gray-800/60">
                    <MapEditor
                      onExport={(scenario, agents) => {
                        loadConfig(scenario, agents).then(() =>
                          setViewMode("simulation"),
                        );
                      }}
                    />
                  </div>
                )}

                {/* ── Key metrics strip ── */}
                {state && state.metrics && (
                  <div className="flex-shrink-0 flex gap-1">
                    {[
                      {
                        label: "Step",
                        value: String(state.step),
                        color: "text-blue-400",
                      },
                      {
                        label: "Retrieved",
                        value: `${state.metrics.objects_retrieved}/${state.metrics.total_objects}`,
                        color: "text-emerald-400",
                      },
                      {
                        label: "Progress",
                        value: `${(state.metrics.retrieval_progress * 100).toFixed(0)}%`,
                        color:
                          state.metrics.retrieval_progress > 0.5
                            ? "text-emerald-400"
                            : "text-yellow-400",
                      },
                    ].map(({ label, value, color }) => (
                      <div
                        key={label}
                        className="flex-1 bg-gray-800/60 border border-gray-700/40 rounded-lg px-2 py-1.5 text-center"
                      >
                        <div className="text-[8px] font-medium text-gray-500 uppercase tracking-widest">
                          {label}
                        </div>
                        <div
                          className={`text-sm font-bold leading-tight ${color}`}
                        >
                          {value}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* ── Action buttons ── */}
                <div className="flex-shrink-0 flex gap-1.5">
                  {!isRunning && !isLoaded && (
                    <button
                      onClick={() => setMobileTab("controls")}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold
                        bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white transition-colors"
                    >
                      <svg
                        className="w-3.5 h-3.5"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"
                        />
                      </svg>
                      Load Config
                    </button>
                  )}
                  {isLoaded && !isRunning && !isStopped && (
                    <button
                      onClick={startSimulation}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold
                        bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white transition-colors"
                    >
                      <svg
                        className="w-3.5 h-3.5"
                        fill="currentColor"
                        viewBox="0 0 20 20"
                      >
                        <path d="M6.3 2.841A1.5 1.5 0 004 4.11v11.78a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z" />
                      </svg>
                      Start
                    </button>
                  )}
                  {isRunning && !isPaused && (
                    <button
                      onClick={pauseSimulation}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold
                        bg-yellow-600 hover:bg-yellow-500 active:bg-yellow-700 text-white transition-colors"
                    >
                      <svg
                        className="w-3.5 h-3.5"
                        fill="currentColor"
                        viewBox="0 0 20 20"
                      >
                        <path d="M5.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75A.75.75 0 007.25 3h-1.5zM12.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75a.75.75 0 00-.75-.75h-1.5z" />
                      </svg>
                      Pause
                    </button>
                  )}
                  {isRunning && isPaused && (
                    <button
                      onClick={resumeSimulation}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold
                        bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white transition-colors"
                    >
                      <svg
                        className="w-3.5 h-3.5"
                        fill="currentColor"
                        viewBox="0 0 20 20"
                      >
                        <path d="M6.3 2.841A1.5 1.5 0 004 4.11v11.78a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z" />
                      </svg>
                      Resume
                    </button>
                  )}
                  {isRunning && (
                    <button
                      onClick={stopSimulation}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold
                        bg-red-600 hover:bg-red-500 active:bg-red-700 text-white transition-colors"
                    >
                      <svg
                        className="w-3.5 h-3.5"
                        fill="currentColor"
                        viewBox="0 0 20 20"
                      >
                        <path d="M5.25 3A2.25 2.25 0 003 5.25v9.5A2.25 2.25 0 005.25 17h9.5A2.25 2.25 0 0017 14.75v-9.5A2.25 2.25 0 0014.75 3h-9.5z" />
                      </svg>
                      Stop
                    </button>
                  )}
                  {!isRunning && isLoaded && (
                    <button
                      onClick={resetSimulation}
                      className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold transition-colors ${
                        isStopped
                          ? "bg-amber-600 hover:bg-amber-500 active:bg-amber-700 text-white"
                          : "bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-gray-300"
                      }`}
                    >
                      <svg
                        className="w-3.5 h-3.5"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182"
                        />
                      </svg>
                      Reset
                    </button>
                  )}
                </div>
              </div>
            )}

            {mobileTab === "agents" && (
              <div className="flex-1 overflow-y-auto bg-gray-900/70 border border-gray-800/60 rounded-xl backdrop-blur-sm m-1.5">
                {state && state.agents && state.agents.length > 0 ? (
                  <AgentList
                    agents={state.agents}
                    selectedAgentId={selectedAgentId}
                    onSelectAgent={setSelectedAgentId}
                  />
                ) : (
                  <div className="p-3">
                    <h2 className="text-sm font-bold tracking-wide uppercase text-gray-300 mb-3">
                      Agents
                    </h2>
                    <div className="text-center text-gray-600 py-8 text-xs">
                      No active agents
                    </div>
                  </div>
                )}
              </div>
            )}

            {mobileTab === "metrics" && (
              <div className="flex-1 overflow-y-auto bg-gray-900/70 border border-gray-800/60 rounded-xl backdrop-blur-sm m-1.5">
                <MetricsDisplay state={state} />
              </div>
            )}

            {mobileTab === "controls" && (
              <div className="flex-1 overflow-y-auto bg-gray-900/70 border border-gray-800/60 rounded-xl backdrop-blur-sm m-1.5">
                <ControlPanel
                  connected={connected}
                  isRunning={isRunning}
                  isPaused={isPaused}
                  isLoaded={isLoaded}
                  isStopped={isStopped}
                  onLoad={loadConfig}
                  onStartRun={startSimulation}
                  onPause={pauseSimulation}
                  onResume={resumeSimulation}
                  onStop={stopSimulation}
                  onReset={resetSimulation}
                  onSpeedChange={setSimulationSpeed}
                />
              </div>
            )}

            {mobileTab === "benchmark" && (
              <div className="flex-1 overflow-y-auto bg-gray-900/70 border border-gray-800/60 rounded-xl backdrop-blur-sm m-1.5">
                <BenchmarkPanel
                  runs={benchmark.runs}
                  recording={benchmark.recording}
                  onStartRecording={handleStartRecording}
                  onStopRecording={benchmark.stopRecording}
                  onCancelRecording={benchmark.cancelRecording}
                  onDeleteRun={benchmark.deleteRun}
                  onClearAll={benchmark.clearAllRuns}
                  onRenameRun={benchmark.renameRun}
                  onExportJSON={benchmark.exportRunsJSON}
                  onImportJSON={benchmark.importRunsJSON}
                  isLoaded={isLoaded}
                  isRunning={isRunning}
                />
              </div>
            )}
          </div>

          {/* ── Mobile bottom tab bar ── */}
          <nav className="flex-shrink-0 border-t border-gray-800/80 bg-gray-900/80 backdrop-blur-md flex safe-bottom">
            {[
              {
                key: "dashboard" as MobileTab,
                label: "Dashboard",
                icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0a1 1 0 01-1-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 01-1 1m-2 0h2",
              },
              {
                key: "agents" as MobileTab,
                label: "Agents",
                icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z",
              },
              {
                key: "metrics" as MobileTab,
                label: "Metrics",
                icon: "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
              },
              {
                key: "controls" as MobileTab,
                label: "Controls",
                icon: "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z",
              },
              {
                key: "benchmark" as MobileTab,
                label: "Bench",
                icon: "M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z",
              },
            ].map(({ key, label, icon }) => (
              <button
                key={key}
                onClick={() => setMobileTab(key)}
                className={`flex-1 flex flex-col items-center gap-0.5 py-2 transition-colors ${
                  mobileTab === key
                    ? "text-blue-400"
                    : "text-gray-500 active:text-gray-300"
                }`}
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d={icon} />
                </svg>
                <span className="text-[10px] font-medium">{label}</span>
              </button>
            ))}
          </nav>
        </>
      ) : (
        /* ═══ DESKTOP LAYOUT: 4 resizable panels ═══ */
        <div className="flex-1 flex flex-row overflow-hidden min-h-0 gap-0 p-1.5">
          {/* Panel 1: Agent list */}
          <div
            className="flex-shrink-0 flex flex-col overflow-hidden bg-gray-900/70 border border-gray-800/60 rounded-xl backdrop-blur-sm"
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
                <div className="p-3">
                  <h2 className="text-sm font-bold tracking-wide uppercase text-gray-300 mb-3">
                    Agents
                  </h2>
                  <div className="text-center text-gray-600 py-8 text-xs">
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
            <div className="flex-shrink-0 flex gap-0.5 mb-1 bg-gray-900/60 p-0.5 rounded-lg border border-gray-800/50">
              {(["simulation", "editor"] as ViewMode[]).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setViewMode(mode)}
                  className={`flex-1 py-1.5 px-3 rounded-md font-medium text-xs transition-all duration-200 ${
                    viewMode === mode
                      ? "bg-gray-700/80 text-white shadow-sm"
                      : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/40"
                  }`}
                >
                  {mode === "simulation" ? "Simulation" : "Map Editor"}
                </button>
              ))}
            </div>

            {/* Content */}
            {viewMode === "simulation" ? (
              <div className="flex-1 bg-gray-900/70 border border-gray-800/60 rounded-xl overflow-hidden flex items-center justify-center p-2 min-h-0 backdrop-blur-sm">
                {state && state.grid ? (
                  <GridCanvas state={state} selectedAgentId={selectedAgentId} />
                ) : (
                  <div className="flex flex-col items-center justify-center text-center gap-2">
                    <svg
                      className="h-10 w-10 text-gray-700"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={1.5}
                        d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
                      />
                    </svg>
                    <p className="text-gray-600 text-xs">
                      Load a configuration to start
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex-1 overflow-auto">
                <MapEditor
                  onExport={(scenario, agents) => {
                    loadConfig(scenario, agents).then(() =>
                      setViewMode("simulation"),
                    );
                  }}
                />
              </div>
            )}
          </div>

          <DragHandle onDrag={(dx) => setMetricsW((w) => clamp(w - dx))} />

          {/* Panel 3: Metrics / Benchmark */}
          <div
            className="flex-shrink-0 flex flex-col overflow-hidden bg-gray-900/70 border border-gray-800/60 rounded-xl backdrop-blur-sm"
            style={{ width: metricsW }}
          >
            {/* Sub-tab toggle */}
            <div className="flex-shrink-0 flex gap-0.5 m-1.5 mb-0 bg-gray-900/60 p-0.5 rounded-lg border border-gray-800/50">
              {(["metrics", "benchmark"] as MetricsPanelView[]).map((v) => (
                <button
                  key={v}
                  onClick={() => setMetricsPanelView(v)}
                  className={`flex-1 py-1 px-2 rounded-md font-medium text-[11px] transition-all duration-200 ${
                    metricsPanelView === v
                      ? "bg-gray-700/80 text-white shadow-sm"
                      : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/40"
                  }`}
                >
                  {v === "metrics" ? "Metrics" : "Benchmark"}
                </button>
              ))}
            </div>
            <div className="flex-1 overflow-y-auto">
              {metricsPanelView === "metrics" ? (
                <MetricsDisplay state={state} />
              ) : (
                <BenchmarkPanel
                  runs={benchmark.runs}
                  recording={benchmark.recording}
                  onStartRecording={handleStartRecording}
                  onStopRecording={benchmark.stopRecording}
                  onCancelRecording={benchmark.cancelRecording}
                  onDeleteRun={benchmark.deleteRun}
                  onClearAll={benchmark.clearAllRuns}
                  onRenameRun={benchmark.renameRun}
                  onExportJSON={benchmark.exportRunsJSON}
                  onImportJSON={benchmark.importRunsJSON}
                  isLoaded={isLoaded}
                  isRunning={isRunning}
                />
              )}
            </div>
          </div>

          <DragHandle onDrag={(dx) => setControlsW((w) => clamp(w - dx))} />

          {/* Panel 4: Controls */}
          <div
            className="flex-shrink-0 flex flex-col overflow-hidden bg-gray-900/70 border border-gray-800/60 rounded-xl backdrop-blur-sm"
            style={{ width: controlsW }}
          >
            <div className="flex-1 overflow-y-auto">
              <ControlPanel
                connected={connected}
                isRunning={isRunning}
                isPaused={isPaused}
                isLoaded={isLoaded}
                isStopped={isStopped}
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
      )}
    </div>
  );
}

export default App;
