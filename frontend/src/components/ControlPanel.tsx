// Control Panel Component

import React, { useState, useRef, useEffect } from "react";
import {
  GridScenarioConfig,
  SimulationAgentsConfig,
  AgentRoleParams,
  DEFAULT_AGENTS_CONFIG,
} from "../types/simulation";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8000";

interface ControlPanelProps {
  connected: boolean;
  isRunning: boolean;
  isPaused: boolean;
  isLoaded: boolean;
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
  simulationMaxSteps: number;
  scouts: AgentOverrideFields;
  coordinators: AgentOverrideFields;
  retrievers: AgentOverrideFields;
}

function extractOverrides(config: GridScenarioConfig): Overrides {
  const def = DEFAULT_AGENTS_CONFIG;
  return {
    simulationMaxSteps: config.metadata?.max_steps ?? 500,
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
  };
}

// ── Shared small helpers ───────────────────────────────────

const Field: React.FC<{
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
}> = ({ label, value, onChange, min = 0, max = 9999, step = 1, disabled }) => (
  <div className="flex items-center justify-between gap-1">
    <span className="text-gray-400 text-[10px] leading-tight flex-1">
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
      className="w-16 bg-gray-700 border border-gray-600 rounded px-1.5 py-0.5 text-xs text-right disabled:opacity-50"
    />
  </div>
);

const SectionLabel: React.FC<{ color: string; label: string }> = ({
  color,
  label,
}) => (
  <p
    className={`text-[11px] font-semibold uppercase tracking-wide mb-1 ${color}`}
  >
    {label}
  </p>
);

const Hr = () => <div className="border-t border-gray-700 my-3" />;

// ── Main Component ─────────────────────────────────────────

