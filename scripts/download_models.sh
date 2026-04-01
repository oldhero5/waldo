#!/usr/bin/env bash
set -euo pipefail

MODEL_ID="${SAM3_MODEL_ID:-facebook/sam3}"

echo "==> Downloading model: $MODEL_ID"
uv run python -c "
from huggingface_hub import snapshot_download
snapshot_download('${MODEL_ID}', token='${HF_TOKEN:-}' or None)
"
echo "==> Model downloaded successfully."
