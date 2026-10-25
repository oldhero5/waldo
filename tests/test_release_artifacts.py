"""Behavior checks for the coupled, fail-closed release artifact gate."""

import importlib.util
import io
import json
import tarfile
import zipfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/release_artifacts.py"
SPEC = importlib.util.spec_from_file_location("release_artifacts", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64


def evidence():
    return {
        "schema": 1,
        "source_sha": SHA,
        "repository": "oldhero5/waldo",
        "run_id": 123,
        "run_attempt": 1,
        "kind": "cpu",
        "platform": "linux/amd64",
        "archive_sha256": "c" * 64,
        "root_digest": DIGEST,
        "runnable_digest": "sha256:" + "d" * 64,
        "attestations": ["https://spdx.dev/Document", "https://slsa.dev/provenance/v0.2"],
        "scan": {
            "runnable_digest": "sha256:" + "d" * 64,
            "scanner": "trivy",
            "version": release.TRIVY_VERSION,
            "database": {"Version": 2, "UpdatedAt": "2026-10-12T00:00:00Z"},
            "report_sha256": "e" * 64,
            "exit_code": 0,
            "high_critical": 0,
        },
        "runtime": {"passed": True, "runnable_digest": "sha256:" + "d" * 64, "report_sha256": "f" * 64},
    }


def test_candidate_accepts_bound_cpu_evidence():
    release.validate_candidate(evidence(), SHA, "cpu")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sha", "b" * 40),
        ("kind", "cuda"),
        ("platform", "linux/arm64"),
        ("archive_sha256", ""),
        ("root_digest", "bad"),
        ("attestations", []),
        ("attestations", [release.SBOM]),
        ("attestations", ["https://slsa.dev/provenance/v1"]),
    ],
)
def test_candidate_rejects_changed_identity_or_missing_graph(field, value):
    data = evidence()
    data[field] = value
    with pytest.raises(release.GateError):
        release.validate_candidate(data, SHA, "cpu")


@pytest.mark.parametrize(
    "field,value",
    [("exit_code", 1), ("high_critical", 1), ("runnable_digest", DIGEST), ("database", {}), ("version", "unpinned")],
)
def test_candidate_rejects_failed_unbound_or_unversioned_scan(field, value):
    data = evidence()
    data["scan"][field] = value
    with pytest.raises(release.GateError):
        release.validate_candidate(data, SHA, "cpu")


@pytest.mark.parametrize("runtime", [{}, {"passed": False}, {"passed": True, "runnable_digest": DIGEST}])
def test_candidate_rejects_missing_failed_or_unbound_runtime(runtime):
    data = evidence()
    data["runtime"] = runtime
    with pytest.raises(release.GateError):
        release.validate_candidate(data, SHA, "cpu")


def producer():
    now = datetime.now(UTC)
    run = {
        "id": 123,
        "run_attempt": 1,
        "workflow_id": 456,
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "repository": {"full_name": "oldhero5/waldo"},
        "created_at": now.isoformat(),
    }
    jobs = {
        "total_count": 3,
        "jobs": [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in ("Qualify cpu candidate", "Qualify cuda candidate", "CUDA model qualification")
        ],
    }
    artifacts = {
        "total_count": 3,
        "artifacts": [
            {
                "id": index,
                "name": name,
                "expired": False,
                "expires_at": (now + timedelta(days=1)).isoformat(),
                "digest": "sha256:" + "e" * 64,
                "workflow_run": {"id": 123, "head_sha": SHA},
            }
            for index, name in enumerate(("candidate-cpu", "candidate-cuda", "cuda-qualification"), 1)
        ],
    }
    return run, jobs, artifacts


def test_producer_accepts_only_successful_main_workflow_and_jobs():
    release.validate_producer(*producer(), "oldhero5/waldo", SHA, 123, 456)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", 789),
        ("event", "pull_request"),
        ("head_branch", "feature"),
        ("head_sha", "b" * 40),
        ("conclusion", "failure"),
        ("run_attempt", 2),
        ("repository", {"full_name": "attacker/waldo"}),
        ("created_at", "2020-01-01T00:00:00Z"),
    ],
)
def test_producer_rejects_untrusted_failed_or_stale_run(field, value):
    run, jobs, artifacts = producer()
    run[field] = value
    with pytest.raises(release.GateError):
        release.validate_producer(run, jobs, artifacts, "oldhero5/waldo", SHA, 123, 456)


