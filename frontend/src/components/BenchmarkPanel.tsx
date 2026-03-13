// Benchmark Panel — recording controls, run history, comparison charts, PNG export

import React, {
  useState,
  useRef,
  useCallback,
  useMemo,
  useEffect,
} from "react";
import { createPortal } from "react-dom";
import { BenchmarkRun, BenchmarkSnapshot } from "../hooks/useBenchmark";
import { SimulationAgentsConfig } from "../types/simulation";

// ── Theme palette ────────────────────────────────────────────────────────────

type ChartTheme = "dark" | "light";

const THEME = {
  dark: {
    bg: "#111318",
    title: "#e5e7eb",
    axisLabel: "#9ca3af",
    tickLabel: "#6b7280",
    grid: "#374151",
    axis: "#4b5563",
    legend: "#d1d5db",
    dotStroke: "#111318",
  },
  light: {
    bg: "#ffffff",
    title: "#111827",
    axisLabel: "#4b5563",
    tickLabel: "#6b7280",
    grid: "#e5e7eb",
    axis: "#9ca3af",
    legend: "#374151",
    dotStroke: "#ffffff",
  },
} as const;

// ── Tiny SVG chart renderer (no external dep) ───────────────────────────────

interface ChartSeries {
  label: string;
  color: string;
  data: { x: number; y: number }[];
}

interface ChartProps {
  title: string;
  series: ChartSeries[];
  yLabel: string;
  xLabel?: string;
  width?: number;
  height?: number;
  theme?: ChartTheme;
}

const CHART_COLORS = [
  "#3b82f6", // blue
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ef4444", // red
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#f97316", // orange
];

function pickColor(index: number) {
  return CHART_COLORS[index % CHART_COLORS.length];
}

const SVGChart: React.FC<ChartProps> = ({
  title,
  series,
  yLabel,
  xLabel = "Step",
  width = 620,
  height = 320,
  theme = "dark",
}) => {
  const t = THEME[theme];
  const pad = { top: 40, right: 20, bottom: 52, left: 58 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  // Compute bounds
  let xMin = Infinity,
    xMax = -Infinity,
    yMin = Infinity,
    yMax = -Infinity;
  for (const s of series) {
    for (const d of s.data) {
      if (d.x < xMin) xMin = d.x;
      if (d.x > xMax) xMax = d.x;
      if (d.y < yMin) yMin = d.y;
      if (d.y > yMax) yMax = d.y;
    }
  }
  if (!isFinite(xMin)) {
    xMin = 0;
    xMax = 1;
    yMin = 0;
    yMax = 1;
  }
  // Add 5% padding on Y
  const yRange = yMax - yMin || 1;
  yMin = Math.max(0, yMin - yRange * 0.05);
  yMax = yMax + yRange * 0.05;

  const sx = (x: number) => pad.left + ((x - xMin) / (xMax - xMin || 1)) * w;
  const sy = (y: number) => pad.top + h - ((y - yMin) / (yMax - yMin || 1)) * h;

  // Grid lines & ticks (5 each axis)
  const yTicks: number[] = [];
  for (let i = 0; i <= 4; i++) yTicks.push(yMin + (yRange * i) / 4);
  const xTicks: number[] = [];
  const xRange = xMax - xMin || 1;
  for (let i = 0; i <= 4; i++) xTicks.push(Math.round(xMin + (xRange * i) / 4));

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="w-full h-auto"
      style={{ background: t.bg, borderRadius: 8 }}
    >
      {/* Title */}
      <text
        x={width / 2}
        y={22}
        textAnchor="middle"
        fill={t.title}
        fontSize={13}
        fontWeight={700}
      >
        {title}
      </text>

      {/* Y axis label */}
      <text
        x={14}
        y={pad.top + h / 2}
        textAnchor="middle"
        fill={t.axisLabel}
        fontSize={10}
        transform={`rotate(-90, 14, ${pad.top + h / 2})`}
      >
        {yLabel}
      </text>

      {/* X axis label */}
      <text
        x={pad.left + w / 2}
        y={height - 6}
        textAnchor="middle"
        fill={t.axisLabel}
        fontSize={10}
      >
        {xLabel}
      </text>

      {/* Grid + tick labels */}
      {yTicks.map((v, i) => (
        <g key={`y${i}`}>
          <line
            x1={pad.left}
            x2={pad.left + w}
            y1={sy(v)}
            y2={sy(v)}
            stroke={t.grid}
            strokeWidth={0.5}
          />
          <text
            x={pad.left - 6}
            y={sy(v) + 3}
            textAnchor="end"
            fill={t.tickLabel}
            fontSize={9}
          >
            {Number.isInteger(v) ? v : v.toFixed(1)}
          </text>
        </g>
      ))}
      {xTicks.map((v, i) => (
        <g key={`x${i}`}>
          <line
            x1={sx(v)}
            x2={sx(v)}
            y1={pad.top}
            y2={pad.top + h}
            stroke={t.grid}
            strokeWidth={0.5}
          />
          <text
            x={sx(v)}
            y={pad.top + h + 14}
            textAnchor="middle"
            fill={t.tickLabel}
            fontSize={9}
          >
            {v}
          </text>
        </g>
      ))}

      {/* Axes */}
      <line
        x1={pad.left}
        x2={pad.left}
        y1={pad.top}
        y2={pad.top + h}
        stroke={t.axis}
        strokeWidth={1}
      />
      <line
        x1={pad.left}
        x2={pad.left + w}
        y1={pad.top + h}
        y2={pad.top + h}
        stroke={t.axis}
        strokeWidth={1}
      />

      {/* Series lines */}
      {series.map((s, si) => {
        if (s.data.length < 2) return null;
        const points = s.data.map((d) => `${sx(d.x)},${sy(d.y)}`).join(" ");
        return (
          <polyline
            key={si}
            points={points}
            fill="none"
            stroke={s.color}
            strokeWidth={1.5}
            strokeLinejoin="round"
          />
        );
      })}

      {/* Legend */}
      {series.length > 0 && (
        <g>
          {series.map((s, si) => {
            const lx = pad.left + 8;
            const ly = pad.top + 10 + si * 14;
            return (
              <g key={si}>
                <line
                  x1={lx}
                  x2={lx + 16}
                  y1={ly}
                  y2={ly}
                  stroke={s.color}
                  strokeWidth={2}
                />
                <text x={lx + 20} y={ly + 3} fill={t.legend} fontSize={9}>
                  {s.label}
                </text>
              </g>
            );
          })}
        </g>
      )}
    </svg>
  );
};

