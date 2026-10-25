#!/usr/bin/env bash
set -euo pipefail
candidate="${1:?candidate-cuda directory}"
output="${2:?GPU report directory}"
mkdir -p "$output"
temporary="$(mktemp -d "${TMPDIR:-/tmp}/waldo-gpu.XXXXXX")"
registry="waldo-gpu-$(basename "$temporary" | tr '[:upper:]' '[:lower:]')"
cleanup() { docker rm -f -v "$registry" >/dev/null 2>&1 || true; rm -rf "$temporary"; }
trap cleanup EXIT
export REGCTL_CONFIG="$temporary/regctl.json"
python3 .github/scripts/release_artifacts.py inspect "$candidate/candidate.oci.tar" --sha "$GITHUB_SHA" \
  --output "$temporary/graph.json"
read -r root runnable archive_sha < <(python3 - "$temporary/graph.json" "$candidate/manifest.json" <<'PY'
import json,sys
a,b=(json.load(open(p)) for p in sys.argv[1:])
if any(b.get(k)!=v for k,v in a.items()): raise SystemExit('CUDA artifact changed')
print(a['root_digest'],a['runnable_digest'],a['archive_sha256'])
PY
)
curl -fsSL --retry 3 -o "$temporary/yolo11n.pt" \
  https://huggingface.co/Ultralytics/YOLO11/resolve/5599de6f3b0ef95f98f37530ed0f3bf149c10b37/yolo11n.pt
printf '%s  %s\n' 0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1 "$temporary/yolo11n.pt" | sha256sum --check
docker run -d --name "$registry" -p 127.0.0.1::5000 \
  registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373 >/dev/null
host="127.0.0.1:$(docker port "$registry" 5000/tcp | sed 's/.*://')"
regctl registry set "$host" --tls disabled
regctl image import "$host/waldo:cuda" "$candidate/candidate.oci.tar"
python3 .github/scripts/release_artifacts.py registry-graph "$host/waldo:cuda" "$root"
docker pull --platform linux/amd64 "$host/waldo@$runnable"
# timeout kills a hung container; the task-owned name is removed by the exit trap.
probe="$(pwd)/tests/release_cuda_probe.py"
container="$registry-probe"
trap 'docker rm -f -v "$container" >/dev/null 2>&1 || true; cleanup' EXIT
mkdir "$temporary/report"
timeout 180 docker run --name "$container" --platform linux/amd64 --gpus all --network none \
  -e DEVICE=cuda:0 -e GITHUB_SHA -e "RELEASE_ROOT_DIGEST=$root" -e "RELEASE_RUNNABLE_DIGEST=$runnable" \
  -e "RELEASE_ARCHIVE_SHA256=$archive_sha" -e YOLO_CONFIG_DIR=/tmp/yolo \
  -v "$probe:/release/probe.py:ro" -v "$temporary/yolo11n.pt:/release/yolo11n.pt:ro" \
  -v "$temporary/report:/evidence" \
  --entrypoint /app/.venv/bin/python "$host/waldo@$runnable" /release/probe.py /release/yolo11n.pt /evidence/probe.json
python3 - "$candidate/manifest.json" "$temporary/report/probe.json" "$output/gpu.json" <<'PY'
import json,sys
c,p=(json.load(open(path)) for path in sys.argv[1:3])
keys=('source_sha','repository','run_id','run_attempt','root_digest','runnable_digest','archive_sha256')
report={key:c[key] for key in keys}; report.update(schema=1, probe=p)
open(sys.argv[3],'w').write(json.dumps(report,indent=2)+'\n')
PY
