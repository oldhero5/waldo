#!/usr/bin/env bash
# No credentials or external registry writes. The local registry belongs to this invocation.
set -euo pipefail
kind="${1:?cpu or cuda}"
archive="${2:?OCI archive}"
output="${3:?report directory}"
platform="${4:-linux/amd64}"
[[ "$kind" == cpu || "$kind" == cuda ]] || exit 1
mkdir -p "$output"
temporary="$(mktemp -d "${TMPDIR:-/tmp}/waldo-candidate.XXXXXX")"
registry="waldo-candidate-$(basename "$temporary" | tr '[:upper:]' '[:lower:]')"
cleanup() {
  docker rm -f -v "$registry" >/dev/null 2>&1 || true
  rm -rf "$temporary"
}
trap cleanup EXIT
export REGCTL_CONFIG="$temporary/regctl.json"
python3 .github/scripts/release_artifacts.py inspect "$archive" --sha "${GITHUB_SHA:?source SHA}" \
  --platform "$platform" --output "$temporary/graph.json"
read -r root runnable < <(python3 - "$temporary/graph.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); print(d['root_digest'], d['runnable_digest'])
PY
)
docker run -d --name "$registry" -p 127.0.0.1::5000 \
  registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373 >/dev/null
port="$(docker port "$registry" 5000/tcp | sed 's/.*://')"
host="127.0.0.1:$port"
regctl registry set "$host" --tls disabled
image="$host/waldo:$kind"
regctl image import "$image" "$archive"
[[ "$(regctl image digest "$image")" == "$root" ]] || { echo 'Import changed root digest' >&2; exit 1; }
python3 .github/scripts/release_artifacts.py registry-graph "$image" "$root"
export TRIVY_INSECURE=true
trivy image --image-src remote --scanners vuln --severity HIGH,CRITICAL --exit-code 1 \
  --format json --output "$output/scan.json" "$host/waldo@$runnable"
trivy --version --format json > "$output/scan-version.json"
if [[ "$kind" == cpu ]]; then
  docker pull --platform "$platform" "$host/waldo@$runnable" >/dev/null
  python3 tests/release_cpu_smoke.py "$host/waldo@$runnable" --platform "$platform" \
    --runnable-digest "$runnable" --output "$output/runtime.json"
fi
python3 - "$kind" "$archive" "$output" "$temporary/graph.json" <<'PY'
import hashlib, json, os, subprocess, sys
from pathlib import Path
kind, archive, output, graph = sys.argv[1:]
output = Path(output)
def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()
data = json.loads(Path(graph).read_text())
scan = json.loads((output / 'scan.json').read_text())
version = json.loads((output / 'scan-version.json').read_text())
if version['Version'] != '0.75.0': raise SystemExit('Wrong scanner version')
data.update(schema=1, kind=kind, source_sha=os.environ['GITHUB_SHA'], repository=os.environ['GITHUB_REPOSITORY'],
            run_id=int(os.environ['GITHUB_RUN_ID']), run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']))
data['tools'] = dict(regctl=subprocess.check_output(['regctl','version'], text=True).strip(),
                     buildx=subprocess.check_output(['docker','buildx','version'], text=True).strip(),
                     buildkit_image='moby/buildkit@sha256:0039c1d47e8748b5afea56f4e85f14febaf34452bd99d9552d2daa82262b5cc5',
                     sbom_generator='docker/buildkit-syft-scanner@sha256:ae4f3b554449e7e25548e7d8ccc029d17357348e30c6e3df01b92bc93654d6a9')
data['scan'] = dict(runnable_digest=data['runnable_digest'], scanner='trivy', version=version['Version'],
                    database=version['VulnerabilityDB'], report_sha256=sha(output/'scan.json'), exit_code=0,
                    high_critical=sum(v['Severity'] in {'HIGH','CRITICAL'} for r in scan.get('Results',[])
                                      for v in r.get('Vulnerabilities',[])))
if kind == 'cpu':
    runtime = json.loads((output/'runtime.json').read_text())
    if runtime.get('passed') is not True: raise SystemExit('Runtime smoke failed')
    data['runtime'] = dict(passed=True, runnable_digest=data['runnable_digest'], report_sha256=sha(output/'runtime.json'))
(output/'manifest.json').write_text(json.dumps(data, indent=2)+'\n')
PY
