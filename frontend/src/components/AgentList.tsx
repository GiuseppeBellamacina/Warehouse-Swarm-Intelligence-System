// Agent List Component - Shows clickable list of agents

import { Agent, AgentMessage } from "../types/simulation";

interface AgentListProps {
  agents: Agent[];
  selectedAgentId: number | null;
  onSelectAgent: (agentId: number | null) => void;
}

const getRoleColor = (role: string): string => {
  switch (role) {
    case "scout":
      return "text-green-400 bg-green-900/30 border-green-500";
    case "coordinator":
      return "text-blue-400 bg-blue-900/30 border-blue-500";
    case "retriever":
      return "text-orange-400 bg-orange-900/30 border-orange-500";
    default:
      return "text-gray-400 bg-gray-800 border-gray-600";
  }
};

const getRoleIcon = (role: string): string => {
  switch (role) {
    case "scout":
      return "🔍";
    case "coordinator":
      return "📋";
    case "retriever":
      return "📦";
    default:
      return "❓";
  }
};

/** Returns Tailwind classes + icon for a given message type */
const getMsgStyle = (
  type: string,
  direction: "sent" | "received",
): { border: string; bg: string; badge: string; icon: string } => {
  const t = type.toLowerCase();
  if (t.includes("task_assignment") || t.includes("retrieve"))
    return {
      border: "border-violet-400",
      bg: direction === "sent" ? "bg-violet-900/30" : "bg-violet-800/20",
      badge: "bg-violet-700 text-violet-100",
      icon: "📌",
    };
  if (t.includes("object_spotted") || t.includes("object_location"))
    return {
      border: "border-yellow-400",
      bg: direction === "sent" ? "bg-yellow-900/30" : "bg-yellow-800/20",
      badge: "bg-yellow-700 text-yellow-100",
      icon: "👁️",
    };
  if (t.includes("object_picked") || t.includes("object_delivered"))
    return {
      border: "border-green-400",
      bg: direction === "sent" ? "bg-green-900/30" : "bg-green-800/20",
      badge: "bg-green-700 text-green-100",
      icon: t.includes("delivered") ? "✅" : "🤲",
    };
  if (t.includes("status") || t.includes("task_status"))
    return {
      border: "border-cyan-400",
      bg: direction === "sent" ? "bg-cyan-900/30" : "bg-cyan-800/20",
      badge: "bg-cyan-700 text-cyan-100",
      icon: "📊",
    };
  if (t.includes("map_data") || t.includes("map"))
    return {
      border: "border-sky-400",
      bg: direction === "sent" ? "bg-sky-900/30" : "bg-sky-800/20",
      badge: "bg-sky-700 text-sky-100",
      icon: "🗺️",
    };
  if (t.includes("idle"))
    return {
      border: "border-gray-500",
      bg: "bg-gray-800/40",
      badge: "bg-gray-600 text-gray-300",
      icon: "💤",
    };
  if (t.includes("clear") || t.includes("yield"))
    return {
      border: "border-red-400",
      bg: direction === "sent" ? "bg-red-900/30" : "bg-red-800/20",
      badge: "bg-red-700 text-red-100",
      icon: "🚧",
    };
  if (t.includes("peer"))
    return {
      border: "border-orange-400",
      bg: direction === "sent" ? "bg-orange-900/30" : "bg-orange-800/20",
      badge: "bg-orange-700 text-orange-100",
      icon: "🤝",
    };
  // default
  return {
    border: direction === "sent" ? "border-blue-400" : "border-green-400",
    bg: direction === "sent" ? "bg-blue-900/30" : "bg-green-900/30",
    badge:
      direction === "sent"
        ? "bg-blue-700 text-blue-100"
        : "bg-green-700 text-green-100",
    icon: direction === "sent" ? "→" : "←",
  };
};

const getStateBadge = (state: string): { cls: string; label: string } => {
  const s = state.toLowerCase();
  if (s === "exploring")
    return { cls: "bg-cyan-700/60 text-cyan-200", label: "Exploring" };
  if (s === "delivering")
    return { cls: "bg-orange-700/60 text-orange-200", label: "Delivering" };
  if (s === "retrieving")
    return { cls: "bg-yellow-700/60 text-yellow-200", label: "Retrieving" };
  if (s === "recharging")
    return { cls: "bg-red-700/60 text-red-200", label: "Recharging" };
  if (s === "moving_to_target")
    return { cls: "bg-blue-700/60 text-blue-200", label: "Moving" };
  if (s === "idle")
    return { cls: "bg-gray-600/60 text-gray-300", label: "Idle" };
  return { cls: "bg-gray-600/60 text-gray-300", label: state };
};