@pytest.mark.parametrize(
    "mutation",
    ["skipped_gpu", "missing_gpu", "expired", "wrong_artifact_sha", "missing_digest", "truncated", "duplicate"],
)
def test_producer_rejects_missing_or_forged_gpu_custody(mutation):
    run, jobs, artifacts = producer()
    if mutation == "skipped_gpu":
        jobs["jobs"][-1]["conclusion"] = "skipped"
    elif mutation == "missing_gpu":
        jobs["jobs"].pop()
        jobs["total_count"] -= 1
    elif mutation == "expired":
        artifacts["artifacts"][-1]["expired"] = True
    elif mutation == "wrong_artifact_sha":
        artifacts["artifacts"][-1]["workflow_run"]["head_sha"] = "b" * 40
    elif mutation == "missing_digest":
        artifacts["artifacts"][-1].pop("digest")
    elif mutation == "truncated":
        artifacts["total_count"] = 101
    else:
        artifacts["artifacts"].append(deepcopy(artifacts["artifacts"][-1]))
        artifacts["total_count"] += 1
    with pytest.raises(release.GateError):
        release.validate_producer(run, jobs, artifacts, "oldhero5/waldo", SHA, 123, 456)


class Registry:
    def __init__(self):
        self.tags = {}
        self.copies = []

    def digest(self, ref):
        return self.tags.get(ref)

    def copy(self, source, target, digest):
        self.copies.append((source, target))
        self.tags[target] = digest


def test_partial_publication_is_incomplete_and_retry_resumes_same_pair(tmp_path):
    registry = Registry()
    pair = {
        kind: {"root_digest": "sha256:" + digit * 64, "source": f"ocidir:/tmp/{kind}"}
        for kind, digit in (("cpu", "a"), ("cuda", "b"))
    }
    journal = tmp_path / "journal.json"
    with pytest.raises(release.GateError, match="injected"):
        release.publish_pair(pair, "1.2.3", "localhost:5000/waldo", journal, registry, lambda refs: None, fail_after=1)
    state = json.loads(journal.read_text())
    assert state["complete"] is False
    assert state["previous_channels"] == {"latest": None, "cuda": None}
    assert len(state["confirmed"]) == 1
    release.publish_pair(pair, "1.2.3", "localhost:5000/waldo", journal, registry, lambda refs: None)
    assert json.loads(journal.read_text())["complete"] is True
    assert registry.tags["localhost:5000/waldo:latest"] == pair["cpu"]["root_digest"]
    assert registry.tags["localhost:5000/waldo:cuda"] == pair["cuda"]["root_digest"]


def test_publish_rejects_conflicting_version_before_writing(tmp_path):
    registry = Registry()
    registry.tags["localhost:5000/waldo:1.2.3"] = DIGEST
    pair = {kind: {"root_digest": "sha256:" + "a" * 64, "source": "ocidir:/tmp/image"} for kind in ("cpu", "cuda")}
    with pytest.raises(release.GateError, match="conflicting"):
        release.publish_pair(
            pair, "1.2.3", "localhost:5000/waldo", tmp_path / "journal.json", registry, lambda refs: None
        )
    assert registry.copies == []


@pytest.mark.parametrize("missing", ["cpu", "cuda"])
def test_pair_requires_both_candidates(missing):
    pair = {"cpu": evidence(), "cuda": {**evidence(), "kind": "cuda"}}
    pair.pop(missing)
    with pytest.raises(release.GateError):
        release.validate_pair(pair, {}, SHA)


