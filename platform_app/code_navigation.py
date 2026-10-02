"""Bounded read-only navigation of a pinned, already isolated repository snapshot."""

from __future__ import annotations

import ast
import hashlib
import os
import re
from pathlib import Path, PurePosixPath

from platform_app.service import ServiceError


class SnapshotNavigator:
    def __init__(self, snapshot_root: Path, commit: str, *, max_files: int = 1000):
        if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            raise ServiceError("CODE_SCOPE_INVALID", "Pinned commit is invalid", 409)
        if not 1 <= max_files <= 10_000:
            raise ValueError("File inventory cap is invalid")
        root = snapshot_root.resolve(strict=True)
        if not root.is_dir():
            raise ServiceError("CODE_SCOPE_INVALID", "Snapshot root is unavailable", 409)
        self.root = root
        self.commit = commit
        self.max_files = max_files

    def _path(self, name: str) -> Path:
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or ":" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or PurePosixPath(name).is_absolute()
        ):
            raise ServiceError("CODE_SCOPE_INVALID", "Code path is invalid", 400)
        candidate = self.root.joinpath(*name.split("/"))
        current = self.root
        for part in name.split("/"):
            current = current / part
            if current.is_symlink():
                raise ServiceError("CODE_SCOPE_INVALID", "Linked code path is unavailable", 409)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise ServiceError("CODE_NOT_FOUND", "Code file is unavailable", 404) from error
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ServiceError("CODE_SCOPE_INVALID", "Code path escaped snapshot", 409)
        return resolved

    def _files(self) -> list[str]:
        names: list[str] = []
        for parent, folders, files in os.walk(self.root, followlinks=False):
            folders[:] = sorted(
                folder for folder in folders
                if not (Path(parent) / folder).is_symlink()
                and not (Path(parent) / folder).is_junction()
                and (Path(parent) / folder).resolve().is_relative_to(self.root)
            )
            for filename in sorted(files):
                selected = Path(parent) / filename
                if (
                    selected.is_symlink()
                    or not selected.is_file()
                    or not selected.resolve().is_relative_to(self.root)
                ):
                    continue
                names.append(selected.relative_to(self.root).as_posix())
                if len(names) > self.max_files:
                    raise ServiceError("CODE_SCOPE_TOO_LARGE", "Snapshot file cap exceeded", 409)
        return sorted(names)

    def list_files(self, *, prefix: str = "", limit: int = 100) -> dict:
        if not 1 <= limit <= 200:
            raise ServiceError("INVALID_LIMIT", "File listing limit is invalid", 400)
        if prefix and (
            "\\" in prefix or ":" in prefix or ".." in prefix.split("/")
            or prefix.startswith("/")
        ):
            raise ServiceError("CODE_SCOPE_INVALID", "Code prefix is invalid", 400)
        files = [name for name in self._files() if name.startswith(prefix)]
        return {"commit": self.commit, "paths": files[:limit], "truncated": len(files) > limit}

    def read_excerpt(self, name: str, *, start_line: int = 1, max_lines: int = 200) -> dict:
        if start_line < 1 or not 1 <= max_lines <= 400:
            raise ServiceError("INVALID_LIMIT", "Code excerpt window is invalid", 400)
        selected = self._path(name)
        if selected.stat().st_size > 100_000:
            raise ServiceError("CODE_SCOPE_TOO_LARGE", "Code file exceeds read policy", 409)
        raw = selected.read_bytes()
        if len(raw) > 100_000:
            raise ServiceError("CODE_SCOPE_TOO_LARGE", "Code file exceeds read policy", 409)
        try:
            decoded = raw.decode("utf-8")
            lines = decoded.splitlines()
        except UnicodeDecodeError as error:
            raise ServiceError("CODE_UNREADABLE", "Code file is not UTF-8 text", 409) from error
        if start_line > max(1, len(lines)):
            raise ServiceError("CODE_NOT_FOUND", "Code line is unavailable", 404)
        return {
            "path": name,
            "commit": self.commit,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "start_line": start_line,
            "end_line": min(len(lines), start_line + max_lines - 1),
            "text": decoded if start_line == 1 and len(lines) <= max_lines else "\n".join(
                lines[start_line - 1 : start_line - 1 + max_lines]
            ),
            "truncated": start_line - 1 + max_lines < len(lines),
            "trust_label": "untrusted_repository_content",
        }

    def search_text(self, query: str, *, max_matches: int = 40) -> dict:
        if not 1 <= len(query) <= 120 or any(ord(c) < 32 for c in query):
            raise ServiceError("CODE_QUERY_INVALID", "Code search query is invalid", 400)
        if not 1 <= max_matches <= 100:
            raise ServiceError("INVALID_LIMIT", "Code search limit is invalid", 400)
        matches = []
        scanned = 0
        for name in self._files():
            selected = self._path(name)
            size = selected.stat().st_size
            if size > 100_000:
                continue
            if scanned + size > 5_000_000:
                return {"commit": self.commit, "matches": matches, "truncated": True}
            raw = selected.read_bytes()
            if len(raw) > 100_000 or scanned + len(raw) > 5_000_000:
                return {"commit": self.commit, "matches": matches, "truncated": True}
            scanned += len(raw)
            try:
                lines = raw.decode("utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            digest = hashlib.sha256(raw).hexdigest()
            for line_number, line in enumerate(lines, start=1):
                if query in line:
                    column = line.index(query)
                    window_start = max(0, column - 100)
                    matches.append({
                        "path": name, "line": line_number, "column": column + 1,
                        "text": line[window_start : window_start + 300],
                        "sha256": digest, "trust_label": "untrusted_repository_content",
                    })
                    if len(matches) >= max_matches:
                        return {"commit": self.commit, "matches": matches, "truncated": True}
        return {"commit": self.commit, "matches": matches, "truncated": False}

    def python_symbols(self, name: str, *, max_symbols: int = 100) -> dict:
        if not name.endswith(".py") or not 1 <= max_symbols <= 200:
            raise ServiceError("CODE_QUERY_INVALID", "Python symbol request is invalid", 400)
        selected = self._path(name)
        if selected.stat().st_size > 100_000:
            raise ServiceError("CODE_SCOPE_TOO_LARGE", "Code file exceeds symbol policy", 409)
        raw = selected.read_bytes()
        if len(raw) > 100_000:
            raise ServiceError("CODE_SCOPE_TOO_LARGE", "Code file exceeds symbol policy", 409)
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=name)
        except (UnicodeDecodeError, SyntaxError) as error:
            raise ServiceError("CODE_UNREADABLE", "Python symbols are unavailable", 409) from error
        symbols = [
            {"name": node.name, "line": node.lineno, "kind": type(node).__name__}
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        return {
            "path": name, "commit": self.commit,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "symbols": symbols[:max_symbols], "truncated": len(symbols) > max_symbols,
        }
