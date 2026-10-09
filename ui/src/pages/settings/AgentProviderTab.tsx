import { useEffect, useState } from "react";
import { authFetch } from "../../api";

interface ProviderStatus { provider: string; model: string; ok: boolean; error?: string | null; source: "environment" | "runtime"; cloud_text_enabled: boolean; connection_verified: boolean }

export function AgentProviderTab({ isAdmin }: { isAdmin: boolean }) {
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const cloud = ["openai", "anthropic", "openrouter"].includes(provider);
  const apply = (data: ProviderStatus) => { setStatus(data); setProvider(data.provider); setModel(data.model); setConsent(data.cloud_text_enabled); };
  useEffect(() => {
    const controller = new AbortController();
    authFetch("/api/v1/agent/health", { signal: controller.signal }).then(async (r) => { if (!r.ok) throw new Error("Provider configuration is unavailable"); return r.json(); })
      .then(apply).catch((e) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  const submit = async (reset = false) => {
    if (busy) return;
    setBusy(true); setError(""); const credential = key; setKey("");
    try {
      const response = await authFetch("/api/v1/agent/provider", { method: reset ? "DELETE" : "POST", headers: { "Content-Type": "application/json" }, body: reset ? undefined : JSON.stringify({ provider, model, api_key: credential, allow_cloud_text: consent }) });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Provider configuration failed");
      apply(data);
    } catch (e) { setError(e instanceof Error ? e.message : "Provider configuration failed"); }
    finally { setBusy(false); }
  };
  const field = "w-full bg-[var(--bg-inset)] border border-[var(--border-default)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)]";
  return <div className="surface p-5 space-y-4">
    <h2 className="font-semibold text-sm">Text model provider</h2>
    <p className="text-sm text-[var(--text-secondary)]">Agent chat and workflow text blocks use this workspace’s provider. Cloud requests include chat and tool results, which may contain workspace data. These integrations do not send images or video.</p>
    {status && <p className="text-xs text-[var(--text-muted)]">Current: {status.provider} · {status.model} · {status.source === "runtime" ? "temporary runtime configuration" : "deployment environment"}. {status.connection_verified ? "Connection test passed." : "Connection has not been tested here."}</p>}
    <p className="text-xs text-[var(--text-muted)]">Environment variables provide durable configuration. Runtime overrides stay in this server process’s memory and clear on restart; deployments with multiple workers should use environment configuration. Keys are write-only and never saved in your browser.</p>
    {status?.error && <p className="text-sm text-[var(--warning)]">{status.error}</p>}
    {!isAdmin ? <p className="text-sm text-[var(--text-secondary)]">A workspace administrator can configure the provider.</p> : <>
      <label className="block text-sm space-y-1"><span>Provider</span><select aria-label="Provider" className={field} value={provider} disabled={busy} onChange={(e) => { setProvider(e.target.value); setKey(""); setModel(""); setConsent(false); }}><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="vllm">vLLM</option><option value="openrouter">OpenRouter</option></select></label>
      <label className="block text-sm space-y-1"><span>Model identifier</span><input aria-label="Model identifier" className={field} value={model} onChange={(e) => setModel(e.target.value)} maxLength={200} disabled={busy} /></label>
      {provider !== "ollama" && <label className="block text-sm space-y-1"><span>API key</span><input aria-label="API key" className={field} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} disabled={busy} placeholder="Blank uses this provider’s server environment key" /></label>}
      <p className="text-xs text-[var(--text-muted)]">Endpoints come from server configuration; this form cannot change local server addresses.</p>
      {cloud && <label className="flex items-start gap-2 text-sm"><input className="mt-1" type="checkbox" checked={consent} disabled={busy} onChange={(e) => setConsent(e.target.checked)} /><span>I allow workspace chat, workflow text, and tool results to be sent to {provider}.</span></label>}
      <p className="text-xs text-[var(--text-muted)]">Testing sends “Reply with OK” to the chosen model and may incur charges. The override is applied only after the test succeeds.</p>
      <div className="flex gap-3 flex-wrap"><button className="px-4 py-2 rounded-lg bg-accent text-on-accent hover:bg-accent-hover text-sm disabled:opacity-50" disabled={busy || !model.trim() || (cloud && !consent)} onClick={() => submit()}>{busy ? "Working…" : "Test and apply temporary configuration"}</button><button className="px-3 py-2 text-sm border border-[var(--border-default)] rounded-lg disabled:opacity-50" disabled={busy} onClick={() => submit(true)}>Use deployment configuration</button></div>
    </>}
    {error && <p role="alert" className="text-sm text-[var(--danger)]">{error}</p>}
  </div>;
}
