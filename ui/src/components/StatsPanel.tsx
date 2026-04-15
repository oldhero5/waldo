import type { JobStats } from "../api";

export default function StatsPanel({ stats }: { stats: JobStats }) {
  return (
    <div className="rounded-lg p-4 space-y-4" style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)" }}>
      <p className="eyebrow">Dataset Stats</p>

      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        {[
          { label: "Annotations", value: stats.total_annotations },
          { label: "Frames", value: `${stats.annotated_frames}/${stats.total_frames}` },
          { label: "Density", value: `${stats.annotation_density}/fr` },
          { label: "Empty", value: stats.empty_frames },
        ].map((s) => (
          <div key={s.label}>
            <span className="eyebrow block">{s.label}</span>
            <span style={{ fontSize: 18, fontWeight: 700, fontFamily: "var(--font-serif)", color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
              {s.value}
            </span>
          </div>
        ))}
      </div>

      {stats.by_class.length > 0 && (
        <div>
          <p className="eyebrow mb-1.5">Classes</p>
          <div className="space-y-1">
            {stats.by_class.map((c) => (
              <div key={c.name} className="flex justify-between items-baseline">
                <span style={{ fontSize: 13, color: "var(--text-primary)" }}>{c.name}</span>
                <span style={{ fontSize: 13, fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>{c.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <p className="eyebrow mb-1.5">Review Status</p>
        <div className="space-y-1" style={{ fontSize: 12 }}>
          {[
            { label: "Accepted", count: stats.by_status.accepted || 0, color: "var(--success)" },
            { label: "Rejected", count: stats.by_status.rejected || 0, color: "var(--danger)" },
            { label: "Pending", count: stats.by_status.pending || 0, color: "var(--text-muted)" },
          ].map((s) => (
            <div key={s.label} className="flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: s.color }} />
                <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>{s.label}</span>
              </span>
              <span style={{ fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums", color: "var(--text-primary)", fontWeight: 600 }}>
                {s.count}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
