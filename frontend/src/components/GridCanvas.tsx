// Grid Canvas Component for visualization

import React, { useRef, useEffect, useState } from "react";
import { SimulationState, WallData, BoxData } from "../types/simulation";

interface GridCanvasProps {
  state: SimulationState;
  selectedAgentId?: number | null;
}

export const GridCanvas: React.FC<GridCanvasProps> = ({
  state,
  selectedAgentId = null,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 400, h: 400 });

  // Auto-size: observe container and update canvas dimensions
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          // Use the smaller dimension to keep the grid square
          const side = Math.floor(Math.min(width, height));
          setSize({ w: side, h: side });
        }
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  const { w: width, h: height } = size;

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

      if (wh.cells && wh.cells.length > 0) {
        ctx.fillStyle = "rgba(59, 130, 246, 0.3)";
        wh.cells.forEach((cell: { x: number; y: number }) => {
          ctx.fillRect(
            cell.x * cellWidth,
            cell.y * cellHeight,
            cellWidth,
            cellHeight,
          );
        });
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
        ctx.fillStyle = "rgba(59, 130, 246, 0.3)";
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

      ctx.fillStyle = "#10b981";
      wh.entrances.forEach((entrance) => {
        ctx.fillRect(
          entrance.x * cellWidth,
          entrance.y * cellHeight,
          cellWidth,
          cellHeight,
        );
      });

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
    ctx.fillStyle = "#4a4a4a";
    state.grid.obstacles.forEach((obstacle) => {
      if (obstacle.type === "wall") {
        const wallData = obstacle.data as WallData;
        const { start, end } = wallData;
        const x0 = start.x,
          y0 = start.y,
          x1 = end.x,
          y1 = end.y;
        const dx = Math.abs(x1 - x0),
          dy = Math.abs(y1 - y0);
        const sx = x0 < x1 ? 1 : -1,
          sy = y0 < y1 ? 1 : -1;
        let err = dx - dy,
          x = x0,
          y = y0;
        let running = true;
        while (running) {
          ctx.fillRect(x * cellWidth, y * cellHeight, cellWidth, cellHeight);
          if (x === x1 && y === y1) {
            running = false;
            break;
          }
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
        const boxData = obstacle.data as BoxData;
        const { top_left, width: w, height: h } = boxData;
        ctx.fillRect(
          top_left.x * cellWidth,
          top_left.y * cellHeight,
          w * cellWidth,
          h * cellHeight,
        );
      }
    });

    // Draw selected agent vision / comm radii
    if (selectedAgent) {
      const centerX = (selectedAgent.x + 0.5) * cellWidth;
      const centerY = (selectedAgent.y + 0.5) * cellHeight;

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
      ctx.setLineDash([]);

      if (selectedAgent.path && selectedAgent.path.length > 0) {
        ctx.strokeStyle = "rgba(168, 85, 247, 0.8)";
        ctx.lineWidth = 3;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        ctx.moveTo(centerX, centerY);
        selectedAgent.path.forEach((wp) => {
          ctx.lineTo((wp.x + 0.5) * cellWidth, (wp.y + 0.5) * cellHeight);
        });
        ctx.stroke();
        ctx.fillStyle = "rgba(168, 85, 247, 0.6)";
        selectedAgent.path.forEach((wp) => {
          ctx.beginPath();
          ctx.arc(
            (wp.x + 0.5) * cellWidth,
            (wp.y + 0.5) * cellHeight,
            3,
            0,
            2 * Math.PI,
          );
          ctx.fill();
        });
      }
    }

    // Draw objects
    state.objects.forEach((obj) => {
      if (!obj.retrieved) {
        ctx.fillStyle = "#facc15";
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

      if (isSelected) {
        ctx.strokeStyle = "rgba(255, 255, 0, 0.6)";
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius * 1.5, 0, 2 * Math.PI);
        ctx.stroke();
      }
      if (isInCommRange) {
        ctx.strokeStyle = "rgba(34, 197, 94, 0.8)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius * 1.3, 0, 2 * Math.PI);
        ctx.stroke();
      }

      let color = "#fff";
      if (agent.role === "scout") color = "#22c55e";
      else if (agent.role === "coordinator") color = "#3b82f6";
      else if (agent.role === "retriever") color = "#f97316";

      ctx.fillStyle = color;
      if (agent.role === "scout") {
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
        ctx.fill();
      } else if (agent.role === "coordinator") {
        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const angle = (Math.PI / 3) * i;
          const x = centerX + radius * Math.cos(angle);
          const y = centerY + radius * Math.sin(angle);
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.fill();
      } else if (agent.role === "retriever") {
        ctx.fillRect(
          centerX - radius,
          centerY - radius,
          radius * 2,
          radius * 2,
        );
      }

      // Energy bar
      const barWidth = cellWidth * 0.8;
      const barX = agent.x * cellWidth + cellWidth * 0.1;
      const barY = (agent.y + 0.9) * cellHeight;
      ctx.fillStyle = "#334155";
      ctx.fillRect(barX, barY, barWidth, 3);
      const maxEnergy = agent.max_energy || 100;
      const ep = Math.min(agent.energy / maxEnergy, 1);
      ctx.fillStyle = ep > 0.5 ? "#22c55e" : ep > 0.25 ? "#facc15" : "#ef4444";
      ctx.fillRect(barX, barY, barWidth * ep, 3);

      if (agent.carrying > 0) {
        ctx.fillStyle = "#facc15";
        ctx.font = `${cellHeight * 0.3}px Arial`;
        ctx.textAlign = "center";
        ctx.fillText(`${agent.carrying}`, centerX, centerY - radius * 1.5);
      }
    });
  }, [state, width, height, selectedAgentId]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full flex items-center justify-center"
    >
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className="border border-gray-700 rounded-lg"
        style={{ backgroundColor: "#1a1a1a" }}
      />
    </div>
  );
};
