/**
 * Visual workflow editor — Pretext-inspired editorial design.
 * Block palette with warm cards, React Flow canvas, config panel.
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
import { Play, Loader2, Trash2, ChevronDown, ChevronRight, Cpu, Scissors, Filter, MessageSquare, ArrowDownToLine, Eye, GitBranch, Scan } from "lucide-react";
import BlockNode from "../components/workflow/BlockNode";

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

const nodeTypes: NodeTypes = { block: BlockNode };

const CATEGORY_META: Record<string, { color: string; icon: typeof Cpu; label: string }> = {
  io: { color: "#3b82f6", icon: ArrowDownToLine, label: "Input / Output" },
  models: { color: "#8b5cf6", icon: Cpu, label: "Models" },
  transforms: { color: "#f59e0b", icon: Scissors, label: "Transforms" },
  visualization: { color: "#ec4899", icon: Eye, label: "Visualization" },
  logic: { color: "#06b6d4", icon: GitBranch, label: "Logic" },
  classical_cv: { color: "#14b8a6", icon: Scan, label: "Classical CV" },
  ai: { color: "#22c55e", icon: MessageSquare, label: "AI" },
  general: { color: "#6b7280", icon: Filter, label: "General" },
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
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch(`${BASE}/workflows/blocks`)
      .then((r) => r.json())
      .then((d) => setBlocks(d.blocks || []))
      .catch(() => {});
  }, []);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({
      ...params,
      animated: true,
      style: { stroke: "var(--accent)", strokeWidth: 2 },
    }, eds)),
    [setEdges]
  );

  const addBlock = useCallback(
    (block: BlockSchema) => {
      const id = getId();
      const catMeta = CATEGORY_META[block.category] || CATEGORY_META.general;
      setNodes((nds) => [...nds, {
        id,
        type: "block",
        position: { x: 300 + Math.random() * 200, y: 80 + nodes.length * 80 },
        data: {
          label: block.display_name,
          blockType: block.name,
          category: block.category,
          color: catMeta.color,
          inputs: block.inputs,
          outputs: block.outputs,
          config: {},
          configSchema: block.config_schema,
        },
      }]);
    },
    [setNodes, nodes.length]
  );

  const deleteSelected = useCallback(() => {
    if (!selectedNode) return;
    setNodes((nds) => nds.filter((n: any) => n.id !== selectedNode));
    setEdges((eds) => eds.filter((e: any) => e.source !== selectedNode && e.target !== selectedNode));
    setSelectedNode(null);
  }, [selectedNode, setNodes, setEdges]);

  const runWorkflow = useCallback(async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const graph = {
        nodes: nodes.map((n: any) => ({ id: n.id, type: n.data.blockType, config: n.data.config || {} })),
        edges: edges.map((e: any) => ({
          source: e.source, source_port: e.sourceHandle || "output",
          target: e.target, target_port: e.targetHandle || "input",
        })),
      };
      const res = await fetch(`${BASE}/workflows/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph }),
      });
      setRunResult(await res.json());
    } catch (e: any) {
      setRunResult({ errors: [e.message] });
    } finally {
      setRunning(false);
    }
  }, [nodes, edges]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.key === "Delete" || e.key === "Backspace") && !(e.target instanceof HTMLInputElement)) deleteSelected();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [deleteSelected]);

  const toggleCategory = (cat: string) => {
    setCollapsedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat); else next.add(cat);
      return next;
    });
  };

  const selectedNodeData = nodes.find((n: any) => n.id === selectedNode)?.data;

  // Group blocks by category
  const grouped = blocks.reduce<Record<string, BlockSchema[]>>((acc, b) => {
    (acc[b.category] = acc[b.category] || []).push(b);
    return acc;
  }, {});

  return (
    <div className="h-screen flex">
      {/* ── Block palette (accordion) ── */}
      <div
        className="w-60 shrink-0 overflow-y-auto"
        style={{ backgroundColor: "var(--bg-surface)", borderRight: "1px solid var(--border-subtle)" }}
      >
        <div className="p-4 pb-2" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
          <h2 className="text-sm font-bold" style={{ fontFamily: "var(--font-serif)", color: "var(--text-primary)" }}>
            Blocks
          </h2>
          <p className="text-[10px] mt-0.5" style={{ color: "var(--text-muted)" }}>
            Drag to canvas or click to add
          </p>
        </div>

        <div className="p-2">
          {Object.entries(grouped).map(([cat, catBlocks]) => {
            const meta = CATEGORY_META[cat] || CATEGORY_META.general;
            const Icon = meta.icon;
            const collapsed = collapsedCategories.has(cat);

            return (
              <div key={cat} className="mb-1">
                {/* Accordion header */}
                <button
                  onClick={() => toggleCategory(cat)}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors"
                  style={{ color: meta.color }}
                  onMouseEnter={(e) => e.currentTarget.style.backgroundColor = "var(--bg-inset)"}
                  onMouseLeave={(e) => e.currentTarget.style.backgroundColor = "transparent"}
                >
                  {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  <Icon size={13} />
                  <span className="uppercase tracking-wider" style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>
                    {meta.label}
                  </span>
                  <span className="ml-auto opacity-50 text-[10px]">{catBlocks.length}</span>
                </button>

                {/* Block cards */}
                {!collapsed && (
                  <div className="space-y-1 pl-1 pr-1 pb-2">
                    {catBlocks.map((b) => (
                      <button
                        key={b.name}
                        onClick={() => addBlock(b)}
                        className="w-full text-left rounded-xl p-3 transition-all duration-150"
                        style={{
                          backgroundColor: "var(--bg-surface)",
                          border: "1px solid var(--border-subtle)",
                          boxShadow: "var(--shadow-sm)",
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.boxShadow = "var(--shadow-md)";
                          e.currentTarget.style.borderColor = meta.color + "44";
                          e.currentTarget.style.transform = "translateY(-1px)";
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.boxShadow = "var(--shadow-sm)";
                          e.currentTarget.style.borderColor = "var(--border-subtle)";
                          e.currentTarget.style.transform = "none";
                        }}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: meta.color }} />
                          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                            {b.display_name}
                          </span>
                        </div>
                        <p className="text-[10px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
                          {b.description}
                        </p>
                        {/* Port preview */}
                        <div className="flex gap-2 mt-2">
                          {b.inputs.length > 0 && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                              {b.inputs.length} in
                            </span>
                          )}
                          {b.outputs.length > 0 && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                              {b.outputs.length} out
                            </span>
                          )}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Canvas ── */}
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
          <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="var(--border-subtle)" />
          <Controls
            style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 16 }}
          />
          <MiniMap
            style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 16, opacity: 0.9 }}
            nodeColor={(n: any) => n.data?.color || "#6b7280"}
            maskColor="var(--bg-page)"
          />

          {/* Empty state */}
          {nodes.length === 0 && (
            <Panel position="top-center">
              <div className="mt-32 text-center" style={{ color: "var(--text-muted)" }}>
                <p className="text-sm" style={{ fontFamily: "var(--font-serif)" }}>
                  Click blocks in the palette to add them here.
                </p>
                <p className="text-xs mt-1">
                  Connect outputs to inputs by dragging between ports.
                </p>
              </div>
            </Panel>
          )}

          {/* Run toolbar */}
          <Panel position="top-right">
            <div className="flex gap-2">
              {selectedNode && (
                <button onClick={deleteSelected} className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium surface" style={{ color: "var(--danger)" }}>
                  <Trash2 size={13} /> Delete
                </button>
              )}
              <button
                onClick={runWorkflow}
                disabled={running || nodes.length === 0}
                className="flex items-center gap-1.5 px-5 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 disabled:opacity-40 transition-all"
                style={{ boxShadow: "0 4px 16px rgb(37 99 235 / 0.3)" }}
              >
                {running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                {running ? "Running..." : "Run Pipeline"}
              </button>
            </div>
          </Panel>

          {/* Results */}
          {runResult && (
            <Panel position="bottom-center">
              <div className="surface p-4 max-w-lg max-h-52 overflow-y-auto" style={{ borderRadius: 20 }}>
                {runResult.errors?.length > 0 ? (
                  <div>
                    <p className="eyebrow mb-2" style={{ color: "var(--danger)" }}>Errors</p>
                    {runResult.errors.map((e: string, i: number) => (
                      <p key={i} className="text-xs mb-1" style={{ color: "var(--danger)" }}>{e}</p>
                    ))}
                  </div>
                ) : (
                  <div>
                    <p className="eyebrow mb-2">Result</p>
                    <pre className="text-xs overflow-x-auto" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                      {JSON.stringify(runResult.result, null, 2)?.slice(0, 500)}
                    </pre>
                    {runResult.metadata && Object.keys(runResult.metadata).length > 0 && (
                      <div className="mt-3 pt-2 flex flex-wrap gap-2" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                        {Object.entries(runResult.metadata).map(([nid, meta]: [string, any]) => (
                          <span key={nid} className="text-[10px] px-2 py-1 rounded-lg" style={{ backgroundColor: "var(--bg-inset)", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                            {meta.block_type} {meta.elapsed_ms}ms
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                <button onClick={() => setRunResult(null)} className="text-[10px] underline mt-2 block" style={{ color: "var(--text-muted)" }}>
                  Dismiss
                </button>
              </div>
            </Panel>
          )}
        </ReactFlow>
      </div>

      {/* ── Config panel ── */}
      {selectedNodeData && (
        <div
          className="w-64 shrink-0 overflow-y-auto"
          style={{ backgroundColor: "var(--bg-surface)", borderLeft: "1px solid var(--border-subtle)" }}
        >
          <div className="p-4" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-3 h-3 rounded-full" style={{ backgroundColor: selectedNodeData.color }} />
              <h3 className="font-semibold text-sm" style={{ fontFamily: "var(--font-serif)", color: "var(--text-primary)" }}>
                {selectedNodeData.label}
              </h3>
            </div>
            <p className="text-[10px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
              {selectedNodeData.blockType}
            </p>
          </div>

          <div className="p-4 space-y-4">
            {/* Ports */}
            {selectedNodeData.inputs?.length > 0 && (
              <div>
                <p className="eyebrow mb-2">Inputs</p>
                {selectedNodeData.inputs.map((p: any) => (
                  <div key={p.name} className="flex items-center gap-2 mb-1">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: selectedNodeData.color }} />
                    <span className="text-xs" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{p.name}</span>
                    <span className="text-[10px] ml-auto" style={{ color: "var(--text-muted)" }}>{p.type}</span>
                  </div>
                ))}
              </div>
            )}
            {selectedNodeData.outputs?.length > 0 && (
              <div>
                <p className="eyebrow mb-2">Outputs</p>
                {selectedNodeData.outputs.map((p: any) => (
                  <div key={p.name} className="flex items-center gap-2 mb-1">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: selectedNodeData.color, opacity: 0.6 }} />
                    <span className="text-xs" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{p.name}</span>
                    <span className="text-[10px] ml-auto" style={{ color: "var(--text-muted)" }}>{p.type}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Config */}
            {Object.keys(selectedNodeData.configSchema || {}).length > 0 && (
              <div>
                <p className="eyebrow mb-2">Configuration</p>
                {Object.entries(selectedNodeData.configSchema).map(([key, schema]: [string, any]) => (
                  <div key={key} className="mb-3">
                    <label className="text-[10px] block mb-1" style={{ color: "var(--text-secondary)", fontFamily: "var(--font-mono)" }}>
                      {schema.label || key}
                    </label>
                    {schema.type === "text" ? (
                      <textarea
                        defaultValue={schema.default ?? ""}
                        rows={3}
                        onChange={(e) => {
                          setNodes((nds: any) => nds.map((n: any) =>
                            n.id === selectedNode ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: e.target.value } } } : n
                          ));
                        }}
                        className="w-full px-2 py-1.5 rounded-lg border text-xs resize-none"
                        style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}
                      />
                    ) : (
                      <input
                        type={schema.type === "number" ? "number" : "text"}
                        defaultValue={schema.default ?? ""}
                        onChange={(e) => {
                          setNodes((nds: any) => nds.map((n: any) =>
                            n.id === selectedNode ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: schema.type === "number" ? Number(e.target.value) : e.target.value } } } : n
                          ));
                        }}
                        className="w-full px-2 py-1.5 rounded-lg border text-xs"
                        style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}
                      />
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
