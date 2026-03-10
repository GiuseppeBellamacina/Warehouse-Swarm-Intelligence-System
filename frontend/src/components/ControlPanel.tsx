// Control Panel Component

import React, { useState, useRef, useEffect } from "react";
import {
  GridScenarioConfig,
  SimulationAgentsConfig,
  AgentRoleParams,
  ScoutBehaviorParams,
  CoordinatorBehaviorParams,
  RetrieverBehaviorParams,
  fetchAgentDefaults,
} from "../types/simulation";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8000";

interface ControlPanelProps {
  connected: boolean;
  isRunning: boolean;
  isPaused: boolean;
  isLoaded: boolean;
  isStopped: boolean;
  onLoad: (
    scenario: GridScenarioConfig,
    agents: SimulationAgentsConfig,
  ) => void;
  onStartRun: () => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onReset: () => void;
  onSpeedChange: (speed: number) => void;
}

interface AgentOverrideFields {
  count: number;
  vision_radius: number;
  communication_radius: number;
  max_energy: number;
  speed: number;
  carrying_capacity: number;
}

interface Overrides {
  simulationSeed: number | null;
  simulationMaxSteps: number;
  mapKnown: boolean;
  scouts: AgentOverrideFields;
  coordinators: AgentOverrideFields;
  retrievers: AgentOverrideFields;
  scoutBehavior: ScoutBehaviorParams;
  coordinatorBehavior: CoordinatorBehaviorParams;
  retrieverBehavior: RetrieverBehaviorParams;
}

function extractOverrides(
  config: GridScenarioConfig,
  def: SimulationAgentsConfig,
): Overrides {
  return {
    simulationSeed: config.metadata?.seed ?? 42,
    simulationMaxSteps: config.metadata?.max_steps ?? 500,
    mapKnown: def.map_known ?? false,
    scouts: {
      count: def.scouts.count,
      vision_radius: def.scouts.vision_radius,
      communication_radius: def.scouts.communication_radius,
      max_energy: def.scouts.max_energy,
      speed: def.scouts.speed,
      carrying_capacity: def.scouts.carrying_capacity,
    },
    coordinators: {
      count: def.coordinators.count,
      vision_radius: def.coordinators.vision_radius,
      communication_radius: def.coordinators.communication_radius,
      max_energy: def.coordinators.max_energy,
      speed: def.coordinators.speed,
      carrying_capacity: def.coordinators.carrying_capacity,
    },
    retrievers: {
      count: def.retrievers.count,
      vision_radius: def.retrievers.vision_radius,
      communication_radius: def.retrievers.communication_radius,
      max_energy: def.retrievers.max_energy,
      speed: def.retrievers.speed,
      carrying_capacity: def.retrievers.carrying_capacity,
    },
    scoutBehavior: { ...def.scout_behavior },
    coordinatorBehavior: { ...def.coordinator_behavior },
    retrieverBehavior: { ...def.retriever_behavior },
  };
}

// ── Tooltip wrapper ────────────────────────────────────────

const Tip: React.FC<{ text: string; children: React.ReactNode }> = ({
  text,
  children,
}) => (
  <div className="group/tip relative">
    {children}
    <div className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2.5 py-1.5 rounded-md bg-gray-950 border border-gray-600 text-[10px] text-gray-200 leading-snug whitespace-normal w-52 opacity-0 group-hover/tip:opacity-100 transition-opacity duration-150 z-50 shadow-lg">
      {text}
    </div>
  </div>
);

// ── Shared small helpers ───────────────────────────────────

const Field: React.FC<{
  label: string;
  tip?: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
}> = ({
  label,
  tip,
  value,
  onChange,
  min = 0,
  max = 9999,
  step = 1,
  disabled,
}) => {
  const inner = (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <span className="text-gray-400 text-[11px] leading-tight flex-1 truncate">
        {label}
      </span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-[4.5rem] bg-gray-800/80 border border-gray-600/60 rounded-md px-2 py-1 text-xs text-right
                   focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 focus:outline-none
                   disabled:opacity-40 transition-colors"
      />
    </div>
  );
  return tip ? <Tip text={tip}>{inner}</Tip> : inner;
};

