import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  activateModel,
  exportModel,
  getServeStatus,
  listModels,
  type ModelOut,
} from "../api";
import { ChevronDown, ChevronRight, Search } from "lucide-react";

const EXPORT_FORMATS = ["onnx", "torchscript", "coreml", "tflite", "openvino"];

function ModelCard({ model, onActivate, isBest }: { model: ModelOut; onActivate: (id: string) => void; isBest?: boolean }) {
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

  return (
    <div className={`border rounded-lg p-4 ${model.is_active ? "border-green-500 bg-green-50" : isBest ? "border-blue-300 bg-blue-50/30" : "border-gray-200"}`}>
      <div className="flex justify-between items-start mb-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-gray-900">{model.name}</h3>
            {isBest && !model.is_active && (
              <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-[10px] rounded-full font-medium">Best mAP</span>
            )}
          </div>
          <p className="text-sm text-gray-500">
            {model.model_variant} &middot; {model.task_type} &middot; v{model.version}
          </p>
        </div>
        {model.is_active ? (
          <span className="px-2 py-1 bg-green-100 text-green-800 text-xs rounded-full font-medium">Active</span>
        ) : (
          <button
            onClick={() => onActivate(model.id)}
            className="px-3 py-1 bg-gray-900 text-white text-xs rounded-lg"
          >
            Activate
          </button>
        )}
      </div>

      {/* Metrics */}
      {Object.keys(model.metrics).length > 0 && (
        <div className="grid grid-cols-3 gap-2 mb-3">
          {Object.entries(model.metrics).slice(0, 6).map(([k, v]) => (
            <div key={k} className="text-xs">
              <span className="text-gray-500">{k}: </span>
              <span className="font-mono">{typeof v === "number" ? v.toFixed(4) : String(v)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Export */}
      <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-100">
        <select
          value={exportFmt}
          onChange={(e) => setExportFmt(e.target.value)}
          className="border rounded px-2 py-1 text-sm bg-white"
        >
          {EXPORT_FORMATS.map((f) => (
            <option key={f} value={f}>{f.toUpperCase()}</option>
          ))}
        </select>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="px-3 py-1 bg-gray-100 text-gray-900 text-sm rounded hover:bg-gray-200 disabled:opacity-50"
        >
          {exporting ? "Exporting..." : "Export"}
        </button>
        {exportMsg && <span className="text-xs text-gray-500">{exportMsg}</span>}
      </div>

      {/* Existing exports */}
      {Object.keys(model.export_formats).length > 0 && (
        <div className="flex gap-1 mt-2">
          {Object.keys(model.export_formats).map((fmt) => (
            <span key={fmt} className="px-2 py-0.5 bg-gray-100 text-gray-600 text-xs rounded">
              {fmt}
            </span>
          ))}
        </div>
      )}

      {model.weights_url && (
        <a
          href={model.weights_url}
          className="text-sm text-blue-600 hover:underline block mt-2"
        >
          Download weights
        </a>
      )}
    </div>
  );
}

export default function DeployPage() {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState("");
  const [apiExpanded, setApiExpanded] = useState(false);

  const { data: models, isLoading: modelsLoading } = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
  });

  const { data: status } = useQuery({
    queryKey: ["serve-status"],
    queryFn: getServeStatus,
    refetchInterval: 10000,
  });

  const [justActivated, setJustActivated] = useState(false);
  const activateMut = useMutation({
    mutationFn: activateModel,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["serve-status"] });
      setJustActivated(true);
    },
  });

  // Filter models by search
  const filteredModels = models?.filter((m) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      m.name.toLowerCase().includes(q) ||
      m.model_variant.toLowerCase().includes(q) ||
      m.task_type.toLowerCase().includes(q)
    );
  });

  // Group models by task type
  const grouped = new Map<string, ModelOut[]>();
  filteredModels?.forEach((m) => {
    const group = grouped.get(m.task_type) || [];
    group.push(m);
    grouped.set(m.task_type, group);
  });

  // Sort groups to show active model's task type first
  const sortedGroups = Array.from(grouped.entries()).sort(([, a], [, b]) => {
    const aHasActive = a.some((m) => m.is_active);
    const bHasActive = b.some((m) => m.is_active);
    if (aHasActive && !bHasActive) return -1;
    if (!aHasActive && bHasActive) return 1;
    return 0;
  });

  // Within each group, active model first
  for (const [, group] of sortedGroups) {
    group.sort((a, b) => (b.is_active ? 1 : 0) - (a.is_active ? 1 : 0));
  }

  // Find the best model by mAP50
  const bestModelId = filteredModels?.reduce<{ id: string; score: number } | null>((best, m) => {
    const mAP = m.metrics?.["metrics/mAP50(B)"] ?? m.metrics?.["metrics/mAP50(M)"] ?? 0;
    if (!best || mAP > best.score) return { id: m.id, score: mAP };
    return best;
  }, null)?.id;

  return (
    <div className="min-h-screen">

      <div className="max-w-4xl mx-auto mt-8 px-4">
        <h1 className="text-2xl font-bold text-gray-900 mb-6">Deploy</h1>

        {/* Server status */}
        <div className="bg-gray-50 rounded-lg p-4 mb-6">
          <h2 className="font-semibold text-gray-900 mb-2">Inference Server</h2>
          {status ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              <div>
                <span className="text-gray-500">Status: </span>
                <span className={status.loaded ? "text-green-600" : "text-gray-400"}>
                  {status.loaded ? "Loaded" : "No model loaded"}
                </span>
              </div>
              <div>
                <span className="text-gray-500">Model: </span>
                <span>{status.model_name || "\u2014"}</span>
              </div>
              <div>
                <span className="text-gray-500">Type: </span>
                <span>{status.task_type || "\u2014"}</span>
              </div>
              <div>
                <span className="text-gray-500">Device: </span>
                <span className="font-mono">{status.device}</span>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500">Loading...</p>
          )}
        </div>

        {/* API usage — collapsible */}
        <button
          onClick={() => setApiExpanded(!apiExpanded)}
          className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-3 hover:text-gray-900"
        >
          {apiExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          API Usage
        </button>
        {apiExpanded && (
          <div className="bg-gray-50 rounded-lg p-4 mb-6">
            <pre className="text-xs bg-gray-900 text-green-400 p-3 rounded overflow-x-auto">
{`# Image prediction
curl -X POST http://localhost:8000/api/v1/predict/image \\
  -F "file=@image.jpg" | jq

# Video prediction (with tracking)
curl -X POST http://localhost:8000/api/v1/predict/video \\
  -F "file=@video.mp4" | jq

# Activate a model
curl -X POST http://localhost:8000/api/v1/models/{model_id}/activate

# Server status
curl http://localhost:8000/api/v1/serve/status | jq`}
            </pre>
          </div>
        )}

        {/* Just activated banner */}
        {justActivated && (
          <div className="flex items-center justify-between bg-green-50 border border-green-200 rounded-lg p-4 mb-6">
            <span className="text-sm text-green-700 font-medium">Model activated and ready for inference.</span>
            <Link to="/demo" className="px-4 py-1.5 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 font-medium">
              Try it now
            </Link>
          </div>
        )}

        {/* Models */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-gray-900">Models</h2>
          {models && models.length > 5 && (
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Filter models..."
                className="pl-8 pr-3 py-1.5 border rounded-lg text-sm w-48"
              />
            </div>
          )}
        </div>

        {modelsLoading ? (
          <p className="text-gray-500 text-sm">Loading models...</p>
        ) : filteredModels && filteredModels.length > 0 ? (
          <div className="space-y-6">
            {sortedGroups.map(([taskType, group]) => (
              <div key={taskType}>
                <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-2">
                  {taskType} ({group.length})
                </h3>
                <div className="space-y-3">
                  {group.map((m) => (
                    <ModelCard key={m.id} model={m} onActivate={(id) => activateMut.mutate(id)} isBest={m.id === bestModelId} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : searchQuery ? (
          <p className="text-gray-500 text-sm">No models matching "{searchQuery}".</p>
        ) : (
          <p className="text-gray-500 text-sm">No models yet. Train a model first.</p>
        )}
      </div>
    </div>
  );
}
