/**
 * Workflows list page — browse saved workflows, create new ones.
 */
import { Link } from "react-router-dom";
import { Workflow, Plus, Sparkles } from "lucide-react";

const TEMPLATES = [
  {
    name: "Detect & Count",
    desc: "Run detection, filter by confidence, count objects",
    blocks: ["Image Input", "Detection", "Filter", "Output"],
  },
  {
    name: "Detect → Crop → Classify",
    desc: "Detect objects, crop regions, classify each crop",
    blocks: ["Image Input", "Detection", "Crop", "Classification", "Output"],
  },
  {
    name: "Smart Analysis",
    desc: "Detect objects then describe the scene with an LLM",
    blocks: ["Image Input", "Detection", "LLM", "Output"],
  },
];

export default function WorkflowsPage() {
  return (
    <div className="max-w-4xl mx-auto mt-6 px-4 sm:px-6 pb-16">
      <div className="flex items-center justify-between mb-6">
        <div>
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
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {TEMPLATES.map((t) => (
            <Link
              key={t.name}
              to="/workflows/new"
              className="surface surface-interactive p-4"
            >
              <div className="flex items-center gap-2 mb-2">
                <Sparkles size={14} style={{ color: "var(--accent)" }} />
                <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>{t.name}</span>
              </div>
              <p className="text-xs mb-3" style={{ color: "var(--text-secondary)" }}>{t.desc}</p>
              <div className="flex flex-wrap gap-1">
                {t.blocks.map((b) => (
                  <span
                    key={b}
                    className="text-[10px] px-1.5 py-0.5 rounded"
                    style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-muted)" }}
                  >
                    {b}
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