const Slider: React.FC<{
  label: string;
  tip?: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step?: number;
  disabled?: boolean;
  unit?: string;
}> = ({
  label,
  tip,
  value,
  onChange,
  min,
  max,
  step = 1,
  disabled,
  unit = "",
}) => {
  const inner = (
    <div className="py-0.5">
      <div className="flex items-center justify-between gap-2 mb-0.5">
        <span className="text-gray-400 text-[11px] leading-tight truncate">
          {label}
        </span>
        <span className="text-[11px] text-gray-300 font-mono tabular-nums min-w-[3rem] text-right">
          {step < 1 ? value.toFixed(2) : value}
          {unit}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-1.5 bg-gray-700 rounded-full appearance-none cursor-pointer disabled:opacity-40
                   [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
                   [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-400 [&::-webkit-slider-thumb]:cursor-pointer
                   [&::-webkit-slider-thumb]:shadow-[0_0_4px_rgba(96,165,250,0.4)]
                   [&::-moz-range-thumb]:w-3 [&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:rounded-full
                   [&::-moz-range-thumb]:bg-blue-400 [&::-moz-range-thumb]:border-0"
      />
    </div>
  );
  return tip ? <Tip text={tip}>{inner}</Tip> : inner;
};

const Toggle: React.FC<{
  label: string;
  tip?: string;
  value: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}> = ({ label, tip, value, onChange, disabled }) => {
  const inner = (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <span className="text-gray-400 text-[11px] leading-tight flex-1 truncate">
        {label}
      </span>
      <button
        onClick={() => onChange(!value)}
        disabled={disabled}
        className={`w-9 h-[18px] rounded-full transition-colors relative flex-shrink-0 ${
          value
            ? "bg-blue-500/80 shadow-[0_0_6px_rgba(96,165,250,0.3)]"
            : "bg-gray-600"
        } disabled:opacity-40`}
      >
        <span
          className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white transition-all duration-150 shadow-sm ${
            value ? "left-[18px]" : "left-[2px]"
          }`}
        />
      </button>
    </div>
  );
  return tip ? <Tip text={tip}>{inner}</Tip> : inner;
};

const Badge: React.FC<{ color: string; children: React.ReactNode }> = ({
  color,
  children,
}) => (
  <span
    className={`inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider ${color}`}
  >
    {children}
  </span>
);

const SectionHeader: React.FC<{
  color: string;
  dot: string;
  label: string;
  open: boolean;
  onToggle: () => void;
}> = ({ color, dot, label, open, onToggle }) => (
  <button
    onClick={onToggle}
    className="w-full flex items-center justify-between py-1.5 group/sec"
  >
    <Badge color={color}>
      <span
        className="inline-block w-2.5 h-2.5 rounded-full"
        style={{ backgroundColor: dot }}
      />
      {label}
    </Badge>
    <span className="text-gray-500 text-[10px] group-hover/sec:text-gray-300 transition-colors">
      {open ? "▲" : "▼"}
    </span>
  </button>
);

const Card: React.FC<{
  children: React.ReactNode;
  className?: string;
}> = ({ children, className = "" }) => (
  <div
    className={`bg-gray-800/50 border border-gray-700/50 rounded-lg backdrop-blur-sm ${className}`}
  >
    {children}
  </div>
);

// ── Main Component ─────────────────────────────────────────

export const ControlPanel: React.FC<ControlPanelProps> = ({
  connected,
  isRunning,
  isPaused,
  isLoaded,
  isStopped,
  onLoad,
  onStartRun,
  onPause,
  onResume,
  onStop,
  onReset,
  onSpeedChange,
}) => {
  const [configName, setConfigName] = useState<string>("");
  const [availableConfigs, setAvailableConfigs] = useState<string[]>([]);
  const [defaults, setDefaults] = useState<SimulationAgentsConfig | null>(null);
  const defaultsRef = useRef<SimulationAgentsConfig | null>(null);
  const [rawConfig, setRawConfig] = useState<GridScenarioConfig | null>(null);
  const [overrides, setOverrides] = useState<Overrides | null>(null);
  const [overridesOpen, setOverridesOpen] = useState(false);
  const [behaviorOpen, setBehaviorOpen] = useState(false);
  const [isFetching, setIsFetching] = useState(false);
  const [speed, setSpeed] = useState(1.0);
  const [dirty, setDirty] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Sub-section toggle states for behavior tuning
  const [scoutBehOpen, setScoutBehOpen] = useState(true);
  const [coordBehOpen, setCoordBehOpen] = useState(false);
  const [retrBehOpen, setRetrBehOpen] = useState(false);
  // Sub-section toggle states for agent overrides
  const [scoutOvOpen, setScoutOvOpen] = useState(true);
  const [coordOvOpen, setCoordOvOpen] = useState(false);
  const [retrOvOpen, setRetrOvOpen] = useState(false);

  // Load defaults and available configs from backend on mount
  useEffect(() => {
    const init = async () => {
      try {
        const [defRes, cfgRes] = await Promise.all([
          fetchAgentDefaults(BACKEND_URL),
          fetch(`${BACKEND_URL}/api/configs`)
            .then((r) => (r.ok ? r.json() : { configs: [] }))
            .catch(() => ({ configs: [] })),
        ]);
        setDefaults(defRes);
        defaultsRef.current = defRes;
        const cfgs: string[] = cfgRes.configs ?? [];
        setAvailableConfigs(cfgs);
        if (cfgs.length > 0) setConfigName(cfgs[0]);
      } catch {
        /* backend not ready yet — retry handled by config fetch below */
      }
    };
    init();
  }, []);

  // Auto-fetch config JSON whenever the dropdown selection changes
  useEffect(() => {
    if (!configName || !defaultsRef.current) return;
    const load = async () => {
      setIsFetching(true);
      try {
        const res = await fetch(`${BACKEND_URL}/configs/${configName}.json`);
        if (!res.ok) return;
        const cfg: GridScenarioConfig = await res.json();
        setRawConfig(cfg);
        setOverrides(extractOverrides(cfg, defaultsRef.current!));
        setDirty(false);
      } catch {
        /* ignore */
      } finally {
        setIsFetching(false);
      }
    };
    load();
  }, [configName, defaults]);

  // Read an uploaded JSON file locally (no server round-trip)
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !defaultsRef.current) return;
    try {
      const text = await file.text();
      const cfg: GridScenarioConfig = JSON.parse(text);
      setRawConfig(cfg);
      setOverrides(extractOverrides(cfg, defaultsRef.current));
      setDirty(false);
      setConfigName(""); // deselect dropdown
    } catch {
      alert("Invalid JSON file");
    }
  };

  // Helpers to patch individual override slices (mark dirty on every change)
  const setOvr = (patch: Partial<Overrides>) => {
    setOverrides((p) => (p ? { ...p, ...patch } : p));
    setDirty(true);
  };
  const setScouts = (patch: Partial<AgentOverrideFields>) => {
    setOverrides((p) => (p ? { ...p, scouts: { ...p.scouts, ...patch } } : p));
    setDirty(true);
  };
  const setCoords = (patch: Partial<AgentOverrideFields>) => {
    setOverrides((p) =>
      p ? { ...p, coordinators: { ...p.coordinators, ...patch } } : p,
    );
    setDirty(true);
  };
  const setRetrs = (patch: Partial<AgentOverrideFields>) => {
    setOverrides((p) =>
      p ? { ...p, retrievers: { ...p.retrievers, ...patch } } : p,
    );
    setDirty(true);
  };
  const setScoutBeh = (patch: Partial<ScoutBehaviorParams>) => {
    setOverrides((p) =>
      p ? { ...p, scoutBehavior: { ...p.scoutBehavior, ...patch } } : p,
    );
    setDirty(true);
  };
  const setCoordBeh = (patch: Partial<CoordinatorBehaviorParams>) => {
    setOverrides((p) =>
      p
        ? { ...p, coordinatorBehavior: { ...p.coordinatorBehavior, ...patch } }
        : p,
    );
    setDirty(true);
  };
  const setRetrBeh = (patch: Partial<RetrieverBehaviorParams>) => {
    setOverrides((p) =>
      p ? { ...p, retrieverBehavior: { ...p.retrieverBehavior, ...patch } } : p,
    );
    setDirty(true);
  };

  const handleLoad = () => {
    if (!rawConfig || !overrides) return;
    const scenario: GridScenarioConfig = {
      ...rawConfig,
      metadata: {
        ...rawConfig.metadata,
        max_steps: overrides.simulationMaxSteps,
        seed: overrides.simulationSeed ?? undefined,
      },
    };
    const agents: SimulationAgentsConfig = {
      scouts: ovToRole(overrides.scouts),
      coordinators: ovToRole(overrides.coordinators),
      retrievers: ovToRole(overrides.retrievers),
      scout_behavior: overrides.scoutBehavior,
      coordinator_behavior: overrides.coordinatorBehavior,
      retriever_behavior: overrides.retrieverBehavior,
      map_known: overrides.mapKnown,
    };
    onLoad(scenario, agents);
    setDirty(false);
  };

  function ovToRole(f: AgentOverrideFields): AgentRoleParams {
    return {
      count: f.count,
      vision_radius: f.vision_radius,
      communication_radius: f.communication_radius,
      max_energy: f.max_energy,
      speed: f.speed,
      carrying_capacity: f.carrying_capacity,
    };
  }

  const handleSpeedChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const sv = parseFloat(e.target.value);
    const actual = Math.round(Math.pow(10, sv) * 10) / 10;
    setSpeed(actual);
    onSpeedChange(actual);
  };

  const canLoad =
    !!rawConfig && !!defaults && !isRunning && connected && !isFetching;
  const canStart = isLoaded && !isRunning && !isStopped && connected;

  return (
    <div className="p-4 space-y-4 text-sm select-none">
      {/* ── Status bar ── */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold tracking-wide uppercase text-gray-300">
          Control Panel
        </h2>
        {(isRunning || isLoaded) && (
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
              isRunning
                ? isPaused
                  ? "bg-yellow-900/60 text-yellow-300 border border-yellow-700/40"
                  : "bg-green-900/60 text-green-300 border border-green-700/40"
                : "bg-blue-900/60 text-blue-300 border border-blue-700/40"
            }`}
          >
            {isRunning ? (isPaused ? "⏸ PAUSED" : "▶ RUNNING") : "● LOADED"}
          </span>
        )}
      </div>

      {/* ── Load Configuration ── */}
      <Card className="p-3 space-y-2.5">
        <h3 className="font-semibold text-xs uppercase tracking-wide text-gray-400 mb-1">
          Configuration
        </h3>

        {/* Dropdown */}
        <select
          value={configName}
          onChange={(e) => setConfigName(e.target.value)}
          disabled={isRunning}
          className="w-full bg-gray-800 border border-gray-600/60 rounded-md px-2.5 py-2 text-xs
                     focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 focus:outline-none
                     disabled:opacity-40 transition-colors"
        >
          {availableConfigs.length > 0 ? (
            availableConfigs.map((c) => (
              <option key={c} value={c}>
                {c.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase())}
              </option>
            ))
          ) : (
            <option value="">Loading…</option>
          )}
        </select>

        {/* File picker */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".json"
          onChange={handleFileChange}
          className="hidden"
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={isRunning}
          className="w-full bg-gray-700/50 hover:bg-gray-600/50 disabled:opacity-40 disabled:cursor-not-allowed
                     px-3 py-1.5 rounded-md border border-gray-600/40 border-dashed text-xs text-gray-400
                     hover:text-gray-300 hover:border-gray-500/60 transition-all"
        >
          📂 Or pick a JSON file…
        </button>

        {/* Grid info bar */}
        {rawConfig && (
          <div className="flex gap-2 text-[10px] text-gray-500">
            <span>
              Grid {rawConfig.metadata?.grid_size ?? "?"}×
              {rawConfig.metadata?.grid_size ?? "?"}
            </span>
            <span>•</span>
            <span>{rawConfig.objects?.length ?? 0} objects</span>
            <span>•</span>
            <span>{rawConfig.warehouses?.length ?? 0} warehouses</span>
          </div>
        )}
      </Card>

      {/* ── Agent Overrides ── */}
      {overrides && (
        <Card>
          <button
            onClick={() => setOverridesOpen((v) => !v)}
            className="w-full flex justify-between items-center px-3 py-2.5 text-xs font-medium text-gray-300
                       hover:bg-gray-700/30 rounded-lg transition-colors"
          >
            <span className="flex items-center gap-1.5">
              <span className="text-gray-500">⚙</span>
              Agent Parameters
            </span>
            <span className="text-gray-500 text-[10px]">
              {overridesOpen ? "▲" : "▼"}
            </span>
          </button>

          {overridesOpen && (
            <div className="px-3 pb-3 space-y-1 border-t border-gray-700/40">
              {/* Seed & Max steps */}
              <div className="pt-2 space-y-0.5">
                <Field
                  label="Seed"
                  tip="Random seed for reproducibility — same seed = identical simulation"
                  value={overrides.simulationSeed ?? 0}
                  onChange={(v) => setOvr({ simulationSeed: v })}
                  min={0}
                  max={999999999}
                  disabled={isRunning}
                />
                <Field
                  label="Max steps"
                  tip="Maximum number of simulation steps before auto-stop"
                  value={overrides.simulationMaxSteps}
                  onChange={(v) => setOvr({ simulationMaxSteps: v })}
                  min={1}
                  disabled={isRunning}
                />
                <Toggle
                  label="Map known"
                  tip="All agents start with full terrain & warehouse knowledge. Only object locations remain hidden."
                  value={overrides.mapKnown}
                  onChange={(v) => setOvr({ mapKnown: v })}
                  disabled={isRunning}
                />
              </div>

              {/* Scouts */}
              <div className="pt-1">
                <SectionHeader
                  color="text-green-400"
                  dot="#4ade80"
                  label="Scouts"
                  open={scoutOvOpen}
                  onToggle={() => setScoutOvOpen((v) => !v)}
                />
                {scoutOvOpen && (
                  <div className="space-y-0.5 pl-1 border-l-2 border-green-800/30 ml-1">
                    <Slider
                      label="Count"
                      tip="Number of scout agents"
                      value={overrides.scouts.count}
                      onChange={(v) => setScouts({ count: v })}
                      min={0}
                      max={10}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Vision radius"
                      tip="How many cells ahead the scout can see"
                      value={overrides.scouts.vision_radius}
                      onChange={(v) => setScouts({ vision_radius: v })}
                      min={1}
                      max={10}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Comm. radius"
                      tip="Range for message exchange with other agents"
                      value={overrides.scouts.communication_radius}
                      onChange={(v) => setScouts({ communication_radius: v })}
                      min={1}
                      max={10}
                      disabled={isRunning}
                    />
                    <Field
                      label="Max energy"
                      tip="Total energy pool — drained by movement"
                      value={overrides.scouts.max_energy}
                      onChange={(v) => setScouts({ max_energy: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Speed"
                      tip="Movement multiplier: >1 means multiple cells per step"
                      value={overrides.scouts.speed}
                      onChange={(v) => setScouts({ speed: v })}
                      min={1}
                      max={3}
                      disabled={isRunning}
                    />
                  </div>
                )}
              </div>

              {/* Coordinators */}
              <div className="pt-1">
                <SectionHeader
                  color="text-blue-400"
                  dot="#60a5fa"
                  label="Coordinators"
                  open={coordOvOpen}
                  onToggle={() => setCoordOvOpen((v) => !v)}
                />
                {coordOvOpen && (
                  <div className="space-y-0.5 pl-1 border-l-2 border-blue-800/30 ml-1">
                    <Slider
                      label="Count"
                      tip="Number of coordinator agents"
                      value={overrides.coordinators.count}
                      onChange={(v) => setCoords({ count: v })}
                      min={0}
                      max={10}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Vision radius"
                      value={overrides.coordinators.vision_radius}
                      onChange={(v) => setCoords({ vision_radius: v })}
                      min={1}
                      max={10}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Comm. radius"
                      value={overrides.coordinators.communication_radius}
                      onChange={(v) => setCoords({ communication_radius: v })}
                      min={1}
                      max={10}
                      disabled={isRunning}
                    />
                    <Field
                      label="Max energy"
                      value={overrides.coordinators.max_energy}
                      onChange={(v) => setCoords({ max_energy: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Speed"
                      value={overrides.coordinators.speed}
                      onChange={(v) => setCoords({ speed: v })}
                      min={1}
                      max={3}
                      disabled={isRunning}
                    />
                  </div>
                )}
              </div>

              {/* Retrievers */}
              <div className="pt-1">
                <SectionHeader
                  color="text-orange-400"
                  dot="#fb923c"
                  label="Retrievers"
                  open={retrOvOpen}
                  onToggle={() => setRetrOvOpen((v) => !v)}
                />
                {retrOvOpen && (
                  <div className="space-y-0.5 pl-1 border-l-2 border-orange-800/30 ml-1">
                    <Slider
                      label="Count"
                      tip="Number of retriever agents"
                      value={overrides.retrievers.count}
                      onChange={(v) => setRetrs({ count: v })}
                      min={0}
                      max={10}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Vision radius"
                      value={overrides.retrievers.vision_radius}
                      onChange={(v) => setRetrs({ vision_radius: v })}
                      min={1}
                      max={10}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Comm. radius"
                      value={overrides.retrievers.communication_radius}
                      onChange={(v) => setRetrs({ communication_radius: v })}
                      min={1}
                      max={10}
                      disabled={isRunning}
                    />
                    <Field
                      label="Max energy"
                      value={overrides.retrievers.max_energy}
                      onChange={(v) => setRetrs({ max_energy: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Speed"
                      value={overrides.retrievers.speed}
                      onChange={(v) => setRetrs({ speed: v })}
                      min={1}
                      max={3}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Carry capacity"
                      tip="Max objects a retriever can carry at once"
                      value={overrides.retrievers.carrying_capacity}
                      onChange={(v) => setRetrs({ carrying_capacity: v })}
                      min={1}
                      max={10}
                      disabled={isRunning}
                    />
                  </div>
                )}
              </div>

              {/* Overflow warning */}
              {(() => {
                const totalAgents =
                  overrides.scouts.count +
                  overrides.coordinators.count +
                  overrides.retrievers.count;
                const totalObjects = rawConfig?.objects?.length ?? 0;
                const total = totalAgents + totalObjects;
                const gs = rawConfig?.metadata?.grid_size ?? 0;
                const capacity = gs > 0 ? Math.floor(gs * gs * 0.25) : 50;
                if (total <= capacity) return null;
                const pct = Math.round((total / capacity) * 100);
                return (
                  <div className="mt-2 flex items-start gap-1.5 bg-yellow-900/30 border border-yellow-700/40 rounded-md px-2 py-1.5">
                    <span className="text-yellow-400 text-sm leading-none mt-0.5">
                      ⚠
                    </span>
                    <div className="text-[10px] text-yellow-300/80 leading-snug">
                      <span className="font-semibold">
                        {totalAgents} agents + {totalObjects} objects
                      </span>{" "}
                      = {total} cells ({pct}% of ~{capacity} walkable)
                    </div>
                  </div>
                );
              })()}
            </div>
          )}
        </Card>
      )}

      {/* ── Behavior Tuning ── */}
      {overrides && (
        <Card>
          <button
            onClick={() => setBehaviorOpen((v) => !v)}
            className="w-full flex justify-between items-center px-3 py-2.5 text-xs font-medium text-gray-300
                       hover:bg-gray-700/30 rounded-lg transition-colors"
          >
            <span className="flex items-center gap-1.5">
              <span className="text-gray-500">🧠</span>
              Behavior Tuning
            </span>
            <span className="text-gray-500 text-[10px]">
              {behaviorOpen ? "▲" : "▼"}
            </span>
          </button>

          {behaviorOpen && (
            <div className="px-3 pb-3 space-y-1 border-t border-gray-700/40">
              {/* ── Scout behavior ── */}
              <div className="pt-2">
                <SectionHeader
                  color="text-green-400"
                  dot="#4ade80"
                  label="Scout Behavior"
                  open={scoutBehOpen}
                  onToggle={() => setScoutBehOpen((v) => !v)}
                />
                {scoutBehOpen && (
                  <div className="space-y-0.5 pl-1 border-l-2 border-green-800/30 ml-1">
                    {/* Exploration */}
                    <p className="text-[9px] uppercase tracking-widest text-gray-600 pt-1">
                      Exploration
                    </p>
                    <Slider
                      label="Min frontier cluster"
                      tip="Minimum unexplored-cell cluster size. Small clusters (1-2 cells) are skipped in favour of larger unexplored zones"
                      value={overrides.scoutBehavior.min_frontier_cluster_size}
                      onChange={(v) =>
                        setScoutBeh({ min_frontier_cluster_size: v })
                      }
                      min={1}
                      max={20}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Anti-cluster distance"
                      tip="Min Manhattan distance from other scouts when choosing frontiers — prevents scouts from clustering together"
                      value={overrides.scoutBehavior.anti_cluster_distance}
                      onChange={(v) =>
                        setScoutBeh({ anti_cluster_distance: v })
                      }
                      min={0}
                      max={20}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Target hysteresis"
                      tip="Min distance before switching to a new frontier — prevents jittering between nearby candidates"
                      value={overrides.scoutBehavior.target_hysteresis}
                      onChange={(v) => setScoutBeh({ target_hysteresis: v })}
                      min={0}
                      max={30}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Target lock duration"
                      tip="Steps a committed target stays locked — prevents erratic direction changes when entering new zones"
                      value={overrides.scoutBehavior.target_lock_duration}
                      onChange={(v) => setScoutBeh({ target_lock_duration: v })}
                      min={1}
                      max={30}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Recent target TTL"
                      tip="Steps a reached frontier is blacklisted — prevents oscillation back to just-explored areas"
                      value={overrides.scoutBehavior.recent_target_ttl}
                      onChange={(v) => setScoutBeh({ recent_target_ttl: v })}
                      min={1}
                      max={200}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Rescan age"
                      tip="Steps without vision before a cell becomes re-eligible for stale-coverage patrol"
                      value={overrides.scoutBehavior.rescan_age}
                      onChange={(v) => setScoutBeh({ rescan_age: v })}
                      min={10}
                      max={500}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Far frontier"
                      tip="Prefer distant frontiers over nearby ones — pushes scouts into genuinely new territory"
                      value={overrides.scoutBehavior.far_frontier_enabled}
                      onChange={(v) => setScoutBeh({ far_frontier_enabled: v })}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Stale patrol"
                      tip="Re-explore cells not seen for rescan_age steps — continuous map cycling"
                      value={overrides.scoutBehavior.stale_coverage_patrol}
                      onChange={(v) =>
                        setScoutBeh({ stale_coverage_patrol: v })
                      }
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Anti-clustering"
                      tip="Prefer frontiers far from other scouts — better map coverage distribution"
                      value={overrides.scoutBehavior.anti_clustering}
                      onChange={(v) => setScoutBeh({ anti_clustering: v })}
                      disabled={isRunning}
                    />

                    {/* Communication */}
                    <p className="text-[9px] uppercase tracking-widest text-gray-600 pt-2">
                      Communication
                    </p>
                    <Toggle
                      label="Seek coordinator"
                      tip="Actively head toward the coordinator to deliver discoveries — when OFF, relies on passive relay only"
                      value={overrides.scoutBehavior.seek_coordinator}
                      onChange={(v) => setScoutBeh({ seek_coordinator: v })}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Seek coord. delay"
                      tip="Steps without ANY agent contact before actively seeking the coordinator — info relays passively via any nearby agent until then"
                      value={overrides.scoutBehavior.seek_coordinator_delay}
                      onChange={(v) =>
                        setScoutBeh({ seek_coordinator_delay: v })
                      }
                      min={0}
                      max={100}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Discovery timeout"
                      tip="Steps of total isolation before discarding undelivered discoveries"
                      value={overrides.scoutBehavior.discovery_timeout}
                      onChange={(v) => setScoutBeh({ discovery_timeout: v })}
                      min={10}
                      max={200}
                      disabled={isRunning}
                    />

                    {/* Movement */}
                    <p className="text-[9px] uppercase tracking-widest text-gray-600 pt-2">
                      Movement
                    </p>
                    <Slider
                      label="Stuck threshold"
                      tip="Consecutive move failures before abandoning the current target"
                      value={overrides.scoutBehavior.stuck_threshold}
                      onChange={(v) => setScoutBeh({ stuck_threshold: v })}
                      min={1}
                      max={20}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Recharge threshold"
                      tip="Energy fraction that triggers warehouse recharge (e.g. 0.25 = recharge at 25%)"
                      value={overrides.scoutBehavior.recharge_threshold}
                      onChange={(v) => setScoutBeh({ recharge_threshold: v })}
                      min={0.05}
                      max={0.5}
                      step={0.05}
                      disabled={isRunning}
                    />
                  </div>
                )}
              </div>

              {/* ── Coordinator behavior ── */}
              <div className="pt-1">
                <SectionHeader
                  color="text-blue-400"
                  dot="#60a5fa"
                  label="Coordinator Behavior"
                  open={coordBehOpen}
                  onToggle={() => setCoordBehOpen((v) => !v)}
                />
                {coordBehOpen && (
                  <div className="space-y-0.5 pl-1 border-l-2 border-blue-800/30 ml-1">
                    <Slider
                      label="Boredom threshold"
                      tip="Idle steps before the coordinator starts exploring on its own"
                      value={overrides.coordinatorBehavior.boredom_threshold}
                      onChange={(v) => setCoordBeh({ boredom_threshold: v })}
                      min={5}
                      max={100}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Position max age"
                      tip="Steps before a remembered retriever position is considered stale"
                      value={overrides.coordinatorBehavior.pos_max_age}
                      onChange={(v) => setCoordBeh({ pos_max_age: v })}
                      min={5}
                      max={100}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Recharge threshold"
                      value={overrides.coordinatorBehavior.recharge_threshold}
                      onChange={(v) => setCoordBeh({ recharge_threshold: v })}
                      min={0.05}
                      max={0.5}
                      step={0.05}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Object centroid bias"
                      tip="How much the coordinator's target position is pulled toward known objects vs center of retrievers"
                      value={overrides.coordinatorBehavior.centroid_object_bias}
                      onChange={(v) => setCoordBeh({ centroid_object_bias: v })}
                      min={0}
                      max={1}
                      step={0.1}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Sync rate limit"
                      tip="Minimum steps between coordinator-to-coordinator sync broadcasts"
                      value={overrides.coordinatorBehavior.sync_rate_limit}
                      onChange={(v) => setCoordBeh({ sync_rate_limit: v })}
                      min={1}
                      max={50}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Seek retrievers"
                      tip="Actively move toward retrievers to deliver task assignments"
                      value={overrides.coordinatorBehavior.seek_retrievers}
                      onChange={(v) => setCoordBeh({ seek_retrievers: v })}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Boredom patrol"
                      tip="Start exploring when idle for too long"
                      value={overrides.coordinatorBehavior.boredom_patrol}
                      onChange={(v) => setCoordBeh({ boredom_patrol: v })}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Object-biased centroid"
                      tip="Pull positioning centroid toward known objects"
                      value={
                        overrides.coordinatorBehavior.object_biased_centroid
                      }
                      onChange={(v) =>
                        setCoordBeh({ object_biased_centroid: v })
                      }
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Smart explore"
                      tip="Head toward unknown map boundaries (vs random walk)"
                      value={overrides.coordinatorBehavior.smart_explore}
                      onChange={(v) => setCoordBeh({ smart_explore: v })}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Explore retarget"
                      tip="Steps between picking new exploration targets"
                      value={
                        overrides.coordinatorBehavior.explore_retarget_interval
                      }
                      onChange={(v) =>
                        setCoordBeh({ explore_retarget_interval: v })
                      }
                      min={1}
                      max={50}
                      disabled={isRunning}
                    />
                  </div>
                )}
              </div>

              {/* ── Retriever behavior ── */}
              <div className="pt-1">
                <SectionHeader
                  color="text-orange-400"
                  dot="#fb923c"
                  label="Retriever Behavior"
                  open={retrBehOpen}
                  onToggle={() => setRetrBehOpen((v) => !v)}
                />
                {retrBehOpen && (
                  <div className="space-y-0.5 pl-1 border-l-2 border-orange-800/30 ml-1">
                    <Slider
                      label="Recharge threshold"
                      value={overrides.retrieverBehavior.recharge_threshold}
                      onChange={(v) => setRetrBeh({ recharge_threshold: v })}
                      min={0.05}
                      max={0.5}
                      step={0.05}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Stale claim age"
                      tip="Steps before an uncompleted task claim is considered stale and can be reassigned"
                      value={overrides.retrieverBehavior.stale_claim_age}
                      onChange={(v) => setRetrBeh({ stale_claim_age: v })}
                      min={10}
                      max={200}
                      disabled={isRunning}
                    />
                    <Slider
                      label="Explore retarget"
                      tip="Steps between picking a new random exploration target while idle"
                      value={
                        overrides.retrieverBehavior.explore_retarget_interval
                      }
                      onChange={(v) =>
                        setRetrBeh({ explore_retarget_interval: v })
                      }
                      min={1}
                      max={50}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Opportunistic pickup"
                      tip="Pick up objects seen en-route, even if not assigned"
                      value={overrides.retrieverBehavior.opportunistic_pickup}
                      onChange={(v) => setRetrBeh({ opportunistic_pickup: v })}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Queue reorder"
                      tip="Reorder task queue by distance to minimize travel"
                      value={overrides.retrieverBehavior.task_queue_reorder}
                      onChange={(v) => setRetrBeh({ task_queue_reorder: v })}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Self-assign (shared map)"
                      tip="Assign tasks to self from objects discovered via shared map data"
                      value={
                        overrides.retrieverBehavior.self_assign_from_shared_map
                      }
                      onChange={(v) =>
                        setRetrBeh({ self_assign_from_shared_map: v })
                      }
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Peer broadcast"
                      tip="Share object info peer-to-peer with other retrievers"
                      value={overrides.retrieverBehavior.peer_broadcast}
                      onChange={(v) => setRetrBeh({ peer_broadcast: v })}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Smart explore"
                      tip="Use frontier-based exploration when idle instead of random walk"
                      value={overrides.retrieverBehavior.smart_explore}
                      onChange={(v) => setRetrBeh({ smart_explore: v })}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="WH congestion reroute"
                      tip="Reroute to a less crowded warehouse when the target entrance is congested"
                      value={
                        overrides.retrieverBehavior.warehouse_congestion_reroute
                      }
                      onChange={(v) =>
                        setRetrBeh({ warehouse_congestion_reroute: v })
                      }
                      disabled={isRunning}
                    />
                    <Slider
                      label="WH congestion threshold"
                      tip="How many agents heading to a warehouse before it is considered congested"
                      value={
                        overrides.retrieverBehavior
                          .warehouse_congestion_threshold
                      }
                      onChange={(v) =>
                        setRetrBeh({ warehouse_congestion_threshold: v })
                      }
                      min={1}
                      max={10}
                      disabled={isRunning}
                    />
                    <Toggle
                      label="Jam priority"
                      tip="In traffic jams, retrievers carrying more objects get movement priority"
                      value={overrides.retrieverBehavior.jam_priority}
                      onChange={(v) => setRetrBeh({ jam_priority: v })}
                      disabled={isRunning}
                    />
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* ── Load button ── */}
      {rawConfig && (
        <div className="space-y-1.5">
          <button
            onClick={handleLoad}
            disabled={!canLoad}
            className={`w-full py-2.5 rounded-lg font-semibold text-sm transition-all shadow-md ${
              dirty && canLoad
                ? "bg-gradient-to-r from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600 shadow-amber-900/30 hover:shadow-amber-800/40 animate-pulse"
                : "bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-500 hover:to-blue-600 shadow-blue-900/30 hover:shadow-blue-800/40"
            } disabled:from-gray-700 disabled:to-gray-700 disabled:cursor-not-allowed`}
          >
            {isFetching ? "Fetching…" : dirty ? "⚠ Apply Changes" : "Preview / Load"}
          </button>
          {dirty && (
            <div className="flex items-center gap-1.5 bg-amber-900/30 border border-amber-700/40 rounded-md px-2 py-1.5">
              <span className="text-amber-400 text-sm leading-none">⚠</span>
              <span className="text-[10px] text-amber-300/80 leading-snug">
                Settings changed — press <strong>Apply Changes</strong> to update the simulation
              </span>
            </div>
          )}
        </div>
      )}

      {/* ── Simulation Controls ── */}
      <Card className="p-3 space-y-2.5">
        <h3 className="font-semibold text-xs uppercase tracking-wide text-gray-400">
          Simulation
        </h3>
        <div className="grid grid-cols-2 gap-1.5">
          <button
            onClick={onStartRun}
            disabled={!canStart}
            className="bg-green-600/80 hover:bg-green-500/80 disabled:bg-gray-700 disabled:cursor-not-allowed
                       py-2 rounded-md font-medium transition-colors text-xs"
          >
            ▶ Start
          </button>

          {isPaused ? (
            <button
              onClick={onResume}
              disabled={!connected}
              className="bg-green-600/80 hover:bg-green-500/80 disabled:bg-gray-700 disabled:cursor-not-allowed
                         py-2 rounded-md font-medium transition-colors text-xs"
            >
              ▶ Resume
            </button>
          ) : (
            <button
              onClick={onPause}
              disabled={!isRunning || !connected}
              className="bg-yellow-600/80 hover:bg-yellow-500/80 disabled:bg-gray-700 disabled:cursor-not-allowed
                         py-2 rounded-md font-medium transition-colors text-xs"
            >
              ⏸ Pause
            </button>
          )}

          <button
            onClick={onStop}
            disabled={!isRunning || !connected}
            className="bg-red-600/80 hover:bg-red-500/80 disabled:bg-gray-700 disabled:cursor-not-allowed
                       py-2 rounded-md font-medium transition-colors text-xs"
          >
            ■ Stop
          </button>

          <button
            onClick={onReset}
            disabled={!isLoaded || !connected}
            className={`py-2 rounded-md font-medium transition-colors text-xs disabled:bg-gray-700 disabled:cursor-not-allowed ${
              isStopped
                ? "bg-amber-600/80 hover:bg-amber-500/80 text-white"
                : "bg-gray-600/80 hover:bg-gray-500/80"
            }`}
          >
            ↺ Reset
          </button>
        </div>
      </Card>

      {/* ── Speed ── */}
      <Card className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-xs uppercase tracking-wide text-gray-400">
            Speed
          </h3>
          <span className="text-sm font-mono font-bold text-blue-400 tabular-nums">
            {speed.toFixed(1)}×
          </span>
        </div>
        <input
          type="range"
          min="-1"
          max="1"
          step="0.01"
          value={Math.log10(Math.max(speed, 0.1))}
          onChange={handleSpeedChange}
          disabled={!connected}
          className="w-full h-1.5 bg-gray-700 rounded-full appearance-none cursor-pointer disabled:opacity-40
                     [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5
                     [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-400 [&::-webkit-slider-thumb]:cursor-pointer
                     [&::-webkit-slider-thumb]:shadow-[0_0_6px_rgba(96,165,250,0.4)]
                     [&::-moz-range-thumb]:w-3.5 [&::-moz-range-thumb]:h-3.5 [&::-moz-range-thumb]:rounded-full
                     [&::-moz-range-thumb]:bg-blue-400 [&::-moz-range-thumb]:border-0"
        />
        <div className="flex justify-between text-[10px] text-gray-600">
          <span>0.1×</span>
          <span className="-translate-x-1/2 ml-[50%]">1×</span>
          <span>10×</span>
        </div>
      </Card>
    </div>
  );
};
