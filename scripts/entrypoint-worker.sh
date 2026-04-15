#!/usr/bin/env bash
# Worker entrypoint — prints GPU status before starting Celery so that
# `make logs` immediately shows whether CUDA passthrough is working.
#
# Exits 0 unconditionally so that workers on CPU-only hosts still start.
set -eu

echo "================================================================"
echo " Waldo worker — GPU check"
echo "================================================================"

if [ "${DEVICE:-}" = "cuda" ]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "[entrypoint] nvidia-smi:"
    nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv 2>&1 || echo "[entrypoint] nvidia-smi failed"
  else
    echo "[entrypoint] WARNING: nvidia-smi not found. Is nvidia-container-toolkit installed on the host?"
  fi

  echo "[entrypoint] torch CUDA check:"
  uv run python -c "
import torch
print(f'  torch: {torch.__version__}')
print(f'  CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'  CUDA version (torch): {torch.version.cuda}')
    print(f'  device count: {torch.cuda.device_count()}')
    for i in range(torch.cuda.device_count()):
        print(f'  [{i}] {torch.cuda.get_device_name(i)}')
else:
    print('  WARNING: torch.cuda.is_available() is False. GPU will not be used.')
" 2>&1 || echo "[entrypoint] torch check failed"
else
  echo "[entrypoint] DEVICE=${DEVICE:-unset} — skipping GPU check"
fi

echo "================================================================"
echo " Starting worker"
echo "================================================================"
exec "$@"
