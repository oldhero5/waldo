import { memo, useState } from "react";
import { exportModel, type ModelOut } from "../../api";
import { pickKeyMetrics } from "../../lib/metrics";

const EXPORT_FORMATS = ["onnx", "torchscript", "coreml", "tflite", "openvino"];

function ModelCardImpl({ model, onActivate, isBest }: { model: ModelOut; onActivate: (id: string) => void; isBest?: boolean }) {
  const [exporting, setExporting] = useState(false);
  const [exportFmt, setExportFmt] = useState("onnx");
  const [exportMsg, setExportMsg] = useState("");

  const handleExport = async () => {
    setExporting(true);
    setExportMsg("");
    try {
      await exportModel(model.id, exportFmt);
      setExportMsg(`Export to ${exportFmt} started`);
    } catch (e: any) {
      setExportMsg(e.message);
    } finally {
      setExporting(false);
    }
  };

  const handlePromoteClick = () => {
    if (!confirm(`Promote "${model.name}" to champion? This redirects all production traffic to this model.`)) return;
    onActivate(model.id);
  };

  const keyMetrics = pickKeyMetrics(model.metrics);

  return (
    <div
      className="rounded-lg p-4"
      style={{
        border: model.is_active
          ? "1px solid var(--success)"
          : isBest
          ? "1px solid var(--accent)"
          : "1px solid var(--border-default)",
        backgroundColor: model.is_active
          ? "var(--success-soft)"
          : "var(--bg-surface)",
      }}
    >
      <div className="flex justify-between items-start mb-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-semibold" style={{ color: "var(--text-primary)" }}>{model.name}</h3>
            {model.is_active && (
              <span className="px-2 py-0.5 text-[10px] rounded-full font-medium" style={{ backgroundColor: "var(--success)", color: "#fff" }}>Active</span>
            )}
            {!model.is_active && isBest && (
              <span className="px-2 py-0.5 text-[10px] rounded-full font-medium" style={{ backgroundColor: "var(--accent-soft)", color: "var(--accent)" }}>Best mAP</span>
            )}
          </div>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            {model.model_variant} &middot; {model.task_type} &middot; v{model.version}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!model.is_active && (
            <button
              onClick={handlePromoteClick}
              className="px-3 py-1 text-xs rounded-lg text-white"
              style={{ backgroundColor: "var(--success)" }}
            >
              Promote
            </button>
          )}
        </div>
      </div>

      {keyMetrics.length > 0 && (
        <div className="grid grid-cols-3 gap-2 mb-3">
          {keyMetrics.map((m) => (
            <div key={m.key} className="text-xs">
              <span style={{ color: "var(--text-muted)" }}>{m.label}: </span>
              <span className="font-mono">{m.value}</span>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 mt-3 pt-3" style={{ borderTop: "1px solid var(--border-subtle)" }}>
        <select
          value={exportFmt}
          onChange={(e) => setExportFmt(e.target.value)}
          className="border rounded px-2 py-1 text-sm"
          style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-surface)" }}
        >
          {EXPORT_FORMATS.map((f) => (
            <option key={f} value={f}>{f.toUpperCase()}</option>
          ))}
        </select>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="px-3 py-1 text-sm rounded disabled:opacity-50"
          style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
        >
          {exporting ? "Exporting..." : "Export"}
        </button>
        {exportMsg && <span className="text-xs" style={{ color: "var(--text-muted)" }}>{exportMsg}</span>}
      </div>

      {Object.keys(model.export_formats).length > 0 && (
        <div className="flex gap-1 mt-2">
          {Object.keys(model.export_formats).map((fmt) => (
            <span key={fmt} className="px-2 py-0.5 text-xs rounded" style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-muted)" }}>
              {fmt}
            </span>
          ))}
        </div>
      )}

      {model.weights_url && (
        <a href={model.weights_url} className="text-sm block mt-2 hover:underline" style={{ color: "var(--accent)" }}>
          Download weights
        </a>
      )}
    </div>
  );
}

export const ModelCard = memo(ModelCardImpl, (prev, next) =>
  prev.model.id === next.model.id &&
  prev.model.is_active === next.model.is_active &&
  prev.isBest === next.isBest,
);
