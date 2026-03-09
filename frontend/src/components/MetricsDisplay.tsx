// Metrics Display Component

import React from "react";
import { SimulationState } from "../types/simulation";

interface MetricsDisplayProps {
  state: SimulationState | null;
}

interface StatCardProps {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  accent?: string;
}

const StatCard: React.FC<StatCardProps> = ({
  label,
  value,
  sub,
  accent = "text-white",
}) => (
  <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5">
    <div className="text-[9px] font-medium text-gray-500 uppercase tracking-widest mb-0.5">
      {label}
    </div>
    <div className={`text-xl font-bold leading-none ${accent}`}>{value}</div>
    {sub && <div className="mt-1">{sub}</div>}
  </div>
);

interface ProgressBarProps {
  pct: number; // 0–100
  colorClass?: string;
}

const ProgressBar: React.FC<ProgressBarProps> = ({ pct, colorClass }) => {
  const color =
    colorClass ??
    (pct > 50 ? "bg-emerald-400" : pct > 25 ? "bg-yellow-400" : "bg-red-400");
  return (
    <div className="mt-1 h-1 rounded-full bg-gray-700/60 overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
};

export const MetricsDisplay: React.FC<MetricsDisplayProps> = ({ state }) => {
  if (!state) {
    return (
      <div className="p-6 flex flex-col items-center justify-center gap-3 text-center h-full">
        <span className="text-3xl opacity-30">📭</span>
        <p className="text-gray-600 text-xs">No simulation loaded</p>
      </div>
    );
  }

  const { metrics, step, agents } = state;
  const maxEnergy = agents.length > 0 ? agents[0].max_energy || 500 : 500;
  const progressPct = parseFloat((metrics.retrieval_progress * 100).toFixed(1));
  const avgEnergyPct = Math.min(
    (metrics.average_energy / maxEnergy) * 100,
    100,
  );

  const scoutCount = agents.filter((a) => a.role === "scout").length;
  const coordinatorCount = agents.filter(
    (a) => a.role === "coordinator",
  ).length;
  const retrieverCount = agents.filter((a) => a.role === "retriever").length;

  const efficiency =
    step > 0 ? ((metrics.objects_retrieved / step) * 100).toFixed(2) : "—";

  return (
    <div className="p-3 space-y-2.5 overflow-y-auto h-full">
      <h2 className="text-sm font-bold tracking-wide uppercase text-gray-300 flex items-center gap-1.5">
        <span className="text-gray-500">📊</span>
        <span>Metrics</span>
      </h2>

      {/* Step + Efficiency row */}
      <div className="grid grid-cols-2 gap-1.5">
        <StatCard label="Step" value={step} accent="text-blue-400" />
        <StatCard
          label="Efficiency"
          value={
            <span className="text-sm">
              {efficiency}
              <span className="text-[10px] font-normal text-gray-500">
                {" "}
                obj/100
              </span>
            </span>
          }
          accent="text-violet-400"
        />
      </div>

      {/* Retrieval progress */}
      <StatCard
        label="Objects Retrieved"
        accent="text-emerald-400"
        value={
          <span>
            {metrics.objects_retrieved}
            <span className="text-gray-600 font-normal text-base">
              {" "}
              / {metrics.total_objects}
            </span>
          </span>
        }
        sub={
          <>
            <ProgressBar pct={progressPct} colorClass="bg-emerald-400" />
            <div className="text-right text-[9px] text-gray-500 mt-0.5">
              {progressPct.toFixed(1)}%
            </div>
          </>
        }
      />

      {/* Average energy */}
      <StatCard
        label="Avg Agent Energy"
        accent={
          avgEnergyPct > 50
            ? "text-emerald-400"
            : avgEnergyPct > 25
              ? "text-yellow-400"
              : "text-red-400"
        }
        value={
          <span>
            {Math.round(metrics.average_energy)}
            <span className="text-gray-600 font-normal text-base">
              {" "}
              / {maxEnergy}
            </span>
          </span>
        }
        sub={
          <>
            <ProgressBar pct={avgEnergyPct} />
            <div className="text-right text-[9px] text-gray-500 mt-0.5">
              {avgEnergyPct.toFixed(1)}%
            </div>
          </>
        }
      />

      {/* Per-agent energy mini bars */}
      {agents.length > 0 && (
        <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5">
          <div className="text-[9px] font-medium text-gray-500 uppercase tracking-widest mb-1.5">
            Agent Energy
          </div>
          <div className="space-y-1">
            {agents.map((a) => {
              const pct = Math.min(
                Math.round((a.energy / (a.max_energy || 500)) * 100),
                100,
              );
              const color =
                pct > 50
                  ? "bg-emerald-400"
                  : pct > 25
                    ? "bg-yellow-400"
                    : "bg-red-400";
              const roleIcon =
                a.role === "scout"
                  ? "🔍"
                  : a.role === "coordinator"
                    ? "📋"
                    : "📦";
              return (
                <div key={a.id} className="flex items-center gap-1.5">
                  <span className="text-[9px] w-3.5 text-center">
                    {roleIcon}
                  </span>
                  <span className="text-[9px] font-mono text-gray-500 w-4">
                    {a.id}
                  </span>
                  <div className="flex-1 h-1 bg-gray-700/60 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${color}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="text-[9px] text-gray-500 font-mono tabular-nums w-7 text-right">
                    {pct}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Active agents */}
      <div className="grid grid-cols-3 gap-1.5">
        <div className="bg-green-950/40 border border-green-800/30 rounded-lg p-2 text-center">
          <div className="text-sm">🔍</div>
          <div className="text-lg font-bold text-green-400">{scoutCount}</div>
          <div className="text-[9px] text-gray-500">Scout</div>
        </div>
        <div className="bg-blue-950/40 border border-blue-800/30 rounded-lg p-2 text-center">
          <div className="text-sm">📋</div>
          <div className="text-lg font-bold text-blue-400">
            {coordinatorCount}
          </div>
          <div className="text-[9px] text-gray-500">Coord</div>
        </div>
        <div className="bg-orange-950/40 border border-orange-800/30 rounded-lg p-2 text-center">
          <div className="text-sm">📦</div>
          <div className="text-lg font-bold text-orange-400">
            {retrieverCount}
          </div>
          <div className="text-[9px] text-gray-500">Retriever</div>
        </div>
      </div>

      {/* Legend */}
      <div className="border-t border-gray-800/60 pt-2.5">
        <div className="text-[9px] font-medium text-gray-500 uppercase tracking-widest mb-1.5">
          Legend
        </div>
        <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[10px]">
          {[
            { shape: "circle", color: "bg-green-500", label: "Scout" },
            {
              shape: "hex",
              color: "bg-blue-500",
              label: "Coordinator",
            },
            { shape: "square", color: "bg-orange-500", label: "Retriever" },
            { shape: "circle", color: "bg-yellow-400", label: "Object" },
            {
              shape: "square",
              color: "bg-blue-700 border border-blue-400",
              label: "Warehouse",
            },
            { shape: "square", color: "bg-green-400", label: "Entrance" },
            { shape: "square", color: "bg-red-400", label: "Exit" },
            { shape: "square", color: "bg-gray-600", label: "Obstacle" },
          ].map(({ shape, color, label }) => (
            <div key={label} className="flex items-center gap-1.5">
              <div
                className={`w-2.5 h-2.5 flex-shrink-0 ${color} ${
                  shape === "circle"
                    ? "rounded-full"
                    : shape === "hex"
                      ? ""
                      : "rounded-sm"
                }`}
                style={
                  shape === "hex"
                    ? {
                        clipPath:
                          "polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%)",
                      }
                    : {}
                }
              />
              <span className="text-gray-400">{label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
