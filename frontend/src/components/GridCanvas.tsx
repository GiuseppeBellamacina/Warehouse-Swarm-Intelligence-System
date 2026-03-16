// Grid Canvas Component for visualization

import React, {
  useRef,
  useEffect,
  useState,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from "react";
import { SimulationState, WallData, BoxData } from "../types/simulation";

/** Duration of the position interpolation in ms */
const LERP_DURATION = 180;

/** Ease-out cubic for smooth deceleration */
const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

interface AgentPos {
  x: number;
  y: number;
}

/** Per-agent trail: maps agent id → ordered list of visited positions */
export type TrailHistory = Map<number, { x: number; y: number }[]>;

interface GridCanvasProps {
  state: SimulationState;
  selectedAgentId?: number | null;
  onSelectAgent?: (agentId: number | null) => void;
  /** Accumulated position history per agent */
  trailHistory?: TrailHistory;
  /** Whether trails are rendered (when no agent is selected) */
  showTrails?: boolean;
}

export interface GridCanvasHandle {
  /** Returns the underlying <canvas> element for snapshot export */
  getCanvas: () => HTMLCanvasElement | null;
}

export const GridCanvas = forwardRef<GridCanvasHandle, GridCanvasProps>(
  (
    {
      state,
      selectedAgentId = null,
      onSelectAgent,
      trailHistory,
      showTrails = true,
    },
    ref,
  ) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [size, setSize] = useState({ w: 400, h: 400 });

    useImperativeHandle(ref, () => ({
      getCanvas: () => canvasRef.current,
    }));

    // Animation refs
    const prevPosRef = useRef<Map<number, AgentPos>>(new Map());
    const targetPosRef = useRef<Map<number, AgentPos>>(new Map());
    const animStartRef = useRef<number>(0);
    const rafRef = useRef<number>(0);
    const lastStepRef = useRef<number>(-1);

    // Auto-size: observe container and update canvas dimensions
    useEffect(() => {
      if (!containerRef.current) return;
      const observer = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const { width, height } = entry.contentRect;
          if (width > 0 && height > 0) {
            const side = Math.floor(Math.min(width, height));
            setSize({ w: side, h: side });
          }
        }
      });
      observer.observe(containerRef.current);
      return () => observer.disconnect();
    }, []);

    const { w: width, h: height } = size;

    // When state changes, snapshot current interpolated positions as "prev"
    // and set new targets. On reset (step goes backward) snap immediately.
    useEffect(() => {
      if (!state) return;
      const now = performance.now();
      const prev = prevPosRef.current;
      const target = targetPosRef.current;
      const jumped =
        state.step <= lastStepRef.current && lastStepRef.current > 0;
      lastStepRef.current = state.step;

      for (const agent of state.agents) {
        if (jumped) {
          // Reset / timeline jump: snap directly, no interpolation
          prev.set(agent.id, { x: agent.x, y: agent.y });
        } else {
          const old = target.get(agent.id);
          prev.set(agent.id, old ?? { x: agent.x, y: agent.y });
        }
        target.set(agent.id, { x: agent.x, y: agent.y });
      }
      animStartRef.current = now;
    }, [state]);

    /** Get the interpolated position for an agent at a given timestamp */
    const getLerpPos = useCallback(
      (agentId: number, now: number): AgentPos => {
        const p = prevPosRef.current.get(agentId);
        const t = targetPosRef.current.get(agentId);
        if (!p || !t) {
          const agent = state.agents.find((a) => a.id === agentId);
          return agent ? { x: agent.x, y: agent.y } : { x: 0, y: 0 };
        }
        const elapsed = now - animStartRef.current;
        const rawT = Math.min(elapsed / LERP_DURATION, 1);
        const eased = easeOutCubic(rawT);
        return {
          x: p.x + (t.x - p.x) * eased,
          y: p.y + (t.y - p.y) * eased,
        };
      },
      [state],
    );

    // Click handler: convert pixel coords to grid coords and find nearest agent
    const handleCanvasClick = useCallback(
      (e: React.MouseEvent<HTMLCanvasElement>) => {
        if (!onSelectAgent || !state || !state.grid) return;
        const canvas = canvasRef.current;
        if (!canvas) return;
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const px = (e.clientX - rect.left) * scaleX;
        const py = (e.clientY - rect.top) * scaleY;
        const cellWidth = width / state.grid.width;
        const cellHeight = height / state.grid.height;
        const now = performance.now();
        let bestId: number | null = null;
        let bestDist = Infinity;
        for (const agent of state.agents) {
          const lp = getLerpPos(agent.id, now);
          const ax = (lp.x + 0.5) * cellWidth;
          const ay = (lp.y + 0.5) * cellHeight;
          const dist = Math.sqrt((px - ax) ** 2 + (py - ay) ** 2);
          const hitRadius = Math.min(cellWidth, cellHeight) * 0.7;
          if (dist < hitRadius && dist < bestDist) {
            bestDist = dist;
            bestId = agent.id;
          }
        }
        onSelectAgent(bestId === selectedAgentId ? null : bestId);
      },
      [onSelectAgent, state, width, height, selectedAgentId, getLerpPos],
    );

    // ── Main draw function (extracted so it can be called from rAF loop) ──
    const draw = useCallback(
      (now: number) => {
        if (!canvasRef.current || !state || !state.grid) return;

        const canvas = canvasRef.current;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        const gridWidth = state.grid.width;
        const gridHeight = state.grid.height;
        const cellWidth = width / gridWidth;
        const cellHeight = height / gridHeight;

        // Find selected agent and agents in communication range
        const selectedAgent =
          selectedAgentId != null
            ? state.agents.find((a) => a.id === selectedAgentId)
            : null;

        // Interpolated position of the selected agent (for radii / path origin)
        const selectedLerp = selectedAgent
          ? getLerpPos(selectedAgent.id, now)
          : null;

        const agentsInCommRange = new Set<number>();
        if (selectedAgent && selectedLerp) {
          state.agents.forEach((agent) => {
            if (agent.id === selectedAgent.id) return;
            const aPos = getLerpPos(agent.id, now);
            const dx = aPos.x - selectedLerp.x;
            const dy = aPos.y - selectedLerp.y;
            const distance = Math.sqrt(dx * dx + dy * dy);
            if (distance <= selectedAgent.communication_radius) {
              agentsInCommRange.add(agent.id);
            }
          });
        }

        // Clear canvas
        ctx.fillStyle = "#0c0e14";
        ctx.fillRect(0, 0, width, height);

        // ── Determine the fog-of-war mask to use ──
        // When an agent is selected we show that agent's personal explored map,
        // otherwise we show the global union of all agents' explored maps.
        const fogMask: number[] | undefined = selectedAgent
          ? selectedAgent.explored
          : state.global_explored;

        // Helper: is cell (x,y) explored according to the active fog mask?
        const isExplored = (x: number, y: number): boolean => {
          if (!fogMask) return true; // no data → treat everything as explored
          return fogMask[y * gridWidth + x] === 1;
        };

        // Draw grid lines (subtle)
        ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
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
              ctx.fillRect(
                x * cellWidth,
                y * cellHeight,
                cellWidth,
                cellHeight,
              );
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

        // ── Fog-of-war overlay ──
        // Two visual modes for the fog:
        // 1. map_known: terrain cells are always visible; a light amber
        //    tint marks cells not yet scanned by agent vision (objects may
        //    still be hiding there).
        // 2. unknown: heavy dark fog on truly unexplored cells.
        // NOTE: in both modes the *exploration heuristics* are identical
        // (frontier detection uses local_map==0).  The only backend
        // difference is that map_known gives A* full-grid access and
        // pre-fills warehouse knowledge.
        const mapKnown = !!state.map_known;

        if (mapKnown) {
          // Use the object-scan mask (vision_explored) for amber tint
          const scanMask: number[] | undefined = selectedAgent
            ? selectedAgent.object_explored
            : state.global_object_explored;

          const isScanned = (x: number, y: number): boolean => {
            if (!scanMask) return true;
            return scanMask[y * gridWidth + x] === 1;
          };

          if (scanMask) {
            for (let gy = 0; gy < gridHeight; gy++) {
              for (let gx = 0; gx < gridWidth; gx++) {
                const px = gx * cellWidth;
                const py = gy * cellHeight;

                if (!isScanned(gx, gy)) {
                  ctx.fillStyle = "rgba(180, 140, 60, 0.18)";
                  ctx.fillRect(px, py, cellWidth, cellHeight);

                  ctx.fillStyle = "rgba(250, 200, 50, 0.15)";
                  ctx.beginPath();
                  ctx.arc(
                    px + cellWidth / 2,
                    py + cellHeight / 2,
                    Math.min(cellWidth, cellHeight) * 0.12,
                    0,
                    2 * Math.PI,
                  );
                  ctx.fill();
                }
              }
            }
          }
        } else if (fogMask) {
          const fogAlpha = selectedAgent ? 0.82 : 0.65;

          for (let gy = 0; gy < gridHeight; gy++) {
            for (let gx = 0; gx < gridWidth; gx++) {
              const px = gx * cellWidth;
              const py = gy * cellHeight;

              if (!isExplored(gx, gy)) {
                // --- Unexplored: dark fill + diagonal hash lines ---
                ctx.fillStyle = `rgba(0, 0, 0, ${fogAlpha})`;
                ctx.fillRect(px, py, cellWidth, cellHeight);

                // Diagonal hash pattern
                ctx.save();
                ctx.beginPath();
                ctx.rect(px, py, cellWidth, cellHeight);
                ctx.clip();
                ctx.strokeStyle = `rgba(255, 255, 255, 0.06)`;
                ctx.lineWidth = 0.5;
                const step = Math.max(4, cellWidth / 3);
                for (
                  let d = -cellHeight;
                  d < cellWidth + cellHeight;
                  d += step
                ) {
                  ctx.beginPath();
                  ctx.moveTo(px + d, py);
                  ctx.lineTo(px + d - cellHeight, py + cellHeight);
                  ctx.stroke();
                }
                ctx.restore();
              } else {
                // --- Explored: subtle bright tint to pop against the dark fog ---
                ctx.fillStyle = "rgba(200, 220, 255, 0.07)";
                ctx.fillRect(px, py, cellWidth, cellHeight);
              }
            }
          }
        }

        // ── Per-agent known warehouses (when an agent is selected) ──
        if (selectedAgent && selectedAgent.known_warehouses) {
          ctx.strokeStyle = "rgba(59, 130, 246, 1)";
          ctx.lineWidth = 2;
          selectedAgent.known_warehouses.forEach((wc) => {
            ctx.strokeRect(
              wc.x * cellWidth + 1,
              wc.y * cellHeight + 1,
              cellWidth - 2,
              cellHeight - 2,
            );
          });
        }

        // Draw objects (global truth) — drawn before known_objects so markers overlay correctly
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

        // ── Per-agent known objects (when an agent is selected) ──
        if (selectedAgent && selectedAgent.known_objects) {
          selectedAgent.known_objects.forEach((obj) => {
            const ox = (obj.x + 0.5) * cellWidth;
            const oy = (obj.y + 0.5) * cellHeight;
            const r = Math.min(cellWidth, cellHeight) * 0.38;

            // Outer glow
            ctx.shadowColor = "#facc15";
            ctx.shadowBlur = 10;

            // Bright filled circle
            ctx.fillStyle = "rgba(250, 204, 21, 0.85)";
            ctx.beginPath();
            ctx.arc(ox, oy, r, 0, 2 * Math.PI);
            ctx.fill();

            // Thick white border
            ctx.strokeStyle = "#fff";
            ctx.lineWidth = 2.5;
            ctx.stroke();

            // Reset shadow
            ctx.shadowColor = "transparent";
            ctx.shadowBlur = 0;

            // Inner marker: black star/dot for contrast
            ctx.fillStyle = "#000";
            ctx.beginPath();
            ctx.arc(ox, oy, r * 0.35, 0, 2 * Math.PI);
            ctx.fill();
          });
        }

        // ── Per-agent dubious objects (when an agent is selected) ──
        if (selectedAgent && selectedAgent.dubious_objects) {
          selectedAgent.dubious_objects.forEach((obj) => {
            const ox = (obj.x + 0.5) * cellWidth;
            const oy = (obj.y + 0.5) * cellHeight;
            const r = Math.min(cellWidth, cellHeight) * 0.38;

            // Dashed purple circle (uncertain)
            ctx.setLineDash([3, 3]);
            ctx.strokeStyle = "rgba(147, 51, 234, 0.95)";
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            ctx.arc(ox, oy, r, 0, 2 * Math.PI);
            ctx.stroke();

            // Semi-transparent fill
            ctx.fillStyle = "rgba(147, 51, 234, 0.2)";
            ctx.fill();

            // Reset dash
            ctx.setLineDash([]);

            // "?" marker in center
            ctx.fillStyle = "rgba(147, 51, 234, 1.0)";
            ctx.font = `bold ${Math.round(r * 1.2)}px sans-serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText("?", ox, oy + 1);
          });
        }

        // ── Per-agent verifying objects (dubious promoted to task queue) ──
        if (selectedAgent && selectedAgent.verifying_objects) {
          selectedAgent.verifying_objects.forEach((obj) => {
            const ox = (obj.x + 0.5) * cellWidth;
            const oy = (obj.y + 0.5) * cellHeight;
            const r = Math.min(cellWidth, cellHeight) * 0.38;

            // Outer glow — red
            ctx.shadowColor = "#dc2626";
            ctx.shadowBlur = 8;

            // Semi-transparent red fill
            ctx.fillStyle = "rgba(220, 38, 38, 0.25)";
            ctx.beginPath();
            ctx.arc(ox, oy, r, 0, 2 * Math.PI);
            ctx.fill();

            // Dashed red border
            ctx.setLineDash([4, 3]);
            ctx.strokeStyle = "rgba(220, 38, 38, 0.95)";
            ctx.lineWidth = 2.5;
            ctx.stroke();

            ctx.shadowColor = "transparent";
            ctx.shadowBlur = 0;
            ctx.setLineDash([]);

            // Crosshair — conveys "going to check this"
            const arm = r * 0.45;
            ctx.strokeStyle = "rgba(220, 38, 38, 1.0)";
            ctx.lineWidth = 1.8;
            ctx.beginPath();
            ctx.moveTo(ox - arm, oy);
            ctx.lineTo(ox + arm, oy);
            ctx.moveTo(ox, oy - arm);
            ctx.lineTo(ox, oy + arm);
            ctx.stroke();

            // Small centre dot
            ctx.fillStyle = "rgba(220, 38, 38, 1.0)";
            ctx.beginPath();
            ctx.arc(ox, oy, r * 0.12, 0, 2 * Math.PI);
            ctx.fill();
          });
        }

        // Draw selected agent vision / comm radii
        if (selectedAgent && selectedLerp) {
          const centerX = (selectedLerp.x + 0.5) * cellWidth;
          const centerY = (selectedLerp.y + 0.5) * cellHeight;

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

        // ── Draw agent trails ──
        if (trailHistory && trailHistory.size > 0) {
          const roleColor: Record<string, string> = {
            scout: "#22c55e",
            coordinator: "#3b82f6",
            retriever: "#f97316",
          };

          // Collect which agents to draw trails for
          const trailAgents: typeof state.agents =
            selectedAgentId != null
              ? state.agents.filter((a) => a.id === selectedAgentId)
              : showTrails
                ? state.agents
                : [];

          // Build per-cell occupancy map for offset computation
          // key = "x,y" → array of agent ids that visited
          const cellVisitors = new Map<string, number[]>();
          for (const agent of trailAgents) {
            const trail = trailHistory.get(agent.id);
            if (!trail) continue;
            for (const pos of trail) {
              const key = `${pos.x},${pos.y}`;
              let arr = cellVisitors.get(key);
              if (!arr) {
                arr = [];
                cellVisitors.set(key, arr);
              }
              if (!arr.includes(agent.id)) arr.push(agent.id);
            }
          }

          // Offset patterns for overlapping dots (subdivide cell into quadrants)
          const offsetPatterns: [number, number][] = [
            [0, 0],
            [-0.2, -0.2],
            [0.2, -0.2],
            [-0.2, 0.2],
            [0.2, 0.2],
            [0, -0.25],
            [0, 0.25],
            [-0.25, 0],
            [0.25, 0],
          ];

          for (const agent of trailAgents) {
            const trail = trailHistory.get(agent.id);
            if (!trail || trail.length === 0) continue;

            const color = roleColor[agent.role] ?? "#fff";
            const dotR = Math.min(cellWidth, cellHeight) * 0.12;

            for (const pos of trail) {
              const key = `${pos.x},${pos.y}`;
              const visitors = cellVisitors.get(key) ?? [agent.id];
              const idx = visitors.indexOf(agent.id);
              const [ox, oy] =
                visitors.length > 1
                  ? offsetPatterns[idx % offsetPatterns.length]
                  : [0, 0];

              ctx.fillStyle = color;
              ctx.globalAlpha = 0.35;
              ctx.beginPath();
              ctx.arc(
                (pos.x + 0.5 + ox) * cellWidth,
                (pos.y + 0.5 + oy) * cellHeight,
                dotR,
                0,
                2 * Math.PI,
              );
              ctx.fill();
            }
            ctx.globalAlpha = 1.0;
          }
        }

        // Draw agents
        state.agents.forEach((agent) => {
          const lerpPos = getLerpPos(agent.id, now);
          const centerX = (lerpPos.x + 0.5) * cellWidth;
          const centerY = (lerpPos.y + 0.5) * cellHeight;
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
          const barX = lerpPos.x * cellWidth + cellWidth * 0.1;
          const barY = (lerpPos.y + 0.9) * cellHeight;
          ctx.fillStyle = "#334155";
          ctx.fillRect(barX, barY, barWidth, 3);
          const maxEnergy = agent.max_energy || 100;
          const ep = Math.min(agent.energy / maxEnergy, 1);
          ctx.fillStyle =
            ep > 0.5 ? "#22c55e" : ep > 0.25 ? "#facc15" : "#ef4444";
          ctx.fillRect(barX, barY, barWidth * ep, 3);

          // Agent number label (type_index) drawn on the agent body
          const labelSize = Math.max(radius * 0.9, 7);
          ctx.shadowColor = "rgba(0,0,0,0.7)";
          ctx.shadowBlur = 3;
          ctx.fillStyle = "#fff";
          ctx.font = `bold ${labelSize}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(`${agent.type_index}`, centerX, centerY);
          ctx.shadowColor = "transparent";
          ctx.shadowBlur = 0;

          if (agent.carrying > 0) {
            ctx.fillStyle = "#facc15";
            ctx.font = `${cellHeight * 0.3}px Arial`;
            ctx.textAlign = "center";
            ctx.textBaseline = "alphabetic";
            ctx.fillText(`${agent.carrying}`, centerX, centerY - radius * 1.5);
          }
        });
      },
      [
        state,
        width,
        height,
        selectedAgentId,
        getLerpPos,
        trailHistory,
        showTrails,
      ],
    );

    // ── Animation loop ──
    useEffect(() => {
      if (!state || !state.grid) return;

      let running = true;
      const loop = (now: number) => {
        if (!running) return;
        draw(now);
        // Keep looping while interpolation is in progress
        const elapsed = now - animStartRef.current;
        if (elapsed < LERP_DURATION) {
          rafRef.current = requestAnimationFrame(loop);
        }
      };

      // Kick off the animation
      rafRef.current = requestAnimationFrame(loop);

      return () => {
        running = false;
        cancelAnimationFrame(rafRef.current);
      };
    }, [state, width, height, selectedAgentId, draw, trailHistory, showTrails]);

    return (
      <div
        ref={containerRef}
        className="w-full h-full flex items-center justify-center"
      >
        <canvas
          ref={canvasRef}
          width={width}
          height={height}
          className="border border-gray-800/60 rounded-xl shadow-2xl shadow-black/30"
          style={{
            backgroundColor: "#0c0e14",
            cursor: onSelectAgent ? "pointer" : undefined,
          }}
          onClick={handleCanvasClick}
        />
      </div>
    );
  },
);

GridCanvas.displayName = "GridCanvas";
