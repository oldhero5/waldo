/**
 * Workflow block node — Pretext-inspired card with color accent,
 * typed port handles, and editorial typography.
 */
import { Handle, Position } from "@xyflow/react";

export default function BlockNode({ data, selected }: any) {
  const color = data.color || "#6b7280";

  return (
    <div
      style={{
        backgroundColor: "var(--bg-surface)",
        border: selected ? `2px solid ${color}` : "1px solid var(--border-default)",
        borderRadius: 16,
        boxShadow: selected
          ? `0 0 0 4px ${color}18, 0 8px 24px rgb(0 0 0 / 0.08)`
          : "0 2px 8px rgb(0 0 0 / 0.04)",
        minWidth: 180,
        transition: "box-shadow 160ms ease, border-color 160ms ease",
        overflow: "hidden",
      }}
    >
      {/* Color accent bar */}
      <div style={{ height: 4, backgroundColor: color, borderRadius: "16px 16px 0 0" }} />

      {/* Content */}
      <div style={{ padding: "10px 14px 12px" }}>
        <p style={{
          fontFamily: "var(--font-serif)",
          fontSize: 13,
          fontWeight: 600,
          color: "var(--text-primary)",
          lineHeight: 1.2,
          marginBottom: 2,
        }}>
          {data.label}
        </p>
        <p style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--text-muted)",
          letterSpacing: "0.02em",
        }}>
          {data.blockType}
        </p>

        {/* Port labels */}
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
          <div>
            {data.inputs?.slice(0, 3).map((p: any) => (
              <div key={p.name} style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginBottom: 1 }}>
                &larr; {p.name}
              </div>
            ))}
          </div>
          <div style={{ textAlign: "right" }}>
            {data.outputs?.slice(0, 3).map((p: any) => (
              <div key={p.name} style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginBottom: 1 }}>
                {p.name} &rarr;
              </div>
            ))}
          </div>
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
            top: 48 + i * 16,
            width: 10,
            height: 10,
            backgroundColor: color,
            border: "2px solid var(--bg-surface)",
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
            top: 48 + i * 16,
            width: 10,
            height: 10,
            backgroundColor: color,
            border: "2px solid var(--bg-surface)",
            borderRadius: "50%",
          }}
        />
      ))}
    </div>
  );
}
