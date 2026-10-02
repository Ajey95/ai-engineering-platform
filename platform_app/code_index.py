"""Canonical, bounded code versions from one already-authorized Git archive."""

from __future__ import annotations

import ast
import hashlib
import re
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.models import CodeFileVersion, CodeIndexSnapshot, OutboxEvent, Run
from platform_app.repository_archive import SourceArchive
from platform_app.repository_connections import github_repository_ref
from platform_app.safe_archive import UnsafeArchive, extract_regular_tar
from platform_app.service import ServiceError

_CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
_JS_IMPORT = re.compile(r"(?:import\s+(?:[^\n]*?\s+from\s+)?|require\s*\()\s*['\"]([^'\"]+)['\"]")
_JS_SYMBOL = re.compile(r"\b(?:class|function)\s+([A-Za-z_$][\w$]*)")


def _python_symbols(tree: ast.AST) -> list[dict]:
    found = []

    def visit(body, prefix: str = ""):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{node.name}" if prefix else node.name
                found.append({
                    "qualified_name": name,
                    "kind": type(node).__name__,
                    "line": node.lineno,
                })
                if len(found) >= 200:
                    return
                visit(node.body, name)

    visit(getattr(tree, "body", []))
    return found[:200]


def _parse_code(path: str, raw: bytes) -> tuple[str, list[dict], list[str]] | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            return "python", [], []
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append("." * node.level + (node.module or ""))
        return "python", _python_symbols(tree), sorted(set(imports))[:200]
    imports = sorted(set(_JS_IMPORT.findall(text)))[:200]
    symbols = [
        {"qualified_name": match.group(1), "kind": "lexical", "line": text.count(
            "\n", 0, match.start()
        ) + 1}
        for match in _JS_SYMBOL.finditer(text)
    ][:200]
    return "javascript" if suffix in {".js", ".jsx"} else "typescript", symbols, imports


def index_source_archive(
    db: Session, run: Run, source: SourceArchive, work_root: Path,
) -> CodeIndexSnapshot:
    """Idempotently pin file hashes, names and imports to the exact run commit."""
    repository_url = (run.config_snapshot or {}).get("repository_url")
    if not isinstance(repository_url, str):
        raise ServiceError("CODE_INDEX_UNAVAILABLE", "Repository identity is missing", 409)
    repository_ref = github_repository_ref(repository_url)
    if (
        source.commit != run.base_commit
        or hashlib.sha256(source.archive).hexdigest() != source.sha256
    ):
        raise ServiceError("CODE_INDEX_CONFLICT", "Pinned source archive changed", 409)
    existing = db.scalar(select(CodeIndexSnapshot).where(
        CodeIndexSnapshot.tenant_id == run.tenant_id,
        CodeIndexSnapshot.project_id == run.project_id,
        CodeIndexSnapshot.repository_ref == repository_ref,
        CodeIndexSnapshot.commit == source.commit,
    ).with_for_update())
    if existing is not None:
        if existing.archive_sha256 != source.sha256:
            raise ServiceError("CODE_INDEX_CONFLICT", "Indexed revision differs", 409)
        return existing
    files = []
    scanned = 0
    truncated = False
    with tempfile.TemporaryDirectory(prefix="aip-code-index-", dir=work_root) as temporary:
        root = Path(temporary)
        try:
            names = extract_regular_tar(
                source.archive, root, max_archive_bytes=50_000_000,
                max_files=10_000, max_file_bytes=20_000_000,
                max_expanded_bytes=200_000_000,
            )
        except UnsafeArchive as error:
            raise ServiceError("CODE_INDEX_UNAVAILABLE", "Pinned archive is unsafe", 409) from error
        for name in sorted(names):
            path = root.joinpath(*name.split("/"))
            if not path.is_file() or Path(name).suffix.lower() not in _CODE_SUFFIXES:
                continue
            size = path.stat().st_size
            if (
                len(name) > 500 or size > 100_000
                or scanned + size > 5_000_000 or len(files) >= 1000
            ):
                truncated = True
                continue
            raw = path.read_bytes()
            parsed = _parse_code(name, raw)
            if parsed is None:
                truncated = True
                continue
            language, symbols, imports = parsed
            scanned += len(raw)
            files.append({
                "path": name, "sha256": hashlib.sha256(raw).hexdigest(),
                "language": language, "symbols": symbols, "imports": imports,
            })
    snapshot = CodeIndexSnapshot(
        tenant_id=run.tenant_id, project_id=run.project_id,
        repository_ref=repository_ref, commit=source.commit,
        archive_sha256=source.sha256, total_files=source.file_count,
        indexed_files=len(files), truncated=truncated,
    )
    db.add(snapshot)
    db.flush()
    for file in files:
        db.add(CodeFileVersion(
            snapshot_id=snapshot.id, tenant_id=run.tenant_id,
            project_id=run.project_id, **file,
        ))
    event_id = str(uuid5(NAMESPACE_URL, f"aip-code-index:{snapshot.id}"))
    db.add(OutboxEvent(
        id=event_id, tenant_id=run.tenant_id, topic="code.project",
        payload={"snapshot_id": snapshot.id, "project_id": run.project_id},
    ))
    return snapshot


def code_files_for_revision(
    db: Session, tenant_id: str, project_id: str,
    repository_ref: str, commit: str, query: str, limit: int = 20,
) -> tuple[CodeIndexSnapshot | None, list[CodeFileVersion]]:
    if not 1 <= len(query) <= 120 or not 1 <= limit <= 100:
        raise ServiceError("CODE_QUERY_INVALID", "Code lookup is outside policy", 400)
    snapshot = db.scalar(select(CodeIndexSnapshot).where(
        CodeIndexSnapshot.tenant_id == tenant_id,
        CodeIndexSnapshot.project_id == project_id,
        CodeIndexSnapshot.repository_ref == repository_ref,
        CodeIndexSnapshot.commit == commit,
    ))
    if snapshot is None:
        return None, []
    rows = db.scalars(select(CodeFileVersion).where(
        CodeFileVersion.snapshot_id == snapshot.id,
        CodeFileVersion.tenant_id == tenant_id,
        CodeFileVersion.project_id == project_id,
    ).order_by(CodeFileVersion.path)).all()
    needle = query.casefold()
    matches = [
        row for row in rows if needle in row.path.casefold()
        or any(needle in item["qualified_name"].casefold() for item in row.symbols)
    ]
    return snapshot, matches[:limit]
