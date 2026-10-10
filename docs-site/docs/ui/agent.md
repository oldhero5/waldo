---
title: Agent
sidebar_position: 6
---

# Agent

Route: `/agent` — Source: [`ui/src/pages/AgentPage.tsx`](https://github.com/oldhero5/waldo/blob/main/ui/src/pages/AgentPage.tsx)

Waldo ships a **LangGraph ReAct agent** wired up to the platform's own data
and actions. You ask questions in plain English; the agent calls real Waldo
tools (read-only and side-effecting), shows you which tools it ran, then
gives you the answer. Text models can run through Ollama, OpenAI, Anthropic, vLLM or OpenRouter.
Ollama remains the default. Hosted text transfer requires an explicit configuration
choice; there is no automatic local-to-cloud fallback.

![Agent page](/img/screenshots/agent.png)

## What it can do

The agent requires a workspace membership. Project-backed tools use exact
workspace ownership; legacy projects without an assigned workspace are excluded.
JWT requests may select a membership with `X-Workspace-ID`; otherwise the oldest
membership is selected. API keys remain pinned to their own workspace.

| Tool | Type | What it does |
| --- | --- | --- |
| `list_projects` | read | List projects with video counts |
| `list_videos` | read | List uploaded videos (optionally filtered to a project) |
| `list_datasets` | read | List completed labeling jobs with annotation counts |
| `list_models` | read | List trained models with mAP and active state |
| `list_training_runs` | read | Recent training runs with progress |
| `get_system_info` | read | Hardware probe — CUDA/MPS/CPU, dtype, active model |
| `get_training_tips` | read | Hyperparameter recommendations for a dataset size + task |
| `start_labeling_job` | **action** | Queue a SAM-3 auto-label run on a video |
| `start_training` | **action** | Queue a YOLO training run on a labeled dataset |
| `activate_model` | **action** | Mark a trained model active for `/predict/*` |

The full-page agent (`/agent`) requests **action mode** by default. The server
grants action tools only to workspace **admin** or **editor** members; other
roles receive read tools. Tick the **Read-only** toggle in the footer to
constrain an authorized action session to inspection tools.

The floating **AgentPanel** (the spark icon in the lower-right of every
page) is **read-only by design** — open the full page to take actions.

## How it works

```
   you ──▶ AgentPage  ──▶  /api/v1/agent/stream  (SSE)
                              │
                              ▼
                       LangGraph ReAct loop
                       (lib/agent/graph.py)
                              │
                              ├──▶ Shared provider adapter ── local or hosted text model
                              │
                              └──▶ ToolNode ──▶ list_models, start_training, …
                                       (auth-scoped to your workspace)
```

Each `/agent/stream` request runs the loop inside an `AgentContext` that
pins every tool call to your user + workspace. The LLM sees the system
prompt, your message history, and the tool descriptions; it decides whether
to answer or to call a tool; the loop iterates until it has a final answer.
The system prompt is server-owned. Client history accepts only user and
assistant messages (up to 100 messages, each at most 32,000 characters);
client system and tool messages are rejected. Page context is supplied as user
content. Tool access uses the same persisted ownership policy as REST/workflows,
with membership and key grants rechecked while a conversation is running.

The endpoint streams Server-Sent Events:

```
data: {"type":"tool_call","name":"list_models","args":{}}
data: {"type":"tool_result","name":"list_models","content":"Models (2): …"}
data: {"type":"token","content":"You have two trained models …"}
data: {"type":"done"}
```

The UI renders each tool call as an inline pill so you can see exactly what
the agent did.

## Try it

Suggestion chips on first load (and a few you can paste yourself):

- "What models are trained in this workspace?"
- "Recommend training settings for a 200-frame dataset"
- "Start a labeling job for 'person' on my latest video"
- "Activate the model with the best mAP"
- "Am I running on GPU or CPU right now?"

## Configuration

In **Settings → Agent provider**, a workspace administrator can select a provider,
enter a model and a write-only API key, explicitly permit hosted text transfer,
and test/apply the configuration. The test sends a short text request and can
incur provider charges. Keys are not returned, logged by application error handlers,
or saved in browser storage. Browser input cannot choose an arbitrary provider
endpoint. OpenAI uses Responses; vLLM/OpenRouter use the compatible chat interface;
Anthropic uses Messages. Tool results stay associated with their tool calls.

**Temporary settings apply only to the current API process and clear on restart.**
They do not synchronize across API workers or Celery processes. Use deployment
secrets/environment configuration for durable operation until encrypted shared
credential storage is implemented. Never put keys in workflow graph JSON.

| Variable | Purpose |
| --- | --- |
| `AGENT_PROVIDER` | `ollama` (default), `openai`, `anthropic`, `vllm`, `openrouter` |
| `AGENT_MODEL` | Default model ID; choose an ID supported by the selected provider |
| `AGENT_ALLOWED_MODELS` | Optional comma-separated permitted model overrides |
| `AGENT_ALLOW_CLOUD_TEXT` | Explicitly enable text transfer to hosted providers (default false) |
| `AGENT_TIMEOUT_SECONDS` | Per-model-call timeout; default 60 seconds |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` | Server-side credential for the selected hosted service |
| `OLLAMA_URL` | Operator-configured Ollama endpoint |
| `VLLM_URL`, `VLLM_API_KEY` | Operator-configured compatible endpoint/credential |
| `AGENT_TEMPERATURE` | Sampling setting; default 0.2 |

An external vLLM/Ollama endpoint can also receive your text; choose those endpoints
according to your deployment's data policy. Each conversation and workflow captures
one provider configuration so a settings change cannot silently switch its model
mid-run. Workflow LLM blocks use the same factory and allowed model policy.

For local chat in Compose, enable the `local-chat` profile and use `AGENT_MODEL`
for both the application and model download. `make up` enables it by default;
`make up CHAT_PROFILE=` starts without it. The API can start without Ollama.

## Configuration status

`GET /api/v1/agent/health` reports provider configuration validity, selected model,
configuration source and `connection_verified: false`. It does not claim the
provider is reachable or that the selected model supports tools. The explicit
Settings connection test checks a text call, not tool/image capabilities.

`GET /api/v1/agent/models` lists the configured/default and explicitly allowed
models. Missing keys, disabled cloud transfer, unavailable local servers, provider
errors and timeouts are surfaced without a silent fallback. Check server-side
configuration for details; provider response bodies are not echoed to clients.

## Current limits

Provider adapters currently support text conversations and tool orchestration.
No video, crop or image submission is wired through this layer. Model-specific
tool capability, routing behavior, service rate limits and live account access
still need qualification. The map/evidence-search tools described in the roadmap
are not implemented by selecting a new chat provider.