// ── SVG bar chart ────────────────────────────────────────────────────────────

interface BarChartBar {
  label: string;
  value: number;
  color: string;
}

interface BarChartProps {
  title: string;
  bars: BarChartBar[];
  yLabel: string;
  width?: number;
  height?: number;
  theme?: ChartTheme;
}

const SVGBarChart: React.FC<BarChartProps> = ({
  title,
  bars,
  yLabel,
  width = 620,
  height = 320,
  theme = "dark",
}) => {
  const t = THEME[theme];
  const pad = { top: 40, right: 20, bottom: 60, left: 58 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  const yMax =
    bars.length > 0 ? Math.max(...bars.map((b) => b.value)) * 1.12 : 1;
  const sy = (v: number) => pad.top + h - (v / yMax) * h;

  // Y-axis ticks (5 ticks)
  const yTicks: number[] = [];
  for (let i = 0; i <= 4; i++) yTicks.push(Math.round((yMax * i) / 4));

  const barGap = 0.3; // fraction of slot used for gap
  const slotW = bars.length > 0 ? w / bars.length : w;
  const barW = slotW * (1 - barGap);

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="w-full h-auto"
      style={{ background: t.bg, borderRadius: 8 }}
    >
      {/* Title */}
      <text
        x={width / 2}
        y={22}
        textAnchor="middle"
        fill={t.title}
        fontSize={13}
        fontWeight={700}
      >
        {title}
      </text>

      {/* Y axis label */}
      <text
        x={14}
        y={pad.top + h / 2}
        textAnchor="middle"
        fill={t.axisLabel}
        fontSize={10}
        transform={`rotate(-90, 14, ${pad.top + h / 2})`}
      >
        {yLabel}
      </text>

      {/* Grid + Y tick labels */}
      {yTicks.map((v, i) => (
        <g key={`y${i}`}>
          <line
            x1={pad.left}
            x2={pad.left + w}
            y1={sy(v)}
            y2={sy(v)}
            stroke={t.grid}
            strokeWidth={0.5}
          />
          <text
            x={pad.left - 6}
            y={sy(v) + 3}
            textAnchor="end"
            fill={t.tickLabel}
            fontSize={9}
          >
            {v}
          </text>
        </g>
      ))}

      {/* Axes */}
      <line
        x1={pad.left}
        x2={pad.left}
        y1={pad.top}
        y2={pad.top + h}
        stroke={t.axis}
        strokeWidth={1}
      />
      <line
        x1={pad.left}
        x2={pad.left + w}
        y1={pad.top + h}
        y2={pad.top + h}
        stroke={t.axis}
        strokeWidth={1}
      />

      {/* Bars */}
      {bars.map((bar, i) => {
        const x = pad.left + i * slotW + (slotW - barW) / 2;
        const barH = (bar.value / yMax) * h;
        return (
          <g key={i}>
            <rect
              x={x}
              y={pad.top + h - barH}
              width={barW}
              height={barH}
              fill={bar.color}
              rx={3}
            />
            {/* Value label on top */}
            <text
              x={x + barW / 2}
              y={pad.top + h - barH - 5}
              textAnchor="middle"
              fill={t.title}
              fontSize={10}
              fontWeight={600}
            >
              {bar.value}
            </text>
            {/* Run label below */}
            <text
              x={x + barW / 2}
              y={pad.top + h + 14}
              textAnchor="middle"
              fill={t.tickLabel}
              fontSize={9}
            >
              {bar.label.length > 14 ? bar.label.slice(0, 13) + "…" : bar.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
};

// ── Chart export helper ──────────────────────────────────────────────────────

function exportSVGAsPNG(svgEl: SVGSVGElement, filename: string) {
  const svgData = new XMLSerializer().serializeToString(svgEl);
  const svgBlob = new Blob([svgData], {
    type: "image/svg+xml;charset=utf-8",
  });
  const url = URL.createObjectURL(svgBlob);
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    // 2x for retina
    const scale = 2;
    canvas.width = svgEl.viewBox.baseVal.width * scale;
    canvas.height = svgEl.viewBox.baseVal.height * scale;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    URL.revokeObjectURL(url);
    canvas.toBlob((blob) => {
      if (!blob) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
    }, "image/png");
  };
  img.src = url;
}

function exportTableAsPNG(
  runs: BenchmarkRun[],
  colorFn: (i: number) => string,
  filename: string,
) {
  const scale = 2;
  const rowH = 28;
  const padX = 12;
  const padY = 8;
  const headers = [
    "Run",
    "Steps",
    "Retrieved",
    "Completion",
    "Efficiency",
    "Avg Energy",
    "Messages",
    "Agents",
  ];
  const rows = runs.map((run) => {
    const s = run.summary;
    const total =
      run.agents.scouts + run.agents.coordinators + run.agents.retrievers;
    return [
      run.label,
      s?.totalSteps?.toString() ?? "—",
      s ? `${s.objectsRetrieved}/${s.totalObjects}` : "—",
      s ? `${s.completionPct.toFixed(1)}%` : "—",
      s ? s.efficiency.toFixed(2) : "—",
      s ? s.avgEnergyOverall.toFixed(0) : "—",
      s ? (s.totalMessagesSent?.toString() ?? "0") : "—",
      `${total} (${run.agents.scouts}S/${run.agents.coordinators}C/${run.agents.retrievers}R)`,
    ];
  });

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  ctx.font = "12px monospace";

  // Measure column widths
  const colWidths = headers.map((h, ci) => {
    let max = ctx.measureText(h).width;
    for (const row of rows) {
      const w = ctx.measureText(row[ci]).width;
      if (w > max) max = w;
    }
    return Math.ceil(max) + padX * 2;
  });

  const totalW = colWidths.reduce((a, b) => a + b, 0);
  const totalH = (rows.length + 1) * rowH + padY * 2;

  canvas.width = totalW * scale;
  canvas.height = totalH * scale;
  ctx.scale(scale, scale);

  // Background
  ctx.fillStyle = "#1f2937";
  ctx.fillRect(0, 0, totalW, totalH);

  // Header row
  ctx.fillStyle = "#111827";
  ctx.fillRect(0, padY, totalW, rowH);

  ctx.font = "bold 11px sans-serif";
  ctx.fillStyle = "#9ca3af";
  let x = 0;
  for (let ci = 0; ci < headers.length; ci++) {
    ctx.fillText(headers[ci], x + padX, padY + rowH * 0.65);
    x += colWidths[ci];
  }

  // Header divider
  ctx.strokeStyle = "#374151";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, padY + rowH);
  ctx.lineTo(totalW, padY + rowH);
  ctx.stroke();

  // Data rows
  ctx.font = "12px monospace";
  for (let ri = 0; ri < rows.length; ri++) {
    const y = padY + (ri + 1) * rowH;

    // Zebra stripe
    if (ri % 2 === 1) {
      ctx.fillStyle = "#111827";
      ctx.fillRect(0, y, totalW, rowH);
    }

    // Row divider
    ctx.strokeStyle = "#1f2937";
    ctx.beginPath();
    ctx.moveTo(0, y + rowH);
    ctx.lineTo(totalW, y + rowH);
    ctx.stroke();

    x = 0;
    for (let ci = 0; ci < rows[ri].length; ci++) {
      if (ci === 0) {
        // Color dot
        ctx.fillStyle = colorFn(ri);
        ctx.beginPath();
        ctx.arc(x + padX + 4, y + rowH * 0.5, 4, 0, Math.PI * 2);
        ctx.fill();
        // Label
        ctx.fillStyle = "#d1d5db";
        ctx.fillText(rows[ri][ci], x + padX + 14, y + rowH * 0.65);
      } else {
        ctx.fillStyle = ci >= 6 ? "#6b7280" : "#d1d5db";
        ctx.fillText(rows[ri][ci], x + padX, y + rowH * 0.65);
      }
      x += colWidths[ci];
    }
  }

  canvas.toBlob((blob) => {
    if (!blob) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }, "image/png");
}

// ── Helper: downsample to max N points for chart performance ─────────────

function downsample(data: { x: number; y: number }[], maxPoints = 300) {
  if (data.length <= maxPoints) return data;
  const step = Math.ceil(data.length / maxPoints);
  const out: { x: number; y: number }[] = [];
  for (let i = 0; i < data.length; i += step) out.push(data[i]);
  // Always include last point
  if (out[out.length - 1] !== data[data.length - 1])
    out.push(data[data.length - 1]);
  return out;
}

// ── Scatter chart for parameter impact ───────────────────────────────────────

interface ScatterPoint {
  x: number;
  y: number;
  label: string;
  color: string;
}

interface ScatterChartProps {
  title: string;
  points: ScatterPoint[];
  xLabel: string;
  yLabel: string;
  width?: number;
  height?: number;
  theme?: ChartTheme;
}

const SVGScatterChart: React.FC<ScatterChartProps> = ({
  title,
  points,
  xLabel,
  yLabel,
  width = 620,
  height = 320,
  theme = "dark",
}) => {
  const t = THEME[theme];
  const pad = { top: 40, right: 20, bottom: 52, left: 58 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  if (points.length === 0) {
    return (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        className="w-full h-auto"
        style={{ background: t.bg, borderRadius: 8 }}
      >
        <text
          x={width / 2}
          y={height / 2}
          textAnchor="middle"
          fill={t.tickLabel}
          fontSize={11}
        >
          No data (select runs with parameter info)
        </text>
      </svg>
    );
  }

  let xMin = Infinity,
    xMax = -Infinity,
    yMin = Infinity,
    yMax = -Infinity;
  for (const p of points) {
    if (p.x < xMin) xMin = p.x;
    if (p.x > xMax) xMax = p.x;
    if (p.y < yMin) yMin = p.y;
    if (p.y > yMax) yMax = p.y;
  }
  // Add padding
  const xRange = xMax - xMin || 1;
  const yRange = yMax - yMin || 1;
  xMin -= xRange * 0.08;
  xMax += xRange * 0.08;
  yMin = Math.max(0, yMin - yRange * 0.08);
  yMax += yRange * 0.08;

  const sx = (x: number) => pad.left + ((x - xMin) / (xMax - xMin || 1)) * w;
  const sy = (y: number) => pad.top + h - ((y - yMin) / (yMax - yMin || 1)) * h;

  const yTicks: number[] = [];
  for (let i = 0; i <= 4; i++) yTicks.push(yMin + ((yMax - yMin) * i) / 4);
  const xTicks: number[] = [];
  for (let i = 0; i <= 4; i++)
    xTicks.push(parseFloat((xMin + ((xMax - xMin) * i) / 4).toFixed(1)));

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="w-full h-auto"
      style={{ background: t.bg, borderRadius: 8 }}
    >
      <text
        x={width / 2}
        y={22}
        textAnchor="middle"
        fill={t.title}
        fontSize={13}
        fontWeight={700}
      >
        {title}
      </text>
      <text
        x={14}
        y={pad.top + h / 2}
        textAnchor="middle"
        fill={t.axisLabel}
        fontSize={10}
        transform={`rotate(-90, 14, ${pad.top + h / 2})`}
      >
        {yLabel}
      </text>
      <text
        x={pad.left + w / 2}
        y={height - 6}
        textAnchor="middle"
        fill={t.axisLabel}
        fontSize={10}
      >
        {xLabel}
      </text>

      {/* Grid */}
      {yTicks.map((v, i) => (
        <g key={`y${i}`}>
          <line
            x1={pad.left}
            x2={pad.left + w}
            y1={sy(v)}
            y2={sy(v)}
            stroke={t.grid}
            strokeWidth={0.5}
          />
          <text
            x={pad.left - 6}
            y={sy(v) + 3}
            textAnchor="end"
            fill={t.tickLabel}
            fontSize={9}
          >
            {Number.isInteger(v) ? v : v.toFixed(1)}
          </text>
        </g>
      ))}
      {xTicks.map((v, i) => (
        <g key={`x${i}`}>
          <line
            x1={sx(v)}
            x2={sx(v)}
            y1={pad.top}
            y2={pad.top + h}
            stroke={t.grid}
            strokeWidth={0.5}
          />
          <text
            x={sx(v)}
            y={pad.top + h + 14}
            textAnchor="middle"
            fill={t.tickLabel}
            fontSize={9}
          >
            {v}
          </text>
        </g>
      ))}
      <line
        x1={pad.left}
        x2={pad.left}
        y1={pad.top}
        y2={pad.top + h}
        stroke={t.axis}
        strokeWidth={1}
      />
      <line
        x1={pad.left}
        x2={pad.left + w}
        y1={pad.top + h}
        y2={pad.top + h}
        stroke={t.axis}
        strokeWidth={1}
      />

      {/* Points */}
      {points.map((p, i) => (
        <g key={i}>
          <circle
            cx={sx(p.x)}
            cy={sy(p.y)}
            r={5}
            fill={p.color}
            opacity={0.85}
            stroke={t.dotStroke}
            strokeWidth={1.5}
          />
          <text
            x={sx(p.x)}
            y={sy(p.y) - 8}
            textAnchor="middle"
            fill={t.legend}
            fontSize={8}
          >
            {p.label}
          </text>
        </g>
      ))}
    </svg>
  );
};

// ── Parameter extractors for impact analysis ─────────────────────────────

type ParamKey =
  | "totalAgents"
  | "scouts"
  | "coordinators"
  | "retrievers"
  | "scoutVision"
  | "scoutCommRadius"
  | "coordVision"
  | "coordCommRadius"
  | "retrieverVision"
  | "retrieverCommRadius";

type MetricKey = "totalSteps" | "efficiency" | "avgEnergy" | "messages";

const PARAM_DEFS: {
  key: ParamKey;
  label: string;
  extract: (r: BenchmarkRun) => number | null;
}[] = [
  {
    key: "totalAgents",
    label: "Total Agents",
    extract: (r) =>
      r.agents.scouts + r.agents.coordinators + r.agents.retrievers,
  },
  { key: "scouts", label: "Scouts", extract: (r) => r.agents.scouts },
  {
    key: "coordinators",
    label: "Coordinators",
    extract: (r) => r.agents.coordinators,
  },
  {
    key: "retrievers",
    label: "Retrievers",
    extract: (r) => r.agents.retrievers,
  },
  {
    key: "scoutVision",
    label: "Scout Vision Radius",
    extract: (r) => r.agentParams?.scouts.visionRadius ?? null,
  },
  {
    key: "scoutCommRadius",
    label: "Scout Comm Radius",
    extract: (r) => r.agentParams?.scouts.communicationRadius ?? null,
  },
  {
    key: "coordVision",
    label: "Coord Vision Radius",
    extract: (r) => r.agentParams?.coordinators.visionRadius ?? null,
  },
  {
    key: "coordCommRadius",
    label: "Coord Comm Radius",
    extract: (r) => r.agentParams?.coordinators.communicationRadius ?? null,
  },
  {
    key: "retrieverVision",
    label: "Retriever Vision Radius",
    extract: (r) => r.agentParams?.retrievers.visionRadius ?? null,
  },
  {
    key: "retrieverCommRadius",
    label: "Retriever Comm Radius",
    extract: (r) => r.agentParams?.retrievers.communicationRadius ?? null,
  },
];

const METRIC_DEFS: {
  key: MetricKey;
  label: string;
  extract: (r: BenchmarkRun) => number | null;
}[] = [
  {
    key: "totalSteps",
    label: "Total Steps",
    extract: (r) => r.summary?.totalSteps ?? null,
  },
  {
    key: "efficiency",
    label: "Efficiency (obj/100 steps)",
    extract: (r) => r.summary?.efficiency ?? null,
  },
  {
    key: "avgEnergy",
    label: "Avg Energy",
    extract: (r) => r.summary?.avgEnergyOverall ?? null,
  },
  {
    key: "messages",
    label: "Messages Sent",
    extract: (r) => r.summary?.totalMessagesSent ?? null,
  },
];

// ── Main component ───────────────────────────────────────────────────────────

type ChartType = "retrieval" | "energy" | "efficiency" | "messages";

const CHART_DEFS: {
  key: ChartType;
  title: string;
  yLabel: string;
  extract: (sn: BenchmarkSnapshot) => number;
}[] = [
  {
    key: "retrieval",
    title: "Objects Retrieved vs Step",
    yLabel: "Objects Retrieved",
    extract: (sn) => sn.objectsRetrieved,
  },
  {
    key: "energy",
    title: "Average Agent Energy vs Step",
    yLabel: "Avg Energy",
    extract: (sn) => sn.averageEnergy,
  },
  {
    key: "efficiency",
    title: "Retrieval Efficiency vs Step",
    yLabel: "Obj / 100 steps",
    extract: (sn) => (sn.step > 0 ? (sn.objectsRetrieved / sn.step) * 100 : 0),
  },
  {
    key: "messages",
    title: "Messages Sent vs Step",
    yLabel: "Messages",
    extract: (sn) => sn.messagesSent,
  },
];

// ── Config diff helper ───────────────────────────────────────────────────────

/** Flatten a SimulationAgentsConfig into a flat record of human-readable key → value. */
function flattenConfig(
  cfg: SimulationAgentsConfig,
): Record<string, string | number | boolean> {
  const flat: Record<string, string | number | boolean> = {};
  for (const role of ["scouts", "coordinators", "retrievers"] as const) {
    const r = cfg[role];
    flat[`${role}.count`] = r.count;
    flat[`${role}.vision_radius`] = r.vision_radius;
    flat[`${role}.communication_radius`] = r.communication_radius;
    flat[`${role}.max_energy`] = r.max_energy;
    flat[`${role}.speed`] = r.speed;
    flat[`${role}.carrying_capacity`] = r.carrying_capacity;
  }
  for (const [bKey, bVal] of [
    ["scout_behavior", cfg.scout_behavior],
    ["coordinator_behavior", cfg.coordinator_behavior],
    ["retriever_behavior", cfg.retriever_behavior],
  ] as const) {
    if (!bVal) continue;
    for (const [k, v] of Object.entries(
      bVal as unknown as Record<string, unknown>,
    )) {
      if (
        typeof v === "number" ||
        typeof v === "boolean" ||
        typeof v === "string"
      )
        flat[`${bKey}.${k}`] = v;
    }
  }
  return flat;
}

interface ConfigDiff {
  key: string;
  values: (string | number | boolean | null)[];
}

/** Compare configSnapshots across runs; return only keys that differ. */
function diffConfigs(runs: BenchmarkRun[]): ConfigDiff[] {
  const configs = runs.map((r) =>
    r.configSnapshot ? flattenConfig(r.configSnapshot) : null,
  );
  if (configs.every((c) => c === null)) return [];
  // Collect all keys
  const allKeys = new Set<string>();
  for (const c of configs) {
    if (c) Object.keys(c).forEach((k) => allKeys.add(k));
  }
  const diffs: ConfigDiff[] = [];
  for (const key of [...allKeys].sort()) {
    const vals = configs.map((c) => (c ? (c[key] ?? null) : null));
    // Check if any value differs
    const first = vals.find((v) => v !== null);
    if (first === undefined) continue;
    const allSame = vals.every((v) => v === null || v === first);
    if (!allSame) {
      diffs.push({ key, values: vals });
    }
  }
  return diffs;
}

interface BenchmarkPanelProps {
  runs: BenchmarkRun[];
  recording: boolean;
  onStartRecording: () => void;
  onStopRecording: () => void;
  onCancelRecording: () => void;
  onDeleteRun: (id: string) => void;
  onClearAll: () => void;
  onRenameRun: (id: string, label: string) => void;
  onUpdateNotes: (id: string, notes: string) => void;
  onExportJSON: () => void;
  onImportJSON: (file: File) => void;
  /** Is the simulation loaded (so we can start recording)? */
  isLoaded: boolean;
  isRunning: boolean;
}

export const BenchmarkPanel: React.FC<BenchmarkPanelProps> = ({
  runs,
  recording,
  onStartRecording,
  onStopRecording,
  onCancelRecording,
  onDeleteRun,
  onClearAll,
  onRenameRun,
  onUpdateNotes,
  onExportJSON,
  onImportJSON,
  isLoaded,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  isRunning: _isRunning,
}) => {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [activeChart, setActiveChart] = useState<ChartType>("retrieval");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [impactParam, setImpactParam] = useState<ParamKey>("totalAgents");
  const [impactMetric, setImpactMetric] = useState<MetricKey>("totalSteps");
  const [chartTheme, setChartTheme] = useState<ChartTheme>("dark");
  const [enlargedChart, setEnlargedChart] = useState<
    "line" | "bar" | "scatter" | null
  >(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const barChartRef = useRef<HTMLDivElement>(null);
  const impactChartRef = useRef<HTMLDivElement>(null);
  const tableRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Close modal on Escape key
  useEffect(() => {
    if (!enlargedChart) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setEnlargedChart(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enlargedChart]);

  const toggleRun = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectedRuns = runs.filter((r) => selectedIds.has(r.id));

  const handleExportChart = useCallback(() => {
    const svg = chartRef.current?.querySelector("svg");
    if (!svg) return;
    const def = CHART_DEFS.find((c) => c.key === activeChart)!;
    exportSVGAsPNG(
      svg,
      `benchmark-${def.key}-${new Date().toISOString().slice(0, 10)}.png`,
    );
  }, [activeChart]);

  const handleExportImpactChart = useCallback(() => {
    const svg = impactChartRef.current?.querySelector("svg");
    if (!svg) return;
    exportSVGAsPNG(
      svg,
      `benchmark-impact-${impactParam}-vs-${impactMetric}-${new Date().toISOString().slice(0, 10)}.png`,
    );
  }, [impactParam, impactMetric]);

  const handleExportBarChart = useCallback(() => {
    const svg = barChartRef.current?.querySelector("svg");
    if (!svg) return;
    exportSVGAsPNG(
      svg,
      `benchmark-steps-comparison-${new Date().toISOString().slice(0, 10)}.png`,
    );
  }, []);

  const handleExportTable = useCallback(() => {
    if (selectedRuns.length === 0) return;
    exportTableAsPNG(
      selectedRuns,
      pickColor,
      `benchmark-table-${new Date().toISOString().slice(0, 10)}.png`,
    );
  }, [selectedRuns]);

  const handleRename = useCallback(
    (id: string) => {
      if (editLabel.trim()) onRenameRun(id, editLabel.trim());
      setEditingId(null);
    },
    [editLabel, onRenameRun],
  );

  // Build chart series from selected runs
  const chartDef = CHART_DEFS.find((c) => c.key === activeChart)!;
  const chartSeries: ChartSeries[] = selectedRuns.map((run, i) => ({
    label: run.label,
    color: pickColor(i),
    data: downsample(
      run.snapshots.map((sn) => ({
        x: sn.step,
        y: chartDef.extract(sn),
      })),
    ),
  }));

  // Auto-diff configs of selected runs
  const configDiffs = useMemo(
    () => (selectedRuns.length >= 2 ? diffConfigs(selectedRuns) : []),
    [selectedRuns],
  );

  const canRecord = isLoaded && !recording;

  return (
    <div className="p-3 space-y-3 overflow-y-auto h-full text-xs">
      <h2 className="text-sm font-bold tracking-wide uppercase text-gray-300 flex items-center gap-1.5">
        <span className="text-gray-500">📈</span>
        <span>Benchmark</span>
      </h2>

      {/* ── Chart theme toggle ── */}
      <div className="flex items-center gap-1.5">
        <span className="text-[9px] text-gray-500">Export theme:</span>
        <div className="flex gap-0.5 bg-gray-900/60 p-0.5 rounded-md">
          <button
            onClick={() => setChartTheme("dark")}
            className={`px-2 py-0.5 rounded text-[9px] font-medium transition-colors ${
              chartTheme === "dark"
                ? "bg-gray-700/80 text-white"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            Dark
          </button>
          <button
            onClick={() => setChartTheme("light")}
            className={`px-2 py-0.5 rounded text-[9px] font-medium transition-colors ${
              chartTheme === "light"
                ? "bg-gray-700/80 text-white"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            Light
          </button>
        </div>
      </div>

      {/* ── Recording controls ── */}
      <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5 space-y-2">
        <div className="text-[9px] font-medium text-gray-500 uppercase tracking-widest">
          Recording
        </div>
        {recording ? (
          <div className="flex gap-1.5">
            <button
              onClick={onStopRecording}
              className="flex-1 py-1.5 rounded-md font-semibold bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white transition-colors flex items-center justify-center gap-1"
            >
              <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse" />
              Stop &amp; Save
            </button>
            <button
              onClick={onCancelRecording}
              className="px-3 py-1.5 rounded-md font-medium bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
            >
              Discard
            </button>
          </div>
        ) : (
          <button
            onClick={onStartRecording}
            disabled={!canRecord}
            className="w-full py-1.5 rounded-md font-semibold transition-colors
              bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white
              disabled:bg-gray-700 disabled:text-gray-500 disabled:cursor-not-allowed
              flex items-center justify-center gap-1.5"
          >
            <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
              <circle cx="10" cy="10" r="6" />
            </svg>
            Start Recording
          </button>
        )}
        {!isLoaded && !recording && (
          <p className="text-[10px] text-gray-600 text-center">
            Load a config to enable recording
          </p>
        )}
        {recording && (
          <p className="text-[10px] text-amber-400/80 text-center">
            Recording in progress — run the simulation to collect data
          </p>
        )}
      </div>

      {/* ── Run history ── */}
      <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5 space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-[9px] font-medium text-gray-500 uppercase tracking-widest">
            Saved Runs ({runs.length})
          </div>
          <div className="flex gap-1">
            <button
              onClick={() => fileInputRef.current?.click()}
              className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-gray-700 hover:bg-gray-600 text-gray-400 transition-colors"
              title="Import JSON"
            >
              ↓ Import
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onImportJSON(f);
                e.target.value = "";
              }}
            />
            {runs.length > 0 && (
              <>
                <button
                  onClick={onExportJSON}
                  className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-gray-700 hover:bg-gray-600 text-gray-400 transition-colors"
                  title="Export all runs as JSON"
                >
                  ↑ Export
                </button>
                <button
                  onClick={onClearAll}
                  className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-red-900/60 hover:bg-red-800/60 text-red-400 transition-colors"
                  title="Delete all runs"
                >
                  ✕ Clear
                </button>
              </>
            )}
          </div>
        </div>

        {runs.length === 0 ? (
          <p className="text-gray-600 text-center py-2 text-[10px]">
            No runs recorded yet
          </p>
        ) : (
          <div className="space-y-1 max-h-64 overflow-y-auto">
            {runs.map((run) => {
              const selected = selectedIds.has(run.id);
              const color = selected
                ? pickColor([...selectedIds].indexOf(run.id))
                : undefined;
              return (
                <div key={run.id} className="space-y-0">
                  <div
                    className={`flex items-center gap-1.5 p-1.5 rounded-md border transition-colors cursor-pointer ${
                      selected
                        ? "bg-gray-700/60 border-gray-600/60"
                        : "bg-gray-800/30 border-gray-800/40 hover:border-gray-700/50"
                    }`}
                    onClick={() => toggleRun(run.id)}
                  >
                    {/* Color indicator */}
                    <div
                      className="w-2.5 h-2.5 rounded-full flex-shrink-0 border"
                      style={{
                        backgroundColor: selected ? color : "transparent",
                        borderColor: selected ? color : "#4b5563",
                      }}
                    />

                    {/* Label / edit */}
                    <div className="flex-1 min-w-0">
                      {editingId === run.id ? (
                        <input
                          autoFocus
                          value={editLabel}
                          onChange={(e) => setEditLabel(e.target.value)}
                          onBlur={() => handleRename(run.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleRename(run.id);
                            if (e.key === "Escape") setEditingId(null);
                          }}
                          className="w-full bg-gray-900 border border-gray-600 rounded px-1 py-0.5 text-[10px] text-gray-200 outline-none"
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <div className="truncate text-[10px] font-medium text-gray-300">
                          {run.label}
                        </div>
                      )}
                      <div className="text-[9px] text-gray-600 truncate">
                        {run.configName} · {run.summary?.totalSteps ?? 0} steps
                        · {run.summary?.completionPct.toFixed(0) ?? 0}%
                      </div>
                    </div>

                    {/* Actions */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingId(run.id);
                        setEditLabel(run.label);
                      }}
                      className="text-gray-600 hover:text-gray-400 p-0.5"
                      title="Rename"
                    >
                      ✎
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteRun(run.id);
                        setSelectedIds((prev) => {
                          const next = new Set(prev);
                          next.delete(run.id);
                          return next;
                        });
                      }}
                      className="text-gray-600 hover:text-red-400 p-0.5"
                      title="Delete"
                    >
                      ✕
                    </button>
                  </div>

                  {/* Notes — shown when selected */}
                  {selected && (
                    <div className="ml-4 mt-0.5 mb-1">
                      <textarea
                        value={run.notes ?? ""}
                        onChange={(e) => onUpdateNotes(run.id, e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        placeholder="Notes (e.g. smart_explore=false, increased vision…)"
                        rows={2}
                        className="w-full bg-gray-900/80 border border-gray-700/50 rounded px-1.5 py-1 text-[9px] text-gray-400 placeholder-gray-700 resize-none outline-none focus:border-gray-500 leading-tight"
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Comparison chart ── */}
      {selectedRuns.length > 0 && (
        <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5 space-y-2">
          {/* Chart type tabs */}
          <div className="flex gap-0.5 bg-gray-900/60 p-0.5 rounded-md">
            {CHART_DEFS.map((cd) => (
              <button
                key={cd.key}
                onClick={() => setActiveChart(cd.key)}
                className={`flex-1 py-1 px-1 rounded text-[9px] font-medium transition-colors ${
                  activeChart === cd.key
                    ? "bg-gray-700/80 text-white"
                    : "text-gray-500 hover:text-gray-300"
                }`}
              >
                {cd.key === "retrieval"
                  ? "Retrieval"
                  : cd.key === "energy"
                    ? "Energy"
                    : cd.key === "messages"
                      ? "Messages"
                      : "Efficiency"}
              </button>
            ))}
          </div>

          {/* Chart */}
          <div
            ref={chartRef}
            onClick={() => setEnlargedChart("line")}
            className="cursor-pointer"
            title="Click to enlarge"
          >
            <SVGChart
              title={chartDef.title}
              series={chartSeries}
              yLabel={chartDef.yLabel}
              theme={chartTheme}
            />
          </div>

          {/* Export chart button */}
          <button
            onClick={handleExportChart}
            className="w-full py-1.5 rounded-md font-medium bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors flex items-center justify-center gap-1"
          >
            <svg
              className="w-3 h-3"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
              />
            </svg>
            Export chart as PNG
          </button>
        </div>
      )}

      {/* ── Steps comparison bar chart ── */}
      {selectedRuns.length >= 2 && (
        <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5 space-y-2">
          <div
            ref={barChartRef}
            onClick={() => setEnlargedChart("bar")}
            className="cursor-pointer"
            title="Click to enlarge"
          >
            <SVGBarChart
              title="Steps Comparison"
              bars={selectedRuns.map((run, i) => ({
                label: run.label,
                value: run.summary?.totalSteps ?? 0,
                color: pickColor(i),
              }))}
              yLabel="Total Steps"
              theme={chartTheme}
            />
          </div>

          {/* Export bar chart button */}
          <button
            onClick={handleExportBarChart}
            className="w-full py-1.5 rounded-md font-medium bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors flex items-center justify-center gap-1"
          >
            <svg
              className="w-3 h-3"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
              />
            </svg>
            Export chart as PNG
          </button>
        </div>
      )}

      {/* ── Config diff between selected runs ── */}
      {selectedRuns.length >= 2 && configDiffs.length > 0 && (
        <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5 space-y-2">
          <div className="text-[9px] font-medium text-gray-500 uppercase tracking-widest">
            Config Differences
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[9px]">
              <thead>
                <tr className="text-gray-500 border-b border-gray-700/40">
                  <th className="text-left py-0.5 pr-2 font-medium">
                    Parameter
                  </th>
                  {selectedRuns.map((run, i) => (
                    <th
                      key={run.id}
                      className="text-right py-0.5 px-1 font-medium"
                    >
                      <span
                        className="inline-block w-1.5 h-1.5 rounded-full mr-0.5"
                        style={{ backgroundColor: pickColor(i) }}
                      />
                      {run.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {configDiffs.map((d) => (
                  <tr
                    key={d.key}
                    className="border-b border-gray-800/30 text-gray-300"
                  >
                    <td className="py-0.5 pr-2 font-mono text-gray-400">
                      {d.key}
                    </td>
                    {d.values.map((v, vi) => (
                      <td key={vi} className="text-right py-0.5 px-1 font-mono">
                        {v === null ? (
                          <span className="text-gray-600">—</span>
                        ) : typeof v === "boolean" ? (
                          <span
                            className={v ? "text-emerald-400" : "text-red-400"}
                          >
                            {String(v)}
                          </span>
                        ) : (
                          String(v)
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Parameter Impact scatter ── */}
      {selectedRuns.length >= 2 && (
        <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5 space-y-2">
          <div className="text-[9px] font-medium text-gray-500 uppercase tracking-widest">
            Parameter Impact
          </div>

          {/* Param / metric selectors */}
          <div className="flex gap-2">
            <label className="flex-1 space-y-0.5">
              <span className="text-[8px] text-gray-500 block">
                Parameter (X)
              </span>
              <select
                value={impactParam}
                onChange={(e) => setImpactParam(e.target.value as ParamKey)}
                className="w-full bg-gray-900/80 border border-gray-700/50 rounded px-1.5 py-0.5 text-[10px] text-gray-300"
              >
                {PARAM_DEFS.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex-1 space-y-0.5">
              <span className="text-[8px] text-gray-500 block">Metric (Y)</span>
              <select
                value={impactMetric}
                onChange={(e) => setImpactMetric(e.target.value as MetricKey)}
                className="w-full bg-gray-900/80 border border-gray-700/50 rounded px-1.5 py-0.5 text-[10px] text-gray-300"
              >
                {METRIC_DEFS.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {/* Scatter chart */}
          <div
            ref={impactChartRef}
            onClick={() => setEnlargedChart("scatter")}
            className="cursor-pointer"
            title="Click to enlarge"
          >
            <SVGScatterChart
              title={`${PARAM_DEFS.find((p) => p.key === impactParam)!.label} vs ${METRIC_DEFS.find((m) => m.key === impactMetric)!.label}`}
              points={selectedRuns.reduce<ScatterPoint[]>((acc, run, i) => {
                const pDef = PARAM_DEFS.find((p) => p.key === impactParam)!;
                const mDef = METRIC_DEFS.find((m) => m.key === impactMetric)!;
                const x = pDef.extract(run);
                const y = mDef.extract(run);
                if (x != null && y != null)
                  acc.push({ x, y, label: run.label, color: pickColor(i) });
                return acc;
              }, [])}
              xLabel={PARAM_DEFS.find((p) => p.key === impactParam)!.label}
              yLabel={METRIC_DEFS.find((m) => m.key === impactMetric)!.label}
              theme={chartTheme}
            />
          </div>

          {/* Export button */}
          <button
            onClick={handleExportImpactChart}
            className="w-full py-1.5 rounded-md font-medium bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors flex items-center justify-center gap-1"
          >
            <svg
              className="w-3 h-3"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
              />
            </svg>
            Export chart as PNG
          </button>
        </div>
      )}

      {/* ── Summary table for selected runs ── */}
      {selectedRuns.length > 0 && (
        <div className="bg-gray-800/50 border border-gray-700/40 rounded-lg p-2.5 space-y-2">
          <div className="text-[9px] font-medium text-gray-500 uppercase tracking-widest">
            Comparison Table
          </div>
          <div ref={tableRef} className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-gray-500 border-b border-gray-700/40">
                  <th className="text-left py-1 pr-2 font-medium">Run</th>
                  <th className="text-right py-1 px-1 font-medium">Steps</th>
                  <th className="text-right py-1 px-1 font-medium">
                    Retrieved
                  </th>
                  <th className="text-right py-1 px-1 font-medium">
                    Completion
                  </th>
                  <th className="text-right py-1 px-1 font-medium">
                    Efficiency
                  </th>
                  <th className="text-right py-1 px-1 font-medium">
                    Avg Energy
                  </th>
                  <th className="text-right py-1 px-1 font-medium">Messages</th>
                  <th className="text-right py-1 pl-1 font-medium">Agents</th>
                </tr>
              </thead>
              <tbody>
                {selectedRuns.map((run, i) => {
                  const s = run.summary;
                  const totalAgents =
                    run.agents.scouts +
                    run.agents.coordinators +
                    run.agents.retrievers;
                  return (
                    <tr
                      key={run.id}
                      className="border-b border-gray-800/30 text-gray-300"
                    >
                      <td className="py-1 pr-2">
                        <div className="flex items-center gap-1">
                          <span
                            className="w-2 h-2 rounded-full inline-block"
                            style={{ backgroundColor: pickColor(i) }}
                          />
                          <span className="truncate max-w-[80px]">
                            {run.label}
                          </span>
                        </div>
                      </td>
                      <td className="text-right py-1 px-1 font-mono">
                        {s?.totalSteps ?? "—"}
                      </td>
                      <td className="text-right py-1 px-1 font-mono">
                        {s ? `${s.objectsRetrieved}/${s.totalObjects}` : "—"}
                      </td>
                      <td className="text-right py-1 px-1 font-mono">
                        {s ? `${s.completionPct.toFixed(1)}%` : "—"}
                      </td>
                      <td className="text-right py-1 px-1 font-mono">
                        {s ? s.efficiency.toFixed(2) : "—"}
                      </td>
                      <td className="text-right py-1 px-1 font-mono">
                        {s ? s.avgEnergyOverall.toFixed(0) : "—"}
                      </td>
                      <td className="text-right py-1 px-1 font-mono">
                        {s ? (s.totalMessagesSent?.toString() ?? "0") : "—"}
                      </td>
                      <td className="text-right py-1 pl-1 text-gray-500">
                        {totalAgents} ({run.agents.scouts}S/
                        {run.agents.coordinators}C/{run.agents.retrievers}R)
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {/* Export table button */}
          <button
            onClick={handleExportTable}
            className="w-full py-1.5 rounded-md font-medium bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors flex items-center justify-center gap-1"
          >
            <svg
              className="w-3 h-3"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
              />
            </svg>
            Export table as PNG
          </button>
        </div>
      )}

      {selectedRuns.length === 0 && runs.length > 0 && (
        <p className="text-gray-600 text-center text-[10px] py-2">
          Select runs above to compare charts &amp; data
        </p>
      )}

      {/* ── Fullscreen chart modal ── */}
      {enlargedChart &&
        createPortal(
          <div
            className="fixed inset-0 z-[9999] bg-black/80 backdrop-blur-sm flex items-center justify-center p-6"
            onClick={() => setEnlargedChart(null)}
          >
            <div
              className="relative bg-gray-900 rounded-xl shadow-2xl max-w-[95vw] max-h-[95vh] overflow-auto p-4"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={() => setEnlargedChart(null)}
                className="absolute top-2 right-2 w-8 h-8 rounded-full bg-gray-800 hover:bg-gray-700 flex items-center justify-center text-gray-400 hover:text-white transition-colors z-10"
              >
                <svg
                  className="w-4 h-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
              {enlargedChart === "line" && (
                <SVGChart
                  title={chartDef.title}
                  series={chartSeries}
                  yLabel={chartDef.yLabel}
                  width={1100}
                  height={560}
                  theme={chartTheme}
                />
              )}
              {enlargedChart === "bar" && (
                <SVGBarChart
                  title="Steps Comparison"
                  bars={selectedRuns.map((run, i) => ({
                    label: run.label,
                    value: run.summary?.totalSteps ?? 0,
                    color: pickColor(i),
                  }))}
                  yLabel="Total Steps"
                  width={1100}
                  height={560}
                  theme={chartTheme}
                />
              )}
              {enlargedChart === "scatter" && (
                <SVGScatterChart
                  title={`${PARAM_DEFS.find((p) => p.key === impactParam)!.label} vs ${METRIC_DEFS.find((m) => m.key === impactMetric)!.label}`}
                  points={selectedRuns.reduce<ScatterPoint[]>((acc, run, i) => {
                    const pDef = PARAM_DEFS.find((p) => p.key === impactParam)!;
                    const mDef = METRIC_DEFS.find(
                      (m) => m.key === impactMetric,
                    )!;
                    const x = pDef.extract(run);
                    const y = mDef.extract(run);
                    if (x != null && y != null)
                      acc.push({ x, y, label: run.label, color: pickColor(i) });
                    return acc;
                  }, [])}
                  xLabel={PARAM_DEFS.find((p) => p.key === impactParam)!.label}
                  yLabel={
                    METRIC_DEFS.find((m) => m.key === impactMetric)!.label
                  }
                  width={1100}
                  height={560}
                  theme={chartTheme}
                />
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
};
