/**
 * Admin-only queue & worker management.
 *
 * Shows live workers, per-queue depth, and stuck jobs with actions to kill
 * tasks, purge queues, and force-fail zombie labeling jobs. Only rendered
 * when the user's workspace role is "admin".
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, RefreshCw, Server, Skull, Trash2 } from "lucide-react";
import {
  getAdminStatus,
  markJobFailed,
  purgeQueue,
  revokeTask,
  type AdminStatus,
  type AdminWorker,
  type StuckJob,
} from "../../api";

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

export function QueueAdminTab() {
  const queryClient = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery<AdminStatus>({
    queryKey: ["admin-status"],
    queryFn: () => getAdminStatus(600),
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-status"] });

  const handleRevoke = async (taskId: string | null) => {
    if (!taskId) return;
    if (!confirm(`Revoke Celery task ${taskId.slice(0, 8)}? The worker will be signalled to stop.`)) return;
    try {
      await revokeTask(taskId, true);
      invalidate();
    } catch (e: any) {
      alert(`Revoke failed: ${e.message}`);
    }
  };

  const handleMarkFailed = async (jobId: string) => {
    if (!confirm(`Force-fail job ${jobId.slice(0, 8)}? This also revokes the underlying Celery task.`)) return;
    try {
      await markJobFailed(jobId, "Marked failed from admin panel");
      invalidate();
    } catch (e: any) {
      alert(`Mark-failed failed: ${e.message}`);
    }
  };

  const handlePurge = async (queueName: string, pending: number) => {
    if (pending === 0) return;
    if (!confirm(`Purge ${pending} pending task(s) from queue "${queueName}"? This cannot be undone.`)) return;
    try {
      const res = await purgeQueue(queueName);
      invalidate();
      alert(`Purged ${res.removed} task(s) from ${queueName}`);
    } catch (e: any) {
      alert(`Purge failed: ${e.message}`);
    }
  };

  if (isLoading) {
    return <p className="text-sm" style={{ color: "var(--text-muted)" }}>Loading queue status…</p>;
  }

  if (error) {
    return (
      <div className="surface p-5" style={{ border: "1px solid var(--danger)" }}>
        <p className="text-sm font-semibold mb-1" style={{ color: "var(--danger)" }}>
          Failed to load admin status
        </p>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          {(error as Error).message}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <p className="eyebrow" style={{ marginBottom: 2 }}>Admin</p>
          <h2 className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
            Queue &amp; Workers
          </h2>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 surface"
          style={{ color: "var(--text-secondary)", borderRadius: 8 }}
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      <WorkersCard workers={data?.workers || []} />
      <QueuesCard
        queues={data?.queues || []}
        onPurge={handlePurge}
      />
      <StuckJobsCard
        jobs={data?.stuck_jobs || []}
        threshold={data?.stuck_threshold_seconds || 600}
        onRevoke={handleRevoke}
        onMarkFailed={handleMarkFailed}
      />
    </div>
  );
}

function WorkersCard({ workers }: { workers: AdminWorker[] }) {
  return (
    <div className="surface p-5">
      <div className="flex items-center gap-2 mb-3">
        <Server size={14} style={{ color: "var(--text-muted)" }} />
        <h3 className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
          Active Workers
        </h3>
        <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
          {workers.length}
        </span>
      </div>

      {workers.length === 0 ? (
        <div
          className="rounded-lg p-3 text-xs flex items-center gap-2"
          style={{ backgroundColor: "var(--warning-soft)", color: "var(--warning)" }}
        >
          <AlertTriangle size={14} />
          No workers reachable. Start one with{" "}
          <code className="font-mono">make dev-labeler</code> or{" "}
          <code className="font-mono">make up-gpu</code>.
        </div>
      ) : (
        <div className="space-y-3">
          {workers.map((w) => (
            <div
              key={w.name}
              className="rounded-lg p-3"
              style={{ backgroundColor: "var(--bg-inset)" }}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium font-mono" style={{ color: "var(--text-primary)" }}>
                  {w.name}
                </span>
                <div className="flex items-center gap-3 text-[10px]" style={{ color: "var(--text-muted)" }}>
                  {w.pool && <span>pool: {w.pool}</span>}
                  {w.uptime_seconds != null && <span>uptime: {formatDuration(w.uptime_seconds)}</span>}
                  <span>reserved: {w.reserved_tasks}</span>
                </div>
              </div>

              {w.active_tasks.length === 0 ? (
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>Idle</p>
              ) : (
                <div className="space-y-1.5">
                  {w.active_tasks.map((t, i) => (
                    <div
                      key={t.id || i}
                      className="flex items-center justify-between text-xs rounded px-2 py-1.5"
                      style={{ backgroundColor: "var(--bg-surface)" }}
                    >
                      <div className="flex-1 min-w-0">
                        <span className="font-mono" style={{ color: "var(--accent)" }}>{t.name}</span>
                        {t.prompt && (
                          <span className="ml-2" style={{ color: "var(--text-secondary)" }}>"{t.prompt}"</span>
                        )}
                        {t.variant && (
                          <span className="ml-2" style={{ color: "var(--text-secondary)" }}>{t.variant}</span>
                        )}
                      </div>
                      <span className="font-mono ml-3 shrink-0" style={{ color: "var(--text-muted)" }}>
                        {formatDuration(t.elapsed_seconds)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function QueuesCard({
  queues,
  onPurge,
}: {
  queues: { name: string; pending: number }[];
  onPurge: (name: string, pending: number) => void;
}) {
  return (
    <div className="surface p-5">
      <h3 className="font-semibold text-sm mb-3" style={{ color: "var(--text-primary)" }}>
        Pending Queues
      </h3>
      <div className="grid grid-cols-2 gap-3">
        {queues.map((q) => (
          <div
            key={q.name}
            className="rounded-lg p-3 flex items-center justify-between"
            style={{ backgroundColor: "var(--bg-inset)" }}
          >
            <div>
              <p className="text-[10px] uppercase tracking-wide mb-0.5" style={{ color: "var(--text-muted)" }}>
                {q.name}
              </p>
              <p className="text-lg font-bold font-mono" style={{ color: "var(--text-primary)" }}>
                {q.pending}
              </p>
            </div>
            <button
              onClick={() => onPurge(q.name, q.pending)}
              disabled={q.pending === 0}
              title={q.pending === 0 ? "Queue is empty" : `Purge ${q.pending} pending`}
              className="p-2 rounded"
              style={{
                color: q.pending === 0 ? "var(--text-muted)" : "var(--danger)",
                opacity: q.pending === 0 ? 0.4 : 1,
                cursor: q.pending === 0 ? "not-allowed" : "pointer",
              }}
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function StuckJobsCard({
  jobs,
  threshold,
  onRevoke,
  onMarkFailed,
}: {
  jobs: StuckJob[];
  threshold: number;
  onRevoke: (taskId: string | null) => void;
  onMarkFailed: (jobId: string) => void;
}) {
  return (
    <div className="surface p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
          Stuck Jobs
        </h3>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          &gt; {formatDuration(threshold)} in pending/labeling
        </span>
      </div>

      {jobs.length === 0 ? (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>No stuck jobs.</p>
      ) : (
        <div className="space-y-2">
          {jobs.map((j) => (
            <div
              key={j.id}
              className="rounded-lg p-3"
              style={{ backgroundColor: "var(--bg-inset)", border: "1px solid var(--warning-soft)" }}
            >
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                    style={{
                      backgroundColor: j.status === "labeling" ? "var(--warning-soft)" : "var(--bg-surface)",
                      color: j.status === "labeling" ? "var(--warning)" : "var(--text-muted)",
                    }}
                  >
                    {j.status}
                  </span>
                  <span className="text-sm truncate" style={{ color: "var(--text-primary)" }}>
                    {j.text_prompt || <em>(no prompt)</em>}
                  </span>
                </div>
                <span className="text-xs font-mono shrink-0 ml-3" style={{ color: "var(--text-muted)" }}>
                  {formatDuration(j.age_seconds)}
                </span>
              </div>

              <div className="flex items-center justify-between mt-2">
                <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                  {j.id.slice(0, 8)}… {j.celery_task_id ? `task ${j.celery_task_id.slice(0, 8)}…` : ""}
                </span>
                <div className="flex items-center gap-1">
                  {j.celery_task_id && (
                    <button
                      onClick={() => onRevoke(j.celery_task_id)}
                      className="flex items-center gap-1 text-[10px] px-2 py-1 rounded"
                      style={{ color: "var(--warning)", border: "1px solid var(--warning)" }}
                      title="Revoke Celery task"
                    >
                      <Ban size={10} /> Revoke
                    </button>
                  )}
                  <button
                    onClick={() => onMarkFailed(j.id)}
                    className="flex items-center gap-1 text-[10px] px-2 py-1 rounded text-white"
                    style={{ backgroundColor: "var(--danger)" }}
                    title="Force job status to failed"
                  >
                    <Skull size={10} /> Mark failed
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