def gpu_evidence(cuda):
    return {
        "schema": 1,
        **{
            key: cuda[key]
            for key in (
                "source_sha",
                "repository",
                "run_id",
                "run_attempt",
                "root_digest",
                "runnable_digest",
                "archive_sha256",
            )
        },
        "probe": {
            "checkpoint_sha256": release.CHECKPOINT_SHA256,
            "checkpoint_revision": release.CHECKPOINT_REVISION,
            "checkpoint_bytes": 5613764,
            "cuda_forward": True,
            "cuda_parameters": True,
            "finite_outputs": True,
            "dtype": "float16",
            "elapsed_seconds": 0.1,
            "gpu": "NVIDIA test device",
            "driver": "test-driver",
            "torch": "test-torch",
            "cuda": "test-cuda",
            "cudnn": "test-cudnn",
            "input_sha256": release.hashlib.sha256(bytes(640 * 640 * 3)).hexdigest(),
            "candidate": {
                "GITHUB_SHA": cuda["source_sha"],
                "RELEASE_ROOT_DIGEST": cuda["root_digest"],
                "RELEASE_RUNNABLE_DIGEST": cuda["runnable_digest"],
                "RELEASE_ARCHIVE_SHA256": cuda["archive_sha256"],
            },
            "parameters": {"device": "cuda:0", "half": True, "conf": 0.25, "imgsz": 640},
        },
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "wrong_digest",
        "wrong_source",
        "wrong_checkpoint",
        "no_forward",
        "cpu_parameters",
        "nonfinite",
        "wrong_dtype",
        "zero_time",
        "missing_hardware",
    ],
)
def test_gpu_report_is_required_and_binds_actual_probe_to_candidate(mutation):
    pair = {"cpu": evidence(), "cuda": {**evidence(), "kind": "cuda"}}
    gpu = gpu_evidence(pair["cuda"])
    if mutation == "missing":
        gpu = {}
    elif mutation == "wrong_digest":
        gpu["runnable_digest"] = DIGEST
    elif mutation == "wrong_source":
        gpu["source_sha"] = "b" * 40
    else:
        key, value = {
            "wrong_checkpoint": ("checkpoint_sha256", "f" * 64),
            "no_forward": ("cuda_forward", False),
            "cpu_parameters": ("cuda_parameters", False),
            "nonfinite": ("finite_outputs", False),
            "wrong_dtype": ("dtype", "float32"),
            "zero_time": ("elapsed_seconds", 0),
            "missing_hardware": ("gpu", ""),
        }[mutation]
        gpu["probe"][key] = value
    with pytest.raises(release.GateError):
        release.validate_pair(pair, gpu, SHA)


def test_gpu_report_still_requires_trusted_producer_custody():
    # This only tests the schema boundary; the CLI separately validates GitHub run/job/artifact custody.
    pair = {"cpu": evidence(), "cuda": {**evidence(), "kind": "cuda"}}
    release.validate_pair(pair, gpu_evidence(pair["cuda"]), SHA)


def synthetic_oci(tmp_path, *, revision=SHA, architecture="amd64", attested=True, wrong_subject=False):
    """A tiny valid graph for negative graph checks; real BuildKit is tested separately."""
    blobs = {}

    def add(value, media):
        raw = json.dumps(value, separators=(",", ":")).encode() if isinstance(value, dict) else value
        digest = "sha256:" + release.hashlib.sha256(raw).hexdigest()
        blobs["blobs/sha256/" + digest.split(":")[1]] = raw
        return {"mediaType": media, "size": len(raw), "digest": digest}

    config = add(
        {
            "architecture": architecture,
            "os": "linux",
            "config": {"Labels": {"org.opencontainers.image.revision": revision}},
        },
        "application/vnd.oci.image.config.v1+json",
    )
    layer = add(b"synthetic-layer", "application/vnd.oci.image.layer.v1.tar+gzip")
    runnable = add(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": config,
            "layers": [layer],
        },
        "application/vnd.oci.image.manifest.v1+json",
    )
    runnable["platform"] = {"os": "linux", "architecture": architecture}
    manifests = [runnable]
    if attested:
        layers = []
        for predicate in (release.SBOM, "https://slsa.dev/provenance/v1"):
            value = {
                "_type": "https://in-toto.io/Statement/v0.1",
                "predicateType": predicate,
                "subject": [{"digest": {"sha256": (DIGEST if wrong_subject else runnable["digest"]).split(":")[1]}}],
                "predicate": {"buildDefinition": {"resolvedDependencies": [{"digest": {"sha1": revision}}]}},
            }
            item = add(value, "application/vnd.in-toto+json")
            item["annotations"] = {"in-toto.io/predicate-type": predicate}
            layers.append(item)
        attestation = add(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": config,
                "layers": layers,
            },
            "application/vnd.oci.image.manifest.v1+json",
        )
        attestation["annotations"] = {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": runnable["digest"],
        }
        attestation["platform"] = {"os": "unknown", "architecture": "unknown"}
        manifests.append(attestation)
    root = add(
        {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": manifests},
        "application/vnd.oci.image.index.v1+json",
    )
    blobs["index.json"] = json.dumps({"schemaVersion": 2, "manifests": [root]}).encode()
    blobs["oci-layout"] = b'{"imageLayoutVersion":"1.0.0"}'
    path = tmp_path / "candidate.oci.tar"
    write_tar(path, blobs)
    return path, blobs


def write_tar(path, blobs):
    with tarfile.open(path, "w") as archive:
        for name, raw in blobs.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))


