// Metrics Display Component

import React from "react";
import { SimulationState } from "../types/simulation";

interface MetricsDisplayProps {
  state: SimulationState | null;
}

export const MetricsDisplay: React.FC<MetricsDisplayProps> = ({ state }) => {
  if (!state) {
    return (
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-xl font-bold mb-4">Metrics</h2>
        <p className="text-gray-400">No simulation running</p>
      </div>
    );
  }

  const { metrics, step, agents } = state;
  const progressPct = (metrics.retrieval_progress * 100).toFixed(1);
  const avgEnergyPct = ((metrics.average_energy / 100) * 100).toFixed(1);

  // Count agents by role
  const scoutCount = agents.filter((a) => a.role === "scout").length;
  const coordinatorCount = agents.filter(
    (a) => a.role === "coordinator",
  ).length;
  const retrieverCount = agents.filter((a) => a.role === "retriever").length;

  return (
    <div className="bg-gray-800 rounded-lg p-6 space-y-4">
      <h2 className="text-2xl font-bold mb-4">Live Metrics</h2>

      {/* Step Counter */}
      <div className="bg-gray-700 rounded p-4">
        <div className="text-sm text-gray-400 mb-1">Current Step</div>
        <div className="text-3xl font-bold text-blue-400">{step}</div>
      </div>

      {/* Objects Retrieved */}
      <div className="bg-gray-700 rounded p-4">
        <div className="text-sm text-gray-400 mb-1">Objects Retrieved</div>
        <div className="text-3xl font-bold text-green-400">
          {metrics.objects_retrieved} / {metrics.total_objects}
        </div>
        <div className="mt-2 bg-gray-600 rounded-full h-3 overflow-hidden">
          <div
            className="bg-green-500 h-full transition-all duration-300"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <div className="text-right text-sm text-gray-400 mt-1">
          {progressPct}%
        </div>
      </div>

      {/* Average Energy */}
      <div className="bg-gray-700 rounded p-4">
        <div className="text-sm text-gray-400 mb-1">Average Agent Energy</div>
        <div className="text-3xl font-bold text-yellow-400">
          {metrics.average_energy.toFixed(1)}
        </div>
        <div className="mt-2 bg-gray-600 rounded-full h-3 overflow-hidden">
          <div
            className={`h-full transition-all duration-300 ${
              parseFloat(avgEnergyPct) > 50
                ? "bg-green-500"
                : parseFloat(avgEnergyPct) > 25
                  ? "bg-yellow-500"
                  : "bg-red-500"
            }`}
            style={{ width: `${avgEnergyPct}%` }}
          />
        </div>
        <div className="text-right text-sm text-gray-400 mt-1">
          {avgEnergyPct}%
        </div>
      </div>

      {/* Active Agents */}
      <div className="bg-gray-700 rounded p-4">
        <div className="text-sm text-gray-400 mb-1">Active Agents</div>
        <div className="text-3xl font-bold text-purple-400">
          {metrics.active_agents} / {agents.length}
        </div>
      </div>

      {/* Agent Breakdown */}
      <div className="bg-gray-700 rounded p-4">
        <div className="text-sm text-gray-400 mb-2">Agent Types</div>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center">
              <div className="w-3 h-3 rounded-full bg-green-500 mr-2" />
              <span>Scouts</span>
            </div>
            <span className="font-bold">{scoutCount}</span>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center">
              <div className="w-3 h-3 rounded-full bg-blue-500 mr-2" />
              <span>Coordinators</span>
            </div>
            <span className="font-bold">{coordinatorCount}</span>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center">
              <div className="w-3 h-3 rounded-full bg-orange-500 mr-2" />
              <span>Retrievers</span>
            </div>
            <span className="font-bold">{retrieverCount}</span>
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="border-t border-gray-700 pt-4">
        <div className="text-sm font-semibold mb-2">Legend</div>
        <div className="space-y-1 text-sm">
          <div className="flex items-center">
            <div className="w-4 h-4 rounded-full bg-green-500 mr-2" />
            <span>Scout</span>
          </div>
          <div className="flex items-center">
            <div
              className="w-4 h-4 bg-blue-500 mr-2"
              style={{
                clipPath:
                  "polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%)",
              }}
            />
            <span>Coordinator</span>
          </div>
          <div className="flex items-center">
            <div className="w-4 h-4 bg-orange-500 mr-2" />
            <span>Retriever</span>
          </div>
          <div className="flex items-center">
            <div className="w-4 h-4 rounded-full bg-yellow-400 mr-2" />
            <span>Object</span>
          </div>
          <div className="flex items-center">
            <div className="w-4 h-4 bg-blue-600 border-2 border-blue-400 mr-2" />
            <span>Warehouse</span>
          </div>
          <div className="flex items-center">
            <div className="w-4 h-4 bg-green-500 mr-2" />
            <span>Entrance</span>
          </div>
        </div>
      </div>
    </div>
  );
};
