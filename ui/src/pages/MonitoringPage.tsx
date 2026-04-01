/**
 * Monitoring dashboard — track inference metrics, model performance, and usage.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { BarChart3, Activity, Zap, AlertTriangle, Clock, TrendingUp } from "lucide-react";
import { listModels, getServeStatus } from "../api";
import { pickKeyMetrics } from "../lib/metrics";

export default function MonitoringPage() {
  const { data: models } = useQuery({ queryKey: ["models"], queryFn: listModels });
  const { data: status } = useQuery({ queryKey: ["serve-status"], queryFn: getServeStatus, refetchInterval: 5000 });

  const activeModel = models?.find((m) => m.is_active);

  return (
    <div className="max-w-5xl mx-auto mt-6 px-4 sm:px-6 pb-16">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
            <BarChart3 size={24} />
            Monitoring
          </h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
            Track model performance, inference latency, and usage patterns.
          </p>
        </div>
      </div>

      {/* Server status */}
      <div className="surface p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>Inference Server</h2>
          <span
            className="flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full"
            style={{
              backgroundColor: status?.loaded ? "var(--success-soft)" : "var(--bg-inset)",
              color: status?.loaded ? "var(--success)" : "var(--text-muted)",
            }}
          >
            <span className={`w-2 h-2 rounded-full ${status?.loaded ? "bg-green-500" : "bg-gray-400"}`} />
            {status?.loaded ? "Active" : "Inactive"}
          </span>
        </div>

        {status?.loaded ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { label: "Model", value: status.model_name || "—", icon: Zap },
              { label: "Variant", value: status.model_variant || "—", icon: Activity },
              { label: "Task", value: status.task_type || "—", icon: TrendingUp },
              { label: "Device", value: status.device, icon: Clock },
            ].map((s) => (
              <div key={s.label} className="rounded-lg p-3" style={{ backgroundColor: "var(--bg-inset)" }}>
                <div className="flex items-center gap-1.5 mb-1">
                  <s.icon size={12} style={{ color: "var(--text-muted)" }} />
                  <span className="text-[10px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{s.label}</span>
                </div>
                <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{s.value}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-6">
            <AlertTriangle size={32} className="mx-auto mb-2" style={{ color: "var(--text-muted)" }} />
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>No model loaded</p>
            <Link to="/deploy" className="text-xs text-blue-600 hover:underline mt-1 inline-block">
              Go to Deploy to activate a model
            </Link>
          </div>
        )}
      </div>

      {/* Active model metrics */}
      {activeModel && (
        <div className="surface p-5 mb-6">
          <h2 className="font-semibold text-sm mb-4" style={{ color: "var(--text-primary)" }}>
            Active Model Performance
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {pickKeyMetrics(activeModel.metrics).map((m) => (
              <div key={m.key} className="rounded-lg p-3" style={{ backgroundColor: "var(--bg-inset)" }}>
                <span className="text-[10px] uppercase tracking-wide block mb-1" style={{ color: "var(--text-muted)" }}>
                  {m.label}
                </span>
                <span className="text-lg font-bold" style={{ color: "var(--text-primary)" }}>{m.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Model registry overview */}
      <div className="surface p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>All Models</h2>
          <Link to="/experiments" className="text-xs text-blue-600 hover:underline">View experiments</Link>
        </div>
        {models && models.length > 0 ? (
          <div className="space-y-2">
            {models.slice(0, 10).map((m) => {
              const mAP = m.metrics?.["metrics/mAP50(B)"] ?? m.metrics?.["metrics/mAP50(M)"];
              return (
                <div
                  key={m.id}
                  className="flex items-center justify-between py-2 px-3 rounded-lg"
                  style={{ backgroundColor: m.is_active ? "var(--success-soft)" : "var(--bg-inset)" }}
                >
                  <div className="flex items-center gap-3">
                    {m.is_active && <span className="w-2 h-2 rounded-full bg-green-500" />}
                    <div>
                      <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{m.name}</span>
                      <span className="text-xs ml-2" style={{ color: "var(--text-muted)" }}>{m.model_variant}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 text-xs">
                    {mAP != null && (
                      <span style={{ color: "var(--text-secondary)" }}>
                        mAP: <span className="font-mono font-medium">{(mAP * 100).toFixed(1)}%</span>
                      </span>
                    )}
                    <span className="px-2 py-0.5 rounded text-[10px]" style={{ backgroundColor: "var(--bg-surface)", color: "var(--text-muted)" }}>
                      {m.task_type}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-center py-4" style={{ color: "var(--text-muted)" }}>
            No models trained yet.{" "}
            <Link to="/datasets" className="text-blue-600 hover:underline">Start with a dataset</Link>
          </p>
        )}
      </div>

      {/* Coming soon features */}
      <div className="surface p-5">
        <h2 className="font-semibold text-sm mb-3" style={{ color: "var(--text-primary)" }}>Coming Soon</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {[
            { title: "Inference Analytics", desc: "Track latency P50/P95/P99, throughput, and error rates over time" },
            { title: "Data Drift Detection", desc: "Detect when input data distribution shifts from training data" },
            { title: "Usage Metrics", desc: "Monitor API calls, compute usage, and storage consumption" },
            { title: "Alert Rules", desc: "Get notified when model accuracy drops or latency spikes" },
          ].map((f) => (
            <div key={f.title} className="rounded-lg p-3" style={{ backgroundColor: "var(--bg-inset)" }}>
              <span className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>{f.title}</span>
              <p className="text-[10px] mt-0.5" style={{ color: "var(--text-muted)" }}>{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
