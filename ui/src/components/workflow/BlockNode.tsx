/**
 * Workflow block node — Pretext-style card with category icon,
 * typed port indicators, and warm editorial design.
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

const PORT_SHAPES: Record<string, string> = {
  image: "●",
  detections: "◆",
  text: "■",
  number: "▲",
  any: "★",
  image_list: "●●",
};

export default function BlockNode({ data, selected }: any) {
  const color = data.color || "#6b7280";
  const Icon = CATEGORY_ICONS[data.category] || Cpu;

  return (
    <div
      style={{
        width: 220,
        borderRadius: 20,
        overflow: "hidden",
        border: selected ? `2px solid ${color}` : "1px solid var(--border-default)",
        boxShadow: selected
          ? `0 0 0 4px color-mix(in srgb, ${color} 12%, transparent 88%), 0 18px 40px rgb(54 40 23 / 0.12)`
          : "0 8px 24px rgb(54 40 23 / 0.06)",
        transition: "all 160ms ease",
        backgroundColor: "var(--bg-surface)",
      }}
    >
      {/* Header with category icon + color tint */}
      <div
        style={{
          padding: "10px 14px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: `color-mix(in srgb, ${color} 8%, var(--bg-surface) 92%)`,
          borderBottom: `1px solid color-mix(in srgb, ${color} 15%, var(--border-subtle) 85%)`,
        }}
      >
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            backgroundColor: `color-mix(in srgb, ${color} 15%, transparent 85%)`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: color,
            flexShrink: 0,
          }}
        >
          <Icon size={14} />
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <p style={{
            fontFamily: "var(--font-serif)",
            fontSize: 13,
            fontWeight: 700,
            color: "var(--text-primary)",
            lineHeight: 1.2,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}>
            {data.label}
          </p>
          <p style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "var(--text-muted)",
            letterSpacing: "0.04em",
          }}>
            {data.blockType}
          </p>
        </div>
      </div>

      {/* Port section */}
      <div style={{ padding: "8px 14px 10px", display: "flex", justifyContent: "space-between", gap: 8 }}>
        {/* Inputs */}
        <div>
          {data.inputs?.map((p: any) => (
            <div key={p.name} style={{
              fontSize: 10,
              color: "var(--text-secondary)",
              fontFamily: "var(--font-mono)",
              display: "flex",
              alignItems: "center",
              gap: 4,
              marginBottom: 3,
            }}>
              <span style={{ color: color, fontSize: 7 }}>{PORT_SHAPES[p.type] || "●"}</span>
              {p.name}
            </div>
          ))}
        </div>
        {/* Outputs */}
        <div style={{ textAlign: "right" }}>
          {data.outputs?.map((p: any) => (
            <div key={p.name} style={{
              fontSize: 10,
              color: "var(--text-secondary)",
              fontFamily: "var(--font-mono)",
              display: "flex",
              alignItems: "center",
              gap: 4,
              justifyContent: "flex-end",
              marginBottom: 3,
            }}>
              {p.name}
              <span style={{ color: color, fontSize: 7 }}>{PORT_SHAPES[p.type] || "●"}</span>
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
            top: 56 + i * 18,
            width: 12,
            height: 12,
            backgroundColor: "var(--bg-surface)",
            border: `2.5px solid ${color}`,
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
            top: 56 + i * 18,
            width: 12,
            height: 12,
            backgroundColor: color,
            border: `2.5px solid var(--bg-surface)`,
            borderRadius: "50%",
          }}
        />
      ))}
    </div>
  );
}