export const ControlPanel: React.FC<ControlPanelProps> = ({
  connected,
  isRunning,
  isPaused,
  isLoaded,
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
  const [rawConfig, setRawConfig] = useState<GridScenarioConfig | null>(null);
  const [overrides, setOverrides] = useState<Overrides | null>(null);
  const [overridesOpen, setOverridesOpen] = useState(false);
  const [isFetching, setIsFetching] = useState(false);
  const [speed, setSpeed] = useState(1.0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load list of available configs on mount
  useEffect(() => {
    const fetchList = async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/configs`);
        if (!res.ok) return;
        const data = await res.json();
        const cfgs: string[] = data.configs ?? [];
        setAvailableConfigs(cfgs);
        if (cfgs.length > 0) setConfigName(cfgs[0]);
      } catch {
        /* ignore network errors */
      }
    };
    fetchList();
  }, []);

  // Auto-fetch config JSON whenever the dropdown selection changes
  useEffect(() => {
    if (!configName) return;
    const load = async () => {
      setIsFetching(true);
      try {
        const res = await fetch(`${BACKEND_URL}/configs/${configName}.json`);
        if (!res.ok) return;
        const cfg: GridScenarioConfig = await res.json();
        setRawConfig(cfg);
        setOverrides(extractOverrides(cfg));
      } catch {
        /* ignore */
      } finally {
        setIsFetching(false);
      }
    };
    load();
  }, [configName]);

  // Read an uploaded JSON file locally (no server round-trip)
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const cfg: GridScenarioConfig = JSON.parse(text);
      setRawConfig(cfg);
      setOverrides(extractOverrides(cfg));
      setConfigName(""); // deselect dropdown
    } catch {
      alert("Invalid JSON file");
    }
  };

  // Helpers to patch individual override slices
  const setOvr = (patch: Partial<Overrides>) =>
    setOverrides((p) => (p ? { ...p, ...patch } : p));
  const setScouts = (patch: Partial<AgentOverrideFields>) =>
    setOverrides((p) => (p ? { ...p, scouts: { ...p.scouts, ...patch } } : p));
  const setCoords = (patch: Partial<AgentOverrideFields>) =>
    setOverrides((p) =>
      p ? { ...p, coordinators: { ...p.coordinators, ...patch } } : p,
    );
  const setRetrs = (patch: Partial<AgentOverrideFields>) =>
    setOverrides((p) =>
      p ? { ...p, retrievers: { ...p.retrievers, ...patch } } : p,
    );

  const handleLoad = () => {
    if (!rawConfig || !overrides) return;
    // Apply max_steps override to scenario metadata (clone)
    const scenario: GridScenarioConfig = {
      ...rawConfig,
      metadata: {
        ...rawConfig.metadata,
        max_steps: overrides.simulationMaxSteps,
      },
    };
    const agents: SimulationAgentsConfig = {
      scouts: ovToRole(overrides.scouts),
      coordinators: ovToRole(overrides.coordinators),
      retrievers: ovToRole(overrides.retrievers),
    };
    onLoad(scenario, agents);
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
    const v = parseFloat(e.target.value);
    setSpeed(v);
    onSpeedChange(v);
  };

  const canLoad = !!rawConfig && !isRunning && connected && !isFetching;
  const canStart = isLoaded && !isRunning && connected;

  return (
    <div className="p-4 space-y-3 text-sm select-none">
      <h2 className="text-base font-bold">Control Panel</h2>

      {/* Simulation status pill */}
      {(isRunning || isLoaded) && (
        <div className="flex items-center">
          {isRunning ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900 text-green-300">
              {isPaused ? "⏸ PAUSED" : "▶ RUNNING"}
            </span>
          ) : (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900 text-blue-300">
              ● LOADED
            </span>
          )}
        </div>
      )}

      <Hr />

      {/* ── Load Configuration ─────────────────────── */}
      <div>
        <h3 className="font-semibold mb-2 text-sm">Load Configuration</h3>

        {/* Dropdown */}
        <div className="mb-2">
          <select
            value={configName}
            onChange={(e) => setConfigName(e.target.value)}
            disabled={isRunning}
            className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-xs"
          >
            {availableConfigs.length > 0 ? (
              availableConfigs.map((c) => (
                <option key={c} value={c}>
                  {c
                    .replace(/_/g, " ")
                    .replace(/\b\w/g, (l) => l.toUpperCase())}
                </option>
              ))
            ) : (
              <option value="">Loading…</option>
            )}
          </select>
        </div>

        {/* File picker */}
        <div className="mb-3">
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
            className="w-full bg-gray-700 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed px-3 py-1.5 rounded border border-gray-600 text-xs transition"
          >
            📂 Or pick a JSON file…
          </button>
        </div>

        {/* Overrides accordion */}
        {overrides && (
          <div className="border border-gray-600 rounded mb-3">
            <button
              onClick={() => setOverridesOpen((v) => !v)}
              className="w-full flex justify-between items-center px-3 py-2 text-xs font-medium text-gray-300 hover:bg-gray-700 rounded transition"
            >
              <span>⚙ Agent &amp; Config Overrides</span>
              <span className="text-gray-500">{overridesOpen ? "▲" : "▼"}</span>
            </button>

            {overridesOpen && (
              <div className="px-3 pb-3 space-y-2 text-xs border-t border-gray-600">
                {/* Simulation */}
                <div className="pt-2">
                  <SectionLabel color="text-gray-300" label="Simulation" />
                  <div className="space-y-1">
                    <Field
                      label="Max steps"
                      value={overrides.simulationMaxSteps}
                      onChange={(v) => setOvr({ simulationMaxSteps: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <div className="flex items-center justify-between gap-1">
                      <span className="text-gray-400 text-[10px] leading-tight flex-1">
                        Objects
                      </span>
                      <span className="w-16 text-right text-xs text-gray-500">
                        {rawConfig?.objects?.length ?? "—"}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Scouts */}
                <div className="border-t border-gray-600 pt-2">
                  <SectionLabel color="text-green-400" label="Scouts" />
                  <div className="space-y-1">
                    <Field
                      label="Count"
                      value={overrides.scouts.count}
                      onChange={(v) => setScouts({ count: v })}
                      min={0}
                      disabled={isRunning}
                    />
                    <Field
                      label="Vision radius"
                      value={overrides.scouts.vision_radius}
                      onChange={(v) => setScouts({ vision_radius: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Comm. radius"
                      value={overrides.scouts.communication_radius}
                      onChange={(v) => setScouts({ communication_radius: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Max energy"
                      value={overrides.scouts.max_energy}
                      onChange={(v) => setScouts({ max_energy: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Speed"
                      value={overrides.scouts.speed}
                      onChange={(v) => setScouts({ speed: v })}
                      min={1}
                      disabled={isRunning}
                    />
                  </div>
                </div>

                {/* Coordinators */}
                <div className="border-t border-gray-600 pt-2">
                  <SectionLabel color="text-blue-400" label="Coordinators" />
                  <div className="space-y-1">
                    <Field
                      label="Count"
                      value={overrides.coordinators.count}
                      onChange={(v) => setCoords({ count: v })}
                      min={0}
                      disabled={isRunning}
                    />
                    <Field
                      label="Vision radius"
                      value={overrides.coordinators.vision_radius}
                      onChange={(v) => setCoords({ vision_radius: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Comm. radius"
                      value={overrides.coordinators.communication_radius}
                      onChange={(v) => setCoords({ communication_radius: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Max energy"
                      value={overrides.coordinators.max_energy}
                      onChange={(v) => setCoords({ max_energy: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Speed"
                      value={overrides.coordinators.speed}
                      onChange={(v) => setCoords({ speed: v })}
                      min={1}
                      disabled={isRunning}
                    />
                  </div>
                </div>

                {/* Retrievers */}
                <div className="border-t border-gray-600 pt-2">
                  <SectionLabel color="text-orange-400" label="Retrievers" />
                  <div className="space-y-1">
                    <Field
                      label="Count"
                      value={overrides.retrievers.count}
                      onChange={(v) => setRetrs({ count: v })}
                      min={0}
                      disabled={isRunning}
                    />
                    <Field
                      label="Vision radius"
                      value={overrides.retrievers.vision_radius}
                      onChange={(v) => setRetrs({ vision_radius: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Comm. radius"
                      value={overrides.retrievers.communication_radius}
                      onChange={(v) => setRetrs({ communication_radius: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Max energy"
                      value={overrides.retrievers.max_energy}
                      onChange={(v) => setRetrs({ max_energy: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Speed"
                      value={overrides.retrievers.speed}
                      onChange={(v) => setRetrs({ speed: v })}
                      min={1}
                      disabled={isRunning}
                    />
                    <Field
                      label="Carry capacity"
                      value={overrides.retrievers.carrying_capacity}
                      onChange={(v) => setRetrs({ carrying_capacity: v })}
                      min={1}
                      disabled={isRunning}
                    />
                  </div>
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
                    <div className="border-t border-yellow-700 pt-2 mt-1">
                      <div className="flex items-start gap-1.5 bg-yellow-900/40 border border-yellow-700 rounded px-2 py-1.5">
                        <span className="text-yellow-400 text-sm leading-none mt-0.5">
                          ⚠
                        </span>
                        <div className="text-[10px] text-yellow-300 leading-snug">
                          <span className="font-semibold">
                            {totalAgents} agents + {totalObjects} objects
                          </span>{" "}
                          = {total} cells ({pct}% of ~{capacity} walkable). Some
                          elements may not spawn.
                        </div>
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        )}

        {/* Load button */}
        <button
          onClick={handleLoad}
          disabled={!canLoad}
          className="w-full bg-blue-700 hover:bg-blue-600 disabled:bg-gray-600 disabled:cursor-not-allowed py-2 rounded font-semibold text-sm transition"
        >
          {isFetching ? "Fetching…" : "Preview / Load"}
        </button>
      </div>

      <Hr />

      {/* ── Simulation Controls ─────────────────────── */}
      <div>
        <h3 className="font-semibold mb-2 text-sm">Simulation Controls</h3>
        <div className="grid grid-cols-2 gap-1.5">
          {/* Start */}
          <button
            onClick={onStartRun}
            disabled={!canStart}
            className="bg-green-700 hover:bg-green-600 disabled:bg-gray-600 disabled:cursor-not-allowed py-2 rounded font-medium transition text-xs"
          >
            ▶ Start
          </button>

          {/* Pause / Resume */}
          {isPaused ? (
            <button
              onClick={onResume}
              disabled={!connected}
              className="bg-green-700 hover:bg-green-600 disabled:bg-gray-600 disabled:cursor-not-allowed py-2 rounded font-medium transition text-xs"
            >
              ▶ Resume
            </button>
          ) : (
            <button
              onClick={onPause}
              disabled={!isRunning || !connected}
              className="bg-yellow-700 hover:bg-yellow-600 disabled:bg-gray-600 disabled:cursor-not-allowed py-2 rounded font-medium transition text-xs"
            >
              ⏸ Pause
            </button>
          )}

          {/* Stop */}
          <button
            onClick={onStop}
            disabled={!isRunning || !connected}
            className="bg-red-700 hover:bg-red-600 disabled:bg-gray-600 disabled:cursor-not-allowed py-2 rounded font-medium transition text-xs"
          >
            ■ Stop
          </button>

          {/* Reset */}
          <button
            onClick={onReset}
            disabled={!isLoaded || !connected}
            className="bg-gray-600 hover:bg-gray-500 disabled:bg-gray-700 disabled:cursor-not-allowed py-2 rounded font-medium transition text-xs"
          >
            ↺ Reset
          </button>
        </div>
      </div>

      <Hr />

      {/* ── Speed ──────────────────────────────────── */}
      <div>
        <h3 className="font-semibold mb-2 text-sm">Speed</h3>
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-gray-400">
            <span>0.1×</span>
            <span className="text-white font-medium">{speed.toFixed(1)}×</span>
            <span>10×</span>
          </div>
          <input
            type="range"
            min="0.1"
            max="10"
            step="0.1"
            value={speed}
            onChange={handleSpeedChange}
            disabled={!connected}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer disabled:opacity-50
                       [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5
                       [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-500 [&::-webkit-slider-thumb]:cursor-pointer
                       [&::-moz-range-thumb]:w-3.5 [&::-moz-range-thumb]:h-3.5 [&::-moz-range-thumb]:rounded-full
                       [&::-moz-range-thumb]:bg-blue-500 [&::-moz-range-thumb]:border-0"
          />
        </div>
      </div>
    </div>
  );
};
