"""Normalize GitHub repository identity without accepting embedded credentials."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from platform_app.service import ServiceError

OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
SECRET_REF = re.compile(r"^secret://[A-Za-z0-9][A-Za-z0-9/_-]{0,249}$")


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
