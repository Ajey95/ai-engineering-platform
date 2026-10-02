"""Publish an explicitly approved draft PR with a process-scoped GitHub token."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.github_publication import GitHubDraftPublisher, GitHubPublicationError
from platform_app.models import PublicationApproval, RepositoryConnection
from platform_app.publication_dispatch import publish_approved_run
from platform_app.service import ServiceError

ENV_REF = re.compile(r"^secret://env/(AIP_[A-Z0-9_]+)$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            approval = db.get(PublicationApproval, args.approval_id)
            if approval is None:
                raise ServiceError("NOT_FOUND", "Publication approval not found", 404)
            connection = db.get(RepositoryConnection, approval.connection_id)
            match = ENV_REF.fullmatch(connection.credential_ref or "") if connection else None
            if match is None:
                raise ServiceError(
                    "CREDENTIAL_UNAVAILABLE", "Connection needs a process secret reference", 409
                )
            token = os.environ.get(match.group(1), "")
            if not token:
                raise ServiceError("CREDENTIAL_UNAVAILABLE", "GitHub token is unavailable", 409)
        body = args.body_file.read_text(encoding="utf-8")
        publisher = GitHubDraftPublisher(token)
        result = publish_approved_run(
            SessionLocal, args.approval_id, settings().artifact_dir,
            publisher, title=args.title, body=body,
        )
        print(json.dumps({
            "status": "PUBLISHED", "draft_pr_url": result.url,
            "number": result.number, "branch": result.branch,
            "commit_sha": result.commit_sha, "reconciled": result.reconciled,
        }))
        return 0
    except (ServiceError, GitHubPublicationError, OSError, UnicodeError) as error:
        parser.error(f"Draft PR publication failed: {type(error).__name__}: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
