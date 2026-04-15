import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { getMetricsSummary, listModels } from "../../api";
import { pickKeyMetrics } from "../../lib/metrics";

export function MonitorTab() {
  const [timeWindow, setTimeWindow] = useState<"1h" | "24h" | "7d">("1h");
  const { data: metrics, isLoading } = useQuery({
    queryKey: ["metrics", timeWindow],
    queryFn: () => getMetricsSummary(timeWindow),
    refetchInterval: 15000,
    refetchIntervalInBackground: false,
  });
  const { data: models } = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    refetchIntervalInBackground: false,
  });
  const activeModel = useMemo(() => models?.find((m) => m.is_active), [models]);

  const s = metrics?.summary;

  const maxReq = useMemo(() => {
    if (!metrics?.timeseries?.length) return 1;
    let m = 1;
    for (const p of metrics.timeseries) if (p.requests > m) m = p.requests;
    return m;
  }, [metrics?.timeseries]);

  const maxClassCount = useMemo(() => {
    if (!metrics?.by_class?.length) return 1;
    let m = 1;
    for (const c of metrics.by_class) if (c.detection_count > m) m = c.detection_count;
    return m;
  }, [metrics?.by_class]);

  const activeKeyMetrics = useMemo(
    () => activeModel ? pickKeyMetrics(activeModel.metrics) : [],
    [activeModel],
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="font-semibold" style={{ color: "var(--text-primary)" }}>Inference Metrics</h2>
        <div className="flex p-0.5 rounded-lg" style={{ backgroundColor: "var(--bg-inset)" }}>
          {(["1h", "24h", "7d"] as const).map((w) => (
            <button key={w} onClick={() => setTimeWindow(w)}
              className="px-3 py-1 text-xs rounded-md transition-all duration-150"
              style={{
                backgroundColor: timeWindow === w ? "var(--bg-surface)" : "transparent",
                color: timeWindow === w ? "var(--text-primary)" : "var(--text-muted)",
                fontWeight: timeWindow === w ? 600 : 400,
                boxShadow: timeWindow === w ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              }}>
              {w}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        {[
          { label: "Requests", value: s?.total_requests ?? 0, fmt: (v: number) => v.toLocaleString() },
          { label: "Avg Latency", value: s?.avg_latency_ms ?? 0, fmt: (v: number) => `${v.toFixed(0)}ms` },
          { label: "P95 Latency", value: s?.p95_latency_ms ?? 0, fmt: (v: number) => `${v.toFixed(0)}ms` },
          { label: "Avg Confidence", value: s?.avg_confidence ?? 0, fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
        ].map((card) => (
          <div key={card.label} className="surface p-4">
            <span className="text-[10px] uppercase tracking-wide block mb-1" style={{ color: "var(--text-muted)" }}>{card.label}</span>
            <span className="text-xl font-bold font-mono" style={{ color: "var(--text-primary)" }}>
              {isLoading ? "..." : card.fmt(card.value)}
            </span>
          </div>
        ))}
      </div>

      {metrics?.timeseries && metrics.timeseries.length > 0 && (
        <div className="surface p-5 mb-6">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>Request Volume & Latency</h3>
          <div className="flex gap-6" style={{ height: 160 }}>
            <div className="flex-1 flex items-end gap-px">
              {metrics.timeseries.map((point, i) => {
                const h = (point.requests / maxReq) * 140;
                const isHot = point.avg_latency_ms > (s?.p95_latency_ms ?? 999);
                return (
                  <div key={i} className="flex-1 flex flex-col items-center justify-end" title={`${point.timestamp?.split("T")[1]?.slice(0, 5) ?? ""}\n${point.requests} req\n${point.avg_latency_ms.toFixed(0)}ms`}>
                    <div
                      className="w-full rounded-t-sm transition-all duration-300"
                      style={{
                        height: Math.max(2, h),
                        backgroundColor: isHot ? "var(--danger)" : "var(--accent)",
                        opacity: isHot ? 0.8 : 0.6,
                      }}
                    />
                  </div>
                );
              })}
            </div>
          </div>
          <div className="flex justify-between mt-2 text-[9px]" style={{ color: "var(--text-muted)" }}>
            <span>{metrics.timeseries[0]?.timestamp?.split("T")[1]?.slice(0, 5) ?? ""}</span>
            <span>{metrics.timeseries[metrics.timeseries.length - 1]?.timestamp?.split("T")[1]?.slice(0, 5) ?? ""}</span>
          </div>
        </div>
      )}

      {activeModel && (
        <div className="surface p-5 mb-6">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>
            Active Model — {activeModel.name}
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {activeKeyMetrics.map((m) => (
              <div key={m.key} className="rounded-lg p-3" style={{ backgroundColor: "var(--bg-inset)" }}>
                <span className="text-[10px] uppercase tracking-wide block mb-1" style={{ color: "var(--text-muted)" }}>{m.label}</span>
                <span className="text-lg font-bold" style={{ color: "var(--text-primary)" }}>{m.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {metrics?.by_class && metrics.by_class.length > 0 && (
        <div className="surface p-5 mb-6">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>Detection by Class</h3>
          <div className="space-y-2">
            {metrics.by_class.map((cls) => (
              <div key={cls.class_name} className="flex items-center gap-3">
                <span className="text-xs w-24 truncate" style={{ color: "var(--text-primary)" }}>{cls.class_name}</span>
                <div className="flex-1 rounded-full h-2" style={{ backgroundColor: "var(--bg-inset)" }}>
                  <div className="h-full rounded-full" style={{ width: `${(cls.detection_count / maxClassCount) * 100}%`, backgroundColor: "var(--accent)" }} />
                </div>
                <span className="text-xs font-mono w-16 text-right" style={{ color: "var(--text-muted)" }}>{cls.detection_count.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {metrics?.by_model && metrics.by_model.length > 0 && (
        <div className="surface p-5 mb-6">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>By Model</h3>
          <div className="space-y-2">
            {metrics.by_model.map((m) => (
              <div key={m.model_id} className="flex items-center justify-between py-2 px-3 rounded-lg" style={{ backgroundColor: "var(--bg-inset)" }}>
                <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{m.model_name || "Unknown"}</span>
                <div className="flex items-center gap-4 text-xs" style={{ color: "var(--text-muted)" }}>
                  <span>{m.request_count} req</span>
                  <span className="font-mono">{m.avg_latency_ms.toFixed(0)}ms</span>
                  <span className="font-mono">{(m.avg_confidence * 100).toFixed(1)}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!isLoading && (!s || s.total_requests === 0) && (
        <div className="surface text-center py-12">
          <BarChart3 size={36} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
          <p className="text-sm mb-1" style={{ color: "var(--text-secondary)" }}>No inference data yet</p>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Metrics appear here as soon as you run predictions via the Test tab or API
          </p>
        </div>
      )}
    </div>
  );
}
