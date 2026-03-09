// Map Editor Component - Visual configuration creator

import React, { useState, useRef, useEffect } from "react";
import {
  GridScenarioConfig,
  GridWarehouse,
  SimulationAgentsConfig,
  fetchAgentDefaults,
} from "../types/simulation";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8000";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyConfig = Record<string, any>;

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
  onExport: (
    scenario: GridScenarioConfig,
    agents: SimulationAgentsConfig,
  ) => void;
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
  const [availableConfigs, setAvailableConfigs] = useState<string[]>([]);
  const [defaults, setDefaults] = useState<SimulationAgentsConfig | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const importRef = useRef<HTMLInputElement>(null);

  // Fetch available configs and defaults from backend
  useEffect(() => {
    (async () => {
      try {
        const [cfgRes, defs] = await Promise.all([
          fetch(`${BACKEND_URL}/api/configs`)
            .then((r) => (r.ok ? r.json() : { configs: [] }))
            .catch(() => ({ configs: [] })),
          fetchAgentDefaults(BACKEND_URL).catch(() => null),
        ]);
        setAvailableConfigs(cfgRes.configs ?? []);
        if (defs) setDefaults(defs);
      } catch {
        /* ignore */
      }
    })();
  }, []);

  // Initialize grid when dimensions change, but skip if cells already match
  useEffect(() => {
    if (
      state.cells.length === state.gridHeight &&
      (state.cells[0]?.length ?? 0) === state.gridWidth
    ) {
      return;
    }
    const newCells = Array(state.gridHeight)
      .fill(null)
      .map(() => Array(state.gridWidth).fill("free"));
    setState((prev) => ({ ...prev, cells: newCells }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // ---------- JSON import ----------
  const loadFromConfig = (json: AnyConfig) => {
    const size: number = json.metadata?.grid_size ?? 25;
    const height: number = json.grid?.length || size;
    const width: number = json.grid?.[0]?.length || size;

    const newCells: CellType[][] = Array(height)
      .fill(null)
      .map(() => Array(width).fill("free" as CellType));

    // Map grid values: 0=free, 1=obstacle, 2=warehouse, 3=entrance, 4=exit
    if (Array.isArray(json.grid)) {
      for (let row = 0; row < height; row++) {
        for (let col = 0; col < width; col++) {
          const v: number = json.grid[row]?.[col] ?? 0;
          switch (v) {
            case 1:
              newCells[row][col] = "obstacle";
              break;
            case 2:
              newCells[row][col] = "warehouse";
              break;
            case 3:
              newCells[row][col] = "entrance";
              break;
            case 4:
              newCells[row][col] = "exit";
              break;
            default:
              newCells[row][col] = "free";
          }
        }
      }
    }

    // Mark object positions as object_zone
    if (Array.isArray(json.objects)) {
      for (const [row, col] of json.objects) {
        if (row >= 0 && row < height && col >= 0 && col < width)
          newCells[row][col] = "object_zone";
      }
    }

    setState((prev) => ({
      ...prev,
      gridWidth: width,
      gridHeight: height,
      cells: newCells,
      objectCount: json.metadata?.num_objects ?? prev.objectCount,
    }));
  };

  const handleImportJSON = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const json = JSON.parse(ev.target?.result as string);
        loadFromConfig(json);
      } catch {
        alert("File JSON non valido.");
      }
    };
    reader.readAsText(file);
    e.target.value = ""; // reset so the same file can be re-imported
  };
  // ------------------------------------

  const paintCell = (x: number, y: number) => {
    setState((prev) => {
      const newCells = prev.cells.map((row) => [...row]);
      newCells[y][x] = prev.tool === "erase" ? "free" : prev.tool;
      return { ...prev, cells: newCells };
    });
  };

  const handleExport = () => {
    const { scenario, agents } = buildGridConfig();
    onExport(scenario, agents);
  };

  const handleDownloadJSON = () => {
    handleExport();
    const { scenario, agents } = buildGridConfig();
    const combined = { ...scenario, agents };
    const blob = new Blob([JSON.stringify(combined, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `custom_config_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  /**
   * Build a GridScenarioConfig + SimulationAgentsConfig from the current editor state.
   *
   * Grid encoding:
   *   0 = free, 1 = obstacle, 2 = warehouse, 3 = entrance, 4 = exit
   *   object_zone cells → 0 in grid, stored as objects list
   */
  const buildGridConfig = (): {
    scenario: GridScenarioConfig;
    agents: SimulationAgentsConfig;
  } => {
    const warehouseArea: [number, number][] = [];
    const entrances: { row: number; col: number }[] = [];
    const exits: { row: number; col: number }[] = [];
    const objects: [number, number][] = [];

    const gridMatrix: number[][] = [];

    for (let row = 0; row < state.gridHeight; row++) {
      const rowArr: number[] = [];
      for (let col = 0; col < state.gridWidth; col++) {
        const cell = state.cells[row][col];
        switch (cell) {
          case "obstacle":
            rowArr.push(1);
            break;
          case "warehouse":
            rowArr.push(2);
            warehouseArea.push([row, col]);
            break;
          case "entrance":
            rowArr.push(3);
            entrances.push({ row, col });
            break;
          case "exit":
            rowArr.push(4);
            exits.push({ row, col });
            break;
          case "object_zone":
            rowArr.push(0); // not encoded in grid
            if (objects.length < state.objectCount) objects.push([row, col]);
            break;
          default:
            rowArr.push(0);
        }
      }
      gridMatrix.push(rowArr);
    }

    // If no object_zone cells, generate objects in the center area
    if (objects.length === 0) {
      const cRow = Math.floor(state.gridHeight / 2);
      const cCol = Math.floor(state.gridWidth / 2);
      const spread = Math.min(
        5,
        Math.floor(Math.min(state.gridHeight, state.gridWidth) * 0.2),
      );
      for (let i = 0; i < state.objectCount; i++) {
        const r = cRow + Math.floor(Math.random() * (spread * 2 + 1)) - spread;
        const c = cCol + Math.floor(Math.random() * (spread * 2 + 1)) - spread;
        if (r >= 0 && r < state.gridHeight && c >= 0 && c < state.gridWidth) {
          objects.push([r, c]);
        }
      }
    }

    // Build warehouse records — pair entrances with exits
    const warehouses: GridWarehouse[] = entrances.map((ent, idx) => {
      const ex = exits[idx] ?? ent;
      return {
        id: idx,
        side: "south",
        entrance: [ent.row, ent.col],
        exit: [ex.row, ex.col],
        area: warehouseArea,
      };
    });

    // If no entrances defined, create a default one at top-left area
    if (warehouses.length === 0) {
      warehouses.push({
        id: 0,
        side: "south",
        entrance: [2, Math.floor(state.gridWidth / 2)],
        exit: [2, Math.floor(state.gridWidth / 2) + 1],
        area: warehouseArea,
      });
    }

    const gridSize = Math.max(state.gridWidth, state.gridHeight);

    const scenario: GridScenarioConfig = {
      metadata: {
        grid_size: gridSize,
        num_warehouses: warehouses.length,
        num_objects: objects.length,
        max_steps: 500,
        seed: 42,
      },
      grid: gridMatrix,
      warehouses,
      objects,
    };

    const agents: SimulationAgentsConfig = defaults
      ? {
          scouts: {
            ...defaults.scouts,
            count: state.scouts,
          },
          coordinators: {
            ...defaults.coordinators,
            count: state.coordinators,
          },
          retrievers: {
            ...defaults.retrievers,
            count: state.retrievers,
          },
          scout_behavior: { ...defaults.scout_behavior },
          coordinator_behavior: { ...defaults.coordinator_behavior },
          retriever_behavior: { ...defaults.retriever_behavior },
        }
      : {
          scouts: {
            count: state.scouts,
            vision_radius: 3,
            communication_radius: 2,
            max_energy: 500,
            speed: 2,
            carrying_capacity: 0,
          },
          coordinators: {
            count: state.coordinators,
            vision_radius: 2,
            communication_radius: 3,
            max_energy: 500,
            speed: 1.0,
            carrying_capacity: 0,
          },
          retrievers: {
            count: state.retrievers,
            vision_radius: 2,
            communication_radius: 2,
            max_energy: 500,
            speed: 1.0,
            carrying_capacity: 2,
          },
          scout_behavior: {} as SimulationAgentsConfig["scout_behavior"],
          coordinator_behavior:
            {} as SimulationAgentsConfig["coordinator_behavior"],
          retriever_behavior:
            {} as SimulationAgentsConfig["retriever_behavior"],
        };

    return { scenario, agents };
  };

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold tracking-wide uppercase text-gray-300">
          Map Editor
        </h2>
        <div>
          <input
            ref={importRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={handleImportJSON}
          />
          <button
            onClick={() => importRef.current?.click()}
            className="bg-indigo-600/80 hover:bg-indigo-500/80 px-3 py-1.5 rounded-lg font-medium transition-colors text-xs"
            title="Import a JSON configuration to pre-populate the map"
          >
            Import JSON
          </button>
        </div>
      </div>

      {/* Preset selector — loaded from backend configs */}
      {availableConfigs.length > 0 && (
        <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-3">
          <p className="text-[10px] font-medium text-gray-500 uppercase tracking-widest mb-2">
            Presets
          </p>
          <div className="flex flex-wrap gap-1.5">
            {availableConfigs.map((name) => (
              <button
                key={name}
                onClick={async () => {
                  try {
                    const res = await fetch(
                      `${BACKEND_URL}/configs/${name}.json`,
                    );
                    if (!res.ok) return;
                    const cfg = await res.json();
                    loadFromConfig(cfg);
                  } catch {
                    /* ignore */
                  }
                }}
                className="bg-gray-700/60 hover:bg-gray-600/60 border border-gray-600/40 hover:border-indigo-500/40 px-2.5 py-1.5 rounded-md text-xs font-medium transition-all duration-200"
              >
                {name
                  .replace(/_/g, " ")
                  .replace(/\b\w/g, (l) => l.toUpperCase())}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Grid Size Controls */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-[10px] font-medium text-gray-500 uppercase tracking-widest mb-1">
            Width
          </label>
          <input
            type="number"
            value={state.gridWidth}
            onChange={(e) =>
              setState({
                ...state,
                gridWidth: Math.max(10, parseInt(e.target.value) || 10),
              })
            }
            className="w-full bg-gray-800/80 border border-gray-600/60 rounded-md px-3 py-1.5 text-xs
                       focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 focus:outline-none transition-colors"
            min="10"
            max="100"
          />
        </div>
        <div>
          <label className="block text-[10px] font-medium text-gray-500 uppercase tracking-widest mb-1">
            Height
          </label>
          <input
            type="number"
            value={state.gridHeight}
            onChange={(e) =>
              setState({
                ...state,
                gridHeight: Math.max(10, parseInt(e.target.value) || 10),
              })
            }
            className="w-full bg-gray-800/80 border border-gray-600/60 rounded-md px-3 py-1.5 text-xs
                       focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 focus:outline-none transition-colors"
            min="10"
            max="100"
          />
        </div>
      </div>

      {/* Tool Selection */}
      <div>
        <label className="block text-[10px] font-medium text-gray-500 uppercase tracking-widest mb-1.5">
          Drawing Tool
        </label>
        <div className="grid grid-cols-3 gap-1.5">
          {[
            {
              value: "obstacle",
              label: "Obstacle",
              color: "bg-gray-600/80 hover:bg-gray-500/80",
            },
            {
              value: "warehouse",
              label: "Warehouse",
              color: "bg-blue-600/80 hover:bg-blue-500/80",
            },
            {
              value: "entrance",
              label: "Entrance",
              color: "bg-emerald-600/80 hover:bg-emerald-500/80",
            },
            {
              value: "exit",
              label: "Exit",
              color: "bg-red-600/80 hover:bg-red-500/80",
            },
            {
              value: "object_zone",
              label: "Object Zone",
              color: "bg-yellow-600/80 hover:bg-yellow-500/80",
            },
            {
              value: "erase",
              label: "Erase",
              color: "bg-gray-800/80 hover:bg-gray-700/80",
            },
          ].map((tool) => (
            <button
              key={tool.value}
              onClick={() => setState({ ...state, tool: tool.value as Tool })}
              className={`${tool.color} ${state.tool === tool.value ? "ring-1 ring-white/60 ring-offset-1 ring-offset-gray-950" : ""} px-2.5 py-1.5 rounded-md font-medium transition-all text-xs`}
            >
              {tool.label}
            </button>
          ))}
        </div>
      </div>

      {/* Canvas */}
      <div className="overflow-auto max-h-[500px] bg-gray-950/60 rounded-lg border border-gray-700/40">
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
      <div className="border-t border-gray-800/60 pt-3">
        <h3 className="text-[10px] font-medium text-gray-500 uppercase tracking-widest mb-2">
          Agents
        </h3>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-[10px] text-gray-400 mb-0.5">
              Scouts
            </label>
            <input
              type="number"
              value={state.scouts}
              onChange={(e) =>
                setState({
                  ...state,
                  scouts: Math.max(0, parseInt(e.target.value) || 0),
                })
              }
              className="w-full bg-gray-800/80 border border-gray-600/60 rounded-md px-3 py-1.5 text-xs
                         focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 focus:outline-none transition-colors"
              min="0"
            />
          </div>
          <div>
            <label className="block text-[10px] text-gray-400 mb-0.5">
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
              className="w-full bg-gray-800/80 border border-gray-600/60 rounded-md px-3 py-1.5 text-xs
                         focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 focus:outline-none transition-colors"
              min="0"
            />
          </div>
          <div>
            <label className="block text-[10px] text-gray-400 mb-0.5">
              Retrievers
            </label>
            <input
              type="number"
              value={state.retrievers}
              onChange={(e) =>
                setState({
                  ...state,
                  retrievers: Math.max(0, parseInt(e.target.value) || 0),
                })
              }
              className="w-full bg-gray-800/80 border border-gray-600/60 rounded-md px-3 py-1.5 text-xs
                         focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 focus:outline-none transition-colors"
              min="0"
            />
          </div>
          <div>
            <label className="block text-[10px] text-gray-400 mb-0.5">
              Objects
            </label>
            <input
              type="number"
              value={state.objectCount}
              onChange={(e) =>
                setState({
                  ...state,
                  objectCount: Math.max(1, parseInt(e.target.value) || 1),
                })
              }
              className="w-full bg-gray-800/80 border border-gray-600/60 rounded-md px-3 py-1.5 text-xs
                         focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 focus:outline-none transition-colors"
              min="1"
            />
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={handleExport}
          className="flex-1 bg-gradient-to-r from-emerald-600 to-emerald-700 hover:from-emerald-500 hover:to-emerald-600
                     py-2 rounded-lg font-semibold transition-all text-xs shadow-md shadow-emerald-900/30"
        >
          Load in Simulation
        </button>
        <button
          onClick={handleDownloadJSON}
          className="flex-1 bg-gradient-to-r from-purple-600 to-purple-700 hover:from-purple-500 hover:to-purple-600
                     py-2 rounded-lg font-semibold transition-all text-xs shadow-md shadow-purple-900/30"
        >
          Download JSON
        </button>
      </div>
    </div>
  );
};
