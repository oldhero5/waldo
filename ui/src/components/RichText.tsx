/**
 * Pretext-style rich inline text — pills, code spans, and chips
 * that stay whole while surrounding text wraps naturally.
 */

/** Inline pill/chip that never breaks across lines */
export function Pill({ children, color, className = "" }: {
  children: React.ReactNode;
  color?: string;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap ${className}`}
      style={{
        backgroundColor: color ? `${color}18` : "var(--bg-inset)",
        color: color || "var(--text-secondary)",
        padding: "2px 8px",
        borderRadius: 8,
        fontSize: 12,
        fontWeight: 500,
        fontFamily: "var(--font-mono)",
        lineHeight: 1.6,
        verticalAlign: "baseline",
      }}
    >
      {children}
    </span>
  );
}

/** Inline code span */
export function Code({ children }: { children: React.ReactNode }) {
  return (
    <code
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "0.9em",
        backgroundColor: "var(--bg-inset)",
        padding: "1px 5px",
        borderRadius: 4,
        color: "var(--accent-warm)",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </code>
  );
}

/** Metric value with label — stays together as a unit */
export function MetricChip({ label, value, trend }: {
  label: string;
  value: string;
  trend?: "up" | "down" | "flat";
}) {
  const trendColor = trend === "up" ? "var(--success)" : trend === "down" ? "var(--danger)" : "var(--text-muted)";
  const trendIcon = trend === "up" ? "↑" : trend === "down" ? "↓" : "";

  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap"
      style={{
        backgroundColor: "var(--bg-inset)",
        padding: "3px 10px",
        borderRadius: 10,
        fontSize: 12,
        lineHeight: 1.6,
      }}
    >
      <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 10 }}>{label}</span>
      <span style={{ color: "var(--text-primary)", fontWeight: 600, fontFamily: "var(--font-mono)" }}>{value}</span>
      {trendIcon && <span style={{ color: trendColor, fontSize: 10 }}>{trendIcon}</span>}
    </span>
  );
}

/** Status indicator with dot */
export function StatusBadge({ status, label }: {
  status: "active" | "inactive" | "training" | "completed" | "failed" | "pending";
  label?: string;
}) {
  const colors: Record<string, string> = {
    active: "var(--success)",
    completed: "var(--success)",
    inactive: "var(--text-muted)",
    pending: "var(--text-muted)",
    training: "var(--accent)",
    failed: "var(--danger)",
  };
  const color = colors[status] || "var(--text-muted)";

  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap"
      style={{
        fontSize: 12,
        fontWeight: 500,
        color,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          backgroundColor: color,
          display: "inline-block",
          animation: status === "training" ? "pulse 2s infinite" : undefined,
        }}
      />
      {label || status}
    </span>
  );
}
