---
title: Agent
sidebar_position: 6
---

# Agent Page

Route: `/agent` — Source: [`ui/src/pages/AgentPage.tsx`](https://github.com/your-org/waldo/blob/main/ui/src/pages/AgentPage.tsx)

Chat interface for the in-app assistant. Backed by Gemma 4 (4B) running natively on Apple Silicon via [`mlx-vlm`](https://github.com/Blaizzy/mlx-vlm), with Ollama as a fallback for non-Mac hosts.

## Capabilities

- **Insights** — summarize a labeling job, highlight class imbalance, suggest prompts.
- **Chat** — answer questions about the current dataset.
- **Vision** — analyze a frame or annotation directly (multimodal).

## Configuration

- `AGENT_MODEL_ID` — model id for the MLX-VLM path (default `google/gemma-4-e4b-it`).
- `OLLAMA_URL` / `OLLAMA_MODEL` — fallback Ollama endpoint.

The page picks the MLX path automatically when `mlx-vlm` is importable; otherwise it falls back to Ollama.

![Agent page](/img/screenshots/agent.png)
