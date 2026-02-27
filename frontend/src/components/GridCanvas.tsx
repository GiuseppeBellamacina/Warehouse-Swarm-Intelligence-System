// Grid Canvas Component for visualization

import React, { useRef, useEffect } from "react";
import { SimulationState } from "../types/simulation";

interface GridCanvasProps {
  state: SimulationState;
  width?: number;
  height?: number;
}

export const GridCanvas: React.FC<GridCanvasProps> = ({
  state,
  width = 800,
  height = 800,
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
    ctx.fillStyle = "#000";
    state.grid.obstacles.forEach((obstacle) => {
      if (obstacle.type === "wall") {
        const { start, end } = obstacle.data;
        ctx.beginPath();
        ctx.moveTo(start.x * cellWidth, start.y * cellHeight);
        ctx.lineTo(end.x * cellWidth, end.y * cellHeight);
        ctx.strokeStyle = "#000";
        ctx.lineWidth = 3;
        ctx.stroke();
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

      // Draw vision radius (subtle)
      ctx.fillStyle = "rgba(255, 255, 255, 0.05)";
      ctx.beginPath();
      ctx.arc(centerX, centerY, radius * 3, 0, 2 * Math.PI);
      ctx.fill();

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
  }, [state, width, height]);

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
