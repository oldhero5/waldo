import type { Node } from "@xyflow/react";
export type ConfigValue = string | number | boolean | null | ConfigValue[] | { [key: string]: ConfigValue };
export interface BlockPort { name: string; type: string; description?: string; required?: boolean }
export interface ConfigField { type: string; label?: string; default?: ConfigValue; min?: number; max?: number }
export interface BlockSchema {
  name: string;
  display_name: string;
  description: string;
  category: string;
  inputs: BlockPort[];
  outputs: BlockPort[];
  config_schema: Record<string, ConfigField>;
}
export interface BlockNodeData extends Record<string, unknown> {
  label: string;
  blockType: string;
  category: string;
  color: string;
  inputs: BlockPort[];
  outputs: BlockPort[];
  config: Record<string, ConfigValue>;
  configSchema: Record<string, ConfigField>;
}
export type WorkflowNode = Node<BlockNodeData, "block">;
export interface WorkflowGraph {
  nodes: { id: string; type: string; config: Record<string, ConfigValue>; position?: { x: number; y: number } }[];
  edges: { source: string; target: string; sourceHandle?: string; targetHandle?: string; source_port?: string; target_port?: string }[];
}
export interface WorkflowRunResult {
  result?: unknown;
  metadata?: Record<string, { block_type: string; elapsed_ms?: number; skipped?: boolean }>;
  errors?: string[];
}
export interface SavedWorkflow {
  id: string;
  name: string;
  slug: string;
  block_count: number;
  is_deployed: boolean;
}

export interface SavedWorkflowDetail {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  graph: WorkflowGraph;
  is_deployed: boolean;
}
