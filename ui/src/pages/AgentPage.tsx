/**
 * AI Agent — chat interface powered by local Ollama.
 * Suggests workflows, helps configure training, answers CV questions.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Send, Loader2, Bot, User, Sparkles } from "lucide-react";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

const SUGGESTIONS = [
  "How do I detect surveillance cameras in my video?",
  "Create a workflow that detects objects and counts them",
  "What augmentation settings should I use for small objects?",
  "Explain the difference between segmentation and detection",
  "Help me improve my model's accuracy",
];

export default function AgentPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(scrollToBottom, [messages, streamText]);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || streaming) return;

    const userMsg: Message = { role: "user", content: text.trim(), timestamp: Date.now() };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setStreaming(true);
    setStreamText("");

    try {
      // Build conversation history for context
      const history = [...messages, userMsg].slice(-10).map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const res = await fetch("http://localhost:11434/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "llama3.2",
          messages: [
            {
              role: "system",
              content: `You are Waldo, an AI assistant for a computer vision platform. You help users with:
- Object detection and segmentation using YOLO and SAM3 models
- Configuring training hyperparameters and augmentation strategies
- Building visual ML workflows (detection → crop → classify pipelines)
- Understanding model metrics (mAP, precision, recall)
- Debugging training issues and improving model accuracy
- Deploying models to production

Be concise and practical. When suggesting training configs, give specific numbers.
When asked about workflows, describe the block chain (e.g., ImageInput → Detection → Filter → Output).
Format your responses with markdown for readability.`,
            },
            ...history,
          ],
          stream: true,
        }),
      });

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response stream");

      let fullText = "";
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split("\n").filter(Boolean);

        for (const line of lines) {
          try {
            const json = JSON.parse(line);
            if (json.message?.content) {
              fullText += json.message.content;
              setStreamText(fullText);
            }
          } catch {
            // Skip malformed JSON
          }
        }
      }

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: fullText, timestamp: Date.now() },
      ]);
      setStreamText("");
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Sorry, I couldn't connect to the AI model. Make sure Ollama is running.\n\nError: ${e.message}`, timestamp: Date.now() },
      ]);
    } finally {
      setStreaming(false);
      inputRef.current?.focus();
    }
  }, [messages, streaming]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  return (
    <div className="h-screen flex flex-col" style={{ backgroundColor: "var(--bg-page)" }}>
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Welcome state */}
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
                Ask me anything about computer vision, model training, or building workflows.
              </p>

              {/* Suggestion chips */}
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

          {/* Message list */}
          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-3 mb-4 ${msg.role === "user" ? "justify-end" : ""}`}>
              {msg.role === "assistant" && (
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-1"
                  style={{ backgroundColor: "var(--accent-soft)" }}
                >
                  <Bot size={16} style={{ color: "var(--accent)" }} />
                </div>
              )}
              <div
                className="rounded-2xl px-4 py-3 max-w-[80%] text-sm leading-relaxed"
                style={{
                  backgroundColor: msg.role === "user" ? "var(--accent)" : "var(--bg-surface)",
                  color: msg.role === "user" ? "white" : "var(--text-primary)",
                  border: msg.role === "assistant" ? "1px solid var(--border-subtle)" : "none",
                }}
              >
                <div className="whitespace-pre-wrap">{msg.content}</div>
              </div>
              {msg.role === "user" && (
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-1"
                  style={{ backgroundColor: "var(--bg-inset)" }}
                >
                  <User size={16} style={{ color: "var(--text-secondary)" }} />
                </div>
              )}
            </div>
          ))}

          {/* Streaming indicator */}
          {streaming && (
            <div className="flex gap-3 mb-4">
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-1"
                style={{ backgroundColor: "var(--accent-soft)" }}
              >
                <Bot size={16} style={{ color: "var(--accent)" }} />
              </div>
              <div
                className="rounded-2xl px-4 py-3 max-w-[80%] text-sm leading-relaxed"
                style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)" }}
              >
                {streamText ? (
                  <div className="whitespace-pre-wrap">{streamText}<span className="animate-pulse">|</span></div>
                ) : (
                  <div className="flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
                    <Loader2 size={14} className="animate-spin" />
                    Thinking...
                  </div>
                )}
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input area */}
      <div style={{ borderTop: "1px solid var(--border-subtle)", backgroundColor: "var(--bg-surface)" }}>
        <div className="max-w-2xl mx-auto px-4 py-3">
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about computer vision, training, or workflows..."
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
          <p className="text-[10px] text-center mt-2" style={{ color: "var(--text-muted)" }}>
            Powered by local Ollama. Your data never leaves your machine.
          </p>
        </div>
      </div>
    </div>
  );
}
