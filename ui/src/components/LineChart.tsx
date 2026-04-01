import { useState } from "react";

interface Series {
  key: string;
  label: string;
  color: string;
}

interface LineChartProps {
  data: Record<string, number>[];
  series: Series[];
  height?: number;
  yMin?: number;
  yMax?: number;
  xKey?: string;
}

export default function LineChart({
  data,
  series,
  height = 140,
  yMin: forcedMin,
  yMax: forcedMax,
  xKey = "epoch",
}: LineChartProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  if (data.length < 2 || series.length === 0) return null;

  // Compute Y bounds across all series
  let allVals: number[] = [];
  for (const s of series) {
    for (const pt of data) {
      const v = pt[s.key];
      if (v != null && isFinite(v)) allVals.push(v);
    }
  }
  if (allVals.length === 0) return null;

  const rawMin = Math.min(...allVals);
  const rawMax = Math.max(...allVals);
  const padding = (rawMax - rawMin) * 0.1 || 0.01;
  const yMin = forcedMin ?? Math.max(0, rawMin - padding);
  const yMax = forcedMax ?? rawMax + padding;
  const yRange = yMax - yMin || 1;

  const w = 600;
  const h = height;
  const padL = 48;
  const padR = 12;
  const padT = 8;
  const padB = 24;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const xScale = (i: number) => padL + (i / (data.length - 1)) * plotW;
  const yScale = (v: number) => padT + plotH - ((v - yMin) / yRange) * plotH;

  // Build SVG paths for each series
  const paths = series.map((s) => {
    const points: string[] = [];
    for (let i = 0; i < data.length; i++) {
      const v = data[i][s.key];
      if (v == null || !isFinite(v)) continue;
      const x = xScale(i);
      const y = yScale(v);
      points.push(`${points.length === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return { ...s, d: points.join(" ") };
  });

  // Y-axis ticks (4 ticks)
  const yTicks = Array.from({ length: 5 }, (_, i) => yMin + (yRange * i) / 4);

  // X-axis labels
  const firstEpoch = data[0]?.[xKey] ?? 0;
  const lastEpoch = data[data.length - 1]?.[xKey] ?? data.length;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="w-full"
        style={{ height }}
        onMouseLeave={() => setHoveredIdx(null)}
      >
        {/* Grid lines */}
        {yTicks.map((tick, i) => (
          <g key={i}>
            <line
              x1={padL} y1={yScale(tick)} x2={w - padR} y2={yScale(tick)}
              stroke="#e5e7eb" strokeWidth={0.5}
            />
            <text
              x={padL - 4} y={yScale(tick) + 3}
              textAnchor="end" fontSize={9} fill="#9ca3af" fontFamily="monospace"
            >
              {tick < 1 ? tick.toFixed(3) : tick.toFixed(1)}
            </text>
          </g>
        ))}

        {/* X axis labels */}
        <text x={padL} y={h - 4} fontSize={9} fill="#9ca3af">{firstEpoch}</text>
        <text x={w - padR} y={h - 4} fontSize={9} fill="#9ca3af" textAnchor="end">{lastEpoch}</text>

        {/* Lines */}
        {paths.map((p) => (
          <path
            key={p.key}
            d={p.d}
            fill="none"
            stroke={p.color}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}

        {/* Hover overlay — invisible rects for each data point */}
        {data.map((_, i) => (
          <rect
            key={i}
            x={xScale(i) - plotW / data.length / 2}
            y={padT}
            width={plotW / data.length}
            height={plotH}
            fill="transparent"
            onMouseEnter={() => setHoveredIdx(i)}
          />
        ))}

        {/* Hover line + dots */}
        {hoveredIdx != null && (
          <g>
            <line
              x1={xScale(hoveredIdx)} y1={padT}
              x2={xScale(hoveredIdx)} y2={padT + plotH}
              stroke="#d1d5db" strokeWidth={1} strokeDasharray="3,3"
            />
            {series.map((s) => {
              const v = data[hoveredIdx]?.[s.key];
              if (v == null || !isFinite(v)) return null;
              return (
                <circle
                  key={s.key}
                  cx={xScale(hoveredIdx)} cy={yScale(v)}
                  r={4} fill={s.color} stroke="white" strokeWidth={2}
                />
              );
            })}
          </g>
        )}
      </svg>

      {/* Hover tooltip */}
      {hoveredIdx != null && (
        <div
          className="absolute bg-gray-900 text-white text-xs rounded-lg px-3 py-2 pointer-events-none shadow-lg z-10"
          style={{
            left: `${(xScale(hoveredIdx) / w) * 100}%`,
            top: 0,
            transform: "translateX(-50%)",
          }}
        >
          <p className="font-medium mb-1">Epoch {data[hoveredIdx]?.[xKey]}</p>
          {series.map((s) => {
            const v = data[hoveredIdx]?.[s.key];
            if (v == null) return null;
            return (
              <p key={s.key} className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: s.color }} />
                <span className="text-gray-300">{s.label}:</span>
                <span className="font-mono">{v.toFixed(4)}</span>
              </p>
            );
          })}
        </div>
      )}

      {/* Legend */}
      <div className="flex gap-4 mt-1 justify-end">
        {series.map((s) => (
          <span key={s.key} className="flex items-center gap-1 text-[10px] text-gray-500">
            <span className="inline-block w-3 h-0.5 rounded" style={{ backgroundColor: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
