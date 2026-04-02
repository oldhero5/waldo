/**
 * Workflow block node — crisp rendering with inline config,
 * category icons, and typed port indicators.
 *
 * Fixes from audit:
 * - No color-mix() in shadows (causes blurriness)
 * - Integer pixel values only (no 2.5px)
 * - Neutral shadows instead of warm brown at small sizes
 * - -webkit-font-smoothing: antialiased
 * - Inline config display for key settings
 */
import { Handle, Position } from "@xyflow/react";
import { Cpu, Scissors, MessageSquare, ArrowDownToLine, Eye, GitBranch, Scan, Rocket } from "lucide-react";

const CATEGORY_ICONS: Record<string, typeof Cpu> = {
  models: Cpu,
  transforms: Scissors,
  visualization: Eye,
  logic: GitBranch,
  classical_cv: Scan,
  ai: MessageSquare,
  io: ArrowDownToLine,
  platform: Rocket,
};

const PORT_TYPE_COLORS: Record<string, string> = {
  image: "#3b82f6",
  detections: "#8b5cf6",
  text: "#f59e0b",
  number: "#22c55e",
  any: "#6b7280",
  image_list: "#3b82f6",
};

export default function BlockNode({ data, selected }: any) {
  const color = data.color || "#6b7280";
  const Icon = CATEGORY_ICONS[data.category] || Cpu;

  // Get the primary config value to show inline
  const configEntries = Object.entries(data.configSchema || {});
  const primaryConfig = configEntries.length > 0 ? configEntries[0] : null;
  const primaryValue = primaryConfig ? (data.config?.[primaryConfig[0]] ?? (primaryConfig[1] as any).default) : null;

  return (
    <div
      style={{
        width: 230,
        borderRadius: 14,
        overflow: "hidden",
        border: selected ? `2px solid ${color}` : `1px solid rgba(0,0,0,0.1)`,
        boxShadow: selected
          ? `0 0 0 3px ${color}20, 0 4px 12px rgba(0,0,0,0.12)`
          : "0 2px 8px rgba(0,0,0,0.06)",
        transition: "box-shadow 160ms ease, border-color 160ms ease",
        backgroundColor: "#ffffff",
        WebkitFontSmoothing: "antialiased",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "10px 12px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          backgroundColor: `${color}08`,
          borderBottom: `1px solid ${color}15`,
        }}
      >
        <div
          style={{
            width: 26,
            height: 26,
            borderRadius: 7,
            backgroundColor: `${color}15`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: color,
            flexShrink: 0,
          }}
        >
          <Icon size={13} strokeWidth={2} />
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <p style={{
            fontFamily: "system-ui, -apple-system, sans-serif",
            fontSize: 12,
            fontWeight: 600,
            color: "#1a1a1a",
            lineHeight: 1.3,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}>
            {data.label}
          </p>
        </div>
      </div>

      {/* Inline config preview */}
      {primaryConfig && primaryValue != null && (
        <div style={{
          padding: "6px 12px",
          backgroundColor: "#f8f8f8",
          borderBottom: "1px solid rgba(0,0,0,0.05)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}>
          <span style={{ fontSize: 10, color: "#888", fontFamily: "ui-monospace, monospace" }}>
            {(primaryConfig[1] as any).label || primaryConfig[0]}
          </span>
          <span style={{ fontSize: 10, color: "#333", fontWeight: 600, fontFamily: "ui-monospace, monospace" }}>
            {typeof primaryValue === "number" ? primaryValue.toFixed(primaryValue < 1 ? 2 : 0) : String(primaryValue).slice(0, 20)}
          </span>
        </div>
      )}

      {/* Ports */}
      <div style={{ padding: "6px 12px 8px", display: "flex", justifyContent: "space-between", gap: 8 }}>
        <div>
          {data.inputs?.map((p: any) => (
            <div key={p.name} style={{
              fontSize: 9,
              color: "#777",
              fontFamily: "ui-monospace, monospace",
              display: "flex",
              alignItems: "center",
              gap: 3,
              marginBottom: 2,
              lineHeight: 1.6,
            }}>
              <span style={{
                width: 5, height: 5, borderRadius: "50%",
                backgroundColor: PORT_TYPE_COLORS[p.type] || "#999",
                display: "inline-block",
              }} />
              {p.name}
            </div>
          ))}
        </div>
        <div style={{ textAlign: "right" }}>
          {data.outputs?.map((p: any) => (
            <div key={p.name} style={{
              fontSize: 9,
              color: "#777",
              fontFamily: "ui-monospace, monospace",
              display: "flex",
              alignItems: "center",
              gap: 3,
              justifyContent: "flex-end",
              marginBottom: 2,
              lineHeight: 1.6,
            }}>
              {p.name}
              <span style={{
                width: 5, height: 5, borderRadius: "50%",
                backgroundColor: PORT_TYPE_COLORS[p.type] || "#999",
                display: "inline-block",
              }} />
            </div>
          ))}
        </div>
      </div>

      {/* Input handles */}
      {data.inputs?.map((port: any, i: number) => (
        <Handle
          key={`in-${port.name}`}
          type="target"
          position={Position.Left}
          id={port.name}
          style={{
            top: primaryConfig ? 72 + i * 16 : 52 + i * 16,
            width: 10,
            height: 10,
            backgroundColor: "#fff",
            border: `2px solid ${PORT_TYPE_COLORS[port.type] || color}`,
            borderRadius: "50%",
          }}
        />
      ))}

      {/* Output handles */}
      {data.outputs?.map((port: any, i: number) => (
        <Handle
          key={`out-${port.name}`}
          type="source"
          position={Position.Right}
          id={port.name}
          style={{
            top: primaryConfig ? 72 + i * 16 : 52 + i * 16,
            width: 10,
            height: 10,
            backgroundColor: PORT_TYPE_COLORS[port.type] || color,
            border: "2px solid #fff",
            borderRadius: "50%",
          }}
        />
      ))}
    </div>
  );
}
