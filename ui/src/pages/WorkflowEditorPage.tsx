/**
 * Visual workflow editor — Pretext-inspired editorial design.
 * Block palette with warm cards, React Flow canvas, config panel.
 */
import { useCallback, useEffect, useRef, useState } from "react";
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
  type Edge,
  type NodeTypes,
  BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Play, Loader2, Trash2, ChevronDown, ChevronRight, Cpu, Scissors, Filter, MessageSquare, ArrowDownToLine, Eye, GitBranch, Scan, Rocket, Save, Cloud } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { authFetch } from "../api";
import type { BlockSchema, WorkflowGraph, WorkflowNode, WorkflowRunResult, SavedWorkflowDetail } from "../lib/workflowTypes";
import BlockNode from "../components/workflow/BlockNode";

const BASE = "/api/v1";

const nodeTypes: NodeTypes = { block: BlockNode };

const CATEGORY_META: Record<string, { color: string; icon: typeof Cpu; label: string }> = {
  io: { color: "#3b82f6", icon: ArrowDownToLine, label: "Input / Output" },
  models: { color: "#8b5cf6", icon: Cpu, label: "Models" },
  transforms: { color: "#f59e0b", icon: Scissors, label: "Transforms" },
  visualization: { color: "#ec4899", icon: Eye, label: "Visualization" },
  logic: { color: "#06b6d4", icon: GitBranch, label: "Logic" },
  classical_cv: { color: "#14b8a6", icon: Scan, label: "Classical CV" },
  ai: { color: "#22c55e", icon: MessageSquare, label: "AI" },
  platform: { color: "#e11d48", icon: Rocket, label: "Platform" },
  general: { color: "#6b7280", icon: Filter, label: "General" },
};

export default function WorkflowEditorPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  // Route identity owns the complete editor session, including drafts and in-flight requests.
  return <WorkflowEditor key={workflowId || "new"} workflowId={workflowId} />;
}

