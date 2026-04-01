import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listJobs, listModels, listProjects, getServeStatus } from "../api";
import Nav from "../components/Nav";
import { pickKeyMetrics } from "../lib/metrics";
import {
  Upload,
  Tag,
  Cpu,
  Rocket,
  Play,
  ArrowRight,
  CheckCircle,
  Clock,
  Zap,
} from "lucide-react";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-700",
  extracting: "bg-blue-100 text-blue-700",
  labeling: "bg-yellow-100 text-yellow-700",
  converting: "bg-purple-100 text-purple-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

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
    <div className="min-h-screen" style={{ backgroundColor: "var(--bg-page)" }}>
      <Nav />
      <div className="max-w-4xl mx-auto mt-6 px-4 sm:px-6 pb-16">
        <h1 className="text-2xl font-bold mb-6" style={{ color: "var(--text-primary)" }}>Dashboard</h1>

        {/* Getting started for new users */}
        {isNewUser && (
          <div className="surface p-6 mb-8">
            <h2 className="text-lg font-bold mb-1" style={{ color: "var(--text-primary)" }}>Welcome to Waldo</h2>
            <p className="text-sm mb-5" style={{ color: "var(--text-secondary)" }}>
              Build custom object detection models in three steps.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {[
                { step: "1", title: "Upload", desc: "Add video footage", icon: Upload, to: "/upload", color: "#2563eb" },
                { step: "2", title: "Label", desc: "AI finds your objects", icon: Tag, to: "/upload", color: "#8b5cf6" },
                { step: "3", title: "Train", desc: "Build your model", icon: Cpu, to: "/experiments", color: "#16a34a" },
              ].map((s) => (
                <Link key={s.step} to={s.to} className="flex items-center gap-3 p-4 rounded-xl border transition-all hover:shadow-md" style={{ borderColor: "var(--border-subtle)" }}>
                  <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0" style={{ backgroundColor: s.color + "18", color: s.color }}>
                    <s.icon size={20} />
                  </div>
                  <div>
                    <p className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>Step {s.step}</p>
                    <p className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>{s.title}</p>
                    <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{s.desc}</p>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        )}

        {/* Summary cards */}
        {!isNewUser && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
              {[
                { label: "Videos", value: totalVideos, icon: Upload, to: "/datasets" },
                { label: "Datasets", value: completedJobs, icon: Tag, to: "/datasets" },
                { label: "Models", value: totalModels, icon: Cpu, to: "/experiments" },
                { label: "Server", value: serveStatus?.loaded ? "Active" : "Inactive", icon: Rocket, to: "/deploy",
                  valueColor: serveStatus?.loaded ? "#4ade80" : "var(--text-muted)" },
              ].map((c) => (
                <Link key={c.label} to={c.to} className="surface surface-interactive p-4">
                  <div className="flex items-center gap-2 text-sm mb-1" style={{ color: "var(--text-muted)" }}>
                    <c.icon size={14} />
                    {c.label}
                  </div>
                  <p className="text-2xl font-bold" style={{ color: c.valueColor || "var(--text-primary)" }}>{c.value}</p>
                </Link>
              ))}
            </div>

            {/* Best model highlight */}
            {bestModel && (
              <div className="surface p-4 mb-6 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-lg flex items-center justify-center bg-green-100">
                    <Zap size={20} className="text-green-600" />
                  </div>
                  <div>
                    <p className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
                      Best model: {bestModel.name}
                    </p>
                    <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                      {pickKeyMetrics(bestModel.metrics).map((m) => `${m.label}: ${m.value}`).join(" · ")}
                    </p>
                  </div>
                </div>
                <div className="flex gap-2">
                  {bestModel.is_active ? (
                    <Link to="/demo" className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">
                      Try Demo
                    </Link>
                  ) : (
                    <Link to="/deploy" className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">
                      Activate
                    </Link>
                  )}
                </div>
              </div>
            )}

            {/* Quick actions */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-8">
              <Link to="/upload" className="surface surface-interactive flex items-center gap-3 p-4">
                <Upload size={20} style={{ color: "var(--text-muted)" }} />
                <div>
                  <p className="font-medium text-sm" style={{ color: "var(--text-primary)" }}>Upload Video</p>
                  <p className="text-xs" style={{ color: "var(--text-muted)" }}>Add new videos to label</p>
                </div>
                <ArrowRight size={14} className="ml-auto" style={{ color: "var(--text-muted)" }} />
              </Link>
              {activeModel && (
                <Link to="/demo" className="surface surface-interactive flex items-center gap-3 p-4">
                  <Play size={20} className="text-green-500" />
                  <div>
                    <p className="font-medium text-sm" style={{ color: "var(--text-primary)" }}>Try Demo</p>
                    <p className="text-xs" style={{ color: "var(--text-muted)" }}>{activeModel.name}</p>
                  </div>
                  <ArrowRight size={14} className="ml-auto" style={{ color: "var(--text-muted)" }} />
                </Link>
              )}
              {recentJobs.length > 0 && recentJobs[0].status === "completed" && (
                <Link to={`/review/${recentJobs[0].job_id}`} className="surface surface-interactive flex items-center gap-3 p-4">
                  <CheckCircle size={20} className="text-blue-500" />
                  <div>
                    <p className="font-medium text-sm" style={{ color: "var(--text-primary)" }}>Continue Reviewing</p>
                    <p className="text-xs" style={{ color: "var(--text-muted)" }}>{recentJobs[0].text_prompt || "Exemplar"}</p>
                  </div>
                  <ArrowRight size={14} className="ml-auto" style={{ color: "var(--text-muted)" }} />
                </Link>
              )}
            </div>

            {/* Recent jobs */}
            {recentJobs.length > 0 && (
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="font-semibold" style={{ color: "var(--text-primary)" }}>Recent Jobs</h2>
                  <Link to="/datasets" className="text-sm text-blue-600 hover:underline">View all</Link>
                </div>
                <div className="space-y-2">
                  {recentJobs.map((job) => (
                    <Link
                      key={job.job_id}
                      to={job.status === "completed" ? `/review/${job.job_id}` : `/label/${job.video_id}`}
                      className="surface surface-interactive flex items-center justify-between p-3"
                    >
                      <div className="flex items-center gap-3">
                        <Clock size={14} style={{ color: "var(--text-muted)" }} />
                        <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                          {job.text_prompt || "Exemplar"}
                        </span>
                        <span className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[job.status] || "bg-gray-100"}`}>
                          {job.status}
                        </span>
                      </div>
                      <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                        {job.processed_frames}/{job.total_frames} frames
                      </span>
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
