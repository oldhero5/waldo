/**
 * Workflows list page — browse saved workflows, create new ones.
 */
import { Link } from "react-router-dom";
import { Workflow, Plus } from "lucide-react";

const TEMPLATES = [
  {
    name: "Detect & Count",
    desc: "Detect objects, filter by confidence, count by class",
    blocks: ["Image Input", "Detection", "Filter", "Count", "Output"],
    color: "#8b5cf6",
  },
  {
    name: "Detect → Visualize",
    desc: "Run detection and draw bounding boxes with labels on the image",
    blocks: ["Image Input", "Detection", "Draw Boxes", "Output"],
    color: "#ec4899",
  },
  {
    name: "Privacy Blur",
    desc: "Detect faces or license plates and blur them for privacy",
    blocks: ["Image Input", "Detection", "Blur Regions", "Output"],
    color: "#06b6d4",
  },
  {
    name: "Smart Analysis",
    desc: "Detect objects, count them, then describe the scene with a local LLM",
    blocks: ["Image Input", "Detection", "Count", "LLM", "Output"],
    color: "#22c55e",
  },
  {
    name: "Detect → Crop → Classify",
    desc: "Detect objects, crop each region, analyze dominant colors",
    blocks: ["Image Input", "Detection", "Crop", "Dominant Color", "Output"],
    color: "#f59e0b",
  },
  {
    name: "Conditional Alert",
    desc: "Detect objects, count them, alert only if count exceeds threshold",
    blocks: ["Image Input", "Detection", "Count", "If/Else", "LLM", "Output"],
    color: "#14b8a6",
  },
];

export default function WorkflowsPage() {
  return (
    <div className="max-w-4xl mx-auto mt-6 px-4 sm:px-6 pb-16">
      <div className="flex items-center justify-between mb-6">
        <div>
          <p className="eyebrow" style={{ marginBottom: 4 }}>Pipeline builder</p>
          <h1 className="text-2xl font-bold flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
            <Workflow size={24} />
            Workflows
          </h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
            Build visual ML pipelines by chaining blocks together.
          </p>
        </div>
        <Link
          to="/workflows/new"
          className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
        >
          <Plus size={14} />
          New Workflow
        </Link>
      </div>

      {/* Templates */}
      <div className="mb-8">
        <h2 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: "var(--text-muted)" }}>
          Templates
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {TEMPLATES.map((t) => (
            <Link
              key={t.name}
              to="/workflows/new"
              className="surface surface-interactive"
              style={{ padding: 18 }}
            >
              <div className="flex items-center gap-2 mb-2">
                <span style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: t.color, flexShrink: 0 }} />
                <span style={{ fontFamily: "var(--font-serif)", fontWeight: 600, fontSize: 14, color: "var(--text-primary)" }}>{t.name}</span>
              </div>
              <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 10, lineHeight: 1.5 }}>{t.desc}</p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, lineHeight: 2 }}>
                {t.blocks.map((b, i) => (
                  <span key={b}>
                    <span style={{
                      fontFamily: "var(--font-mono)", fontSize: 10, padding: "2px 7px", borderRadius: 6,
                      backgroundColor: "var(--bg-inset)", color: "var(--text-muted)", whiteSpace: "nowrap",
                    }}>{b}</span>
                    {i < t.blocks.length - 1 && <span style={{ color: "var(--border-default)", margin: "0 2px", fontSize: 10 }}> → </span>}
                  </span>
                ))}
              </div>
            </Link>
          ))}
        </div>
      </div>

      {/* Empty state */}
      <div className="surface p-8 text-center">
        <Workflow size={40} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
        <h2 className="font-semibold text-sm mb-1" style={{ color: "var(--text-primary)" }}>
          No saved workflows yet
        </h2>
        <p className="text-xs mb-4" style={{ color: "var(--text-secondary)" }}>
          Create a workflow by chaining detection, cropping, filtering, and LLM blocks
          into a visual pipeline. Deploy them as API endpoints.
        </p>
        <Link
          to="/workflows/new"
          className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
        >
          <Plus size={14} />
          Create Your First Workflow
        </Link>
      </div>
    </div>
  );
}
