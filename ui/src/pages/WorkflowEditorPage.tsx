/**
 * Visual workflow editor — drag blocks from palette, connect ports, run pipelines.
 * Uses React Flow for the canvas with a Pretext-inspired warm design.
 */
import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Panel,
  useNodesState,
  useEdgesState,
  addEdge,
  type Connection,
  type NodeTypes,
  BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Play, Loader2, Trash2 } from "lucide-react";
import BlockNode from "../components/workflow/BlockNode";

// API
const BASE = "/api/v1";

interface BlockSchema {
  name: string;
  display_name: string;
  description: string;
  category: string;
  inputs: { name: string; type: string; description: string; required: boolean }[];
  outputs: { name: string; type: string; description: string }[];
  config_schema: Record<string, any>;
}

const nodeTypes: NodeTypes = {
  block: BlockNode,
};

const CATEGORY_COLORS: Record<string, string> = {
  io: "#3b82f6",
  models: "#8b5cf6",
  transforms: "#f59e0b",
  ai: "#22c55e",
  general: "#6b7280",
};

let nodeId = 0;
const getId = () => `node_${++nodeId}`;

export default function WorkflowEditorPage() {
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);
  const [blocks, setBlocks] = useState<BlockSchema[]>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  // Fetch available blocks
  useEffect(() => {
    fetch(`${BASE}/workflows/blocks`)
      .then((r) => r.json())
      .then((d) => setBlocks(d.blocks || []))
      .catch(() => {});
  }, []);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({ ...params, animated: true, style: { stroke: "var(--accent)" } }, eds)),
    [setEdges]
  );

  const addBlock = useCallback(
    (block: BlockSchema) => {
      const id = getId();
      const newNode = {
        id,
        type: "block",
        position: { x: 250 + Math.random() * 200, y: 100 + Math.random() * 200 },
        data: {
          label: block.display_name,
          blockType: block.name,
          category: block.category,
          color: CATEGORY_COLORS[block.category] || "#6b7280",
          inputs: block.inputs,
          outputs: block.outputs,
          config: {},
          configSchema: block.config_schema,
        },
      };
      setNodes((nds) => [...nds, newNode]);
    },
    [setNodes]
  );

  const deleteSelected = useCallback(() => {
    if (!selectedNode) return;
    setNodes((nds) => nds.filter((n) => n.id !== selectedNode));
    setEdges((eds) => eds.filter((e) => e.source !== selectedNode && e.target !== selectedNode));
    setSelectedNode(null);
  }, [selectedNode, setNodes, setEdges]);

  const runWorkflow = useCallback(async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const graph = {
        nodes: nodes.map((n) => ({
          id: n.id,
          type: n.data.blockType,
          config: n.data.config || {},
        })),
        edges: edges.map((e) => ({
          source: e.source,
          source_port: e.sourceHandle || "output",
          target: e.target,
          target_port: e.targetHandle || "input",
        })),
      };
      const res = await fetch(`${BASE}/workflows/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph }),
      });
      const data = await res.json();
      setRunResult(data);
    } catch (e: any) {
      setRunResult({ errors: [e.message] });
    } finally {
      setRunning(false);
    }
  }, [nodes, edges]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Delete" || e.key === "Backspace") deleteSelected();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [deleteSelected]);

  const selectedNodeData = nodes.find((n) => n.id === selectedNode)?.data;

  return (
    <div className="h-screen flex" style={{ backgroundColor: "var(--bg-page)" }}>
      {/* Block palette */}
      <div
        className="w-56 shrink-0 overflow-y-auto p-3"
        style={{ backgroundColor: "var(--bg-surface)", borderRight: "1px solid var(--border-subtle)" }}
      >
        <h2 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: "var(--text-muted)" }}>
          Blocks
        </h2>
        {Object.entries(
          blocks.reduce<Record<string, BlockSchema[]>>((acc, b) => {
            (acc[b.category] = acc[b.category] || []).push(b);
            return acc;
          }, {})
        ).map(([cat, catBlocks]) => (
          <div key={cat} className="mb-4">
            <p className="text-[10px] uppercase tracking-wider mb-1.5 font-medium" style={{ color: CATEGORY_COLORS[cat] || "var(--text-muted)" }}>
              {cat}
            </p>
            <div className="space-y-1">
              {catBlocks.map((b) => (
                <button
                  key={b.name}
                  onClick={() => addBlock(b)}
                  className="w-full text-left px-3 py-2 rounded-lg text-xs transition-all duration-150 surface surface-interactive"
                >
                  <span className="font-medium block" style={{ color: "var(--text-primary)" }}>{b.display_name}</span>
                  <span className="block mt-0.5" style={{ color: "var(--text-muted)", fontSize: "10px" }}>{b.description.slice(0, 60)}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Canvas */}
      <div className="flex-1 relative">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={(_, node) => setSelectedNode(node.id)}
          onPaneClick={() => setSelectedNode(null)}
          nodeTypes={nodeTypes}
          fitView
          deleteKeyCode={null}
          style={{ backgroundColor: "var(--bg-page)" }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--border-subtle)" />
          <Controls
            style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 12 }}
          />
          <MiniMap
            style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 12 }}
            nodeColor={(n: any) => n.data?.color || "#6b7280"}
          />

          {/* Toolbar */}
          <Panel position="top-right">
            <div className="flex gap-2">
              {selectedNode && (
                <button
                  onClick={deleteSelected}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-red-600 surface"
                >
                  <Trash2 size={13} /> Delete
                </button>
              )}
              <button
                onClick={runWorkflow}
                disabled={running || nodes.length === 0}
                className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-40 transition-colors"
              >
                {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                {running ? "Running..." : "Run"}
              </button>
            </div>
          </Panel>

          {/* Run result panel */}
          {runResult && (
            <Panel position="bottom-center">
              <div className="surface p-4 max-w-lg max-h-48 overflow-y-auto" style={{ minWidth: 300 }}>
                {runResult.errors?.length > 0 ? (
                  <div>
                    <p className="text-xs font-medium text-red-500 mb-1">Errors</p>
                    {runResult.errors.map((e: string, i: number) => (
                      <p key={i} className="text-xs text-red-400">{e}</p>
                    ))}
                  </div>
                ) : (
                  <div>
                    <p className="text-xs font-medium mb-1" style={{ color: "var(--text-primary)" }}>Result</p>
                    <pre className="text-xs font-mono overflow-x-auto" style={{ color: "var(--text-secondary)" }}>
                      {JSON.stringify(runResult.result, null, 2)?.slice(0, 500)}
                    </pre>
                    {runResult.metadata && (
                      <div className="mt-2 pt-2" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                        <p className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>Timing</p>
                        {Object.entries(runResult.metadata).map(([nid, meta]: [string, any]) => (
                          <span key={nid} className="text-[10px] mr-3" style={{ color: "var(--text-secondary)" }}>
                            {meta.block_type}: {meta.elapsed_ms}ms
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                <button onClick={() => setRunResult(null)} className="text-[10px] mt-2 underline" style={{ color: "var(--text-muted)" }}>
                  Dismiss
                </button>
              </div>
            </Panel>
          )}
        </ReactFlow>
      </div>

      {/* Config panel (right) */}
      {selectedNodeData && (
        <div
          className="w-64 shrink-0 overflow-y-auto p-4"
          style={{ backgroundColor: "var(--bg-surface)", borderLeft: "1px solid var(--border-subtle)" }}
        >
          <h3 className="font-semibold text-sm mb-1" style={{ color: "var(--text-primary)" }}>
            {selectedNodeData.label}
          </h3>
          <p className="text-[10px] mb-4" style={{ color: "var(--text-muted)" }}>
            {selectedNodeData.blockType}
          </p>

          {/* Ports */}
          <div className="mb-4">
            <p className="text-[10px] uppercase tracking-wide font-medium mb-1" style={{ color: "var(--text-muted)" }}>Inputs</p>
            {selectedNodeData.inputs?.map((p: any) => (
              <div key={p.name} className="text-xs py-0.5" style={{ color: "var(--text-secondary)" }}>
                <span className="font-mono">{p.name}</span>
                <span className="ml-1 opacity-60">({p.type})</span>
              </div>
            ))}
          </div>
          <div className="mb-4">
            <p className="text-[10px] uppercase tracking-wide font-medium mb-1" style={{ color: "var(--text-muted)" }}>Outputs</p>
            {selectedNodeData.outputs?.map((p: any) => (
              <div key={p.name} className="text-xs py-0.5" style={{ color: "var(--text-secondary)" }}>
                <span className="font-mono">{p.name}</span>
                <span className="ml-1 opacity-60">({p.type})</span>
              </div>
            ))}
          </div>

          {/* Config fields */}
          {Object.keys(selectedNodeData.configSchema || {}).length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wide font-medium mb-2" style={{ color: "var(--text-muted)" }}>Configuration</p>
              {Object.entries(selectedNodeData.configSchema).map(([key, schema]: [string, any]) => (
                <div key={key} className="mb-3">
                  <label className="text-xs block mb-1" style={{ color: "var(--text-secondary)" }}>
                    {schema.label || key}
                  </label>
                  <input
                    type={schema.type === "number" ? "number" : "text"}
                    defaultValue={schema.default ?? ""}
                    onChange={(e) => {
                      setNodes((nds) =>
                        nds.map((n) =>
                          n.id === selectedNode
                            ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: schema.type === "number" ? Number(e.target.value) : e.target.value } } }
                            : n
                        )
                      );
                    }}
                    className="w-full px-2 py-1.5 rounded-md border text-xs"
                    style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
