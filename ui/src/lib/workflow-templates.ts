/**
 * Pre-built workflow templates — complete graph definitions
 * with nodes, edges, and positions for instant loading.
 */

export interface WorkflowTemplate {
  name: string;
  desc: string;
  color: string;
  tags: string[];
  graph: {
    nodes: { id: string; type: string; config: Record<string, any>; position: { x: number; y: number } }[];
    edges: { source: string; sourceHandle: string; target: string; targetHandle: string }[];
  };
}

export const TEMPLATES: WorkflowTemplate[] = [
  {
    name: "Detect & Count",
    desc: "Run object detection, filter by confidence, count results by class.",
    color: "#8b5cf6",
    tags: ["detection", "counting"],
    graph: {
      nodes: [
        { id: "n1", type: "image_input", config: {}, position: { x: 50, y: 120 } },
        { id: "n2", type: "detection", config: { confidence: 0.5 }, position: { x: 340, y: 80 } },
        { id: "n3", type: "filter", config: { min_confidence: 0.6 }, position: { x: 630, y: 80 } },
        { id: "n4", type: "count", config: {}, position: { x: 920, y: 80 } },
        { id: "n5", type: "output", config: {}, position: { x: 1200, y: 120 } },
      ],
      edges: [
        { source: "n1", sourceHandle: "image", target: "n2", targetHandle: "image" },
        { source: "n2", sourceHandle: "detections", target: "n3", targetHandle: "detections" },
        { source: "n3", sourceHandle: "detections", target: "n4", targetHandle: "detections" },
        { source: "n4", sourceHandle: "by_class", target: "n5", targetHandle: "data" },
      ],
    },
  },
  {
    name: "Detect & Visualize",
    desc: "Detect objects and draw labeled bounding boxes on the image.",
    color: "#ec4899",
    tags: ["detection", "visualization"],
    graph: {
      nodes: [
        { id: "n1", type: "image_input", config: {}, position: { x: 50, y: 100 } },
        { id: "n2", type: "detection", config: { confidence: 0.25 }, position: { x: 340, y: 60 } },
        { id: "n3", type: "visualize_bbox", config: { thickness: 2 }, position: { x: 630, y: 60 } },
        { id: "n4", type: "output", config: {}, position: { x: 920, y: 100 } },
      ],
      edges: [
        { source: "n1", sourceHandle: "image", target: "n2", targetHandle: "image" },
        { source: "n2", sourceHandle: "detections", target: "n3", targetHandle: "detections" },
        { source: "n2", sourceHandle: "image", target: "n3", targetHandle: "image" },
        { source: "n3", sourceHandle: "image", target: "n4", targetHandle: "data" },
      ],
    },
  },
  {
    name: "Privacy Blur",
    desc: "Detect faces or sensitive objects and blur them for privacy compliance.",
    color: "#06b6d4",
    tags: ["privacy", "blur", "GDPR"],
    graph: {
      nodes: [
        { id: "n1", type: "image_input", config: {}, position: { x: 50, y: 100 } },
        { id: "n2", type: "detection", config: { confidence: 0.3 }, position: { x: 340, y: 60 } },
        { id: "n3", type: "visualize_blur", config: { kernel_size: 51 }, position: { x: 630, y: 60 } },
        { id: "n4", type: "output", config: {}, position: { x: 920, y: 100 } },
      ],
      edges: [
        { source: "n1", sourceHandle: "image", target: "n2", targetHandle: "image" },
        { source: "n2", sourceHandle: "detections", target: "n3", targetHandle: "detections" },
        { source: "n2", sourceHandle: "image", target: "n3", targetHandle: "image" },
        { source: "n3", sourceHandle: "image", target: "n4", targetHandle: "data" },
      ],
    },
  },
  {
    name: "Smart Scene Analysis",
    desc: "Detect objects, count them, then use an LLM to describe the scene.",
    color: "#22c55e",
    tags: ["AI", "analysis", "LLM"],
    graph: {
      nodes: [
        { id: "n1", type: "image_input", config: {}, position: { x: 50, y: 120 } },
        { id: "n2", type: "detection", config: { confidence: 0.25 }, position: { x: 340, y: 60 } },
        { id: "n3", type: "count", config: {}, position: { x: 630, y: 60 } },
        { id: "n4", type: "llm", config: { model: "qwen3.5:9b", system_prompt: "Describe this scene based on the detection counts." }, position: { x: 920, y: 120 } },
        { id: "n5", type: "output", config: {}, position: { x: 1200, y: 120 } },
      ],
      edges: [
        { source: "n1", sourceHandle: "image", target: "n2", targetHandle: "image" },
        { source: "n2", sourceHandle: "detections", target: "n3", targetHandle: "detections" },
        { source: "n3", sourceHandle: "by_class", target: "n4", targetHandle: "context" },
        { source: "n4", sourceHandle: "response", target: "n5", targetHandle: "data" },
      ],
    },
  },
  {
    name: "Detect → Crop → Analyze Color",
    desc: "Detect objects, crop each region, extract dominant colors.",
    color: "#f59e0b",
    tags: ["detection", "crop", "color"],
    graph: {
      nodes: [
        { id: "n1", type: "image_input", config: {}, position: { x: 50, y: 100 } },
        { id: "n2", type: "detection", config: { confidence: 0.4 }, position: { x: 340, y: 60 } },
        { id: "n3", type: "crop", config: { padding: 5 }, position: { x: 630, y: 60 } },
        { id: "n4", type: "output", config: {}, position: { x: 920, y: 100 } },
      ],
      edges: [
        { source: "n1", sourceHandle: "image", target: "n2", targetHandle: "image" },
        { source: "n2", sourceHandle: "detections", target: "n3", targetHandle: "detections" },
        { source: "n2", sourceHandle: "image", target: "n3", targetHandle: "image" },
        { source: "n3", sourceHandle: "crops", target: "n4", targetHandle: "data" },
      ],
    },
  },
  {
    name: "Conditional Alert",
    desc: "Detect objects, count them, send webhook alert if count exceeds threshold.",
    color: "#e11d48",
    tags: ["alert", "webhook", "conditional"],
    graph: {
      nodes: [
        { id: "n1", type: "image_input", config: {}, position: { x: 50, y: 120 } },
        { id: "n2", type: "detection", config: { confidence: 0.3 }, position: { x: 340, y: 80 } },
        { id: "n3", type: "count", config: {}, position: { x: 630, y: 80 } },
        { id: "n4", type: "conditional", config: { operator: "gt", threshold: 5 }, position: { x: 920, y: 80 } },
        { id: "n5", type: "webhook", config: { url: "https://hooks.slack.com/..." }, position: { x: 1200, y: 80 } },
        { id: "n6", type: "output", config: {}, position: { x: 1200, y: 200 } },
      ],
      edges: [
        { source: "n1", sourceHandle: "image", target: "n2", targetHandle: "image" },
        { source: "n2", sourceHandle: "detections", target: "n3", targetHandle: "detections" },
        { source: "n3", sourceHandle: "total", target: "n4", targetHandle: "value" },
        { source: "n3", sourceHandle: "by_class", target: "n6", targetHandle: "data" },
        { source: "n4", sourceHandle: "passed", target: "n5", targetHandle: "data" },
      ],
    },
  },
];
