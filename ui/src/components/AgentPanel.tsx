/**
 * Floating AI Agent panel — accessible from every page.
 * Click the fab button to open a compact chat interface.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import { Sparkles, X, Send, Loader2, Bot, User } from "lucide-react";

interface Message { role: "user" | "assistant"; content: string; }

function stripThinking(text: string): string {
  return text.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
}

export default function AgentPanel({ context }: { context?: string }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, streamText]);

  const send = useCallback(async (text: string) => {
    if (!text.trim() || streaming) return;
    const userMsg: Message = { role: "user", content: text.trim() };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setStreaming(true);
    setStreamText("");

    try {
      const history = [...messages, userMsg].slice(-8).map((m) => ({ role: m.role, content: m.content }));
      const contextNote = context ? `\nThe user is currently on the ${context} page.` : "";

      const res = await fetch("/api/v1/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          use_tools: false, // Fast mode for panel
          messages: [
            { role: "system", content: `You are Waldo, a concise AI assistant for a computer vision platform. Be brief — this is a sidebar chat, not a full conversation. Answer in 2-3 sentences when possible.${contextNote}` },
            ...history,
          ],
        }),
      });

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No stream");
      let fullText = "";
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const line of decoder.decode(value).split("\n").filter(Boolean)) {
          try {
            const json = JSON.parse(line);
            if (json.message?.content) { fullText += json.message.content; setStreamText(fullText); }
          } catch { /* skip */ }
        }
      }
      setMessages((prev) => [...prev, { role: "assistant", content: stripThinking(fullText) }]);
      setStreamText("");
    } catch (e: any) {
      setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${e.message}` }]);
    } finally {
      setStreaming(false);
    }
  }, [messages, streaming, context]);

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
      {/* Header */}
      <div style={{
        padding: "12px 16px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        borderBottom: "1px solid var(--border-subtle)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Sparkles size={16} style={{ color: "var(--accent)" }} />
          <span style={{ fontFamily: "var(--font-serif)", fontWeight: 600, fontSize: 14, color: "var(--text-primary)" }}>
            Waldo AI
          </span>
        </div>
        <button onClick={() => setOpen(false)} style={{ color: "var(--text-muted)", background: "none", border: "none" }}>
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px" }}>
        {messages.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "center", marginTop: 40 }}>
            Ask me anything about your projects, models, or workflows.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 10, display: "flex", gap: 8, flexDirection: m.role === "user" ? "row-reverse" : "row" }}>
            <div style={{
              width: 24, height: 24, borderRadius: 8, flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              backgroundColor: m.role === "user" ? "var(--bg-inset)" : "var(--accent-soft)",
            }}>
              {m.role === "user" ? <User size={12} style={{ color: "var(--text-secondary)" }} /> : <Bot size={12} style={{ color: "var(--accent)" }} />}
            </div>
            <div style={{
              padding: "8px 12px", borderRadius: 14, maxWidth: "80%", fontSize: 13, lineHeight: 1.5,
              backgroundColor: m.role === "user" ? "var(--accent)" : "var(--bg-inset)",
              color: m.role === "user" ? "white" : "var(--text-primary)",
            }}>
              {m.role === "assistant" ? <div className="markdown-body" style={{ fontSize: 12 }}><Markdown>{m.content}</Markdown></div> : m.content}
            </div>
          </div>
        ))}
        {streaming && streamText && (
          <div style={{ marginBottom: 10, display: "flex", gap: 8 }}>
            <div style={{ width: 24, height: 24, borderRadius: 8, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", backgroundColor: "var(--accent-soft)" }}>
              <Bot size={12} style={{ color: "var(--accent)" }} />
            </div>
            <div style={{ padding: "8px 12px", borderRadius: 14, maxWidth: "80%", fontSize: 12, lineHeight: 1.5, backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}>
              <div className="markdown-body" style={{ fontSize: 12 }}><Markdown>{stripThinking(streamText)}</Markdown></div>
              <span className="animate-pulse">|</span>
            </div>
          </div>
        )}
        {streaming && !streamText && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-muted)", fontSize: 12, marginLeft: 32 }}>
            <Loader2 size={12} className="animate-spin" /> Thinking...
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* Input */}
      <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border-subtle)" }}>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
            placeholder="Ask Waldo..."
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
