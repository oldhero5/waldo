/** Dashboard — workspace activity and the next useful action. */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listJobs, listModels, listProjects, listTrainingRuns, getServeStatus } from "../api";
import { pickKeyMetrics } from "../lib/metrics";
import { MetricChip, StatusBadge } from "../components/RichText";
import {
  Upload, Cpu, Rocket, ArrowRight, Play, FlaskConical, Database,
  Sparkles, TrendingUp, Eye, Zap,
} from "lucide-react";


export default function DashboardPage() {
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: () => listJobs() });
  const { data: models } = useQuery({ queryKey: ["models"], queryFn: listModels });
  const { data: runs } = useQuery({ queryKey: ["training-runs"], queryFn: listTrainingRuns });
  const { data: serveStatus } = useQuery({ queryKey: ["serve-status"], queryFn: getServeStatus });

  const totalVideos = projects?.reduce((s, p) => s + p.video_count, 0) || 0;
  const completedJobs = jobs?.filter((j) => j.status === "completed") || [];
  const totalAnnotations = completedJobs.reduce((s, j) => s + (j.annotation_count || 0), 0);
  const totalModels = models?.length || 0;
  const activeModel = models?.find((m) => m.is_active);
  const bestModel = models?.reduce<typeof models extends (infer T)[] ? T | null : never>((best, m) => {
    const mAP = m.metrics?.["metrics/mAP50(B)"] ?? m.metrics?.["metrics/mAP50(M)"] ?? 0;
    const bestMAP = best ? (best.metrics?.["metrics/mAP50(B)"] ?? best.metrics?.["metrics/mAP50(M)"] ?? 0) : 0;
    return mAP > bestMAP ? m : best;
  }, null);
  const bestMaP = bestModel ? (bestModel.metrics?.["metrics/mAP50(B)"] ?? bestModel.metrics?.["metrics/mAP50(M)"] ?? 0) : 0;

  const completedRuns = runs?.filter((r) => r.status === "completed") || [];
  const activeRun = runs?.find((r) => ["training", "validating", "queued", "preparing"].includes(r.status));
  const isNewUser = totalVideos === 0 && completedJobs.length === 0;

  // Recent jobs panel — slice/reverse only when jobs change, not every render.
  const recentJobs = useMemo(
    () => (jobs ? [...jobs.slice(-4)].reverse() : []),
    [jobs]
  );

  // Determine what the user should do next
  const nextAction = activeRun
    ? { icon: Eye, label: "Monitor training", desc: `${activeRun.name} — epoch ${activeRun.epoch_current}/${activeRun.total_epochs}`, to: `/train/${activeRun.run_id}`, color: "var(--accent)" }
    : isNewUser
    ? { icon: Upload, label: "Upload your first video", desc: "Drag and drop footage to get started", to: "/upload", color: "var(--accent)" }
    : bestModel && !activeModel
    ? { icon: Rocket, label: "Deploy your best model", desc: `${bestModel.name} — mAP ${(bestMaP * 100).toFixed(1)}%`, to: "/deploy", color: "var(--success)" }
    : completedJobs.length > 0 && totalModels === 0
    ? { icon: Zap, label: "Train your first model", desc: `${completedJobs.length} dataset${completedJobs.length !== 1 ? "s" : ""} ready`, to: "/experiments", color: "var(--accent)" }
    : activeModel
    ? { icon: Play, label: "Try your model", desc: activeModel.name, to: "/deploy/test", color: "var(--success)" }
    : { icon: Upload, label: "Upload more footage", desc: "Add videos to improve your dataset", to: "/upload", color: "var(--accent)" };

  return (
    <div className="max-w-4xl mx-auto mt-6 px-4 sm:px-6 pb-16">

      <section className="mb-6" aria-labelledby="dashboard-title">
        <h1 id="dashboard-title" className="text-2xl font-semibold" style={{ color: "var(--text-primary)" }}>Workspace overview</h1>
        <p className="text-sm mt-2 mb-6" style={{ color: "var(--text-secondary)" }}>Review your footage, labeled datasets, and model activity.</p>
          {/* Stat strip */}
          <div className="flex flex-wrap gap-x-8 gap-y-4">
            {[
              { label: "Videos", value: totalVideos },
              { label: "Annotations", value: totalAnnotations.toLocaleString() },
              { label: "Models", value: totalModels },
              ...(bestMaP > 0 ? [{ label: "Best mAP", value: `${(bestMaP * 100).toFixed(1)}%`, accent: true }] : []),
            ].map((s) => (
              <div key={s.label}>
                <span className="eyebrow block">{s.label}</span>
                <span style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: 26,
                  fontWeight: 700,
                  color: "accent" in s && s.accent ? "var(--success)" : "var(--text-primary)",
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing: "-0.02em",
                }}>
                  {s.value}
                </span>
              </div>
            ))}
          </div>
      </section>

      {/* Next action — the ONE thing to do */}
      <Link
        to={nextAction.to}
        className="surface surface-interactive flex items-center gap-4 mb-6"
        style={{ padding: "18px 20px" }}
      >
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
          style={{ backgroundColor: `color-mix(in srgb, ${nextAction.color} 15%, transparent)`, color: nextAction.color }}
        >
          <nextAction.icon size={20} />
        </div>
        <div className="flex-1">
          <p style={{ fontFamily: "var(--font-serif)", fontSize: 15, fontWeight: 600, color: "var(--text-primary)" }}>
            {nextAction.label}
          </p>
          <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 1 }}>{nextAction.desc}</p>
        </div>
        <ArrowRight size={16} style={{ color: "var(--text-muted)" }} />
      </Link>

      {/* Active training progress (if running) */}
      {activeRun && (
        <div className="surface p-4 mb-6" style={{ borderColor: "var(--accent)" }}>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Cpu size={14} className="animate-pulse" style={{ color: "var(--accent)" }} />
              <span style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)" }}>Training: {activeRun.name}</span>
            </div>
            <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
              Epoch {activeRun.epoch_current}/{activeRun.total_epochs}
            </span>
          </div>
          <div className="w-full h-1.5 rounded-full" style={{ backgroundColor: "var(--bg-inset)" }}>
            <div className="h-full rounded-full transition-all" style={{ width: `${(activeRun.epoch_current / activeRun.total_epochs) * 100}%`, backgroundColor: "var(--accent)" }} />
          </div>
        </div>
      )}

      {/* Three-column grid: workflow shortcuts */}
      <div className="grid grid-cols-3 gap-3 mb-6">
        {[
          { icon: Database, label: "Datasets", count: completedJobs.length, to: "/datasets" },
          { icon: FlaskConical, label: "Experiments", count: completedRuns.length, to: "/experiments" },
          { icon: Rocket, label: "Deploy", count: serveStatus?.loaded ? "Active" : "—", to: "/deploy" },
        ].map((s) => {
          const Icon = s.icon;
          return (
            <Link key={s.label} to={s.to} className="surface surface-interactive" style={{ padding: 16 }}>
              <Icon size={16} style={{ color: "var(--text-muted)", marginBottom: 8 }} />
              <p style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
                {s.count}
              </p>
              <span className="eyebrow">{s.label}</span>
            </Link>
          );
        })}
      </div>

      {/* Two-column: best model + recent activity */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Best model */}
        {bestModel && (
          <div className="surface" style={{ padding: 18 }}>
            <div className="flex items-center gap-2 mb-3">
              <Sparkles size={13} style={{ color: "var(--text-secondary)" }} />
              <span className="eyebrow" style={{ color: "var(--success)" }}>Best model</span>
            </div>
            <p style={{ fontFamily: "var(--font-serif)", fontSize: 15, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>
              {bestModel.name}
            </p>
            <div className="flex flex-wrap gap-1.5 mb-3">
              {pickKeyMetrics(bestModel.metrics).map((m) => (
                <MetricChip key={m.key} label={m.label} value={m.value} />
              ))}
            </div>
            <Link
              to={bestModel.is_active ? "/deploy/test" : "/deploy"}
              className="inline-flex items-center gap-1.5 text-xs font-medium"
              style={{ color: "var(--accent)" }}
            >
              {bestModel.is_active ? "Try in demo" : "Deploy this model"} <ArrowRight size={11} />
            </Link>
          </div>
        )}

        {/* Recent activity */}
        <div className="surface" style={{ padding: 18 }}>
          <span className="eyebrow block mb-3">Recent activity</span>
          {recentJobs.length > 0 ? (
            <div className="space-y-2">
              {recentJobs.map((job) => (
                <Link
                  key={job.job_id}
                  to={job.status === "completed" ? `/review/${job.job_id}` : "/datasets"}
                  className="flex items-center justify-between py-1.5 transition-colors"
                  style={{ color: "var(--text-primary)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.color = "var(--accent)")}
                  onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-primary)")}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <StatusBadge status={job.status} />
                    <span className="truncate" style={{ fontSize: 13, fontWeight: 500 }}>
                      {job.name || job.text_prompt || "Exemplar"}
                    </span>
                  </div>
                  <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", flexShrink: 0, marginLeft: 8 }}>
                    {job.annotation_count != null ? `${job.annotation_count} labels` : `${job.total_frames} vid`}
                  </span>
                </Link>
              ))}
            </div>
          ) : (
            <p style={{ fontSize: 13, color: "var(--text-muted)" }}>
              No activity yet. <Link to="/upload" style={{ color: "var(--accent)" }}>Upload a video</Link> to start.
            </p>
          )}
        </div>

        {/* Quick links (only show if no best model to fill the space) */}
        {!bestModel && (
          <div className="surface" style={{ padding: 18 }}>
            <span className="eyebrow block mb-3">Get started</span>
            <div className="space-y-2">
              {[
                { icon: Upload, label: "Upload footage", to: "/upload" },
                { icon: TrendingUp, label: "Browse experiments", to: "/experiments" },
                { icon: Rocket, label: "Deploy a model", to: "/deploy" },
              ].map((a) => {
                const Icon = a.icon;
                return (
                  <Link key={a.label} to={a.to} className="flex items-center gap-2 py-1.5" style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                    <Icon size={13} style={{ color: "var(--text-muted)" }} />
                    {a.label}
                  </Link>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
