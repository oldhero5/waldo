import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Camera } from "lucide-react";
import { listModels, type ModelOut } from "../../api";

type Lang = "curl" | "python" | "js";

function buildSnippet(baseUrl: string, model: ModelOut, lang: Lang): string {
  const imgUrl = `${baseUrl}/api/v1/predict/image?model_id=${model.id}`;
  const vidUrl = `${baseUrl}/api/v1/predict/video?model_id=${model.id}`;
  if (lang === "curl") {
    return `# Image prediction
curl -X POST "${imgUrl}" \\
  -H "Authorization: Bearer wld_YOUR_KEY" \\
  -F "file=@image.jpg"

# Video prediction (with object tracking)
curl -X POST "${vidUrl}" \\
  -H "Authorization: Bearer wld_YOUR_KEY" \\
  -F "file=@video.mp4"`;
  }
  if (lang === "python") {
    return `import requests

# Image
r = requests.post(
    "${imgUrl}",
    headers={"Authorization": "Bearer wld_YOUR_KEY"},
    files={"file": open("image.jpg", "rb")},
)
detections = r.json()["detections"]

# Video (returns per-frame detections with tracking)
r = requests.post(
    "${vidUrl}",
    headers={"Authorization": "Bearer wld_YOUR_KEY"},
    files={"file": open("video.mp4", "rb")},
)
frames = r.json()["frames"]`;
  }
  return `// Image prediction
const form = new FormData();
form.append("file", imageFile);
const res = await fetch("${imgUrl}", {
  method: "POST",
  headers: { Authorization: "Bearer wld_YOUR_KEY" },
  body: form,
});
const { detections } = await res.json();

// Video prediction (with tracking)
const vForm = new FormData();
vForm.append("file", videoFile);
const vRes = await fetch("${vidUrl}", {
  method: "POST",
  headers: { Authorization: "Bearer wld_YOUR_KEY" },
  body: vForm,
});
const { frames } = await vRes.json();`;
}

