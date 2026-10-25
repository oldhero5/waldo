"""Real, bounded BuildKit OCI graph round trip. No application image or external push."""

import importlib.util
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_oci_gate", Path(__file__).resolve().parents[1] / ".github/scripts/release_artifacts.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


@pytest.mark.skipif(
    os.environ.get("WALDO_RELEASE_OCI_INTEGRATION") != "1", reason="Requires disposable local Docker registry"
)
def test_real_oci_graph_copy_retry_and_partial_publication(tmp_path, monkeypatch):
    """Build real SPDX/provenance, repair a missing blob, and fail after image/channel writes."""
    identity = "waldo-oci-" + uuid.uuid4().hex[:10]
    builder = identity + "-builder"
    registry = identity + "-registry"
    monkeypatch.setenv("REGCTL_CONFIG", str(tmp_path / "regctl.json"))
    sha = "a" * 40

    def run(*args):
        return subprocess.run(args, check=True, text=True, capture_output=True, timeout=180).stdout.strip()

    try:
        run(
            "docker",
            "buildx",
            "create",
            "--name",
            builder,
            "--driver",
            "docker-container",
            "--driver-opt",
            "image=moby/buildkit@sha256:0039c1d47e8748b5afea56f4e85f14febaf34452bd99d9552d2daa82262b5cc5",
        )
        context = tmp_path / "context"
        context.mkdir()
        (context / "Dockerfile").write_text("FROM scratch\nCOPY marker /marker\n")
        (context / "marker").write_text("synthetic public release graph test\n")
        archive = tmp_path / "candidate.oci.tar"
        run(
            "docker",
            "buildx",
            "build",
            "--builder",
            builder,
            "--platform",
            "linux/amd64",
            "--provenance=mode=max",
            "--attest",
            "type=sbom,generator=docker/buildkit-syft-scanner@sha256:"
            "ae4f3b554449e7e25548e7d8ccc029d17357348e30c6e3df01b92bc93654d6a9",
            "--label",
            f"org.opencontainers.image.revision={sha}",
            "--output",
            f"type=oci,dest={archive}",
            str(context),
        )
        # Local synthetic context has no Git provenance source. Verify graph/platform;
        # actual candidate workflows use an immutable Git context and require source binding.
        graph = release.inspect_archive(archive)
        assert release.SBOM in graph["attestations"] and set(graph["attestations"]) & release.PROVENANCE
        run(
            "docker",
            "run",
            "-d",
            "--name",
            registry,
            "-p",
            "127.0.0.1::5000",
            "registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373",
        )
        host = "127.0.0.1:" + run("docker", "port", registry, "5000/tcp").rsplit(":", 1)[1]
        run("regctl", "registry", "set", host, "--tls", "disabled")
        source = f"{host}/source:tiny"
        run("regctl", "image", "import", source, str(archive))
        assert release.Registry().digest(source) == graph["root_digest"]
        release.verify_registry_graph(source, graph["root_digest"])
        layout = tmp_path / "layout"
        layout_ref = f"ocidir://{layout}:tiny"
        run("regctl", "image", "copy", source, layout_ref, "--force-recursive")
        returned = tmp_path / "returned.oci.tar"
        run("regctl", "image", "export", layout_ref, str(returned))
        after = release.inspect_archive(returned)
        assert {key: value for key, value in after.items() if key != "archive_sha256"} == {
            key: value for key, value in graph.items() if key != "archive_sha256"
        }
        root = json.loads((layout / "blobs/sha256" / graph["root_digest"].split(":")[1]).read_text())
        attestation = next(
            item
            for item in root["manifests"]
            if item.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest"
        )
        attested = json.loads((layout / "blobs/sha256" / attestation["digest"].split(":")[1]).read_text())
        missing = layout / "blobs/sha256" / attested["layers"][0]["digest"].split(":")[1]
        missing.unlink()
        broken = subprocess.run(
            ["regctl", "image", "export", layout_ref, str(returned)], capture_output=True, check=False
        )
        assert broken.returncode != 0, "An existing root must not conceal missing attestation content"
        run("regctl", "image", "copy", source, layout_ref, "--force-recursive")
        assert missing.exists()
        run("regctl", "image", "export", layout_ref, str(returned))
        assert release.inspect_archive(returned)["root_digest"] == graph["root_digest"]
        (context / "marker").write_text("distinct synthetic CUDA variant marker\n")
        cuda_archive = tmp_path / "cuda.oci.tar"
        run(
            "docker",
            "buildx",
            "build",
            "--builder",
            builder,
            "--platform",
            "linux/amd64",
            "--provenance=mode=max",
            "--attest",
            "type=sbom,generator=docker/buildkit-syft-scanner@sha256:"
            "ae4f3b554449e7e25548e7d8ccc029d17357348e30c6e3df01b92bc93654d6a9",
            "--label",
            f"org.opencontainers.image.revision={sha}",
            "--output",
            f"type=oci,dest={cuda_archive}",
            str(context),
        )
        cuda_graph = release.inspect_archive(cuda_archive)
        assert cuda_graph["root_digest"] != graph["root_digest"]
        cuda_layout = f"ocidir://{tmp_path / 'cuda-layout'}:tiny"
        run("regctl", "image", "import", cuda_layout, str(cuda_archive))
        pair = {
            "cpu": {"root_digest": graph["root_digest"], "source": layout_ref},
            "cuda": {"root_digest": cuda_graph["root_digest"], "source": cuda_layout},
        }

        def graph_smoke(refs):
            for kind, ref in refs.items():
                release.verify_registry_graph(ref, pair[kind]["root_digest"])

        # Separate repositories ensure each injection performs all four fresh mutations.
        for fail_after in (1, 3):
            repository = f"{host}/pair-{fail_after}"
            journal = tmp_path / f"journal-{fail_after}.json"
            previous = {"latest": None, "cuda": None}
            if fail_after == 3:
                # Seed a distinct old pair, then test restoration of BOTH saved channels.
                previous = {"latest": cuda_graph["root_digest"], "cuda": graph["root_digest"]}
                release.Registry().copy(cuda_layout, repository + ":latest", previous["latest"])
                release.Registry().copy(layout_ref, repository + ":cuda", previous["cuda"])
            with pytest.raises(release.GateError, match="injected"):
                release.publish_pair(
                    pair, "1.2.3", repository, journal, release.Registry(), graph_smoke, fail_after=fail_after
                )
            state = json.loads(journal.read_text())
            assert state["complete"] is False and len(state["confirmed"]) == fail_after
            assert state["previous_channels"] == previous
            if fail_after == 3:
                assert release.Registry().digest(repository + ":latest") == graph["root_digest"]
                assert release.Registry().digest(repository + ":cuda") == previous["cuda"]
                for channel, digest in state["previous_channels"].items():
                    release.Registry().copy(f"{repository}@{digest}", f"{repository}:{channel}", digest)
                assert all(
                    release.Registry().digest(f"{repository}:{channel}") == digest
                    for channel, digest in previous.items()
                )
            release.publish_pair(pair, "1.2.3", repository, journal, release.Registry(), graph_smoke)
            assert json.loads(journal.read_text())["complete"] is True
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", registry], capture_output=True, check=False)
        subprocess.run(["docker", "buildx", "rm", builder], capture_output=True, check=False)
