import { useCallback, useRef, useState } from "react";

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
  /** Index of the "best" epoch to mark with a vertical line */
  bestEpoch?: number;
  /** Enable log scale on Y axis */
  logScale?: boolean;
}

export default function LineChart({
  data,
  series,
  height = 140,
  yMin: forcedMin,
  yMax: forcedMax,
  xKey = "epoch",
  bestEpoch,
  logScale = false,
}: LineChartProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [pinnedIdx, setPinnedIdx] = useState<number | null>(null);
  const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(new Set());
  const [zoom, setZoom] = useState(1);
  const [panX, setPanX] = useState(0);
  const dragging = useRef(false);
  const dragStart = useRef({ x: 0, panX: 0 });
  const svgRef = useRef<SVGSVGElement>(null);

  if (data.length < 2 || series.length === 0) return null;

  const visibleSeries = series.filter((s) => !hiddenKeys.has(s.key));

  // Compute Y bounds across visible series
  const allVals: number[] = [];
  for (const s of visibleSeries) {
    for (const pt of data) {
      const v = pt[s.key];
      if (v != null && isFinite(v)) allVals.push(v);
    }
  }
  if (allVals.length === 0) return null;

  const rawMin = Math.min(...allVals);
  const rawMax = Math.max(...allVals);
  const padding = (rawMax - rawMin) * 0.1 || 0.01;
  const yMin = forcedMin ?? Math.max(logScale ? 0.0001 : 0, rawMin - padding);
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

  // Zoom + pan applied to X
  const visibleW = plotW / zoom;
  const xOffset = panX * (plotW - visibleW);

  const xScale = (i: number) => padL + xOffset + (i / (data.length - 1)) * plotW * zoom;
  const yScale = (v: number) => {
    if (logScale && v > 0) {
      const logMin = Math.log10(Math.max(yMin, 0.0001));
      const logMax = Math.log10(Math.max(yMax, 0.001));
      const logV = Math.log10(v);
      return padT + plotH - ((logV - logMin) / (logMax - logMin)) * plotH;
    }
    return padT + plotH - ((v - yMin) / yRange) * plotH;
  };

  // Build SVG paths
  const paths = visibleSeries.map((s) => {
    const points: string[] = [];
    for (let i = 0; i < data.length; i++) {
      const v = data[i][s.key];
      if (v == null || !isFinite(v)) continue;
      if (logScale && v <= 0) continue;
      const x = xScale(i);
      const y = yScale(v);
      points.push(`${points.length === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return { ...s, d: points.join(" ") };
  });

  // Y-axis ticks
  const yTicks = Array.from({ length: 5 }, (_, i) => yMin + (yRange * i) / 4);

  const firstEpoch = data[0]?.[xKey] ?? 0;
  const lastEpoch = data[data.length - 1]?.[xKey] ?? data.length;

  const activeIdx = pinnedIdx ?? hoveredIdx;

  // Mouse handlers for pan
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (zoom <= 1) return;
    dragging.current = true;
    dragStart.current = { x: e.clientX, panX };
  }, [zoom, panX]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (dragging.current && svgRef.current) {
      const rect = svgRef.current.getBoundingClientRect();
      const dx = (e.clientX - dragStart.current.x) / rect.width;
      setPanX(Math.max(0, Math.min(1, dragStart.current.panX - dx)));
    }
  }, []);

  const handleMouseUp = useCallback(() => { dragging.current = false; }, []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.max(1, Math.min(z * (e.deltaY < 0 ? 1.2 : 1 / 1.2), 10)));
  }, []);

  const toggleSeries = (key: string) => {
    setHiddenKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${w} ${h}`}
        className="w-full"
        style={{ height, cursor: zoom > 1 ? "grab" : undefined }}
        onMouseLeave={() => { setHoveredIdx(null); handleMouseUp(); }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onWheel={handleWheel}
      >
        {/* Clip region for chart area */}
        <defs>
          <clipPath id="plot-clip">
            <rect x={padL} y={padT} width={plotW} height={plotH} />
          </clipPath>
        </defs>

        {/* Grid lines */}
        {yTicks.map((tick, i) => (
          <g key={i}>
            <line
              x1={padL} y1={yScale(tick)} x2={w - padR} y2={yScale(tick)}
              stroke="var(--border-subtle)" strokeWidth={0.5}
            />
            <text
              x={padL - 4} y={yScale(tick) + 3}
              textAnchor="end" fontSize={9} fill="var(--text-muted)" fontFamily="monospace"
            >
              {tick < 1 ? tick.toFixed(3) : tick.toFixed(1)}
            </text>
          </g>
        ))}

        {/* X axis labels */}
        <text x={padL} y={h - 4} fontSize={9} fill="var(--text-muted)">{firstEpoch}</text>
        <text x={w - padR} y={h - 4} fontSize={9} fill="var(--text-muted)" textAnchor="end">{lastEpoch}</text>

        {/* Best epoch marker */}
        {bestEpoch != null && bestEpoch >= 0 && bestEpoch < data.length && (
          <g clipPath="url(#plot-clip)">
            <line
              x1={xScale(bestEpoch)} y1={padT}
              x2={xScale(bestEpoch)} y2={padT + plotH}
              stroke="var(--success)" strokeWidth={1.5} strokeDasharray="4,3" opacity={0.6}
            />
            <circle cx={xScale(bestEpoch)} cy={padT + 6} r={3} fill="var(--success)" />
          </g>
        )}

        {/* Lines (clipped) */}
        <g clipPath="url(#plot-clip)">
          {paths.map((p) => (
            <path
              key={p.key}
              d={p.d}
              fill="none"
              stroke={p.color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity={hiddenKeys.size > 0 ? 1 : 0.85}
            />
          ))}
        </g>

        {/* Hover overlay */}
        {data.map((_, i) => (
          <rect
            key={i}
            x={xScale(i) - plotW / data.length / 2}
            y={padT}
            width={plotW / data.length}
            height={plotH}
            fill="transparent"
            onMouseEnter={() => setHoveredIdx(i)}
            onClick={() => setPinnedIdx(pinnedIdx === i ? null : i)}
          />
        ))}

        {/* Hover/pinned line + dots */}
        {activeIdx != null && (
          <g clipPath="url(#plot-clip)">
            <line
              x1={xScale(activeIdx)} y1={padT}
              x2={xScale(activeIdx)} y2={padT + plotH}
              stroke="var(--border-default)" strokeWidth={1} strokeDasharray="3,3"
            />
            {visibleSeries.map((s) => {
              const v = data[activeIdx]?.[s.key];
              if (v == null || !isFinite(v)) return null;
              return (
                <circle
                  key={s.key}
                  cx={xScale(activeIdx)} cy={yScale(v)}
                  r={4} fill={s.color} stroke="white" strokeWidth={2}
                />
              );
            })}
          </g>
        )}
      </svg>

      {/* Tooltip */}
      {activeIdx != null && (
        <div
          className="absolute rounded-lg px-3 py-2 pointer-events-none z-10"
          style={{
            left: `${(xScale(activeIdx) / w) * 100}%`,
            top: 0,
            transform: "translateX(-50%)",
            backgroundColor: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            boxShadow: "var(--shadow-md)",
            fontSize: 11,
          }}
        >
          <p className="font-medium mb-1" style={{ color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
            Epoch {data[activeIdx]?.[xKey]}
            {pinnedIdx === activeIdx && <span style={{ color: "var(--text-muted)", marginLeft: 4 }}>pinned</span>}
          </p>
          {visibleSeries.map((s) => {
            const v = data[activeIdx]?.[s.key];
            if (v == null) return null;
            return (
              <p key={s.key} className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: s.color }} />
                <span style={{ color: "var(--text-muted)" }}>{s.label}:</span>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{v.toFixed(4)}</span>
              </p>
            );
          })}
        </div>
      )}

      {/* Legend — clickable to toggle series */}
      <div className="flex flex-wrap gap-3 mt-1 justify-end">
        {series.map((s) => {
          const hidden = hiddenKeys.has(s.key);
          return (
            <button
              key={s.key}
              onClick={() => toggleSeries(s.key)}
              className="flex items-center gap-1"
              style={{ fontSize: 10, color: hidden ? "var(--text-muted)" : "var(--text-secondary)", opacity: hidden ? 0.4 : 1, fontFamily: "var(--font-mono)" }}
            >
              <span className="inline-block w-3 h-0.5 rounded" style={{ backgroundColor: hidden ? "var(--text-muted)" : s.color }} />
              {s.label}
            </button>
          );
        })}
        {zoom > 1 && (
          <button
            onClick={() => { setZoom(1); setPanX(0); }}
            style={{ fontSize: 10, color: "var(--accent)", fontFamily: "var(--font-mono)" }}
          >
            Reset zoom
          </button>
        )}
      </div>
    </div>
  );
}