export function EndpointsTab() {
  const { data: models, isLoading } = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    refetchIntervalInBackground: false,
  });
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [selectedLang, setSelectedLang] = useState<Lang>("curl");

  const baseUrl = window.location.origin;

  const copyToClipboard = useCallback((text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  const sorted = useMemo(() => {
    return [...(models || [])].sort((a, b) => {
      if (a.is_active && !b.is_active) return -1;
      if (!a.is_active && b.is_active) return 1;
      const mA = (a.metrics?.["metrics/mAP50(B)"] as number | undefined) ?? 0;
      const mB = (b.metrics?.["metrics/mAP50(B)"] as number | undefined) ?? 0;
      return mB - mA;
    });
  }, [models]);

  const snippetsByModel = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of sorted) map.set(m.id, buildSnippet(baseUrl, m, selectedLang));
    return map;
  }, [sorted, selectedLang, baseUrl]);

  return (
    <div>
      <div className="mb-5">
        <h2 className="font-semibold" style={{ color: "var(--text-primary)" }}>API Endpoints</h2>
        <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
          Every trained model has a prediction API. Copy the URL, connect your app.
        </p>
      </div>

      <div className="flex gap-1 mb-4" role="tablist" aria-label="Code language">
        {(["curl", "python", "js"] as const).map((lang) => (
          <button
            key={lang}
            role="tab"
            aria-selected={selectedLang === lang}
            onClick={() => setSelectedLang(lang)}
            className="px-3 py-1 text-xs rounded border font-mono"
            style={{
              borderColor: selectedLang === lang ? "var(--accent)" : "var(--border-subtle)",
              backgroundColor: selectedLang === lang ? "var(--accent-soft, #eff6ff)" : "transparent",
              color: selectedLang === lang ? "var(--accent)" : "var(--text-muted)",
              fontWeight: selectedLang === lang ? 600 : 400,
            }}
          >
            {lang === "js" ? "JavaScript" : lang === "python" ? "Python" : "cURL"}
          </button>
        ))}
      </div>

      {isLoading ? (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>Loading...</p>
      ) : sorted.length > 0 ? (
        <div className="flex flex-col gap-3">
          {sorted.map((model) => {
            const mAP = (model.metrics?.["metrics/mAP50(B)"] as number | undefined)
              ?? (model.metrics?.["metrics/mAP50(M)"] as number | undefined);
            const imgEndpoint = `${baseUrl}/api/v1/predict/image?model_id=${model.id}`;
            const vidEndpoint = `${baseUrl}/api/v1/predict/video?model_id=${model.id}`;
            const snippet = snippetsByModel.get(model.id) || "";

            return (
              <div key={model.id} className="surface p-4" style={{ border: model.is_active ? "1px solid var(--success)" : undefined }}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    {model.is_active && <span className="w-2 h-2 rounded-full" style={{ backgroundColor: "var(--success)" }} />}
                    <span className="font-semibold text-[15px]" style={{ fontFamily: "var(--font-serif)", color: "var(--text-primary)" }}>
                      {model.name}
                    </span>
                    <span className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>
                      {model.model_variant}
                    </span>
                    {model.is_active && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded font-medium" style={{ backgroundColor: "var(--success)", color: "#fff" }}>
                        active
                      </span>
                    )}
                  </div>
                  <div className="flex gap-3 text-[11px] font-mono">
                    {mAP != null && (
                      <span style={{ color: "var(--text-secondary)" }}>
                        mAP <strong>{(mAP * 100).toFixed(1)}%</strong>
                      </span>
                    )}
                    <span style={{ color: "var(--text-muted)" }}>{model.task_type}</span>
                  </div>
                </div>

                <div className="flex flex-col gap-1 mb-3">
                  {[
                    { label: "Image", url: imgEndpoint, id: `img-${model.id}` },
                    { label: "Video", url: vidEndpoint, id: `vid-${model.id}` },
                  ].map((ep) => (
                    <div key={ep.id} className="flex items-center gap-2 px-3 py-1.5 rounded-lg"
                      style={{ backgroundColor: "var(--bg-inset)", border: "1px solid var(--border-subtle)" }}>
                      <span className="text-[9px] font-mono shrink-0 w-9" style={{ color: "var(--text-muted)" }}>
                        {ep.label}
                      </span>
                      <code className="flex-1 text-[11px] font-mono overflow-x-auto whitespace-nowrap" style={{ color: "var(--text-primary)" }}>
                        {ep.url}
                      </code>
                      <button
                        onClick={() => copyToClipboard(ep.url, ep.id)}
                        aria-label={`Copy ${ep.label} URL`}
                        className="px-2 py-0.5 rounded text-[10px] border"
                        style={{
                          borderColor: "var(--border-subtle)",
                          backgroundColor: "transparent",
                          color: copiedId === ep.id ? "var(--success)" : "var(--text-muted)",
                        }}
                      >
                        {copiedId === ep.id ? "Copied" : "Copy"}
                      </button>
                    </div>
                  ))}
                </div>

                <div className="relative">
                  <pre className="font-mono text-[11px] p-3 rounded-lg overflow-auto"
                    style={{ backgroundColor: "#1a1a2e", color: "#4ade80", lineHeight: 1.5, maxHeight: 140 }}>
                    {snippet}
                  </pre>
                  <button
                    onClick={() => copyToClipboard(snippet, `code-${model.id}`)}
                    className="absolute top-2 right-2 px-2.5 py-0.5 rounded text-[10px]"
                    style={{
                      backgroundColor: "rgba(255,255,255,0.1)",
                      color: copiedId === `code-${model.id}` ? "#4ade80" : "rgba(255,255,255,0.5)",
                    }}
                  >
                    {copiedId === `code-${model.id}` ? "Copied" : "Copy"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="surface text-center py-12">
          <Camera size={36} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
          <p className="text-sm mb-1" style={{ color: "var(--text-secondary)" }}>No trained models yet</p>
          <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
            Train a model to get an API endpoint. Every model automatically gets a prediction URL.
          </p>
          <Link
            to="/datasets"
            className="px-4 py-2 text-white rounded-xl text-sm inline-block"
            style={{ backgroundColor: "var(--accent)", textDecoration: "none" }}
          >
            Go to Datasets
          </Link>
        </div>
      )}

      {/* Reference section — formerly the API tab. Kept inline because there is one source of truth now. */}
      <div className="mt-8 pt-6" style={{ borderTop: "1px solid var(--border-subtle)" }}>
        <h3 className="text-sm font-semibold mb-1" style={{ color: "var(--text-primary)" }}>Reference</h3>
        <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
          Authentication, response format, and the OpenAPI spec for all endpoints.
        </p>

        <div className="grid sm:grid-cols-2 gap-3">
          <div className="surface p-4">
            <h4 className="text-xs font-semibold mb-2 uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>Authentication</h4>
            <p className="text-xs mb-2" style={{ color: "var(--text-secondary)" }}>
              All endpoints require an API key in the Authorization header.
            </p>
            <code className="block text-[11px] font-mono p-2 rounded" style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}>
              Authorization: Bearer wld_YOUR_KEY
            </code>
            <Link to="/settings" className="text-xs hover:underline mt-2 inline-block" style={{ color: "var(--accent)" }}>
              Manage API keys &rarr;
            </Link>
          </div>

          <div className="surface p-4">
            <h4 className="text-xs font-semibold mb-2 uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>OpenAPI Spec</h4>
            <p className="text-xs mb-2" style={{ color: "var(--text-secondary)" }}>
              Full schema and interactive docs for every endpoint.
            </p>
            <a
              href="/docs"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs hover:underline"
              style={{ color: "var(--accent)" }}
            >
              Open /docs &rarr;
            </a>
          </div>

          <div className="surface p-4">
            <h4 className="text-xs font-semibold mb-2 uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>Response Format</h4>
            <p className="text-xs mb-2" style={{ color: "var(--text-secondary)" }}>
              Image prediction returns detections with bbox, class, confidence.
            </p>
            <pre className="text-[10px] font-mono p-2 rounded overflow-x-auto" style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}>
{`{
  "detections": [
    {
      "class_name": "person",
      "confidence": 0.92,
      "bbox": [x1, y1, x2, y2],
      "track_id": null
    }
  ]
}`}
            </pre>
          </div>

          <div className="surface p-4">
            <h4 className="text-xs font-semibold mb-2 uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>Video Streaming</h4>
            <p className="text-xs mb-2" style={{ color: "var(--text-secondary)" }}>
              Videos under 500 frames return synchronously. Longer videos stream over WebSocket.
            </p>
            <code className="block text-[11px] font-mono p-2 rounded" style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}>
              ws://.../api/v1/predict/stream/{`{session_id}`}
            </code>
          </div>
        </div>
      </div>
    </div>
  );
}
