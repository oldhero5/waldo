---
title: Agent
sidebar_position: 6
---

import Demo from "@site/src/components/Demo";

# Agent Page

Route: `/agent` — Source: [`ui/src/pages/AgentPage.tsx`](https://github.com/oldhero5/waldo/blob/main/ui/src/pages/AgentPage.tsx)

Chat interface for the in-app assistant. Backed by **Gemma 4 (4B)** running natively on Apple Silicon via [`mlx-vlm`](https://github.com/Blaizzy/mlx-vlm), with Ollama as a fallback for non-Mac hosts.

![Agent page](/img/screenshots/agent.png)

<Demo
  src="/img/recordings/agent.mp4"
  poster="/img/recordings/agent.poster.jpg"
  caption="Opening the agent panel."
/>

## Capabilities

| Capability | What it does |
| --- | --- |
| **Insights** | Summarize a labeling job, highlight class imbalance, suggest prompts |
| **Chat** | Answer free-form questions about the current dataset |
| **Vision** | Analyze a frame or annotation directly (multimodal — pass an image as context) |
| **Suggest** | Recommend the next action: more frames? new prompt? larger model? |

The same model is wired into the **AI Insights** drawer on the Review page — the agent panel is just a freer interface to the same backend.

## Configuration

| Var | Default | Purpose |
| --- | --- | --- |
| `AGENT_MODEL_ID` | `google/gemma-4-e4b-it` | Model id for the MLX-VLM path |
| `OLLAMA_URL` | `http://localhost:11434` | Fallback Ollama endpoint |
| `OLLAMA_MODEL` | `gemma2:4b` | Fallback Ollama model |

The page picks the MLX path automatically when `mlx-vlm` is importable; otherwise it falls back to Ollama. There is no third path — install one or the other.

## Privacy

The agent runs locally. Nothing in your dataset, prompts, or frames leaves the host. That's the whole point of running it via MLX or Ollama instead of a hosted API.
