#!/usr/bin/env python3
"""Regenerate docs-site/static/codebase-map.json by walking the repo.

Run from anywhere — paths resolve relative to the script's own location.

    python3 docs-site/scripts/build-codebase-map.py

The companion HTML viewer is at docs-site/static/codebase-map.html. Both
files are static — the docs site just serves them as assets.

This script is intentionally dependency-free: stdlib only, no Docusaurus,
no parsers heavier than `re`. The goal is a quick refresh — accuracy of
summaries depends on the docstrings we already write.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs-site" / "static" / "codebase-map.json"

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    "app/static",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
}


def short(text: str | None) -> str:
    if not text:
        return ""
    text = text.strip()
    # First sentence or first line, whichever is shorter.
    line = text.splitlines()[0].strip()
    sent = re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]
    return sent[:200]


def py_module_doc(path: Path) -> tuple[str, list[str]]:
    """Return (first-sentence summary, exported names) from a Python module."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return "", []
    summary = short(ast.get_docstring(tree))
    exports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                    exports = [
                        e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    ]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                exports.append(node.name)
    return summary, exports[:12]


def shell_module_doc(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in text[1:30]:
        s = line.strip()
        if not s.startswith("#"):
            continue
        s = s.lstrip("#").strip()
        if s and not s.startswith("!"):
            return short(s)
    return ""


def kind_for(path: str) -> str:
    p = path.lower()
    if p.startswith("alembic/versions/"):
        return "migration"
    if p.startswith("tests/"):
        return "test"
    if p.startswith("install/") or p in {"install.sh", "install.ps1", "install.cmd"}:
        return "installer"
    if p.startswith("docs-site/"):
        return "docs"
    if p.startswith("ui/src/pages/"):
        return "frontend-page"
    if p.startswith("ui/src/components/"):
        return "frontend-component"
    if p.startswith("ui/"):
        return "frontend"
    if p.startswith("scripts/"):
        return "script"
    if p.startswith("app/api/"):
        return "api"
    if p.startswith("app/"):
        return "app"
    if p.startswith("lib/agent/"):
        return "agent"
    if p.startswith("lib/workflow_blocks/"):
        return "workflow-block"
    if p.startswith("lib/db.py") or p.startswith("alembic/"):
        return "db"
    if p.startswith("lib/tasks.py"):
        return "task"
    if p.startswith("lib/"):
        return "util"
    if p.startswith("labeler/"):
        return "labeler"
    if p.startswith("trainer/"):
        return "trainer"
    return "other"


def walk_modules() -> list[dict]:
    out: list[dict] = []
    targets = [
        REPO / "app",
        REPO / "lib",
        REPO / "labeler",
        REPO / "trainer",
        REPO / "tests",
        REPO / "alembic" / "versions",
        REPO / "install",
        REPO / "scripts",
    ]
    # Top-level scripts.
    for f in [REPO / "install.sh", REPO / "install.ps1"]:
        if f.exists():
            targets.append(f)

    for t in targets:
        if t.is_file():
            files = [t]
        else:
            if not t.exists():
                continue
            files = sorted(t.rglob("*"))
        for f in files:
            if not f.is_file():
                continue
            rel = str(f.relative_to(REPO))
            if any(part in SKIP_DIRS for part in rel.split("/")):
                continue
            if f.suffix not in {".py", ".sh", ".ps1"}:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.count("\n") + 1
            if f.suffix == ".py":
                summary, exports = py_module_doc(f)
            else:
                summary, exports = shell_module_doc(f), []
            out.append(
                {
                    "path": rel,
                    "package": rel.replace("/", ".").rsplit(".", 1)[0] if f.suffix == ".py" else "",
                    "kind": kind_for(rel),
                    "lines": lines,
                    "summary": summary,
                    "exports": exports,
                }
            )

    # Frontend pages + AgentPanel — we want them in the tree even though they're TSX.
    ui_pages = REPO / "ui" / "src" / "pages"
    if ui_pages.exists():
        for f in sorted(ui_pages.glob("*.tsx")):
            rel = str(f.relative_to(REPO))
            text = f.read_text(encoding="utf-8", errors="replace")
            summary = ""
            m = re.search(r"/\*\*\n([^*]|\*(?!/))*?\*/", text)
            if m:
                summary = short(re.sub(r"^\s*\*\s?", "", m.group(0).strip("/*"), flags=re.M))
            out.append(
                {
                    "path": rel,
                    "package": "",
                    "kind": "frontend-page",
                    "lines": text.count("\n") + 1,
                    "summary": summary,
                    "exports": [],
                }
            )
    panel = REPO / "ui" / "src" / "components" / "AgentPanel.tsx"
    if panel.exists():
        text = panel.read_text(encoding="utf-8", errors="replace")
        out.append(
            {
                "path": str(panel.relative_to(REPO)),
                "package": "",
                "kind": "frontend-component",
                "lines": text.count("\n") + 1,
                "summary": "Floating sidebar agent panel — read-only, /agent/stream backend.",
                "exports": [],
            }
        )
    return out


def parse_services() -> list[dict]:
    """Light YAML parser for top-level services in docker-compose.yml."""
    out: list[dict] = []
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    # Slice the file into per-service blocks. Robust enough for our compose layout.
    lines = compose.splitlines()
    in_services = False
    blocks: list[tuple[str, list[str]]] = []
    cur_name = None
    cur_lines: list[str] = []
    for line in lines:
        if line.rstrip() == "services:":
            in_services = True
            continue
        if not in_services:
            continue
        if re.match(r"^[a-zA-Z_]", line):
            # Top-level key — end of services section.
            break
        m = re.match(r"^  ([a-zA-Z0-9_-]+):\s*$", line)
        if m:
            if cur_name:
                blocks.append((cur_name, cur_lines))
            cur_name = m.group(1)
            cur_lines = []
        elif cur_name:
            cur_lines.append(line)
    if cur_name:
        blocks.append((cur_name, cur_lines))

    KIND = {
        "postgres": "infra",
        "redis": "infra",
        "minio": "infra",
        "minio-init": "infra",
        "ollama": "llm",
        "ollama-init": "llm",
    }
    DESC = {
        "postgres": "Primary database — workspaces, projects, jobs, models.",
        "redis": "Celery broker + cache.",
        "minio": "S3-compatible object store for videos, frames, weights.",
        "minio-init": "One-shot bucket creation.",
        "ollama": "Local LLM server for the agent (gemma4:e4b by default).",
        "ollama-init": "One-shot model pull on first boot.",
        "waldo-app": "FastAPI app — REST API, WebSocket, SPA host.",
        "waldo-labeler": "SAM 3 auto-labeling worker (Apple/CPU).",
        "waldo-trainer": "YOLO training worker (Apple/CPU).",
        "waldo-labeler-nvidia": "SAM 3 auto-labeling worker (CUDA).",
        "waldo-trainer-nvidia": "YOLO training worker (CUDA).",
    }
    for name, body in blocks:
        text = "\n".join(body)
        ports_m = re.findall(r'-\s*"([0-9]+:[0-9]+(?:/(?:tcp|udp))?)"', text)
        depends = re.findall(r"^\s+([a-z][a-z0-9_-]+):\s*\n\s+condition:", text, flags=re.M)
        profile_m = re.search(r"^\s+profiles:\s*\n((?:\s+-\s+\S+\n?)+)", text, flags=re.M)
        profile = None
        if profile_m:
            ps = re.findall(r"-\s*([a-z]+)", profile_m.group(1))
            profile = ps[0] if ps else None
        df_m = re.search(r"dockerfile:\s*(\S+)", text)
        img_m = re.search(r"image:\s*(\S+)", text)
        out.append(
            {
                "id": name,
                "name": name,
                "kind": KIND.get(name) or ("worker" if "labeler" in name or "trainer" in name else "app"),
                "description": DESC.get(name, ""),
                "image_or_dockerfile": df_m.group(1) if df_m else (img_m.group(1) if img_m else ""),
                "ports": ports_m,
                "depends_on": depends,
                "profile": profile,
            }
        )
    return out


def parse_endpoints() -> list[dict]:
    out: list[dict] = []
    api_dir = REPO / "app" / "api"
    if not api_dir.exists():
        return out
    for f in sorted(api_dir.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        # Detect router-level auth dep.
        router_authed = bool(re.search(r"APIRouter\([^)]*Depends\(get_current_user\)", text))
        rel = str(f.relative_to(REPO))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            method = path = None
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    m = dec.func.attr.lower()
                    if m in {"get", "post", "put", "patch", "delete"} and dec.args:
                        a = dec.args[0]
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            method, path = m.upper(), a.value
            if not method:
                continue
            full_path = f"/api/v1{path}" if not path.startswith("/api") else path
            summary = short(ast.get_docstring(node))
            handler_authed = (
                any(isinstance(arg.annotation, ast.Name) and arg.annotation.id in {"User"} for arg in node.args.args)
                or "Depends(get_current_user)" in text[max(0, node.col_offset) : node.end_col_offset or len(text)]
            )
            auth = "required" if (router_authed or handler_authed) else "none"
            out.append(
                {
                    "method": method,
                    "path": full_path,
                    "module": rel,
                    "auth": auth,
                    "summary": summary,
                }
            )
    return out


def parse_celery_tasks() -> list[dict]:
    f = REPO / "lib" / "tasks.py"
    if not f.exists():
        return []
    text = f.read_text(encoding="utf-8")
    out: list[dict] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = queue = None
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        name = kw.value.value
                    if kw.arg == "queue" and isinstance(kw.value, ast.Constant):
                        queue = kw.value.value
        if name and name.startswith("waldo."):
            out.append(
                {
                    "name": name,
                    "module": "lib/tasks.py",
                    "queue": queue or "default",
                    "summary": short(ast.get_docstring(node)),
                }
            )
    return out


def parse_db_tables() -> list[dict]:
    f = REPO / "lib" / "db.py"
    if not f.exists():
        return []
    tree = ast.parse(f.read_text(encoding="utf-8"))
    out: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        is_model = any(
            (isinstance(b, ast.Name) and b.id == "Base") or (isinstance(b, ast.Attribute) and b.attr == "Base")
            for b in node.bases
        )
        if not is_model:
            continue
        tablename = ""
        cols: list[str] = []
        fks: list[str] = []
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        if tgt.id == "__tablename__" and isinstance(stmt.value, ast.Constant):
                            tablename = stmt.value.value
                        elif isinstance(stmt.value, ast.Call):
                            cf = stmt.value.func
                            cf_name = (
                                cf.attr
                                if isinstance(cf, ast.Attribute)
                                else (cf.id if isinstance(cf, ast.Name) else "")
                            )
                            if cf_name in {"Column", "deferred"}:
                                cols.append(tgt.id)
                                # Look for ForeignKey args.
                                target = stmt.value.args[0] if cf_name == "deferred" and stmt.value.args else stmt.value
                                if isinstance(target, ast.Call):
                                    for a in target.args:
                                        if (
                                            isinstance(a, ast.Call)
                                            and isinstance(a.func, ast.Name)
                                            and a.func.id == "ForeignKey"
                                        ):
                                            for fa in a.args:
                                                if isinstance(fa, ast.Constant):
                                                    fks.append(fa.value)
        if tablename:
            out.append(
                {
                    "name": tablename,
                    "module": "lib/db.py",
                    "summary": short(ast.get_docstring(node)),
                    "columns": cols,
                    "fk_to": fks,
                }
            )
    return out


def parse_frontend_pages() -> list[dict]:
    pages_dir = REPO / "ui" / "src" / "pages"
    main_tsx = REPO / "ui" / "src" / "main.tsx"
    routes: dict[str, str] = {}
    if main_tsx.exists():
        for m in re.finditer(r'<Route\s+path="([^"]+)"\s+element=\{<(\w+)', main_tsx.read_text()):
            routes[m.group(2)] = m.group(1)
    out: list[dict] = []
    if not pages_dir.exists():
        return out
    for f in sorted(pages_dir.glob("*.tsx")):
        comp = f.stem
        route = routes.get(comp, "")
        text = f.read_text(encoding="utf-8")
        endpoints = sorted(set(re.findall(r"/api/v1/[A-Za-z0-9_/{}-]+", text)))
        # Lazy detection: imported via lazy() in main.tsx.
        lazy = main_tsx.exists() and f"const {comp} = lazy(" in main_tsx.read_text()
        # Page summary from leading JSDoc.
        summary = ""
        m = re.match(r"\s*/\*\*([^*]|\*(?!/))*\*/", text)
        if m:
            summary = short(re.sub(r"^\s*\*\s?", "", m.group(0).strip("/*"), flags=re.M))
        out.append(
            {
                "route": route or "/" + comp.lower(),
                "file": str(f.relative_to(REPO)),
                "lazy": bool(lazy),
                "summary": summary,
                "calls_endpoints": endpoints,
            }
        )
    return out


def parse_agent_tools() -> list[dict]:
    f = REPO / "lib" / "agent" / "tools.py"
    if not f.exists():
        return []
    text = f.read_text(encoding="utf-8")
    tree = ast.parse(text)
    read_set: set[str] = set()
    action_set: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tgt = node.targets[0].id
            if tgt in {"READ_TOOLS", "ACTION_TOOLS"} and isinstance(node.value, ast.List):
                names = {e.id for e in node.value.elts if isinstance(e, ast.Name)}
                if tgt == "READ_TOOLS":
                    read_set = names
                else:
                    action_set = names
    out: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        is_tool = any(
            (isinstance(d, ast.Name) and d.id == "tool")
            or (isinstance(d, ast.Attribute) and d.attr == "tool")
            or (
                isinstance(d, ast.Call)
                and (
                    (isinstance(d.func, ast.Name) and d.func.id == "tool")
                    or (isinstance(d.func, ast.Attribute) and d.func.attr == "tool")
                )
            )
            for d in node.decorator_list
        )
        if not is_tool:
            continue
        kind = "action" if node.name in action_set else "read" if node.name in read_set else "read"
        out.append(
            {
                "name": node.name,
                "kind": kind,
                "module": "lib/agent/tools.py",
                "summary": short(ast.get_docstring(node)),
            }
        )
    return out


def build_graphs(services: list[dict], endpoints: list[dict], tasks: list[dict], tables: list[dict]) -> dict:
    s2s: list[dict] = []
    for s in services:
        for d in s.get("depends_on", []):
            s2s.append({"from": s["id"], "to": d, "label": "depends_on"})
    # Add the two extras the diagram cares about.
    s2s.append({"from": "waldo-app", "to": "ollama", "label": "HTTP /api/chat"})

    # Endpoint -> task: scan handler bodies for send_task("waldo.…").
    e2t: list[dict] = []
    for f in (REPO / "app" / "api").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        tree = ast.parse(text)
        # Index endpoints in this file by line range.
        ep_lines: list[tuple[int, int, str, str]] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            method = path = None
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.args:
                    m = dec.func.attr.lower()
                    if m in {"get", "post", "put", "patch", "delete"}:
                        a = dec.args[0]
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            method, path = m.upper(), a.value
            if method:
                ep_lines.append((node.lineno, node.end_lineno or node.lineno, method, path))
        for m in re.finditer(r'send_task\(\s*["\'](waldo\.[^"\']+)', text):
            line = text.count("\n", 0, m.start()) + 1
            best = None
            for s_ln, e_ln, method, path in ep_lines:
                if s_ln <= line <= e_ln:
                    best = (method, path)
                    break
            if best:
                full = best[1] if best[1].startswith("/api") else f"/api/v1{best[1]}"
                e2t.append({"endpoint": f"{best[0]} {full}", "task": m.group(1)})

    # Task -> table: which tasks add/query which models, scanned across labeler/trainer.
    table_classes: dict[str, str] = {}
    db = (REPO / "lib" / "db.py").read_text(encoding="utf-8")
    for cls_match in re.finditer(r"class (\w+)\(Base\):", db):
        cls = cls_match.group(1)
        body_start = cls_match.end()
        tname_m = re.search(r'__tablename__\s*=\s*"([^"]+)"', db[body_start : body_start + 2000])
        if tname_m:
            table_classes[cls] = tname_m.group(1)

    t2t: list[dict] = []
    task_name_to_func = {t["name"].split(".")[-1]: t["name"] for t in tasks}
    tasks_text = (REPO / "lib" / "tasks.py").read_text(encoding="utf-8") if (REPO / "lib" / "tasks.py").exists() else ""
    # For each Celery task function, scan a simple set of import-ed handlers and look for `session.add(<Cls>(` or `query(<Cls>)`.
    for func_name, task_name in task_name_to_func.items():
        # Scan tasks.py + likely handlers.
        scan = [tasks_text]
        for sub in (
            "labeler/video_labeler.py",
            "labeler/text_labeler.py",
            "labeler/exemplar_labeler.py",
            "trainer/train_manager.py",
            "trainer/exporter.py",
            "labeler/predict_video.py",
        ):
            p = REPO / sub
            if p.exists():
                scan.append(p.read_text(encoding="utf-8"))
        touched: set[str] = set()
        for blob in scan:
            for cls, tbl in table_classes.items():
                if re.search(rf"\b{cls}\s*\(", blob) or re.search(rf"query\(\s*{cls}\b", blob):
                    touched.add(tbl)
        if touched:
            t2t.append({"task": task_name, "writes": sorted(touched)})

    return {"service_to_service": s2s, "endpoint_to_task": e2t, "task_to_table": t2t}


def main():
    services = parse_services()
    modules = walk_modules()
    endpoints = parse_endpoints()
    tasks = parse_celery_tasks()
    tables = parse_db_tables()
    pages = parse_frontend_pages()
    tools = parse_agent_tools()
    graphs = build_graphs(services, endpoints, tasks, tables)

    out = {
        "generated_at": str(date.today()),
        "repo": "oldhero5/waldo",
        "main_branch": "main",
        "services": services,
        "modules": modules,
        "api_endpoints": endpoints,
        "celery_tasks": tasks,
        "db_tables": tables,
        "frontend_pages": pages,
        "agent_tools": tools,
        "graphs": graphs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {OUT.relative_to(REPO)} — "
        f"{len(services)} services, {len(modules)} modules, "
        f"{len(endpoints)} endpoints, {len(tasks)} tasks, "
        f"{len(tables)} tables, {len(pages)} pages, {len(tools)} agent tools",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