def test_archive_verifies_all_reachable_blobs_and_attestation_binding(tmp_path):
    path, _ = synthetic_oci(tmp_path)
    graph = release.inspect_archive(path, SHA)
    assert graph["blob_count"] > 5 and graph["platform"] == "linux/amd64"


@pytest.mark.parametrize(
    "mutation",
    ["wrong_source", "wrong_platform", "missing_attestation", "wrong_subject", "changed_bytes", "missing_blob"],
)
def test_archive_rejects_wrong_identity_and_changed_or_missing_bytes(tmp_path, mutation):
    kwargs = {
        "wrong_source": {"revision": "b" * 40},
        "wrong_platform": {"architecture": "arm64"},
        "missing_attestation": {"attested": False},
        "wrong_subject": {"wrong_subject": True},
    }.get(mutation, {})
    path, blobs = synthetic_oci(tmp_path, **kwargs)
    if mutation in {"changed_bytes", "missing_blob"}:
        name = next(name for name, raw in blobs.items() if raw == b"synthetic-layer")
        if mutation == "changed_bytes":
            blobs[name] = b"modified-layer!"
        else:
            blobs.pop(name)
        write_tar(path, blobs)
    with pytest.raises(release.GateError):
        release.inspect_archive(path, SHA)


def test_registry_write_uncertainty_remains_in_incomplete_record(tmp_path):
    class UnknownRegistry(Registry):
        def copy(self, source, target, digest):
            super().copy(source, target, digest)
            raise release.GateError("Readback connection lost after possible write")

    pair = {kind: {"root_digest": DIGEST, "source": "ocidir:/tmp/test"} for kind in ("cpu", "cuda")}
    journal = tmp_path / "journal.json"
    with pytest.raises(release.GateError, match="connection lost"):
        release.publish_pair(pair, "1.2.3", "localhost:5000/waldo", journal, UnknownRegistry(), lambda refs: None)
    state = json.loads(journal.read_text())
    assert state["complete"] is False and state["confirmed"] == [] and len(state["uncertain"]) == 1


def test_published_runtime_failure_prevents_both_channel_promotions(tmp_path):
    registry = Registry()
    pair = {kind: {"root_digest": DIGEST, "source": "ocidir:/tmp/test"} for kind in ("cpu", "cuda")}

    def failed_smoke(refs):
        raise release.GateError("Published runtime failed")

    journal = tmp_path / "journal.json"
    with pytest.raises(release.GateError, match="runtime failed"):
        release.publish_pair(pair, "1.2.3", "localhost:5000/waldo", journal, registry, failed_smoke)
    assert registry.digest("localhost:5000/waldo:latest") is None
    assert registry.digest("localhost:5000/waldo:cuda") is None
    assert json.loads(journal.read_text())["complete"] is False


def test_retry_rejects_a_different_qualified_pair(tmp_path):
    registry = Registry()
    pair = {kind: {"root_digest": DIGEST, "source": "ocidir:/tmp/test"} for kind in ("cpu", "cuda")}
    journal = tmp_path / "journal.json"
    with pytest.raises(release.GateError, match="injected"):
        release.publish_pair(pair, "1.2.3", "localhost:5000/waldo", journal, registry, lambda refs: None, fail_after=1)
    pair["cuda"]["root_digest"] = "sha256:" + "f" * 64
    with pytest.raises(release.GateError, match="same immutable pair"):
        release.publish_pair(pair, "1.2.3", "localhost:5000/waldo", journal, registry, lambda refs: None)


def test_prepare_saves_intent_and_previous_pair_without_a_registry_write(tmp_path):
    registry = Registry()
    registry.tags["localhost:5000/waldo:latest"] = "sha256:" + "a" * 64
    registry.tags["localhost:5000/waldo:cuda"] = "sha256:" + "c" * 64
    pair = {kind: {"root_digest": DIGEST} for kind in ("cpu", "cuda")}
    journal = tmp_path / "journal.json"
    release.prepare_pair(pair, "1.2.3", "localhost:5000/waldo", journal, registry, {"candidate_run_id": 123})
    state = json.loads(journal.read_text())
    assert registry.copies == [] and state["complete"] is False
    assert state["previous_channels"] == {"latest": "sha256:" + "a" * 64, "cuda": "sha256:" + "c" * 64}
    assert state["evidence"]["candidate_run_id"] == 123


