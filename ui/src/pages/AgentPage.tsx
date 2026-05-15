/**
 * AI Agent — full-page chat. Talks to /api/v1/agent/stream over SSE,
 * renders streamed tokens, surfaces tool calls and their results.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  Send,
  Loader2,
  Bot,
  User,
  Sparkles,
  Wrench,
  CheckCircle2,
  AlertTriangle,
  ShieldAlert,
} from "lucide-react";

import { streamAgent, type AgentEvent, type ChatMessageWire } from "../lib/agentStream";

interface ToolEvent {
  name: string;
  args?: Record<string, unknown>;
  result?: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  tools?: ToolEvent[];
  timestamp: number;
}

const SUGGESTIONS = [
  "What models are trained in this workspace?",
  "Recommend training settings for a 200-frame dataset",
  "Start a labeling job for 'person' on my latest video",
  "Activate the model with the best mAP",
  "Am I running on GPU or CPU right now?",
];

interface AgentModel {
  name: string;
  size: number;
  backend: string;
}

export default function AgentPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [streamTools, setStreamTools] = useState<ToolEvent[]>([]);
  const [models, setModels] = useState<AgentModel[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [allowActions, setAllowActions] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Fetch available local models on mount.
  useEffect(() => {
    fetch("/api/v1/agent/models", {
      headers: localStorage.getItem("waldo_token")
        ? { Authorization: `Bearer ${localStorage.getItem("waldo_token")}` }
        : {},
    })
      .then((r) => (r.ok ? r.json() : { models: [], default: "" }))
      .then((d) => {
        setModels(d.models || []);
        setSelectedModel(d.default || (d.models || [])[0]?.name || "");
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamText, streamTools]);

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || streaming) return;

      const userMsg: Message = { role: "user", content: text.trim(), timestamp: Date.now() };
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setStreaming(true);
      setStreamText("");
      setStreamTools([]);
      setError(null);

      const wireMessages: ChatMessageWire[] = [
        ...messages,
        userMsg,
      ]
        .slice(-10)
        .map((m) => ({ role: m.role, content: m.content }));

      const controller = new AbortController();
      abortRef.current = controller;

      let acc = "";
      const tools: ToolEvent[] = [];

      try {
        await streamAgent({
          messages: wireMessages,
          model: selectedModel || undefined,
          allowActions,
          signal: controller.signal,
          onEvent: (event: AgentEvent) => {
            if (event.type === "token") {
              acc += event.content;
              setStreamText(acc);
            } else if (event.type === "tool_call") {
              tools.push({ name: event.name, args: event.args });
              setStreamTools([...tools]);
            } else if (event.type === "tool_result") {
              const last = [...tools].reverse().find((t) => t.name === event.name && !t.result);
              if (last) last.result = event.content;
              else tools.push({ name: event.name, result: event.content });
              setStreamTools([...tools]);
            } else if (event.type === "error") {
              throw new Error(event.message);
            }
          },
        });

        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: acc || "(no response)",
            tools: tools.length ? tools : undefined,
            timestamp: Date.now(),
          },
        ]);
        setStreamText("");
        setStreamTools([]);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        if (acc) {
          setMessages((prev) => [
            ...prev,
            { role: "assistant", content: acc, tools: tools.length ? tools : undefined, timestamp: Date.now() },
          ]);
        }
        setStreamText("");
        setStreamTools([]);
      } finally {
        setStreaming(false);
        abortRef.current = null;
        inputRef.current?.focus();
      }
    },
    [messages, streaming, selectedModel, allowActions],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  return (
    <div className="h-screen flex flex-col" style={{ backgroundColor: "var(--bg-page)" }}>
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {messages.length === 0 && (
            <div className="text-center pt-16 pb-8">
              <div
                className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4"
                style={{ backgroundColor: "var(--accent-soft)" }}
              >
                <Sparkles size={24} style={{ color: "var(--accent)" }} />
              </div>
              <h1 className="text-xl font-bold mb-2" style={{ color: "var(--text-primary)" }}>
                Waldo AI Assistant
              </h1>
              <p className="text-sm mb-8" style={{ color: "var(--text-secondary)" }}>
                Ask me about your data, get training recommendations, or have me run jobs for you.
              </p>

              <div className="flex flex-wrap gap-2 justify-center max-w-lg mx-auto">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => sendMessage(s)}
                    className="px-3 py-2 rounded-xl text-xs text-left surface surface-interactive"
                    style={{ maxWidth: 240 }}
                  >
                    <span style={{ color: "var(--text-primary)" }}>{s}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <MessageBubble key={i} message={msg} />
          ))}

          {streaming && (
            <div className="flex gap-3 mb-4">
              <Avatar role="assistant" />
              <div className="space-y-2 max-w-[80%]">
                {streamTools.map((t, idx) => (
                  <ToolPill key={idx} tool={t} />
                ))}
                <div
                  className="rounded-2xl px-4 py-3 text-sm leading-relaxed"
                  style={{
                    backgroundColor: "var(--bg-surface)",
                    border: "1px solid var(--border-subtle)",
                    color: "var(--text-primary)",
                  }}
                >
                  {streamText ? (
                    <div className="markdown-body">
                      <Markdown>{streamText}</Markdown>
                      <span className="animate-pulse">|</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
                      <Loader2 size={14} className="animate-spin" />
                      Thinking…
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {error && (
            <div
              className="flex items-start gap-2 mb-4 px-3 py-2 rounded-lg text-sm"
              style={{ backgroundColor: "rgba(220, 38, 38, 0.08)", color: "var(--text-primary)" }}
            >
              <AlertTriangle size={16} style={{ color: "rgb(220, 38, 38)", marginTop: 2 }} />
              <div>
                <div style={{ fontWeight: 600 }}>Agent error</div>
                <div style={{ color: "var(--text-secondary)" }}>{error}</div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      <div style={{ borderTop: "1px solid var(--border-subtle)", backgroundColor: "var(--bg-surface)" }}>
        <div className="max-w-2xl mx-auto px-4 py-3">
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about your projects, models, or just have me run jobs…"
              rows={1}
              className="flex-1 resize-none px-4 py-2.5 rounded-xl border text-sm"
              style={{
                borderColor: "var(--border-default)",
                backgroundColor: "var(--bg-inset)",
                color: "var(--text-primary)",
                maxHeight: 120,
              }}
              onInput={(e) => {
                const t = e.currentTarget;
                t.style.height = "auto";
                t.style.height = Math.min(t.scrollHeight, 120) + "px";
              }}
            />
            <button
              onClick={() => sendMessage(input)}
              disabled={!input.trim() || streaming}
              className="p-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 transition-colors shrink-0"
            >
              <Send size={16} />
            </button>
          </div>
          <div className="flex items-center justify-between mt-2">
            <div className="flex items-center gap-3">
              {models.length > 0 && (
                <select
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  className="text-[10px] px-2 py-1 rounded border"
                  style={{
                    borderColor: "var(--border-default)",
                    backgroundColor: "var(--bg-inset)",
                    color: "var(--text-secondary)",
                  }}
                >
                  {models.map((m) => (
                    <option key={m.name} value={m.name}>
                      {m.name}
                    </option>
                  ))}
                </select>
              )}
              <label
                className="text-[10px] flex items-center gap-1.5 cursor-pointer"
                style={{ color: allowActions ? "var(--text-secondary)" : "rgb(220, 38, 38)" }}
              >
                <input
                  type="checkbox"
                  checked={!allowActions}
                  onChange={(e) => setAllowActions(!e.target.checked)}
                />
                <ShieldAlert size={10} />
                Read-only
              </label>
            </div>
            <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              Local Ollama · Data stays on your machine
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function Avatar({ role }: { role: "user" | "assistant" }) {
  if (role === "user") {
    return (
      <div
        className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-1"
        style={{ backgroundColor: "var(--bg-inset)" }}
      >
        <User size={16} style={{ color: "var(--text-secondary)" }} />
      </div>
    );
  }
  return (
    <div
      className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-1"
      style={{ backgroundColor: "var(--accent-soft)" }}
    >
      <Bot size={16} style={{ color: "var(--accent)" }} />
    </div>
  );
}

function MessageBubble({ message: msg }: { message: Message }) {
  return (
    <div className={`flex gap-3 mb-4 ${msg.role === "user" ? "justify-end" : ""}`}>
      {msg.role === "assistant" && <Avatar role="assistant" />}
      <div className="space-y-2 max-w-[80%]">
        {msg.tools?.map((t, idx) => <ToolPill key={idx} tool={t} />)}
        <div
          className="rounded-2xl px-4 py-3 text-sm leading-relaxed"
          style={{
            backgroundColor: msg.role === "user" ? "var(--accent)" : "var(--bg-surface)",
            color: msg.role === "user" ? "white" : "var(--text-primary)",
            border: msg.role === "assistant" ? "1px solid var(--border-subtle)" : "none",
          }}
        >
          {msg.role === "assistant" ? (
            <div className="markdown-body">
              <Markdown>{msg.content}</Markdown>
            </div>
          ) : (
            <div>{msg.content}</div>
          )}
        </div>
      </div>
      {msg.role === "user" && <Avatar role="user" />}
    </div>
  );
}

function ToolPill({ tool }: { tool: ToolEvent }) {
  const done = tool.result !== undefined;
  return (
    <div
      className="rounded-lg px-3 py-2 text-xs flex items-start gap-2"
      style={{
        backgroundColor: "var(--bg-inset)",
        border: "1px solid var(--border-subtle)",
        color: "var(--text-secondary)",
      }}
    >
      {done ? (
        <CheckCircle2 size={12} style={{ color: "rgb(34, 197, 94)", marginTop: 2 }} />
      ) : (
        <Wrench size={12} style={{ color: "var(--accent)", marginTop: 2 }} />
      )}
      <div className="flex-1 min-w-0">
        <div style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
          {tool.name}
          {tool.args && Object.keys(tool.args).length > 0 ? (
            <span style={{ color: "var(--text-muted)" }}>
              ({Object.entries(tool.args)
                .map(([k, v]) => `${k}=${typeof v === "string" ? `"${v}"` : JSON.stringify(v)}`)
                .join(", ")})
            </span>
          ) : null}
        </div>
        {tool.result && (
          <details className="mt-1">
            <summary
              className="cursor-pointer"
              style={{ color: "var(--text-muted)", fontSize: 10 }}
            >
              result
            </summary>
            <pre
              className="mt-1 text-[10px] overflow-x-auto"
              style={{ color: "var(--text-muted)", whiteSpace: "pre-wrap" }}
            >
              {tool.result}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}
