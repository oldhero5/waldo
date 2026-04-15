import type { ServeStatus } from "../../api";

export function ServerStatusHeader({ status }: { status: ServeStatus | undefined }) {
  return (
    <div
      className="surface flex items-center justify-between px-5 py-3 mb-5"
      style={{ borderRadius: 12 }}
    >
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span
            className={`w-2.5 h-2.5 rounded-full ${status?.loaded ? "bg-green-500" : "bg-gray-400"}`}
          />
          <span
            className="text-sm font-semibold"
            style={{ color: "var(--text-primary)" }}
          >
            Inference Server
          </span>
          <span
            className="text-xs font-medium px-2 py-0.5 rounded-full"
            style={{
              backgroundColor: status?.loaded
                ? "var(--success-soft)"
                : "var(--bg-inset)",
              color: status?.loaded
                ? "var(--success)"
                : "var(--text-muted)",
            }}
          >
            {status?.loaded ? "Active" : "Inactive"}
          </span>
        </div>
        {status?.loaded && (
          <div className="flex items-center gap-4 text-xs" style={{ color: "var(--text-secondary)" }}>
            <span>
              <span style={{ color: "var(--text-muted)" }}>Model: </span>
              <span className="font-medium" style={{ color: "var(--text-primary)" }}>{status.model_name}</span>
            </span>
            <span>
              <span style={{ color: "var(--text-muted)" }}>Variant: </span>
              {status.model_variant}
            </span>
            <span>
              <span style={{ color: "var(--text-muted)" }}>Task: </span>
              {status.task_type}
            </span>
            <span>
              <span style={{ color: "var(--text-muted)" }}>Device: </span>
              <span className="font-mono">{status.device}</span>
            </span>
          </div>
        )}
      </div>
      {!status?.loaded && (
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          Activate a model below to start serving
        </span>
      )}
    </div>
  );
}
