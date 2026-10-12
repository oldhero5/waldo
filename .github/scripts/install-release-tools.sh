#!/usr/bin/env bash
# Official release assets, pinned by SHA256. Install only into a caller-owned directory.
set -euo pipefail
destination="${1:?tool directory required}"
mkdir -p "$destination"
case "$(uname -s)/$(uname -m)" in
  Linux/x86_64)
    reg_asset=regctl-linux-amd64
    reg_hash=b4f352d3acbe5cd3d2255ebac618a0f8da80b72f16ec30182275cb891f5e6ee5
    trivy_asset=trivy_0.75.0_Linux-64bit.tar.gz
    trivy_hash=c6e65abddb348e25f10549df887045629cf28cc72453cd1c63acb717316b3f3f ;;
  Darwin/arm64)
    reg_asset=regctl-darwin-arm64
    reg_hash=ca1006be2b686e9945fb595720a8a64fb6f00ab0f0dd68c55f2ece1f027219fd
    trivy_asset=trivy_0.75.0_macOS-ARM64.tar.gz
    trivy_hash=4a77108cccf8e55c8d6823e1e759939a622277e66cd0daa3c1fc621ed69e4568 ;;
  *) echo 'Release tools support Linux AMD64 or local Mac ARM64 only.' >&2; exit 1 ;;
esac
verify() { python3 - "$1" "$2" <<'PY'
import hashlib, sys
with open(sys.argv[1], 'rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
if actual != sys.argv[2]:
    raise SystemExit('Official tool asset checksum mismatch')
PY
}
curl -fsSL --retry 3 -o "$destination/regctl" "https://github.com/regclient/regclient/releases/download/v0.8.3/$reg_asset"
verify "$destination/regctl" "$reg_hash"
chmod +x "$destination/regctl"
curl -fsSL --retry 3 -o "$destination/trivy.tar.gz" "https://github.com/aquasecurity/trivy/releases/download/v0.75.0/$trivy_asset"
verify "$destination/trivy.tar.gz" "$trivy_hash"
tar -xzf "$destination/trivy.tar.gz" -C "$destination" trivy
rm "$destination/trivy.tar.gz"
"$destination/regctl" version | grep -F 'v0.8.3'
"$destination/trivy" --version | grep -F 'Version: 0.75.0'
if [[ -n "${GITHUB_PATH:-}" ]]; then echo "$destination" >> "$GITHUB_PATH"; fi
