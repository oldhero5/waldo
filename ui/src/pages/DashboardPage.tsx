/**
 * Dashboard — Pretext-inspired editorial layout with accordion sections,
 * rich text pills, and dynamic content reflow.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listJobs, listModels, listProjects, getServeStatus } from "../api";
import { pickKeyMetrics } from "../lib/metrics";
import Accordion from "../components/Accordion";
import { Pill, MetricChip, StatusBadge } from "../components/RichText";
import {
  Upload, Tag, Cpu, Play, ArrowRight, Clock, Zap,
} from "lucide-react";

export default function DashboardPage() {
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: () => listJobs() });
  const { data: models } = useQuery({ queryKey: ["models"], queryFn: listModels });
  const { data: serveStatus } = useQuery({ queryKey: ["serve-status"], queryFn: getServeStatus });

  const totalVideos = projects?.reduce((s, p) => s + p.video_count, 0) || 0;
  const recentJobs = jobs?.slice(-5).reverse() || [];
  const completedJobs = jobs?.filter((j) => j.status === "completed").length || 0;
  const totalModels = models?.length || 0;
  const activeModel = models?.find((m) => m.is_active);
  const bestModel = models?.reduce<typeof models extends (infer T)[] ? T | null : never>((best, m) => {
    const mAP = m.metrics?.["metrics/mAP50(B)"] ?? m.metrics?.["metrics/mAP50(M)"] ?? 0;
    const bestMAP = best ? (best.metrics?.["metrics/mAP50(B)"] ?? best.metrics?.["metrics/mAP50(M)"] ?? 0) : 0;
    return mAP > bestMAP ? m : best;
  }, null);
  const isNewUser = totalVideos === 0 && completedJobs === 0;

  return (
    <div className="max-w-4xl mx-auto mt-6 px-4 sm:px-6 pb-16">
      {/* Hero heading — editorial serif */}
      <h1 style={{ fontFamily: "var(--font-serif)", fontSize: 28, fontWeight: 700, color: "var(--text-primary)", letterSpacing: "-0.02em", marginBottom: 24 }}>
        Dashboard
      </h1>

      {/* Getting started — accordion for new users */}
      {isNewUser && (
        <Accordion title="Getting Started" eyebrow="New to Waldo" defaultOpen accentColor="var(--accent)">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
            {[
              { step: "1", title: "Upload", desc: "Add video footage", icon: Upload, to: "/upload", color: "#2563eb" },
              { step: "2", title: "Label", desc: "AI finds objects", icon: Tag, to: "/upload", color: "#8b5cf6" },
              { step: "3", title: "Train", desc: "Build your model", icon: Cpu, to: "/experiments", color: "#16a34a" },
            ].map((s) => (
              <Link key={s.step} to={s.to} className="surface surface-interactive" style={{ padding: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                  <div style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: s.color + "18", color: s.color, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <s.icon size={18} />
                  </div>
                  <Pill color={s.color}>Step {s.step}</Pill>
                </div>
                <p style={{ fontFamily: "var(--font-serif)", fontWeight: 600, fontSize: 15, color: "var(--text-primary)" }}>{s.title}</p>
                <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>{s.desc}</p>
              </Link>
            ))}
          </div>
        </Accordion>
      )}

      {!isNewUser && (
        <>
          {/* Summary — editorial multi-column layout */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
            {[
              { label: "Videos", value: totalVideos, to: "/datasets" },
              { label: "Datasets", value: completedJobs, to: "/datasets" },
              { label: "Models", value: totalModels, to: "/experiments" },
              { label: "Server", value: serveStatus?.loaded ? "Active" : "Idle", to: "/deploy" },
            ].map((c) => (
              <Link key={c.label} to={c.to} className="surface surface-interactive" style={{ padding: 18 }}>
                <span className="eyebrow">{c.label}</span>
                <p style={{ fontFamily: "var(--font-serif)", fontSize: 26, fontWeight: 700, color: "var(--text-primary)", marginTop: 4 }}>
                  {c.value}
                </p>
              </Link>
            ))}
          </div>

          {/* Best model — rich text with metric pills */}
          {bestModel && (
            <div className="surface" style={{ padding: 18, marginBottom: 20 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <span className="eyebrow" style={{ color: "var(--success)" }}>Best model</span>
                  <p style={{ fontFamily: "var(--font-serif)", fontSize: 16, fontWeight: 600, color: "var(--text-primary)", marginTop: 2 }}>
                    {bestModel.name}
                  </p>
                  {/* Rich inline text with metric pills that don't break */}
                  <p style={{ marginTop: 6, lineHeight: 2 }}>
                    {pickKeyMetrics(bestModel.metrics).map((m) => (
                      <MetricChip key={m.key} label={m.label} value={m.value} />
                    )).reduce<React.ReactNode[]>((acc, chip, i) => {
                      if (i > 0) acc.push(<span key={`sep-${i}`} style={{ margin: "0 4px" }}> </span>);
                      acc.push(chip);
                      return acc;
                    }, [])}
                  </p>
                </div>
                {bestModel.is_active ? (
                  <Link to="/demo" className="px-4 py-2 bg-blue-600 text-white rounded-xl text-sm hover:bg-blue-700" style={{ transition: "all 160ms ease" }}>
                    Try Demo
                  </Link>
                ) : (
                  <Link to="/deploy" className="px-4 py-2 bg-blue-600 text-white rounded-xl text-sm hover:bg-blue-700" style={{ transition: "all 160ms ease" }}>
                    Activate
                  </Link>
                )}
              </div>
            </div>
          )}

          {/* Quick actions — dynamic 3-column grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, marginBottom: 28 }}>
            <Link to="/upload" className="surface surface-interactive" style={{ padding: 16, display: "flex", alignItems: "center", gap: 12 }}>
              <Upload size={18} style={{ color: "var(--text-muted)" }} />
              <div style={{ flex: 1 }}>
                <p style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>Upload Video</p>
                <p style={{ fontSize: 11, color: "var(--text-muted)" }}>Add new footage</p>
              </div>
              <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
            </Link>
            {activeModel && (
              <Link to="/demo" className="surface surface-interactive" style={{ padding: 16, display: "flex", alignItems: "center", gap: 12 }}>
                <Play size={18} style={{ color: "var(--success)" }} />
                <div style={{ flex: 1 }}>
                  <p style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>Try Demo</p>
                  <p style={{ fontSize: 11, color: "var(--text-muted)" }}>{activeModel.name}</p>
                </div>
                <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
              </Link>
            )}
            <Link to="/workflows/new" className="surface surface-interactive" style={{ padding: 16, display: "flex", alignItems: "center", gap: 12 }}>
              <Zap size={18} style={{ color: "var(--accent)" }} />
              <div style={{ flex: 1 }}>
                <p style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>New Workflow</p>
                <p style={{ fontSize: 11, color: "var(--text-muted)" }}>Build a pipeline</p>
              </div>
              <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
            </Link>
          </div>

          {/* Recent activity — accordion with rich text */}
          <Accordion title="Recent Activity" eyebrow="Labeling jobs" count={recentJobs.length} defaultOpen>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {recentJobs.map((job) => (
                <Link
                  key={job.job_id}
                  to={job.status === "completed" ? `/review/${job.job_id}` : `/label/${job.video_id}`}
                  className="surface surface-interactive"
                  style={{ padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <Clock size={13} style={{ color: "var(--text-muted)" }} />
                    <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>
                      {job.text_prompt || "Exemplar"}
                    </span>
                    <StatusBadge status={job.status as any} />
                  </div>
                  <Pill>{job.processed_frames}/{job.total_frames} frames</Pill>
                </Link>
              ))}
              {recentJobs.length === 0 && (
                <p style={{ fontSize: 13, color: "var(--text-muted)", textAlign: "center", padding: 20 }}>
                  No activity yet. <Link to="/upload" style={{ color: "var(--accent)" }}>Upload a video</Link> to get started.
                </p>
              )}
            </div>
          </Accordion>

          {/* Models accordion */}
          {models && models.length > 0 && (
            <Accordion title="Trained Models" eyebrow="Model registry" count={models.length} className="mt-4">
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {models.slice(0, 5).map((m) => {
                  const mAP = m.metrics?.["metrics/mAP50(B)"] ?? m.metrics?.["metrics/mAP50(M)"];
                  return (
                    <Link key={m.id} to="/deploy" className="surface surface-interactive" style={{ padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Cpu size={13} style={{ color: "var(--text-muted)" }} />
                        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>{m.name}</span>
                        <Pill>{m.model_variant}</Pill>
                        {m.is_active && <StatusBadge status="active" label="Active" />}
                      </div>
                      {mAP != null && <MetricChip label="mAP" value={`${(mAP * 100).toFixed(1)}%`} />}
                    </Link>
                  );
                })}
              </div>
            </Accordion>
          )}
        </>
      )}
    </div>
  );
}
