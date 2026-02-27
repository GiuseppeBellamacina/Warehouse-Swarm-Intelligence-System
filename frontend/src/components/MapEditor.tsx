// Map Editor Component - Visual configuration creator

import React, { useState, useRef, useEffect } from "react";
import { SimulationConfig } from "../types/simulation";

type CellType =
  | "free"
  | "obstacle"
  | "warehouse"
  | "entrance"
  | "exit"
  | "object_zone";

type Tool = CellType | "erase";

interface EditorState {
  gridWidth: number;
  gridHeight: number;
  cells: CellType[][];
  tool: Tool;
  scouts: number;
  coordinators: number;
  retrievers: number;
  objectCount: number;
}

export const MapEditor: React.FC<{
  onExport: (config: SimulationConfig) => void;
}> = ({ onExport }) => {
  const [state, setState] = useState<EditorState>({
    gridWidth: 25,
    gridHeight: 25,
    cells: Array(25)
      .fill(null)
      .map(() => Array(25).fill("free")),
    tool: "obstacle",
    scouts: 4,
    coordinators: 2,
    retrievers: 4,
    objectCount: 20,
  });

  const [isDrawing, setIsDrawing] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Initialize grid when dimensions change
  useEffect(() => {
    const newCells = Array(state.gridHeight)
      .fill(null)
      .map(() => Array(state.gridWidth).fill("free"));
    setState((prev) => ({ ...prev, cells: newCells }));
  }, [state.gridWidth, state.gridHeight]);

  // Draw grid
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const cellSize = 20;
    canvas.width = state.gridWidth * cellSize;
    canvas.height = state.gridHeight * cellSize;

    // Clear canvas
    ctx.fillStyle = "#1a1a1a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Draw cells
    for (let y = 0; y < state.gridHeight; y++) {
      for (let x = 0; x < state.gridWidth; x++) {
        const cellType = state.cells[y][x];
        let color = "#2a2a2a";

        switch (cellType) {
          case "obstacle":
            color = "#666";
            break;
          case "warehouse":
            color = "#3b82f6";
            break;
          case "entrance":
            color = "#10b981";
            break;
          case "exit":
            color = "#ef4444";
            break;
          case "object_zone":
            color = "#f59e0b";
            break;
        }

        ctx.fillStyle = color;
        ctx.fillRect(x * cellSize, y * cellSize, cellSize - 1, cellSize - 1);
      }
    }

    // Draw grid lines
    ctx.strokeStyle = "#333";
    ctx.lineWidth = 0.5;
    for (let x = 0; x <= state.gridWidth; x++) {
      ctx.beginPath();
      ctx.moveTo(x * cellSize, 0);
      ctx.lineTo(x * cellSize, canvas.height);
      ctx.stroke();
    }
    for (let y = 0; y <= state.gridHeight; y++) {
      ctx.beginPath();
      ctx.moveTo(0, y * cellSize);
      ctx.lineTo(canvas.width, y * cellSize);
      ctx.stroke();
    }
  }, [state.cells, state.gridWidth, state.gridHeight]);

  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const cellSize = 20;
    const x = Math.floor((e.clientX - rect.left) / cellSize);
    const y = Math.floor((e.clientY - rect.top) / cellSize);

    if (x >= 0 && x < state.gridWidth && y >= 0 && y < state.gridHeight) {
      paintCell(x, y);
    }
  };

  const handleCanvasMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing) return;
    handleCanvasClick(e);
  };

  const paintCell = (x: number, y: number) => {
    setState((prev) => {
      const newCells = prev.cells.map((row) => [...row]);
      newCells[y][x] = prev.tool === "erase" ? "free" : prev.tool;
      return { ...prev, cells: newCells };
    });
  };

  const handleExport = () => {
    // Extract positions for each cell type
    const obstacles: Array<{ x: number; y: number }> = [];
    const warehouseCells: Array<{ x: number; y: number }> = [];
    const entrances: Array<{ x: number; y: number; direction: string }> = [];
    const exits: Array<{ x: number; y: number; direction: string }> = [];
    const objectZones: Array<{ x: number; y: number }> = [];

    for (let y = 0; y < state.gridHeight; y++) {
      for (let x = 0; x < state.gridWidth; x++) {
        const cell = state.cells[y][x];
        switch (cell) {
          case "obstacle":
            obstacles.push({ x, y });
            break;
          case "warehouse":
            warehouseCells.push({ x, y });
            break;
          case "entrance":
            entrances.push({ x, y, direction: "south" });
            break;
          case "exit":
            exits.push({ x, y, direction: "south" });
            break;
          case "object_zone":
            objectZones.push({ x, y });
            break;
        }
      }
    }

    // Find object zone bounds
    let minX = state.gridWidth,
      maxX = 0,
      minY = state.gridHeight,
      maxY = 0;
    if (objectZones.length > 0) {
      objectZones.forEach(({ x, y }) => {
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
      });
    } else {
      // Default to center area
      minX = Math.floor(state.gridWidth * 0.2);
      maxX = Math.floor(state.gridWidth * 0.8);
      minY = Math.floor(state.gridHeight * 0.2);
      maxY = Math.floor(state.gridHeight * 0.8);
    }

    // Build configuration
    const config: SimulationConfig = {
      simulation: {
        grid_width: state.gridWidth,
        grid_height: state.gridHeight,
        timestep_duration_ms: 50,
        max_steps: 5000,
        seed: 42,
      },
      warehouse: {
        position: { x: 1, y: 1 },
        width: 2,
        height: 2,
        warehouse_cells: warehouseCells.length > 0 ? warehouseCells : undefined,
        entrances:
          entrances.length > 0
            ? entrances
            : [
                {
                  x: Math.floor(state.gridWidth / 2),
                  y: 5,
                  direction: "south",
                },
              ],
        exits:
          exits.length > 0
            ? exits
            : [
                {
                  x: Math.floor(state.gridWidth / 2) + 1,
                  y: 5,
                  direction: "south",
                },
              ],
        recharge_rate: 5.0,
      },
      obstacles: obstacles
        .map((obs, idx) => {
          if (
            idx === 0 ||
            obstacles[idx - 1].x !== obs.x ||
            obstacles[idx - 1].y !== obs.y - 1
          ) {
            // Start of new wall segment
            return {
              type: "wall" as const,
              start: { x: obs.x, y: obs.y },
              end: { x: obs.x, y: obs.y },
            };
          }
          return null;
        })
        .filter((w): w is NonNullable<typeof w> => w !== null),
      objects: {
        count: state.objectCount,
        spawn_zones: [
          {
            x_range: [minX, maxX] as [number, number],
            y_range: [minY, maxY] as [number, number],
            probability: 1.0,
          },
        ],
      },
      agents: {
        scouts: {
          count: state.scouts,
          spawn_location: {
            x: Math.floor(state.gridWidth / 2),
            y: Math.floor(state.gridHeight / 2),
          },
          parameters: {
            vision_radius: 5,
            communication_radius: 15,
            max_energy: 100.0,
            speed: 1.5,
            carrying_capacity: 0,
          },
        },
        coordinators: {
          count: state.coordinators,
          spawn_location: {
            x: Math.floor(state.gridWidth / 2),
            y: Math.floor(state.gridHeight / 2),
          },
          parameters: {
            vision_radius: 7,
            communication_radius: 20,
            max_energy: 120.0,
            speed: 1.0,
            carrying_capacity: 0,
          },
        },
        retrievers: {
          count: state.retrievers,
          spawn_location: {
            x: Math.floor(state.gridWidth / 2),
            y: Math.floor(state.gridHeight / 2),
          },
          parameters: {
            vision_radius: 4,
            communication_radius: 12,
            max_energy: 100.0,
            speed: 1.0,
            carrying_capacity: 2,
          },
        },
      },
      logging: {
        enabled: true,
        log_interval: 10,
        metrics: ["coverage", "energy", "objects_retrieved"],
      },
    };

    onExport(config);
  };

  const handleDownloadJSON = () => {
    // Extract config same as handleExport
    handleExport();

    // But also download the file
    const config = buildConfig();
    const blob = new Blob([JSON.stringify(config, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `custom_config_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const buildConfig = () => {
    const obstacles: Array<{ x: number; y: number }> = [];
    const warehouseCells: Array<{ x: number; y: number }> = [];
    const entrances: Array<{ x: number; y: number; direction: string }> = [];
    const exits: Array<{ x: number; y: number; direction: string }> = [];
    const objectZones: Array<{ x: number; y: number }> = [];

    for (let y = 0; y < state.gridHeight; y++) {
      for (let x = 0; x < state.gridWidth; x++) {
        const cell = state.cells[y][x];
        switch (cell) {
          case "obstacle":
            obstacles.push({ x, y });
            break;
          case "warehouse":
            warehouseCells.push({ x, y });
            break;
          case "entrance":
            entrances.push({ x, y, direction: "south" });
            break;
          case "exit":
            exits.push({ x, y, direction: "south" });
            break;
          case "object_zone":
            objectZones.push({ x, y });
            break;
        }
      }
    }

    let minX = state.gridWidth,
      maxX = 0,
      minY = state.gridHeight,
      maxY = 0;
    if (objectZones.length > 0) {
      objectZones.forEach(({ x, y }) => {
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
      });
    } else {
      minX = Math.floor(state.gridWidth * 0.2);
      maxX = Math.floor(state.gridWidth * 0.8);
      minY = Math.floor(state.gridHeight * 0.2);
      maxY = Math.floor(state.gridHeight * 0.8);
    }

    return {
      simulation: {
        grid_width: state.gridWidth,
        grid_height: state.gridHeight,
        timestep_duration_ms: 50,
        max_steps: 5000,
        seed: 42,
      },
      warehouse: {
        position: { x: 1, y: 1 },
        width: 2,
        height: 2,
        warehouse_cells: warehouseCells.length > 0 ? warehouseCells : undefined,
        entrances:
          entrances.length > 0
            ? entrances
            : [
                {
                  x: Math.floor(state.gridWidth / 2),
                  y: 5,
                  direction: "south",
                },
              ],
        exits:
          exits.length > 0
            ? exits
            : [
                {
                  x: Math.floor(state.gridWidth / 2) + 1,
                  y: 5,
                  direction: "south",
                },
              ],
        recharge_rate: 5.0,
      },
      obstacles: obstacles.map((obs) => ({
        type: "wall",
        start: { x: obs.x, y: obs.y },
        end: { x: obs.x, y: obs.y },
      })),
      objects: {
        count: state.objectCount,
        spawn_zones: [
          {
            x_range: [minX, maxX],
            y_range: [minY, maxY],
            probability: 1.0,
          },
        ],
      },
      agents: {
        scouts: {
          count: state.scouts,
          spawn_location: {
            x: Math.floor(state.gridWidth / 2),
            y: Math.floor(state.gridHeight / 2),
          },
          parameters: {
            vision_radius: 5,
            communication_radius: 15,
            max_energy: 100.0,
            speed: 1.5,
            carrying_capacity: 0,
          },
        },
        coordinators: {
          count: state.coordinators,
          spawn_location: {
            x: Math.floor(state.gridWidth / 2),
            y: Math.floor(state.gridHeight / 2),
          },
          parameters: {
            vision_radius: 7,
            communication_radius: 20,
            max_energy: 120.0,
            speed: 1.0,
            carrying_capacity: 0,
          },
        },
        retrievers: {
          count: state.retrievers,
          spawn_location: {
            x: Math.floor(state.gridWidth / 2),
            y: Math.floor(state.gridHeight / 2),
          },
          parameters: {
            vision_radius: 4,
            communication_radius: 12,
            max_energy: 100.0,
            speed: 1.0,
            carrying_capacity: 2,
          },
        },
      },
      logging: {
        enabled: true,
        log_interval: 10,
        metrics: ["coverage", "energy", "objects_retrieved"],
      },
    };
  };

  return (
    <div className="bg-gray-800 rounded-lg p-6 space-y-4">
      <h2 className="text-2xl font-bold mb-4">Map Editor</h2>

      {/* Grid Size Controls */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">Grid Width</label>
          <input
            type="number"
            value={state.gridWidth}
            onChange={(e) =>
              setState({
                ...state,
                gridWidth: Math.max(10, parseInt(e.target.value) || 10),
              })
            }
            className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2"
            min="10"
            max="100"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Grid Height</label>
          <input
            type="number"
            value={state.gridHeight}
            onChange={(e) =>
              setState({
                ...state,
                gridHeight: Math.max(10, parseInt(e.target.value) || 10),
              })
            }
            className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2"
            min="10"
            max="100"
          />
        </div>
      </div>

      {/* Tool Selection */}
      <div>
        <label className="block text-sm font-medium mb-2">Drawing Tool</label>
        <div className="grid grid-cols-3 gap-2">
          {[
            { value: "obstacle", label: "Obstacle", color: "bg-gray-600" },
            { value: "warehouse", label: "Warehouse", color: "bg-blue-600" },
            { value: "entrance", label: "Entrance", color: "bg-green-600" },
            { value: "exit", label: "Exit", color: "bg-red-600" },
            {
              value: "object_zone",
              label: "Object Zone",
              color: "bg-yellow-600",
            },
            { value: "erase", label: "Erase", color: "bg-gray-900" },
          ].map((tool) => (
            <button
              key={tool.value}
              onClick={() => setState({ ...state, tool: tool.value as Tool })}
              className={`${tool.color} ${state.tool === tool.value ? "ring-2 ring-white" : ""} px-3 py-2 rounded font-medium transition`}
            >
              {tool.label}
            </button>
          ))}
        </div>
      </div>

      {/* Canvas */}
      <div className="overflow-auto max-h-[500px] bg-gray-900 rounded border border-gray-700">
        <canvas
          ref={canvasRef}
          onMouseDown={() => setIsDrawing(true)}
          onMouseUp={() => setIsDrawing(false)}
          onMouseLeave={() => setIsDrawing(false)}
          onClick={handleCanvasClick}
          onMouseMove={handleCanvasMouseMove}
          className="cursor-crosshair"
        />
      </div>

      {/* Agent Configuration */}
      <div className="border-t border-gray-700 pt-4">
        <h3 className="text-lg font-semibold mb-3">Agents</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Scouts</label>
            <input
              type="number"
              value={state.scouts}
              onChange={(e) =>
                setState({
                  ...state,
                  scouts: Math.max(0, parseInt(e.target.value) || 0),
                })
              }
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2"
              min="0"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">
              Coordinators
            </label>
            <input
              type="number"
              value={state.coordinators}
              onChange={(e) =>
                setState({
                  ...state,
                  coordinators: Math.max(0, parseInt(e.target.value) || 0),
                })
              }
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2"
              min="0"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Retrievers</label>
            <input
              type="number"
              value={state.retrievers}
              onChange={(e) =>
                setState({
                  ...state,
                  retrievers: Math.max(0, parseInt(e.target.value) || 0),
                })
              }
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2"
              min="0"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Objects</label>
            <input
              type="number"
              value={state.objectCount}
              onChange={(e) =>
                setState({
                  ...state,
                  objectCount: Math.max(1, parseInt(e.target.value) || 1),
                })
              }
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2"
              min="1"
            />
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={handleExport}
          className="flex-1 bg-green-600 hover:bg-green-700 px-4 py-2 rounded font-medium transition"
        >
          Load in Simulation
        </button>
        <button
          onClick={handleDownloadJSON}
          className="flex-1 bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded font-medium transition"
        >
          Download JSON
        </button>
      </div>
    </div>
  );
};
