"""Verify a scoped GitHub repository connection using a process secret reference."""

from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import select

from platform_app.db import SessionLocal
from platform_app.models import RepositoryConnection
from platform_app.repository_connections import (
    environment_secret_name,
    qualify_repository_connection,
)
from platform_app.service import ServiceError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            connection = db.scalar(select(RepositoryConnection).where(
                RepositoryConnection.id == args.connection_id,
            ).with_for_update())
            if connection is None:
                raise ServiceError("NOT_FOUND", "Repository connection not found", 404)
            token = os.environ.get(environment_secret_name(connection.credential_ref), "")
            if not token:
                raise ServiceError("CREDENTIAL_UNAVAILABLE", "GitHub token is unavailable", 409)
            result = qualify_repository_connection(db, connection, token, args.actor)
            print(json.dumps({
                "connection_id": connection.id, "status": connection.status,
                "reason": result.reason, "default_branch": result.default_branch,
                "head_sha": result.head_sha,
            }))
            return 0 if result.ready else 2
    except ServiceError as error:
        parser.error(f"Repository qualification failed: {error.code}: {error.message}")


if __name__ == "__main__":
    raise SystemExit(main())
