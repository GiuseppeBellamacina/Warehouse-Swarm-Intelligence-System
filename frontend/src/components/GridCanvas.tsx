// Grid Canvas Component for visualization

import React, { useRef, useEffect } from "react";
import { SimulationState } from "../types/simulation";

interface GridCanvasProps {
  state: SimulationState;
  width?: number;
  height?: number;
  selectedAgentId?: number | null;
}

export const GridCanvas: React.FC<GridCanvasProps> = ({
  state,
  width = 800,
  height = 800,
  selectedAgentId = null,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!canvasRef.current || !state || !state.grid) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const gridWidth = state.grid.width;
    const gridHeight = state.grid.height;
    const cellWidth = width / gridWidth;
    const cellHeight = height / gridHeight;

    // Find selected agent and agents in communication range
    const selectedAgent = selectedAgentId
      ? state.agents.find((a) => a.id === selectedAgentId)
      : null;

    const agentsInCommRange = new Set<number>();
    if (selectedAgent) {
      state.agents.forEach((agent) => {
        if (agent.id === selectedAgent.id) return;
        const dx = agent.x - selectedAgent.x;
        const dy = agent.y - selectedAgent.y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance <= selectedAgent.communication_radius) {
          agentsInCommRange.add(agent.id);
        }
      });
    }

    // Clear canvas
    ctx.fillStyle = "#1a1a1a";
    ctx.fillRect(0, 0, width, height);

    // Draw grid lines (subtle)
    ctx.strokeStyle = "#333";
    ctx.lineWidth = 0.5;
    for (let x = 0; x <= gridWidth; x++) {
      ctx.beginPath();
      ctx.moveTo(x * cellWidth, 0);
      ctx.lineTo(x * cellWidth, height);
      ctx.stroke();
    }
    for (let y = 0; y <= gridHeight; y++) {
      ctx.beginPath();
      ctx.moveTo(0, y * cellHeight);
      ctx.lineTo(width, y * cellHeight);
      ctx.stroke();
    }

    // Draw warehouse
    if (state.grid.warehouse) {
      const wh = state.grid.warehouse;

      // Draw all warehouse cells
      if (wh.cells && wh.cells.length > 0) {
        ctx.fillStyle = "rgba(59, 130, 246, 0.3)"; // Blue
        wh.cells.forEach((cell: { x: number; y: number }) => {
          ctx.fillRect(
            cell.x * cellWidth,
            cell.y * cellHeight,
            cellWidth,
            cellHeight,
          );
        });

        // Draw borders for each cell
        ctx.strokeStyle = "rgba(59, 130, 246, 0.5)";
        ctx.lineWidth = 1;
        wh.cells.forEach((cell: { x: number; y: number }) => {
          ctx.strokeRect(
            cell.x * cellWidth,
            cell.y * cellHeight,
            cellWidth,
            cellHeight,
          );
        });
      } else {
        // Fallback to old rectangle drawing if no cells provided
        ctx.fillStyle = "rgba(59, 130, 246, 0.3)"; // Blue
        ctx.fillRect(
          wh.x * cellWidth,
          wh.y * cellHeight,
          wh.width * cellWidth,
          wh.height * cellHeight,
        );
        ctx.strokeStyle = "rgba(59, 130, 246, 0.8)";
        ctx.lineWidth = 2;
        ctx.strokeRect(
          wh.x * cellWidth,
          wh.y * cellHeight,
          wh.width * cellWidth,
          wh.height * cellHeight,
        );
      }

      // Draw entrances (green)
      ctx.fillStyle = "#10b981";
      wh.entrances.forEach((entrance) => {
        ctx.fillRect(
          entrance.x * cellWidth,
          entrance.y * cellHeight,
          cellWidth,
          cellHeight,
        );
      });

      // Draw exits (red)
      ctx.fillStyle = "#ef4444";
      wh.exits.forEach((exit) => {
        ctx.fillRect(
          exit.x * cellWidth,
          exit.y * cellHeight,
          cellWidth,
          cellHeight,
        );
      });
    }

    // Draw obstacles
    ctx.fillStyle = "#4a4a4a"; // Dark gray for walls
    state.grid.obstacles.forEach((obstacle) => {
      if (obstacle.type === "wall") {
        const { start, end } = obstacle.data;

        // Draw filled cells for wall segments using Bresenham's line algorithm
        const x0 = start.x;
        const y0 = start.y;
        const x1 = end.x;
        const y1 = end.y;

        const dx = Math.abs(x1 - x0);
        const dy = Math.abs(y1 - y0);
        const sx = x0 < x1 ? 1 : -1;
        const sy = y0 < y1 ? 1 : -1;
        let err = dx - dy;

        let x = x0;
        let y = y0;

        while (true) {
          // Draw filled cell
          ctx.fillRect(x * cellWidth, y * cellHeight, cellWidth, cellHeight);

          if (x === x1 && y === y1) break;

          const e2 = 2 * err;
          if (e2 > -dy) {
            err -= dy;
            x += sx;
          }
          if (e2 < dx) {
            err += dx;
            y += sy;
          }
        }
      } else if (obstacle.type === "box") {
        const { top_left, width: w, height: h } = obstacle.data;
        ctx.fillRect(
          top_left.x * cellWidth,
          top_left.y * cellHeight,
          w * cellWidth,
          h * cellHeight,
        );
      }
    });

    // Draw selected agent vision and communication radii
    if (selectedAgent) {
      const centerX = (selectedAgent.x + 0.5) * cellWidth;
      const centerY = (selectedAgent.y + 0.5) * cellHeight;

      // Draw communication radius (outer circle - green)
      ctx.strokeStyle = "rgba(34, 197, 94, 0.4)";
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.arc(
        centerX,
        centerY,
        selectedAgent.communication_radius * cellWidth,
        0,
        2 * Math.PI,
      );
      ctx.stroke();

      // Draw vision radius (inner circle - blue)
      ctx.strokeStyle = "rgba(59, 130, 246, 0.6)";
      ctx.lineWidth = 2;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.arc(
        centerX,
        centerY,
        selectedAgent.vision_radius * cellWidth,
        0,
        2 * Math.PI,
      );
      ctx.stroke();

      // Reset line dash
      ctx.setLineDash([]);

      // Draw agent's path in purple
      if (selectedAgent.path && selectedAgent.path.length > 0) {
        ctx.strokeStyle = "rgba(168, 85, 247, 0.8)"; // Purple
        ctx.lineWidth = 3;
        ctx.setLineDash([]);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";

        ctx.beginPath();
        // Start from agent's current position
        ctx.moveTo(centerX, centerY);

        // Draw line through each path waypoint
        selectedAgent.path.forEach((waypoint) => {
          const wpX = (waypoint.x + 0.5) * cellWidth;
          const wpY = (waypoint.y + 0.5) * cellHeight;
          ctx.lineTo(wpX, wpY);
        });

        ctx.stroke();

        // Draw waypoint markers
        ctx.fillStyle = "rgba(168, 85, 247, 0.6)";
        selectedAgent.path.forEach((waypoint) => {
          const wpX = (waypoint.x + 0.5) * cellWidth;
          const wpY = (waypoint.y + 0.5) * cellHeight;
          ctx.beginPath();
          ctx.arc(wpX, wpY, 3, 0, 2 * Math.PI);
          ctx.fill();
        });
      }
    }

    // Draw objects
    state.objects.forEach((obj) => {
      if (!obj.retrieved) {
        ctx.fillStyle = "#facc15"; // Yellow
        ctx.beginPath();
        ctx.arc(
          (obj.x + 0.5) * cellWidth,
          (obj.y + 0.5) * cellHeight,
          Math.min(cellWidth, cellHeight) * 0.3,
          0,
          2 * Math.PI,
        );
        ctx.fill();
      }
    });

    // Draw agents
    state.agents.forEach((agent) => {
      const centerX = (agent.x + 0.5) * cellWidth;
      const centerY = (agent.y + 0.5) * cellHeight;
      const radius = Math.min(cellWidth, cellHeight) * 0.4;

      const isSelected = agent.id === selectedAgentId;
      const isInCommRange = agentsInCommRange.has(agent.id);

      // Highlight selected agent with glow
      if (isSelected) {
        ctx.strokeStyle = "rgba(255, 255, 0, 0.6)";
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius * 1.5, 0, 2 * Math.PI);
        ctx.stroke();
      }

      // Highlight agents in communication range with ring
      if (isInCommRange) {
        ctx.strokeStyle = "rgba(34, 197, 94, 0.8)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius * 1.3, 0, 2 * Math.PI);
        ctx.stroke();
      }

      // Agent color based on role
      let color = "#fff";
      if (agent.role === "scout") {
        color = "#22c55e"; // Green
      } else if (agent.role === "coordinator") {
        color = "#3b82f6"; // Blue
      } else if (agent.role === "retriever") {
        color = "#f97316"; // Orange
      }

      // Draw agent body
      ctx.fillStyle = color;

      if (agent.role === "scout") {
        // Draw circle for scouts
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
        ctx.fill();
      } else if (agent.role === "coordinator") {
        // Draw hexagon for coordinators
        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const angle = (Math.PI / 3) * i;
          const x = centerX + radius * Math.cos(angle);
          const y = centerY + radius * Math.sin(angle);
          if (i === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        }
        ctx.closePath();
        ctx.fill();
      } else if (agent.role === "retriever") {
        // Draw square for retrievers
        ctx.fillRect(
          centerX - radius,
          centerY - radius,
          radius * 2,
          radius * 2,
        );
      }

      // Draw energy bar
      const barWidth = cellWidth * 0.8;
      const barHeight = 3;
      const barX = agent.x * cellWidth + cellWidth * 0.1;
      const barY = (agent.y + 0.9) * cellHeight;

      ctx.fillStyle = "#334155";
      ctx.fillRect(barX, barY, barWidth, barHeight);

      const energyPct = agent.energy / 100;
      const energyColor =
        energyPct > 0.5 ? "#22c55e" : energyPct > 0.25 ? "#facc15" : "#ef4444";
      ctx.fillStyle = energyColor;
      ctx.fillRect(barX, barY, barWidth * energyPct, barHeight);

      // Draw carrying indicator
      if (agent.carrying > 0) {
        ctx.fillStyle = "#facc15";
        ctx.font = `${cellHeight * 0.3}px Arial`;
        ctx.textAlign = "center";
        ctx.fillText(`${agent.carrying}`, centerX, centerY - radius * 1.5);
      }
    });
  }, [state, width, height, selectedAgentId]);

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      className="border border-gray-700 rounded-lg"
      style={{ backgroundColor: "#1a1a1a" }}
    />
  );
};
