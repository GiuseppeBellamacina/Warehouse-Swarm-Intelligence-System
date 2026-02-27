// Agent List Component - Shows clickable list of agents

import { Agent } from "../types/simulation";

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
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-gray-400 mb-2">{title}</h3>
        <div className="space-y-1">
          {groupAgents.map((agent) => {
            const isSelected = selectedAgentId === agent.id;
            const roleColor = getRoleColor(agent.role);
            const isLowEnergy = agent.energy < 30;

            return (
              <button
                key={agent.id}
                onClick={() => onSelectAgent(isSelected ? null : agent.id)}
                className={`w-full p-2 rounded border text-left transition-all ${roleColor} ${
                  isSelected
                    ? "ring-2 ring-offset-2 ring-offset-gray-900 scale-105"
                    : "hover:scale-102"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-lg">{getRoleIcon(agent.role)}</span>
                    <span className="font-mono text-sm">ID {agent.id}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {agent.carrying > 0 && (
                      <span className="text-xs bg-purple-600 px-1.5 py-0.5 rounded">
                        📦×{agent.carrying}
                      </span>
                    )}
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded ${
                        isLowEnergy
                          ? "bg-red-600 text-white"
                          : "bg-gray-700 text-gray-300"
                      }`}
                    >
                      ⚡{Math.round(agent.energy)}%
                    </span>
                  </div>
                </div>

                {isSelected && (
                  <div className="mt-2 pt-2 border-t border-current/20 text-xs space-y-1">
                    <div className="flex justify-between">
                      <span className="text-gray-400">Position:</span>
                      <span className="font-mono">
                        ({agent.x}, {agent.y})
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">State:</span>
                      <span className="capitalize">{agent.state}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Vision:</span>
                      <span>{agent.vision_radius} cells</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Comm:</span>
                      <span>{agent.communication_radius} cells</span>
                    </div>

                    {/* Recent Messages */}
                    {agent.recent_messages &&
                      agent.recent_messages.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-current/20">
                          <div className="text-gray-400 mb-1 font-semibold">
                            Recent Messages:
                          </div>
                          <div className="space-y-1 max-h-32 overflow-y-auto">
                            {agent.recent_messages
                              .slice()
                              .reverse()
                              .map((msg, idx) => (
                                <div
                                  key={idx}
                                  className={`p-1.5 rounded text-[10px] ${
                                    msg.direction === "sent"
                                      ? "bg-blue-900/30 border-l-2 border-blue-400"
                                      : "bg-green-900/30 border-l-2 border-green-400"
                                  }`}
                                >
                                  <div className="flex items-center gap-1 mb-0.5">
                                    <span className="font-semibold">
                                      {msg.direction === "sent" ? "→" : "←"}{" "}
                                      Step {msg.step}
                                    </span>
                                    <span className="text-gray-500">•</span>
                                    <span className="text-gray-400">
                                      {msg.type}
                                    </span>
                                  </div>
                                  <div className="text-gray-300">
                                    {msg.details}
                                  </div>
                                  {msg.targets.length > 0 && (
                                    <div className="text-gray-500 mt-0.5">
                                      {msg.direction === "sent"
                                        ? "To:"
                                        : "From:"}{" "}
                                      Agent {msg.targets.join(", ")}
                                    </div>
                                  )}
                                </div>
                              ))}
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
    <div className="bg-gray-800 rounded-lg p-4 h-full overflow-y-auto">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">Agents</h2>
        {selectedAgentId !== null && (
          <button
            onClick={() => onSelectAgent(null)}
            className="text-xs text-gray-400 hover:text-white transition"
          >
            Clear Selection
          </button>
        )}
      </div>

      <div className="text-sm text-gray-400 mb-4">
        Click an agent to view its communication and vision radii
      </div>

      {renderAgentGroup(scouts, `Scouts (${scouts.length})`)}
      {renderAgentGroup(coordinators, `Coordinators (${coordinators.length})`)}
      {renderAgentGroup(retrievers, `Retrievers (${retrievers.length})`)}

      {agents.length === 0 && (
        <div className="text-center text-gray-500 py-8">
          No agents in simulation
        </div>
      )}
    </div>
  );
}
