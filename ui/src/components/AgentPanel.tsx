/**
 * Floating AI Agent panel — accessible from every page. Compact chat that
 * uses the same /api/v1/agent/stream backend as the full-page agent, but
 * defaults to read-only so a stray "yes" doesn't kick off a training run.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import { Sparkles, X, Send, Loader2, Bot, User, Wrench } from "lucide-react";

import { streamAgent, type AgentEvent, type ChatMessageWire } from "../lib/agentStream";

interface ToolEvent {
  name: string;
  result?: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  tools?: ToolEvent[];
}

export default function AgentPanel({ context }: { context?: string }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [streamTools, setStreamTools] = useState<ToolEvent[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamText, streamTools]);

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || streaming) return;
      const userMsg: Message = { role: "user", content: text.trim() };
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setStreaming(true);
      setStreamText("");
      setStreamTools([]);

      const wire: ChatMessageWire[] = [
        {
          role: "system",
          content: `You're answering inside a sidebar — keep replies under 3 sentences unless asked for detail.${
            context ? ` The user is on the ${context} page.` : ""
          }`,
        },
        ...[...messages, userMsg].slice(-8).map((m) => ({ role: m.role, content: m.content })),
      ];

      let acc = "";
      const tools: ToolEvent[] = [];

      try {
        await streamAgent({
          messages: wire,
          // Sidebar = read-only by default. Use the full /agent page to take actions.
          allowActions: false,
          onEvent: (event: AgentEvent) => {
            if (event.type === "token") {
              acc += event.content;
              setStreamText(acc);
            } else if (event.type === "tool_call") {
              tools.push({ name: event.name });
              setStreamTools([...tools]);
            } else if (event.type === "tool_result") {
              const last = [...tools].reverse().find((t) => t.name === event.name && !t.result);
              if (last) last.result = event.content;
              setStreamTools([...tools]);
            } else if (event.type === "error") {
              throw new Error(event.message);
            }
          },
        });
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: acc || "(no response)", tools: tools.length ? tools : undefined },
        ]);
        setStreamText("");
        setStreamTools([]);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${msg}` }]);
      } finally {
        setStreaming(false);
      }
    },
    [messages, streaming, context],
  );

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        style={{
          position: "fixed", bottom: 24, right: 24, zIndex: 50,
          width: 48, height: 48, borderRadius: 16,
          backgroundColor: "var(--accent)", color: "white",
          display: "flex", alignItems: "center", justifyContent: "center",
          boxShadow: "0 8px 24px rgb(37 99 235 / 0.3)",
          transition: "all 160ms ease",
          border: "none",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.transform = "scale(1.05)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.transform = "scale(1)"; }}
      >
        <Sparkles size={20} />
      </button>
    );
  }

  return (
    <div
      style={{
        position: "fixed", bottom: 24, right: 24, zIndex: 50,
        width: 380, height: 520,
        borderRadius: 20,
        backgroundColor: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        boxShadow: "0 22px 44px rgb(54 40 23 / 0.15)",
        display: "flex", flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <div style={{
        padding: "12px 16px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        borderBottom: "1px solid var(--border-subtle)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Sparkles size={16} style={{ color: "var(--accent)" }} />
          <span style={{ fontFamily: "var(--font-serif)", fontWeight: 600, fontSize: 14, color: "var(--text-primary)" }}>
            Waldo AI · read-only
          </span>
        </div>
        <button onClick={() => setOpen(false)} style={{ color: "var(--text-muted)", background: "none", border: "none" }}>
          <X size={16} />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px" }}>
        {messages.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "center", marginTop: 40 }}>
            Ask about your projects, models, or workflows. To start a labeling or training job, open the full <a href="/agent" style={{ color: "var(--accent)" }}>Agent page</a>.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 10, display: "flex", gap: 8, flexDirection: m.role === "user" ? "row-reverse" : "row" }}>
            <div style={{
              width: 24, height: 24, borderRadius: 8, flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              backgroundColor: m.role === "user" ? "var(--bg-inset)" : "var(--accent-soft)",
            }}>
              {m.role === "user"
                ? <User size={12} style={{ color: "var(--text-secondary)" }} />
                : <Bot size={12} style={{ color: "var(--accent)" }} />}
            </div>
            <div style={{ maxWidth: "80%", display: "flex", flexDirection: "column", gap: 6 }}>
              {m.tools?.map((t, idx) => (
                <div key={idx} style={{
                  fontSize: 10, fontFamily: "var(--font-mono)",
                  padding: "4px 8px", borderRadius: 8,
                  backgroundColor: "var(--bg-inset)", color: "var(--text-secondary)",
                  display: "flex", alignItems: "center", gap: 4,
                }}>
                  <Wrench size={10} /> {t.name}{t.result ? " ✓" : ""}
                </div>
              ))}
              <div style={{
                padding: "8px 12px", borderRadius: 14, fontSize: 13, lineHeight: 1.5,
                backgroundColor: m.role === "user" ? "var(--accent)" : "var(--bg-inset)",
                color: m.role === "user" ? "white" : "var(--text-primary)",
              }}>
                {m.role === "assistant"
                  ? <div className="markdown-body" style={{ fontSize: 12 }}><Markdown>{m.content}</Markdown></div>
                  : m.content}
              </div>
            </div>
          </div>
        ))}
        {streaming && (
          <div style={{ marginBottom: 10, display: "flex", gap: 8 }}>
            <div style={{ width: 24, height: 24, borderRadius: 8, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", backgroundColor: "var(--accent-soft)" }}>
              <Bot size={12} style={{ color: "var(--accent)" }} />
            </div>
            <div style={{ maxWidth: "80%", display: "flex", flexDirection: "column", gap: 6 }}>
              {streamTools.map((t, idx) => (
                <div key={idx} style={{
                  fontSize: 10, fontFamily: "var(--font-mono)",
                  padding: "4px 8px", borderRadius: 8,
                  backgroundColor: "var(--bg-inset)", color: "var(--text-secondary)",
                  display: "flex", alignItems: "center", gap: 4,
                }}>
                  <Wrench size={10} /> {t.name}{t.result ? " ✓" : "…"}
                </div>
              ))}
              {streamText ? (
                <div style={{ padding: "8px 12px", borderRadius: 14, fontSize: 12, lineHeight: 1.5, backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}>
                  <div className="markdown-body" style={{ fontSize: 12 }}><Markdown>{streamText}</Markdown></div>
                  <span className="animate-pulse">|</span>
                </div>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-muted)", fontSize: 12 }}>
                  <Loader2 size={12} className="animate-spin" /> Thinking…
                </div>
              )}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border-subtle)" }}>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
            placeholder="Ask Waldo…"
            style={{
              flex: 1, padding: "8px 12px", borderRadius: 12, fontSize: 12,
              border: "1px solid var(--border-default)",
              backgroundColor: "var(--bg-inset)", color: "var(--text-primary)",
              outline: "none",
            }}
          />
          <button
            onClick={() => send(input)}
            disabled={!input.trim() || streaming}
            style={{
              width: 36, height: 36, borderRadius: 12,
              backgroundColor: "var(--accent)", color: "white",
              display: "flex", alignItems: "center", justifyContent: "center",
              border: "none", opacity: !input.trim() || streaming ? 0.4 : 1,
            }}
          >
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