function WorkflowEditor({ workflowId }: { workflowId?: string }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [blocks, setBlocks] = useState<BlockSchema[]>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<WorkflowRunResult | null>(null);
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [deployInfo, setDeployInfo] = useState<{ url: string; curl: string } | null>(null);
  const [savedWorkflow, setSavedWorkflow] = useState<SavedWorkflowDetail | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const saveRequest = useRef<AbortController | null>(null);
  const runRequest = useRef<AbortController | null>(null);

  useEffect(() => () => {
    saveRequest.current?.abort();
    runRequest.current?.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const read = async (url: string) => {
      const response = await authFetch(url, { signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not load workflow");
      return data;
    };
    const load = async () => {
      try {
        const [catalog, saved]: [{ blocks: BlockSchema[] }, SavedWorkflowDetail | null] = await Promise.all([
          read(`${BASE}/workflows/blocks`),
          workflowId ? read(`${BASE}/workflows/saved/${encodeURIComponent(workflowId)}`) : Promise.resolve(null),
        ]);
        if (!active || controller.signal.aborted) return;
        const blockList = catalog.blocks;
        let graph = saved?.graph;
        if (!workflowId) {
          const templateJson = sessionStorage.getItem("waldo_workflow_template");
          if (templateJson) {
            const template: { graph: WorkflowGraph } = JSON.parse(templateJson);
            graph = template.graph;
            sessionStorage.removeItem("waldo_workflow_template");
          }
        }
        const blockMap = new Map(blockList.map((block) => [block.name, block]));
        const loadedNodes: WorkflowNode[] = (graph?.nodes || []).map((node, i) => {
          const schema = blockMap.get(node.type);
          const category = schema?.category || "general";
          return {
            id: node.id, type: "block", position: node.position || { x: 300, y: 80 + i * 100 },
            data: {
              label: schema?.display_name || node.type, blockType: node.type, category,
              color: (CATEGORY_META[category] || CATEGORY_META.general).color,
              inputs: schema?.inputs || [], outputs: schema?.outputs || [],
              config: node.config || {}, configSchema: schema?.config_schema || {},
            },
          };
        });
        const loadedEdges: Edge[] = (graph?.edges || []).map((edge, i) => ({
          id: `e${i}`, source: edge.source, target: edge.target,
          sourceHandle: edge.sourceHandle || edge.source_port,
          targetHandle: edge.targetHandle || edge.target_port,
          animated: true, style: { stroke: "var(--accent)", strokeWidth: 2 },
        }));
        setBlocks(blockList);
        setNodes(loadedNodes);
        setEdges(loadedEdges);
        setSavedWorkflow(saved);
        setLoadState("ready");
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        setLoadError(error instanceof Error ? error.message : "Could not load workflow");
        setLoadState("error");
      }
    };
    void load();
    return () => { active = false; controller.abort(); };
  }, [workflowId, setNodes, setEdges]);

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
      // randomUUID is unavailable on non-loopback HTTP origins.
      const randomId = typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
      const id = `node_${randomId}`;
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
    setNodes((nds) => nds.filter((n) => n.id !== selectedNode));
    setEdges((eds) => eds.filter((e) => e.source !== selectedNode && e.target !== selectedNode));
    setSelectedNode(null);
  }, [selectedNode, setNodes, setEdges]);

  const runWorkflow = useCallback(async () => {
    if (runRequest.current) return;
    const controller = new AbortController();
    runRequest.current = controller;
    setRunning(true);
    setRunResult(null);
    try {
      const graph: WorkflowGraph = {
        nodes: nodes.map((n) => ({ id: n.id, type: n.data.blockType, config: n.data.config || {} })),
        edges: edges.map((e) => ({
          source: e.source, source_port: e.sourceHandle || "output",
          target: e.target, target_port: e.targetHandle || "input",
        })),
      };
      const res = await authFetch(`${BASE}/workflows/run`, {
        method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph }),
      });
      const data = await res.json();
      if (controller.signal.aborted) return;
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Workflow request failed");
      setRunResult(data);
    } catch (e) {
      if (!controller.signal.aborted) setRunResult({ errors: [e instanceof Error ? e.message : "Workflow request failed"] });
    } finally {
      if (!controller.signal.aborted) { runRequest.current = null; setRunning(false); }
    }
  }, [nodes, edges]);

  const saveWorkflow = async () => {
    if (saveRequest.current) return;
    const name = savedWorkflow?.name || prompt("Workflow name:", "My Workflow");
    if (!name) return;
    const controller = new AbortController();
    saveRequest.current = controller;
    setSaving(true);
    setRunResult(null);
    setDeployInfo(null);
    const graph: WorkflowGraph = {
      nodes: nodes.map((node) => ({ id: node.id, type: node.data.blockType, config: node.data.config, position: node.position })),
      edges: edges.map((edge) => ({ source: edge.source, source_port: edge.sourceHandle || "output", target: edge.target, target_port: edge.targetHandle || "input" })),
    };
    try {
      const response = await authFetch(savedWorkflow ? `${BASE}/workflows/saved/${encodeURIComponent(savedWorkflow.slug)}` : `${BASE}/workflows`, {
        method: savedWorkflow ? "PUT" : "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description: savedWorkflow?.description || "", graph }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save workflow");
      if (controller.signal.aborted) return;
      const saved: SavedWorkflowDetail = { ...data, graph };
      // Remember successful creation before deploying: a failed deployment retry must update this record.
      setSavedWorkflow(saved);
      const deployResponse = await authFetch(`${BASE}/workflows/saved/${encodeURIComponent(saved.slug)}/deploy`, {
        method: "POST", signal: controller.signal,
      });
      const deployed = await deployResponse.json();
      if (!deployResponse.ok) throw new Error(typeof deployed.detail === "string" ? deployed.detail : "Workflow saved, but deployment failed");
      if (!controller.signal.aborted) setDeployInfo({ url: deployed.endpoint, curl: deployed.curl });
    } catch (error) {
      if (!controller.signal.aborted) setRunResult({ errors: [error instanceof Error ? error.message : "Could not save workflow"] });
    } finally {
      if (!controller.signal.aborted) { saveRequest.current = null; setSaving(false); }
    }
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.key === "Delete" || e.key === "Backspace") && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement) && !(e.target instanceof HTMLElement && e.target.isContentEditable)) deleteSelected();
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

  if (loadState !== "ready") {
    return (
      <div className="p-6">
        {loadState === "loading" ? <p role="status">Loading workflow...</p> : <p role="alert" style={{ color: "var(--danger)" }}>{loadError}</p>}
        <Link to="/workflows" className="inline-block mt-3 underline text-sm">Back to workflows</Link>
      </div>
    );
  }

  const selectedNodeData = nodes.find((n) => n.id === selectedNode)?.data;

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
            nodeColor={(n) => typeof n.data.color === "string" ? n.data.color : "#6b7280"}
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

          {/* Toolbar */}
          <Panel position="top-right">
            <div className="flex gap-2 items-center">
              {savedWorkflow && <span className="text-sm mr-2" style={{ color: "var(--text-secondary)" }}>{savedWorkflow.name}</span>}
              {selectedNode && (
                <button onClick={deleteSelected} className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium surface" style={{ color: "var(--danger)" }}>
                  <Trash2 size={13} /> Delete
                </button>
              )}
              <button
                onClick={saveWorkflow}
                disabled={saving || nodes.length === 0}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl text-sm font-medium surface"
                style={{ color: "var(--text-primary)" }}
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                Save & Deploy
              </button>
              <button
                onClick={runWorkflow}
                disabled={running || nodes.length === 0}
                className="flex items-center gap-1.5 px-5 py-2.5 bg-accent text-on-accent rounded-xl text-sm font-semibold hover:bg-accent-hover disabled:opacity-40 transition-all"
                style={{ boxShadow: "0 4px 16px rgb(37 99 235 / 0.3)" }}
              >
                {running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                {running ? "Running..." : "Run Pipeline"}
              </button>
            </div>
          </Panel>

          {/* Deploy info */}
          {deployInfo && (
            <Panel position="top-center">
              <div className="surface" style={{ padding: 16, maxWidth: 500, borderRadius: 20 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <Cloud size={16} style={{ color: "var(--success)" }} />
                  <span style={{ fontFamily: "var(--font-serif)", fontWeight: 600, fontSize: 14, color: "var(--text-primary)" }}>
                    Workflow Deployed
                  </span>
                </div>
                <p style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 8 }}>
                  Your workflow is now serving at:
                </p>
                <pre style={{
                  fontFamily: "var(--font-mono)", fontSize: 11, padding: "8px 12px", borderRadius: 10,
                  backgroundColor: "var(--bg-inset)", color: "var(--text-primary)", overflow: "auto",
                }}>
                  {deployInfo.curl}
                </pre>
                <button onClick={() => setDeployInfo(null)} style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, textDecoration: "underline", background: "none", border: "none" }}>
                  Dismiss
                </button>
              </div>
            </Panel>
          )}

          {/* Results */}
          {runResult && (
            <Panel position="bottom-center">
              <div className="surface p-4 max-w-lg max-h-52 overflow-y-auto" style={{ borderRadius: 20 }}>
                {(runResult.errors?.length ?? 0) > 0 ? (
                  <div>
                    <p className="eyebrow mb-2" style={{ color: "var(--danger)" }}>Errors</p>
                    {runResult.errors?.map((e: string, i: number) => (
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
                        {Object.entries(runResult.metadata).map(([nid, meta]) => (
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
                {selectedNodeData.inputs.map((p) => (
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
                {selectedNodeData.outputs.map((p) => (
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
                {Object.entries(selectedNodeData.configSchema).map(([key, schema]) => (
                  <div key={`${selectedNode}:${key}`} className="mb-3">
                    <label htmlFor={`config-${selectedNode}-${key}`} className="text-[10px] block mb-1" style={{ color: "var(--text-secondary)", fontFamily: "var(--font-mono)" }}>
                      {schema.label || key}
                    </label>
                    {schema.type === "text" ? (
                      <textarea
                        id={`config-${selectedNode}-${key}`}
                        value={String(selectedNodeData.config[key] ?? schema.default ?? "")}
                        rows={3}
                        onChange={(e) => {
                          setNodes((nds) => nds.map((n) =>
                            n.id === selectedNode ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: e.target.value } } } : n
                          ));
                        }}
                        className="w-full px-2 py-1.5 rounded-lg border text-xs resize-none"
                        style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}
                      />
                    ) : (
                      <input
                        id={`config-${selectedNode}-${key}`}
                        type={schema.type === "number" ? "number" : "text"}
                        value={String(selectedNodeData.config[key] ?? schema.default ?? "")}
                        onChange={(e) => {
                          setNodes((nds) => nds.map((n) =>
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
