/**
 * Custom React Flow node for workflow blocks.
 * Shows block name, category color, and input/output handles.
 */
import { Handle, Position } from "@xyflow/react";

export default function BlockNode({ data, selected }: any) {
  const color = data.color || "#6b7280";

  return (
    <div
      className="rounded-xl overflow-hidden transition-shadow duration-150"
      style={{ 
        backgroundColor: "var(--bg-surface)",
        border: selected ? `2px solid ${color}` : "1px solid var(--border-default)",
        boxShadow: selected ? `0 0 0 3px ${color}22` : "var(--shadow-sm)",
        minWidth: 160,
      }}
    >
      {/* Color bar */}
      <div style={{ height: 3, backgroundColor: color }} />

      {/* Body */}
      <div className="px-3 py-2.5">
        <p className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
          {data.label}
        </p>
        <p className="text-[10px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          {data.blockType}
        </p>
      </div>

      {/* Input handles (left side) */}
      {data.inputs?.map((port: any, i: number) => (
        <Handle
          key={`in-${port.name}`}
          type="target"
          position={Position.Left}
          id={port.name}
          style={{ 
            top: `${30 + i * 20}%`,
            width: 10,
            height: 10,
            backgroundColor: color,
            border: "2px solid var(--bg-surface)",
          }}
          title={`${port.name} (${port.type})`}
        />
      ))}

      {/* Output handles (right side) */}
      {data.outputs?.map((port: any, i: number) => (
        <Handle
          key={`out-${port.name}`}
          type="source"
          position={Position.Right}
          id={port.name}
          style={{ 
            top: `${30 + i * 20}%`,
            width: 10,
            height: 10,
            backgroundColor: color,
            border: "2px solid var(--bg-surface)",
          }}
          title={`${port.name} (${port.type})`}
        />
      ))}
    </div>
  );
}
