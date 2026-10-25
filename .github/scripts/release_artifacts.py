#!/usr/bin/env python3
"""Small release gates. Trust comes from GitHub producer custody, never report input."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

TRIVY_VERSION = "0.75.0"
REGCTL_VERSION = "v0.8.3"
PLATFORM = "linux/amd64"
SBOM = "https://spdx.dev/Document"
PROVENANCE = {"https://slsa.dev/provenance/v0.2", "https://slsa.dev/provenance/v1"}
WORKFLOW = ".github/workflows/release-candidate.yml"
CHECKPOINT_SHA256 = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
CHECKPOINT_REVISION = "5599de6f3b0ef95f98f37530ed0f3bf149c10b37"


class GateError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise GateError(message)


def command(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()  # noqa: S603


def checksum(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value)


def inspect_archive(path, source_sha=None, platform=PLATFORM):
    """Read the complete OCI graph, hashing blobs without loading image layers into RAM."""
    with tarfile.open(path) as archive:
        names = [member.name.removeprefix("./") for member in archive.getmembers() if member.isfile()]
        require(len(names) == len(set(names)), "Duplicate archive member")
        members = {member.name.removeprefix("./"): member for member in archive.getmembers()}
        for name, member in members.items():
            require(member.isfile() or member.isdir(), "Archive links are forbidden")
            require(not name.startswith("/") and ".." not in Path(name).parts, "Unsafe archive path")

        def read(name):
            require(name in members and members[name].isfile(), f"Missing OCI member: {name}")
            return archive.extractfile(members[name]).read()

        require(json.loads(read("oci-layout"))["imageLayoutVersion"] == "1.0.0", "Unsupported OCI layout")
        index = json.loads(read("index.json"))
        require(len(index.get("manifests", [])) == 1, "Expected one immutable root index")
        visited = set()

        def blob(descriptor, parse=False):
            digest = descriptor["digest"]
            require(valid_digest(digest), "Invalid OCI digest")
            name = "blobs/sha256/" + digest.split(":")[1]
            require(name in members and members[name].size == descriptor["size"], "OCI blob size mismatch")
            if digest not in visited:
                with archive.extractfile(members[name]) as stream:
                    require(
                        hashlib.file_digest(stream, "sha256").hexdigest() == digest.split(":")[1],
                        "OCI blob checksum mismatch",
                    )
                visited.add(digest)
            return json.loads(read(name)) if parse else None

        root_descriptor = index["manifests"][0]
        root = blob(root_descriptor, True)
        require(root.get("mediaType") == "application/vnd.oci.image.index.v1+json", "Missing OCI root index")
        runnable = []
        attested = []
        for descriptor in root.get("manifests", []):
            manifest = blob(descriptor, True)
            config = blob(manifest["config"], True)
            for layer in manifest["layers"]:
                blob(layer)
            if descriptor.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest":
                attested.append((descriptor, manifest))
            else:
                runnable.append((descriptor, config))
        require(len(runnable) == 1, "Expected exactly one runnable image")
        descriptor, config = runnable[0]
        actual_platform = f"{config.get('os')}/{config.get('architecture')}"
        require(actual_platform == platform, "Wrong runnable platform")
        require(
            descriptor.get("platform", {}).get("os") == platform.split("/")[0]
            and descriptor.get("platform", {}).get("architecture") == platform.split("/")[1],
            "Index and config platform mismatch",
        )
        if source_sha:
            require(
                config.get("config", {}).get("Labels", {}).get("org.opencontainers.image.revision") == source_sha,
                "Wrong image source revision",
            )
        predicates = set()
        for attestation, manifest in attested:
            require(
                attestation.get("annotations", {}).get("vnd.docker.reference.digest") == descriptor["digest"],
                "Attestation references another image",
            )
            for layer in manifest["layers"]:
                require(layer["mediaType"] == "application/vnd.in-toto+json", "Wrong attestation layer type")
                statement = blob(layer, True)
                # BuildKit's OCI exporter leaves subject empty and binds through the
                # index child's vnd.docker.reference.digest annotation instead.
                subjects = statement.get("subject")
                require(
                    isinstance(subjects, list)
                    and (
                        not subjects
                        or any(
                            subject.get("digest", {}).get("sha256") == descriptor["digest"].split(":")[1]
                            for subject in subjects
                        )
                    ),
                    "Unbound attestation subject",
                )
                predicate = statement.get("predicateType")
                require(
                    predicate == layer.get("annotations", {}).get("in-toto.io/predicate-type"),
                    "Attestation predicate mismatch",
                )
                require(
                    isinstance(statement.get("predicate"), dict) and statement["predicate"],
                    "Empty attestation predicate",
                )
                if source_sha and predicate in PROVENANCE:
                    provenance = statement["predicate"]
                    materials = provenance.get("materials", []) + provenance.get("buildDefinition", {}).get(
                        "resolvedDependencies", []
                    )
                    vcs = (
                        provenance.get("runDetails", {}).get("metadata", {}).get("buildkit_metadata", {}).get("vcs", {})
                    )
                    require(
                        any(material.get("digest", {}).get("sha1") == source_sha for material in materials)
                        or vcs.get("revision") == source_sha,
                        "Provenance source SHA mismatch",
                    )
                predicates.add(predicate)
        require(SBOM in predicates and predicates & PROVENANCE, "Missing SBOM or provenance")
        return {
            "archive_sha256": checksum(path),
            "root_digest": root_descriptor["digest"],
            "runnable_digest": descriptor["digest"],
            "platform": actual_platform,
            "attestations": sorted(predicates),
            "blob_count": len(visited),
        }


def validate_candidate(data, sha, kind):
    require(
        data.get("schema") == 1 and data.get("source_sha") == sha and data.get("kind") == kind,
        "Wrong candidate source or kind",
    )
    require(data.get("platform") == PLATFORM, "Release requires linux/amd64")
    require(re.fullmatch(r"[0-9a-f]{64}", data.get("archive_sha256", "")), "Missing archive checksum")
    require(
        valid_digest(data.get("root_digest")) and valid_digest(data.get("runnable_digest")), "Missing image digests"
    )
    predicates = set(data.get("attestations", []))
    require(SBOM in predicates and predicates & PROVENANCE, "Missing attestations")
    scan = data.get("scan", {})
    require(
        scan.get("exit_code") == 0 and scan.get("high_critical") == 0,
        "Missing/failed scan or HIGH/CRITICAL findings (including unfixed)",
    )
    require(
        scan.get("runnable_digest") == data["runnable_digest"]
        and scan.get("scanner") == "trivy"
        and scan.get("version") == TRIVY_VERSION
        and scan.get("database", {}).get("Version")
        and scan.get("database", {}).get("UpdatedAt")
        and re.fullmatch(r"[0-9a-f]{64}", scan.get("report_sha256", "")),
        "Unbound scan/tool/database evidence",
    )
    if kind == "cpu":
        runtime = data.get("runtime", {})
        require(
            runtime.get("passed") is True
            and runtime.get("runnable_digest") == data["runnable_digest"]
            and re.fullmatch(r"[0-9a-f]{64}", runtime.get("report_sha256", "")),
            "Missing/failed CPU runtime qualification",
        )


def validate_pair(pair, gpu, sha):
    require(set(pair) == {"cpu", "cuda"}, "Both CPU and CUDA candidates are required")
    for kind, data in pair.items():
        validate_candidate(data, sha, kind)
    cpu, cuda = pair["cpu"], pair["cuda"]
    require(
        cpu.get("repository") == cuda.get("repository")
        and cpu.get("run_id") == cuda.get("run_id")
        and cpu.get("run_attempt") == cuda.get("run_attempt") == 1,
        "Candidates have different producers",
    )
    require(
        gpu.get("schema") == 1
        and gpu.get("source_sha") == sha
        and gpu.get("run_id") == cuda.get("run_id")
        and gpu.get("run_attempt") == 1
        and gpu.get("repository") == cuda.get("repository")
        and gpu.get("root_digest") == cuda["root_digest"]
        and gpu.get("runnable_digest") == cuda["runnable_digest"]
        and gpu.get("archive_sha256") == cuda["archive_sha256"],
        "Missing or unbound trusted GPU report",
    )
    probe = gpu.get("probe", {})
    require(
        probe.get("checkpoint_sha256") == CHECKPOINT_SHA256
        and probe.get("checkpoint_revision") == CHECKPOINT_REVISION
        and probe.get("checkpoint_bytes") == 5613764
        and probe.get("cuda_forward") is True
        and probe.get("cuda_parameters") is True
        and probe.get("finite_outputs") is True
        and probe.get("dtype") == "float16"
        and probe.get("elapsed_seconds", 0) > 0
        and probe.get("gpu")
        and probe.get("driver")
        and probe.get("torch")
        and probe.get("cuda")
        and probe.get("cudnn")
        and probe.get("input_sha256") == hashlib.sha256(bytes(640 * 640 * 3)).hexdigest()
        and probe.get("candidate")
        == {
            "GITHUB_SHA": sha,
            "RELEASE_ROOT_DIGEST": cuda["root_digest"],
            "RELEASE_RUNNABLE_DIGEST": cuda["runnable_digest"],
            "RELEASE_ARCHIVE_SHA256": cuda["archive_sha256"],
        }
        and probe.get("parameters") == {"device": "cuda:0", "half": True, "conf": 0.25, "imgsz": 640},
        "Actual CUDA inference evidence is absent or incomplete",
    )


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_producer(run, jobs, artifacts, repo, sha, run_id, workflow_id):
    require(
        run.get("id") == run_id
        and run.get("workflow_id") == workflow_id
        and run.get("repository", {}).get("full_name") == repo
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == "main"
        and run.get("head_sha") == sha
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("run_attempt") == 1,
        "Untrusted or unsuccessful candidate producer",
    )
    require(
        datetime.now(UTC) - timedelta(days=2) < parse_time(run["created_at"]) <= datetime.now(UTC), "Producer is stale"
    )
    require(jobs.get("total_count") == len(jobs.get("jobs", [])) <= 100, "Incomplete producer jobs")
    for name in ("Qualify cpu candidate", "Qualify cuda candidate", "CUDA model qualification"):
        matching = [job for job in jobs["jobs"] if job.get("name") == name]
        require(
            len(matching) == 1
            and matching[0].get("status") == "completed"
            and matching[0].get("conclusion") == "success",
            f"Producer job did not pass: {name}",
        )
    require(artifacts.get("total_count") == len(artifacts.get("artifacts", [])) <= 100, "Incomplete producer artifacts")
    selected = {}
    for name in ("candidate-cpu", "candidate-cuda", "cuda-qualification"):
        matching = [item for item in artifacts["artifacts"] if item.get("name") == name]
        require(len(matching) == 1, f"Missing or duplicate artifact: {name}")
        artifact = matching[0]
        require(
            artifact.get("expired") is False
            and parse_time(artifact["expires_at"]) > datetime.now(UTC)
            and artifact.get("workflow_run", {}).get("id") == run_id
            and artifact.get("workflow_run", {}).get("head_sha") == sha
            and isinstance(artifact.get("id"), int)
            and valid_digest(artifact.get("digest")),
            f"Expired or unbound artifact: {name}",
        )
        selected[name] = artifact
    return selected


def github(path):
    return json.loads(command(["gh", "api", path]))


def fetch_candidates(run_id, directory):
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    workflow = github(f"repos/{repo}/actions/workflows/release-candidate.yml")
    require(workflow.get("path") == WORKFLOW and workflow.get("state") == "active", "Wrong producer workflow")
    base = f"repos/{repo}/actions/runs/{run_id}"
    artifacts = validate_producer(
        github(base),
        github(base + "/jobs?per_page=100"),
        github(base + "/artifacts?per_page=100"),
        repo,
        sha,
        run_id,
        workflow["id"],
    )
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, artifact in artifacts.items():
        target = directory / name
        target.mkdir(exist_ok=True)
        # Download by immutable API artifact ID; never search an untrusted run's report filename.
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".zip") as archive:
            with Path(archive.name).open("wb") as stream:
                subprocess.run(
                    ["gh", "api", f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"], stdout=stream, check=True
                )  # noqa: S603
            require("sha256:" + checksum(archive.name) == artifact["digest"], "GitHub artifact checksum mismatch")
            with zipfile.ZipFile(archive.name) as bundle:
                allowed = {"candidate.oci.tar", "manifest.json", "scan.json", "runtime.json", "scan-version.json"}
                if name == "cuda-qualification":
                    allowed = {"gpu.json"}
                require(
                    set(bundle.namelist()) <= allowed and len(bundle.namelist()) == len(set(bundle.namelist())),
                    "Unexpected or duplicate artifact members",
                )
                for member in bundle.namelist():
                    require(not bundle.getinfo(member).is_dir(), "Unexpected artifact directory")
                    with bundle.open(member) as source, (target / member).open("wb") as destination:
                        shutil.copyfileobj(source, destination)
        write_json(target / "custody.json", artifact)
    validate_downloads(directory, sha, repo, run_id)


def validate_downloads(directory, sha, repo, run_id):
    directory = Path(directory)
    pair = {}
    for kind in ("cpu", "cuda"):
        candidate = directory / f"candidate-{kind}"
        data = json.loads((candidate / "manifest.json").read_text())
        require(
            data.get("repository") == repo and data.get("run_id") == run_id and data.get("run_attempt") == 1,
            "Report producer binding mismatch",
        )
        actual = inspect_archive(candidate / "candidate.oci.tar", sha)
        require(all(data.get(key) == value for key, value in actual.items()), "Archive changed after qualification")
        require(
            data.get("scan", {}).get("report_sha256") == checksum(candidate / "scan.json"),
            "Scan report checksum mismatch",
        )
        report = json.loads((candidate / "scan.json").read_text())
        require(report.get("ArtifactName", "").endswith("@" + data["runnable_digest"]), "Wrong scanned image")
        require(
            not any(
                item.get("Severity") in {"HIGH", "CRITICAL"}
                for result in report.get("Results", [])
                for item in result.get("Vulnerabilities", [])
            ),
            "High/critical scan finding",
        )
        if kind == "cpu":
            require(
                data.get("runtime", {}).get("report_sha256") == checksum(candidate / "runtime.json"),
                "Runtime report checksum mismatch",
            )
            runtime = json.loads((candidate / "runtime.json").read_text())
            require(
                runtime.get("passed") is True
                and runtime.get("runnable_digest") == data["runnable_digest"]
                and runtime.get("platform") == PLATFORM,
                "CPU runtime report failed or identity changed",
            )
        version = json.loads((candidate / "scan-version.json").read_text())
        require(
            version.get("Version") == TRIVY_VERSION
            and version.get("VulnerabilityDB") == data.get("scan", {}).get("database"),
            "Scanner/database version report mismatch",
        )
        pair[kind] = data
    gpu = json.loads((directory / "cuda-qualification/gpu.json").read_text())
    validate_pair(pair, gpu, sha)
    write_json(
        directory / "pair.json",
        {"schema": 1, "source_sha": sha, "run_id": run_id, "repository": repo, "candidates": pair, "gpu": gpu},
    )
    return pair


class Registry:
    def digest(self, ref):
        result = subprocess.run(
            ["regctl", "image", "digest", ref],  # noqa: S603
            text=True,
            capture_output=True,
        )
        if result.returncode:
            # Only an explicit manifest-not-found is absence. Network/auth failures cannot invent rollback state.
            if "not found" in result.stderr.lower() or "manifest unknown" in result.stderr.lower():
                return None
            raise GateError("Registry read failed: " + result.stderr)
        require(valid_digest(result.stdout.strip()), "Registry returned invalid digest")
        return result.stdout.strip()

    def copy(self, source, target, digest):
        command(["regctl", "image", "copy", source, target, "--force-recursive"])
        require(self.digest(target) == digest, "Registry digest changed during copy")
        verify_registry_graph(target, digest)


def verify_registry_graph(reference, root_digest):
    require(Registry().digest(reference) == root_digest, "Registry root digest mismatch")
    repository = reference.split("@")[0]
    # Strip a tag only after the final slash (registry ports are not tags).
    prefix, slash, name = repository.rpartition("/")
    repository = prefix + slash + name.split(":")[0]
    seen = set()

    def visit(digest):
        if digest in seen:
            return
        seen.add(digest)
        raw = subprocess.check_output(["regctl", "manifest", "get", f"{repository}@{digest}", "--format", "raw-body"])  # noqa: S603
        require("sha256:" + hashlib.sha256(raw).hexdigest() == digest, "Registry manifest bytes changed")
        manifest = json.loads(raw)
        if "manifests" in manifest:
            for child in manifest["manifests"]:
                visit(child["digest"])
        else:
            for child in [manifest["config"], *manifest["layers"]]:
                command(["regctl", "blob", "head", repository, child["digest"]])

    visit(root_digest)


def prepare_pair(pair, version, repository, journal, registry, evidence=None):
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version), "Explicit release version is required")
    require(set(pair) == {"cpu", "cuda"}, "Both images required")
    desired = {kind: value["root_digest"] for kind, value in pair.items()}
    require(all(valid_digest(value) for value in desired.values()), "Invalid intended image digest")
    targets = {"cpu": f"{repository}:{version}", "cuda": f"{repository}:{version}-cuda"}
    for kind, target in targets.items():
        existing = registry.digest(target)
        require(existing is None or existing == desired[kind], f"Version tag has conflicting content: {target}")
    journal = Path(journal)
    if journal.exists():
        state = json.loads(journal.read_text())
        require(
            state.get("intended") == desired
            and state.get("version") == version
            and state.get("repository") == repository,
            "Retry must use the same immutable pair",
        )
        if evidence is not None:
            require(state.get("evidence") == evidence, "Retry qualification evidence changed")
    else:
        state = {
            "schema": 1,
            "version": version,
            "repository": repository,
            "intended": desired,
            "previous_channels": {name: registry.digest(f"{repository}:{name}") for name in ("latest", "cuda")},
            "confirmed": [],
            "uncertain": [],
            "complete": False,
        }
        if evidence is not None:
            state["evidence"] = evidence
    state["complete"] = False
    write_json(journal, state)  # Durable pair intent and old channels precede the first registry write.
    return state


def publish_pair(pair, version, repository, journal, registry, smoke, fail_after=0, evidence=None):
    state = prepare_pair(pair, version, repository, journal, registry, evidence)
    desired = state["intended"]
    targets = {"cpu": f"{repository}:{version}", "cuda": f"{repository}:{version}-cuda"}
    mutations = 0

    def mutate(kind, target, source):
        nonlocal mutations
        changed = registry.digest(target) != desired[kind]
        operation = {"target": target, "digest": desired[kind]}
        # An existing matching root can still lack nested blobs after a partial
        # copy. Always repair its complete graph before accepting the readback.
        if operation not in state["uncertain"]:
            state["uncertain"].append(operation)
        write_json(journal, state)
        registry.copy(source, target, desired[kind])
        if changed:
            mutations += 1
        state["uncertain"] = [item for item in state["uncertain"] if item != operation]
        if operation not in state["confirmed"]:
            state["confirmed"].append(operation)
        write_json(journal, state)
        if changed and fail_after and mutations == fail_after:
            raise GateError("injected failure after registry mutation")
        require(registry.digest(target) == desired[kind], "Registry readback failed")

    for kind, target in targets.items():
        mutate(kind, target, pair[kind]["source"])
    smoke(targets)  # Both published immutable versions must run before any channel changes.
    for kind, channel in (("cpu", "latest"), ("cuda", "cuda")):
        mutate(kind, f"{repository}:{channel}", f"{repository}@{desired[kind]}")
    require(
        all(
            registry.digest(f"{repository}:{channel}") == desired[kind]
            for kind, channel in (("cpu", "latest"), ("cuda", "cuda"))
        ),
        "Partial channel publication",
    )
    state["complete"] = True
    state["uncertain"] = []
    write_json(journal, state)


def restore_journal(run_id, destination, source_sha, repository):
    workflow = github(f"repos/{repository}/actions/workflows/release.yml")
    require(workflow.get("path") == ".github/workflows/release.yml", "Wrong publisher workflow")
    run = github(f"repos/{repository}/actions/runs/{run_id}")
    require(
        run.get("workflow_id") == workflow.get("id")
        and run.get("head_sha") == source_sha
        and run.get("repository", {}).get("full_name") == repository
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == "main"
        and run.get("status") == "completed"
        and run.get("conclusion") in {"success", "failure", "cancelled", "timed_out"}
        and run.get("run_attempt") == 1,
        "Untrusted previous publisher run",
    )
    artifacts = github(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100")
    require(
        artifacts.get("total_count") == len(artifacts.get("artifacts", [])) <= 100, "Incomplete publisher artifacts"
    )
    selected = [artifact for artifact in artifacts["artifacts"] if artifact.get("name") == "release-record"]
    if not selected:
        selected = [artifact for artifact in artifacts["artifacts"] if artifact.get("name") == "release-intent"]
    require(len(selected) == 1, "Missing previous release record or durable intent")
    artifact = selected[0]
    require(
        artifact.get("expired") is False
        and parse_time(artifact["expires_at"]) > datetime.now(UTC)
        and artifact.get("workflow_run", {}).get("id") == run_id
        and artifact.get("workflow_run", {}).get("head_sha") == source_sha
        and valid_digest(artifact.get("digest")),
        "Expired/unbound release record",
    )
    with tempfile.NamedTemporaryFile(suffix=".zip") as temporary:
        with Path(temporary.name).open("wb") as stream:
            subprocess.run(
                ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact['id']}/zip"], stdout=stream, check=True
            )  # noqa: S603
        require("sha256:" + checksum(temporary.name) == artifact["digest"], "Release record checksum mismatch")
        with zipfile.ZipFile(temporary.name) as bundle:
            require(bundle.namelist() == ["journal.json"], "Unexpected release record members")
            Path(destination).write_bytes(bundle.read("journal.json"))


def verify_durable_intent(artifact_id, journal, repository, sha):
    artifact = github(f"repos/{repository}/actions/artifacts/{artifact_id}")
    require(
        artifact.get("id") == artifact_id
        and artifact.get("name") == "release-intent"
        and artifact.get("workflow_run", {}).get("id") == int(os.environ["GITHUB_RUN_ID"])
        and artifact.get("workflow_run", {}).get("head_sha") == sha
        and artifact.get("expired") is False
        and valid_digest(artifact.get("digest")),
        "Current publisher durable intent is missing/unbound",
    )
    with tempfile.NamedTemporaryFile(suffix=".zip") as temporary:
        with Path(temporary.name).open("wb") as stream:
            subprocess.run(
                ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip"], stdout=stream, check=True
            )  # noqa: S603
        require("sha256:" + checksum(temporary.name) == artifact["digest"], "Durable intent checksum mismatch")
        with zipfile.ZipFile(temporary.name) as bundle:
            require(bundle.namelist() == ["journal.json"], "Unexpected durable intent members")
            intended = json.loads(bundle.read("journal.json"))
    require(intended == json.loads(Path(journal).read_text()), "Local intent differs from durable GitHub intent")
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("archive")
    inspect.add_argument("--sha")
    inspect.add_argument("--platform", default=PLATFORM)
    inspect.add_argument("--output")
    fetch = commands.add_parser("fetch")
    fetch.add_argument("run_id", type=int)
    fetch.add_argument("directory")
    graph = commands.add_parser("registry-graph")
    graph.add_argument("reference")
    graph.add_argument("root_digest")
    space = commands.add_parser("space")
    space.add_argument("path")
    space.add_argument("minimum_gib", type=int)
    for name in ("prepare", "publish"):
        publication = commands.add_parser(name)
        publication.add_argument("run_id", type=int)
        publication.add_argument("version")
        publication.add_argument("--directory", required=True)
        publication.add_argument("--repository", default="docker.io/oldhero5/waldo")
        publication.add_argument("--journal", required=True)
        publication.add_argument("--dry-run", action="store_true")
        publication.add_argument("--resume-run", type=int)
    args = parser.parse_args()
    if args.action == "inspect":
        result = inspect_archive(args.archive, args.sha, args.platform)
        if args.output:
            write_json(args.output, result)
        else:
            print(json.dumps(result, indent=2))
    elif args.action == "fetch":
        fetch_candidates(args.run_id, args.directory)
    elif args.action == "registry-graph":
        verify_registry_graph(args.reference, args.root_digest)
    elif args.action == "space":
        free = shutil.disk_usage(args.path).free / 1024**3
        require(
            free >= args.minimum_gib,
            f"Only {free:.1f} GiB free; this phase requires an estimated {args.minimum_gib} GiB. "
            "Use an approved runner with more disk; do not prune unrelated resources.",
        )
        print(
            f"{free:.1f} GiB free; conservative capacity estimate is {args.minimum_gib} GiB (not measured image size)."
        )
    else:
        # The only release evidence input is an immutable GitHub producer run ID.
        # Download and validate its artifacts here; a user-supplied report path is never authority.
        fetch_candidates(args.run_id, args.directory)
        pair = validate_downloads(
            args.directory, os.environ["GITHUB_SHA"], os.environ["GITHUB_REPOSITORY"], args.run_id
        )
        require(re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", args.version), "Explicit version is required")
        # Artifact downloads and the protected environment can both wait. Recheck
        # the current source, CI and exact-commit approvals after that wait.
        command(["bash", ".github/scripts/check-release.sh"], env={**os.environ, "RELEASE_VERSION": args.version})
        if args.dry_run:
            print("Credential-free validation passed. No registry login or write performed.")
            return
        require(args.repository == "docker.io/oldhero5/waldo", "Unexpected publication repository")
        if args.resume_run and args.action == "prepare":
            restore_journal(args.resume_run, args.journal, os.environ["GITHUB_SHA"], os.environ["GITHUB_REPOSITORY"])
        custody = {
            name: json.loads((Path(args.directory) / name / "custody.json").read_text())
            for name in ("candidate-cpu", "candidate-cuda", "cuda-qualification")
        }
        record = {
            "source_sha": os.environ["GITHUB_SHA"],
            "candidate_run_id": args.run_id,
            "repository": os.environ["GITHUB_REPOSITORY"],
            "artifacts": custody,
            "candidates": pair,
            "gpu": json.loads((Path(args.directory) / "cuda-qualification/gpu.json").read_text()),
            "limits": "YOLO11 FP16 CUDA synthetic inference only; no SAM3/bfloat16/training/accuracy claim",
        }
        # Archive layout paths are execution details, not stable release identity.
        record["candidates"] = {
            kind: {key: value for key, value in data.items() if key != "source"} for kind, data in pair.items()
        }
        if args.action == "prepare":
            prepare_pair(pair, args.version.removeprefix("v"), args.repository, args.journal, Registry(), record)
            print("Pair intent prepared. Upload it as release-intent before publishing.")
            return
        intent_id = os.environ.get("RELEASE_INTENT_ARTIFACT_ID", "")
        require(intent_id.isdecimal(), "Durable prewrite GitHub intent artifact ID is required")
        intent = verify_durable_intent(
            int(intent_id), args.journal, os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
        )
        state = json.loads(Path(args.journal).read_text())
        require(state.get("evidence") == record, "Qualified pair differs from durable intent")
        state["intent_artifact"] = intent
        write_json(args.journal, state)
        username = os.environ.get("RELEASE_DOCKERHUB_USERNAME")
        token = os.environ.get("RELEASE_DOCKERHUB_TOKEN")
        require(username and token, "Protected release credentials are missing")
        command(["docker", "login", "docker.io", "--username", username, "--password-stdin"], input=token)
        # Extract only after verified archive names and bytes. Never use Docker load, which drops attestations.
        for kind, data in pair.items():
            candidate = Path(args.directory) / f"candidate-{kind}"
            layout = candidate / "layout"
            layout.mkdir(exist_ok=True)
            with tarfile.open(candidate / "candidate.oci.tar") as archive:
                archive.extractall(layout, filter="data")
            data["source"] = f"ocidir://{layout.resolve()}@{data['root_digest']}"

        def smoke(refs):
            for kind, ref in refs.items():
                command(
                    [
                        "bash",
                        ".github/scripts/smoke-published.sh",
                        kind,
                        ref,
                        pair[kind]["root_digest"],
                        pair[kind]["runnable_digest"],
                    ]
                )

        publish_pair(
            pair, args.version.removeprefix("v"), args.repository, args.journal, Registry(), smoke, evidence=record
        )


if __name__ == "__main__":
    try:
        main()
    except (GateError, KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Release gate failed: {error}") from error