def test_dry_run_uses_trusted_run_id_and_never_logs_in(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("GITHUB_REPOSITORY", "oldhero5/waldo")
    monkeypatch.setattr(release, "fetch_candidates", lambda run, path: calls.append(("fetch", run)))
    monkeypatch.setattr(release, "validate_downloads", lambda *args: {"cpu": evidence(), "cuda": evidence()})
    monkeypatch.setattr(release, "command", lambda *args, **kwargs: calls.append(("command", args)))
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_artifacts.py",
            "publish",
            "123",
            "v1.2.3",
            "--directory",
            str(tmp_path),
            "--journal",
            str(tmp_path / "journal.json"),
            "--dry-run",
        ],
    )
    release.main()
    assert calls[0] == ("fetch", 123)
    assert len(calls) == 2 and calls[1][1][0] == ["bash", ".github/scripts/check-release.sh"]


def test_cli_rejects_arbitrary_report_path_as_release_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_artifacts.py",
            "publish",
            str(tmp_path / "fake.json"),
            "v1.2.3",
            "--directory",
            str(tmp_path),
            "--journal",
            str(tmp_path / "journal.json"),
        ],
    )
    with pytest.raises(SystemExit):
        release.main()


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_download_validation_rejects_unfixed_findings_even_if_manifest_claims_zero(tmp_path, severity):
    directory = tmp_path / "candidate-cpu"
    directory.mkdir()
    archive, _ = synthetic_oci(directory)
    graph = release.inspect_archive(archive, SHA)
    report = {
        "ArtifactName": "local/waldo@" + graph["runnable_digest"],
        "Results": [{"Vulnerabilities": [{"Severity": severity, "FixedVersion": ""}]}],
    }
    scan = directory / "scan.json"
    scan.write_text(json.dumps(report))
    manifest = evidence()
    manifest.update(graph)
    manifest["scan"].update(report_sha256=release.checksum(scan), runnable_digest=graph["runnable_digest"])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(release.GateError, match="High/critical"):
        release.validate_downloads(tmp_path, SHA, "oldhero5/waldo", 123)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 99),
        ("name", "fake-report"),
        ("expired", True),
        ("workflow_run", {"id": 2, "head_sha": SHA}),
        ("digest", ""),
    ],
)
def test_publication_rejects_missing_or_unbound_durable_intent(tmp_path, monkeypatch, field, value):
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    artifact = {
        "id": 5,
        "name": "release-intent",
        "expired": False,
        "digest": DIGEST,
        "workflow_run": {"id": 1, "head_sha": SHA},
    }
    artifact[field] = value
    monkeypatch.setattr(release, "github", lambda path: artifact)
    with pytest.raises(release.GateError):
        release.verify_durable_intent(5, tmp_path / "journal.json", "oldhero5/waldo", SHA)


@pytest.mark.parametrize("changed", [False, True])
def test_durable_intent_download_checksum_and_local_binding(tmp_path, monkeypatch, changed):
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    state = {"schema": 1, "intended": {"cpu": DIGEST, "cuda": DIGEST}, "complete": False}
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("journal.json", json.dumps(state))
    zipped = body.getvalue()
    artifact = {
        "id": 5,
        "name": "release-intent",
        "expired": False,
        "digest": "sha256:" + release.hashlib.sha256(zipped).hexdigest(),
        "workflow_run": {"id": 1, "head_sha": SHA},
    }
    monkeypatch.setattr(release, "github", lambda path: artifact)

    def download(args, *, stdout, check):
        stdout.write(zipped)

    monkeypatch.setattr(release.subprocess, "run", download)
    if changed:
        state["intended"]["cpu"] = "sha256:" + "f" * 64
    journal = tmp_path / "journal.json"
    journal.write_text(json.dumps(state))
    if changed:
        with pytest.raises(release.GateError, match="differs"):
            release.verify_durable_intent(5, journal, "oldhero5/waldo", SHA)
    else:
        assert release.verify_durable_intent(5, journal, "oldhero5/waldo", SHA) == artifact
