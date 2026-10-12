#!/usr/bin/env bash
set -euo pipefail
kind="${1:?kind}"
image="${2:?published image}"
root="${3:?root digest}"
runnable="${4:?runnable digest}"
[[ "$(regctl image digest "$image")" == "$root" ]]
python3 .github/scripts/release_artifacts.py registry-graph "$image" "$root"
reference="${image%:*}@$runnable"
docker pull --platform linux/amd64 "$reference"
if [[ "$kind" == cpu ]]; then
  python3 tests/release_cpu_smoke.py "$reference" --output "${RUNNER_TEMP:?}/published-cpu-runtime.json" \
    --runnable-digest "$runnable"
elif [[ "$kind" == cuda ]]; then
  # No GPU in the publisher. Actual CUDA inference is separately bound to these bytes by trusted producer custody.
  docker run --rm --platform linux/amd64 --network none --entrypoint /app/.venv/bin/python "$reference" \
    -c 'import torch, torchvision, ultralytics; from lib.inference_engine import InferenceEngine; assert torch.version.cuda; assert torch.backends.cudnn.version()'
else
  exit 1
fi