export function AgentList({
  agents,
  selectedAgentId,
  onSelectAgent,
}: AgentListProps) {
  // Group agents by role
  const scouts = agents.filter((a) => a.role === "scout");
  const coordinators = agents.filter((a) => a.role === "coordinator");
  const retrievers = agents.filter((a) => a.role === "retriever");

  const renderAgentGroup = (groupAgents: Agent[], title: string) => {
    if (groupAgents.length === 0) return null;

    return (
      <div className="mb-3">
        <h3 className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider mb-1.5 px-1">
          {title}
        </h3>
        <div className="space-y-1">
          {groupAgents.map((agent) => {
            const isSelected = selectedAgentId === agent.id;
            const roleColor = getRoleColor(agent.role);
            const agentMaxEnergy = agent.max_energy || 500;
            const energyPct = Math.round((agent.energy / agentMaxEnergy) * 100);
            const isLowEnergy = energyPct < 20;
            const energyBarColor =
              energyPct > 50
                ? "bg-emerald-400"
                : energyPct > 25
                  ? "bg-yellow-400"
                  : "bg-red-400";
            const stateBadge = getStateBadge(agent.state);

            return (
              <button
                key={agent.id}
                onClick={() => onSelectAgent(isSelected ? null : agent.id)}
                className={`w-full p-2 rounded-lg border text-left transition-all duration-200 ${roleColor} ${
                  isSelected
                    ? "ring-1 ring-offset-1 ring-offset-gray-950 ring-blue-500/50 border-opacity-80"
                    : "hover:brightness-110 border-opacity-40"
                }`}
              >
                {/* Header row */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm">{getRoleIcon(agent.role)}</span>
                    <span className="font-mono text-xs font-bold">
                      #{agent.id}
                    </span>
                    <span
                      className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${stateBadge.cls}`}
                    >
                      {stateBadge.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {agent.carrying > 0 && (
                      <span className="text-[9px] bg-purple-600/60 text-purple-200 px-1.5 py-0.5 rounded-full font-medium">
                        📦{agent.carrying}
                      </span>
                    )}
                    <span
                      className={`text-[9px] px-1.5 py-0.5 rounded-full font-mono tabular-nums ${
                        isLowEnergy
                          ? "bg-red-500/30 text-red-300 border border-red-500/30"
                          : "text-gray-400"
                      }`}
                    >
                      {energyPct}%
                    </span>
                  </div>
                </div>

                {/* Mini energy bar */}
                <div className="mt-1.5 h-0.5 rounded-full bg-gray-700/60 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${energyBarColor}`}
                    style={{ width: `${energyPct}%` }}
                  />
                </div>

                {/* Expanded detail */}
                {isSelected && (
                  <div className="mt-2 pt-2 border-t border-current/10 text-xs space-y-1.5">
                    <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
                      <span className="text-gray-500 text-[10px]">
                        Position
                      </span>
                      <span className="font-mono text-right text-[10px]">
                        ({agent.x}, {agent.y})
                      </span>
                      <span className="text-gray-500 text-[10px]">Energy</span>
                      <span className="text-right text-[10px]">
                        {Math.round(agent.energy)}/{agentMaxEnergy}
                      </span>
                      <span className="text-gray-500 text-[10px]">Vision</span>
                      <span className="text-right text-[10px]">
                        {agent.vision_radius} cells
                      </span>
                      <span className="text-gray-500 text-[10px]">Comm.</span>
                      <span className="text-right text-[10px]">
                        {agent.communication_radius} cells
                      </span>
                    </div>

                    {/* Message log */}
                    {agent.recent_messages &&
                      agent.recent_messages.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-current/10">
                          <div className="text-gray-500 mb-1 font-semibold text-[9px] uppercase tracking-widest">
                            Messages
                          </div>
                          <div className="space-y-0.5 max-h-40 overflow-y-auto pr-0.5">
                            {agent.recent_messages
                              .slice()
                              .reverse()
                              .map((msg: AgentMessage, idx: number) => {
                                const style = getMsgStyle(
                                  msg.type,
                                  msg.direction,
                                );
                                return (
                                  <div
                                    key={idx}
                                    className={`p-1.5 rounded-md text-[10px] border-l-2 ${style.bg} ${style.border}`}
                                  >
                                    <div className="flex items-center gap-1 flex-wrap">
                                      <span className="text-[9px]">
                                        {style.icon}
                                      </span>
                                      <span
                                        className={`px-1 py-0.5 rounded text-[8px] font-bold ${style.badge}`}
                                      >
                                        {msg.type.replace(/_/g, " ")}
                                      </span>
                                      <span className="text-gray-500 ml-auto text-[9px]">
                                        {msg.direction === "sent" ? "↑" : "↓"} s
                                        {msg.step}
                                      </span>
                                    </div>
                                    {msg.details && (
                                      <div className="mt-0.5 text-gray-300 leading-tight">
                                        {msg.details}
                                      </div>
                                    )}
                                    {msg.targets.length > 0 && (
                                      <div className="mt-0.5 text-gray-600 text-[9px]">
                                        {msg.direction === "sent" ? "→" : "←"} #
                                        {msg.targets.join(", #")}
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                          </div>
                        </div>
                      )}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="p-3 h-full overflow-y-auto">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold tracking-wide uppercase text-gray-300">
          Agents
        </h2>
        {selectedAgentId !== null && (
          <button
            onClick={() => onSelectAgent(null)}
            className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      <p className="text-[10px] text-gray-600 mb-3 leading-relaxed">
        Click an agent to highlight radii on the map
      </p>

      {renderAgentGroup(scouts, `Scouts · ${scouts.length}`)}
      {renderAgentGroup(coordinators, `Coordinators · ${coordinators.length}`)}
      {renderAgentGroup(retrievers, `Retrievers · ${retrievers.length}`)}

      {agents.length === 0 && (
        <div className="text-center text-gray-600 py-8 text-xs">No agents</div>
      )}
    </div>
  );
}
