/**
 * Workflows list — browse templates (with real graphs) and saved workflows.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Workflow, Plus, ArrowRight } from "lucide-react";
import { TEMPLATES } from "../lib/workflow-templates";
import { authFetch } from "../api";

const BASE = "/api/v1";

export default function WorkflowsPage() {
  const navigate = useNavigate();

  const { data: saved } = useQuery({
    queryKey: ["saved-workflows"],
    queryFn: async () => {
      const res = await authFetch(`${BASE}/workflows/saved`);
      if (!res.ok) return [];
      return res.json();
    },
  });

  const loadTemplate = (idx: number) => {
    // Store template in sessionStorage so the editor can load it
    sessionStorage.setItem("waldo_workflow_template", JSON.stringify(TEMPLATES[idx]));
    navigate("/workflows/new?template=" + idx);
  };

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
            Build visual ML pipelines. Start from a template or create from scratch.
          </p>
        </div>
        <Link
          to="/workflows/new"
          className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm hover:bg-blue-700"
          style={{ transition: "all 160ms ease" }}
        >
          <Plus size={14} />
          Blank Workflow
        </Link>
      </div>

      {/* Templates — click to load pre-built graph */}
      <div className="mb-8">
        <p className="eyebrow mb-3">Start from a template</p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
          {TEMPLATES.map((t, idx) => (
            <button
              key={t.name}
              onClick={() => loadTemplate(idx)}
              className="surface surface-interactive text-left"
              style={{ padding: 16 }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: t.color, flexShrink: 0 }} />
                <span style={{ fontFamily: "var(--font-serif)", fontWeight: 600, fontSize: 14, color: "var(--text-primary)" }}>
                  {t.name}
                </span>
              </div>
              <p style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: 10 }}>
                {t.desc}
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {t.tags.map((tag) => (
                  <span key={tag} style={{
                    fontFamily: "var(--font-mono)", fontSize: 9, padding: "2px 6px", borderRadius: 5,
                    backgroundColor: "var(--bg-inset)", color: "var(--text-muted)",
                  }}>
                    {tag}
                  </span>
                ))}
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", marginTop: 8 }}>
                <span style={{ fontSize: 11, color: "var(--accent)", display: "flex", alignItems: "center", gap: 4 }}>
                  Use template <ArrowRight size={12} />
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Saved workflows */}
      <div>
        <p className="eyebrow mb-3">Your workflows</p>
        {saved && saved.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {saved.map((wf: any) => (
              <Link
                key={wf.id}
                to={`/workflows/${wf.slug}`}
                className="surface surface-interactive"
                style={{ padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}
              >
                <div>
                  <p style={{ fontFamily: "var(--font-serif)", fontWeight: 600, fontSize: 14, color: "var(--text-primary)" }}>
                    {wf.name}
                  </p>
                  <p style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {wf.block_count} blocks {wf.is_deployed && "· deployed"}
                  </p>
                </div>
                {wf.is_deployed && (
                  <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 6, backgroundColor: "#dcfce7", color: "#16a34a", fontWeight: 600 }}>
                    LIVE
                  </span>
                )}
              </Link>
            ))}
          </div>
        ) : (
          <div className="surface text-center" style={{ padding: 40 }}>
            <Workflow size={32} style={{ margin: "0 auto 8px", color: "var(--text-muted)" }} />
            <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              No saved workflows yet. Start from a template above or create a blank workflow.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
