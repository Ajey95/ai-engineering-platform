"""Fetch one pinned GitHub commit in a trusted worker without exposing its token."""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.models import RepositoryConnection, Run
from platform_app.repository_archive import SourceArchive, archive_repository_commit
from platform_app.repository_connections import environment_secret_name, github_repository_ref
from platform_app.service import ServiceError


class RepositoryFetchError(ValueError):
    pass


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _git(args: list[str], *, env: dict[str, str], timeout: int) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], env=env, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RepositoryFetchError("Pinned repository fetch did not complete") from error
    if completed.returncode:
        # Git output can contain a URL, repository name or authentication detail.
        raise RepositoryFetchError("Pinned repository fetch was rejected")
    return completed.stdout.strip()


def _fetch_archive(
    url: str, commit: str, work_root: Path, *, authorization: str | None
) -> SourceArchive:
    if not _COMMIT.fullmatch(commit):
        raise RepositoryFetchError("Pinned Git commit is invalid")
    work_root = work_root.resolve(strict=True)
    if not work_root.is_dir():
        raise RepositoryFetchError("Trusted repository work root is unavailable")
    with tempfile.TemporaryDirectory(prefix="aip-fetch-", dir=work_root) as temporary:
        checkout = Path(temporary).resolve(strict=True)
        if not checkout.is_relative_to(work_root):
            raise RepositoryFetchError("Repository work root changed")
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "HOME": str(checkout),
        }
        if os.name == "nt":
            environment["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        if authorization is not None:
            environment.update({
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"Authorization: Basic {authorization}",
            })
        _git(["init", "--quiet", str(checkout)], env=environment, timeout=20)
        _git([
            "-C", str(checkout), "fetch", "--no-tags", "--depth=1",
            url, commit,
        ], env=environment, timeout=120)
        resolved = _git([
            "-C", str(checkout), "rev-parse", "--verify", "FETCH_HEAD^{commit}",
        ], env=environment, timeout=15)
        if resolved != commit:
            raise RepositoryFetchError("Remote returned a different commit")
        return archive_repository_commit(checkout, commit)


def fetch_pinned_github_archive(
    repository_ref: str, commit: str, token: str, work_root: Path
) -> SourceArchive:
    if not _COMMIT.fullmatch(commit):
        raise RepositoryFetchError("Pinned Git commit is invalid")
    try:
        canonical = github_repository_ref(f"https://github.com/{repository_ref}")
    except ServiceError as error:
        raise RepositoryFetchError("GitHub repository identity is invalid") from error
    if canonical != repository_ref:
        raise RepositoryFetchError("GitHub repository identity is invalid")
    if not token or len(token) > 8192 or any(ord(char) < 33 for char in token):
        raise RepositoryFetchError("Repository credential is unavailable")
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    return _fetch_archive(
        f"https://github.com/{repository_ref}.git", commit, work_root,
        authorization=encoded,
    )


def fetch_authorized_run_source(
    db: Session, run_id: str, work_root: Path
) -> SourceArchive:
    """Resolve only a ready, run-scoped GitHub connection in a trusted worker."""
    run = db.get(Run, run_id)
    if run is None:
        raise RepositoryFetchError("Run is unavailable")
    url = run.config_snapshot.get("repository_url")
    if not isinstance(url, str):
        raise RepositoryFetchError("Pinned repository identity is missing")
    try:
        repository_ref = github_repository_ref(url)
    except ServiceError as error:
        raise RepositoryFetchError("Pinned repository identity is invalid") from error
    connection = db.scalar(select(RepositoryConnection).where(
        RepositoryConnection.tenant_id == run.tenant_id,
        RepositoryConnection.project_id == run.project_id,
        RepositoryConnection.provider == "github",
        RepositoryConnection.repository_ref == repository_ref,
        RepositoryConnection.status == "ready",
    ))
    if connection is None:
        raise RepositoryFetchError("Run has no ready repository connection")
    try:
        secret_name = environment_secret_name(connection.credential_ref)
    except ServiceError as error:
        raise RepositoryFetchError("Repository credential reference is unavailable") from error
    token = os.environ.get(secret_name, "")
    return fetch_pinned_github_archive(repository_ref, run.base_commit, token, work_root)
