"""Normalize GitHub repository identity without accepting embedded credentials."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import AuditEvent, RepositoryConnection, Tenant
from platform_app.service import ServiceError, canonical_hash

OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
SECRET_REF = re.compile(r"^secret://[A-Za-z0-9][A-Za-z0-9/_-]{0,249}$")
ENV_REF = re.compile(r"^secret://env/(AIP_[A-Z0-9_]+)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


@dataclass(frozen=True)
class RepositoryProbe:
    ready: bool
    reason: str
    default_branch: str | None = None
    head_sha: str | None = None


def environment_secret_name(reference: str | None) -> str:
    match = ENV_REF.fullmatch(reference or "")
    if match is None:
        raise ServiceError(
            "CREDENTIAL_UNAVAILABLE", "Connection needs a process secret reference", 409
        )
    return match.group(1)


def probe_github_repository(
    repository_ref: str, token: str, client: httpx.Client | None = None,
) -> RepositoryProbe:
    """Read only probe with conservative permission checks."""
    try:
        if github_repository_ref(f"https://github.com/{repository_ref}") != repository_ref:
            return RepositoryProbe(False, "repository_identity_invalid")
    except ServiceError:
        return RepositoryProbe(False, "repository_identity_invalid")
    if not token or len(token) > 8192 or any(ord(c) < 33 for c in token):
        return RepositoryProbe(False, "credential_unavailable")
    owned = client is None
    client = client or httpx.Client(
        base_url="https://api.github.com", timeout=15, follow_redirects=False
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        def read(path: str, params: dict | None = None):
            response = client.get(path, headers=headers, params=params)
            if response.status_code != 200:
                raise ValueError("github_read_failed")
            return response.json()

        repo = read(f"/repos/{repository_ref}")
        if not isinstance(repo, dict) or str(repo.get("full_name", "")).lower() != repository_ref:
            return RepositoryProbe(False, "repository_identity_mismatch")
        if repo.get("archived") is True or repo.get("disabled") is True:
            return RepositoryProbe(False, "repository_inactive")
        if (not isinstance(repo.get("permissions"), dict)
                or repo["permissions"].get("push") is not True):
            return RepositoryProbe(False, "push_permission_unconfirmed")
        branch = repo.get("default_branch")
        if (not isinstance(branch, str) or not BRANCH.fullmatch(branch)
                or ".." in branch or "//" in branch or branch.endswith(("/", "."))):
            return RepositoryProbe(False, "default_branch_invalid")
        ref = read(f"/repos/{repository_ref}/git/ref/heads/{quote(branch, safe='/')}")
        if not isinstance(ref, dict) or ref.get("ref") != f"refs/heads/{branch}":
            return RepositoryProbe(False, "default_ref_invalid")
        head = (ref.get("object") or {}).get("sha")
        if not isinstance(head, str) or not SHA.fullmatch(head):
            return RepositoryProbe(False, "default_ref_invalid")
        pulls = read(f"/repos/{repository_ref}/pulls", {"state": "open", "per_page": 1})
        if not isinstance(pulls, list):
            return RepositoryProbe(False, "pulls_read_invalid")
        return RepositoryProbe(True, "read_and_push_verified", branch, head)
    except (httpx.HTTPError, ValueError, TypeError):
        return RepositoryProbe(False, "github_read_failed")
    finally:
        if owned:
            client.close()


def qualify_repository_connection(
    db: Session, connection: RepositoryConnection, token: str, actor: str,
    client: httpx.Client | None = None,
) -> RepositoryProbe:
    if connection.status == "disabled" or not connection.credential_ref:
        raise ServiceError("REPOSITORY_UNAVAILABLE", "Connection is not active", 409)
    if not actor.strip():
        raise ServiceError("INVALID_ACTOR", "Qualification actor is required", 400)
    probe = probe_github_repository(connection.repository_ref, token, client)
    connection.status = "ready" if probe.ready else "unverified"
    connection.checked_at = utcnow()
    tenant = db.get(Tenant, connection.tenant_id)
    db.add(AuditEvent(
        tenant_id=connection.tenant_id, actor=actor,
        action="repository.connection_qualify", target_ref=connection.id,
        arguments_hash=canonical_hash({
            "repository_ref": connection.repository_ref,
            "reason": probe.reason, "default_branch": probe.default_branch,
            "head_sha": probe.head_sha,
        }), policy_revision=tenant.policy_revision,
        outcome="ready" if probe.ready else "unverified",
    ))
    db.commit()
    return probe


def github_repository_ref(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
        or parsed.username or parsed.password or parsed.port is not None
        or parsed.query or parsed.fragment or "\\" in url
    ):
        raise ServiceError("REPOSITORY_INVALID", "Use a GitHub HTTPS repository URL", 400)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2:
        raise ServiceError("REPOSITORY_INVALID", "Repository URL must name an owner and repo", 400)
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not OWNER.fullmatch(owner) or not REPO.fullmatch(repo) or repo in {".", ".."}:
        raise ServiceError("REPOSITORY_INVALID", "Repository owner or name is invalid", 400)
    return f"{owner.lower()}/{repo.lower()}"


def validate_credential_ref(value: str | None) -> str | None:
    if value is not None and not SECRET_REF.fullmatch(value):
        raise ServiceError(
            "CREDENTIAL_REF_INVALID", "Use an opaque secret:// reference, not a token", 400
        )
    return value
