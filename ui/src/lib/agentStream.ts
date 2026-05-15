/**
 * Tiny SSE reader for /api/v1/agent/stream.
 *
 * The endpoint emits framed text/event-stream lines like
 *   data: {"type":"token","content":"Hello"}
 * Each event is JSON; we parse and yield it. The reader handles partial
 * frames (Server-Sent Events are newline-delimited but a single TCP read
 * can split them mid-frame).
 */

export type AgentEvent =
  | { type: "token"; content: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; content: string }
  | { type: "done" }
  | { type: "error"; message: string };

export interface ChatMessageWire {
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  tool_call_id?: string;
}

export interface StreamAgentArgs {
  messages: ChatMessageWire[];
  model?: string;
  allowActions?: boolean;
  signal?: AbortSignal;
  onEvent: (event: AgentEvent) => void;
}

export async function streamAgent({
  messages,
  model,
  allowActions = true,
  signal,
  onEvent,
}: StreamAgentArgs): Promise<void> {
  const res = await fetch("/api/v1/agent/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(localStorage.getItem("waldo_token")
        ? { Authorization: `Bearer ${localStorage.getItem("waldo_token")}` }
        : {}),
    },
    body: JSON.stringify({ messages, model, allow_actions: allowActions }),
    signal,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`agent stream failed: ${res.status} ${text || res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line ("\n\n"). Split on it.
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          const parsed = JSON.parse(payload) as AgentEvent;
          onEvent(parsed);
        } catch {
          // Drop malformed frames silently — a token boundary inside JSON
          // is the usual cause and the next frame will recover.
        }
      }
    }
  }
}
