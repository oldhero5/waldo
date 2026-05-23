---
name: download-models
description: Pull the SAM 3 weights from Hugging Face into the local model cache so the labeler doesn't pay the first-call cost. Reads HF_TOKEN from .env, falls back to $HF_TOKEN. Use when the user says "download models", "pull SAM weights", or "fix the slow first inference".
disable-model-invocation: true
---

You're pulling SAM 3 weights into the local HuggingFace cache so the first labeler request doesn't time out.

## Steps

1. **Find HF_TOKEN**
   - Try `.env` first: `awk -F= '$1=="HF_TOKEN"{sub(/^[^=]*=/,""); print; exit}' .env`
   - Fall back to `$HF_TOKEN` from the environment.
   - If neither is set, stop and tell the user to either set `HF_TOKEN` in `.env` or accept the license at https://huggingface.co/facebook/sam3 and create a read token at https://huggingface.co/settings/tokens.

2. **Run the script** — the repo already ships `scripts/download_models.sh` which calls `huggingface_hub.snapshot_download`. Don't reinvent it.
   ```bash
   set -a && . ./.env && set +a
   bash scripts/download_models.sh
   ```

3. **Confirm what landed** — check the cache size and warn if it's suspiciously small (SAM 3 is ~2 GB):
   ```bash
   du -sh ~/.cache/huggingface/hub/models--facebook--sam3 2>/dev/null || \
       echo "no local cache (download may have failed)"
   ```

4. **If running against a live stack**, also warm the labeler's volume:
   ```bash
   docker compose ps waldo-labeler-nvidia waldo-labeler 2>/dev/null
   # If a worker is up, the next /api/v1/label call will be fast.
   ```

## Guardrails

- Never log the token. The download script reads it from env; don't echo `$HF_TOKEN`.
- Don't retry forever — if `snapshot_download` fails twice (network, 401, 403), surface the error and stop.
